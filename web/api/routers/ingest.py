"""ingestion endpoint — upload files and ingest into the wiki."""

from __future__ import annotations

import io
import json
import queue
import threading
import time
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from web.api.services import (
    EMBED_URL,
    LLAMA_URL,
    RAW_DIR,
    START_EMBED_SH,
    check_server_health,
    list_raw_files,
)

router = APIRouter()
logger = __import__("logging").getLogger(__name__)


# --- embed server auto-management. ---

def _ensure_embed_server() -> bool:
    """start the embedding server if it's not already running.

    blocks up to 30s waiting for healthy status. returns True if
    the server is healthy, False on timeout.
    """
    if check_server_health(EMBED_URL):
        return True

    if not START_EMBED_SH.exists():
        logger.warning("start_embed_server.sh not found, can't auto-start")
        return False

    logger.info("auto-starting embed server for ingest + embeddings")
    import subprocess
    subprocess.Popen(
        ["bash", str(START_EMBED_SH), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # wait for healthy (30s timeout, 1s polling).
    for _ in range(30):
        time.sleep(1)
        if check_server_health(EMBED_URL):
            logger.info("embed server is healthy")
            return True

    logger.warning("embed server failed to start within 30s")
    return False


def _stop_embed_server() -> None:
    """stop the embedding server to free RAM."""
    if not check_server_health(EMBED_URL):
        return
    if not START_EMBED_SH.exists():
        return

    logger.info("auto-stopping embed server to free RAM")
    import subprocess
    subprocess.run(
        ["bash", str(START_EMBED_SH), "stop"],
        capture_output=True,
        timeout=10,
    )


# --- task tracking. ---
# only one ingest can run at a time (llama.cpp has limited slots).

_active_task: dict[str, Any] | None = None
_task_lock = threading.Lock()
_task_events: dict[str, queue.Queue] = {}
_cancel_flag = threading.Event()

# stale-queue reaper: if an SSE client never connects (or drops before a
# terminal event), a completed task's queue would otherwise sit in memory
# forever. every progress request also reaps anything older than this.
# declared up here (next to the other shared task-tracking state) so
# every callsite sees the same binding regardless of import order.
_TASK_QUEUE_TTL_SECONDS = 30 * 60  # 30 minutes
_task_created_at: dict[str, float] = {}


def _run_ingest(
    task_id: str,
    filename: str,
    overwrite: bool,
    use_embeddings: bool,
) -> None:
    """run ingestion in a background thread, capturing output as events."""
    global _active_task
    event_q = _task_events[task_id]
    t0 = time.time()
    embed_was_off = False

    try:
        if _cancel_flag.is_set():
            event_q.put({"event": "cancelled", "message": "Cancelled before starting."})
            return

        # auto-start embed server if needed.
        if use_embeddings:
            embed_was_off = not check_server_health(EMBED_URL)
            if embed_was_off:
                event_q.put({"event": "embed_starting",
                             "message": "Starting embedding server (~2 GB RAM)..."})
                event_q.put({"event": "progress",
                             "message": "Starting embedding server (~2 GB RAM)..."})
            else:
                event_q.put({"event": "embed_was_manual",
                             "message": "Embedding server already running."})
            if embed_was_off and not _ensure_embed_server():
                event_q.put({
                    "event": "error",
                    "message": "Embedding server failed to start. Try without embeddings.",
                    "elapsed_seconds": round(time.time() - t0, 1),
                })
                return
            if embed_was_off:
                event_q.put({"event": "embed_ready",
                             "message": "Embedding server ready."})

        if _cancel_flag.is_set():
            event_q.put({"event": "cancelled", "message": "Cancelled."})
            return

        event_q.put({"event": "started", "filename": filename})

        # capture print output from the ingest pipeline.
        output_buffer = io.StringIO()
        from ingest import ingest as run_ingest

        with redirect_stdout(output_buffer):
            success = run_ingest(
                filename,
                overwrite=overwrite,
                use_embeddings=use_embeddings,
            )

        # parse captured output for progress info.
        output = output_buffer.getvalue()
        for line in output.splitlines():
            line = line.strip()
            if line:
                event_q.put({"event": "progress", "message": line})

        elapsed = time.time() - t0
        event_q.put({
            "event": "complete",
            "success": success,
            "elapsed_seconds": round(elapsed, 1),
            "output": output,
            "embed_was_manual": use_embeddings and not embed_was_off,
        })

    except Exception as e:
        elapsed = time.time() - t0
        event_q.put({
            "event": "error",
            "message": f"{type(e).__name__}: {e}",
            "elapsed_seconds": round(elapsed, 1),
        })

    finally:
        with _task_lock:
            global _active_task
            _active_task = None
        # auto-stop embed server if we started it.
        if use_embeddings and embed_was_off:
            event_q.put({"event": "embed_stopping",
                         "message": "Stopping embedding server to free RAM."})
            event_q.put({"event": "progress",
                         "message": "Stopping embedding server to free RAM..."})
            _stop_embed_server()


@router.get("/files")
async def list_files() -> dict[str, Any]:
    """list all files in raw/ with their ingestion status.

    status is one of: 'pending' (not yet ingested), 'ingested' (up to date),
    'changed' (content hash differs from last ingest).
    """
    files = list_raw_files()
    pending = sum(1 for f in files if f["status"] == "pending")
    ingested = sum(1 for f in files if f["status"] == "ingested")
    changed = sum(1 for f in files if f["status"] == "changed")

    return {
        "files": files,
        "total": len(files),
        "pending": pending,
        "ingested": ingested,
        "changed": changed,
    }


# upload size cap. picked to keep a single upload well under the pdf / xml
# sizes we expect for personal notes without allowing a tmp-disk attack.
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# permitted extensions for the ingest pipeline. keep in sync with the ingest
# script's parsers — unknown extensions are rejected early rather than
# wasting disk on files we can't process.
_ALLOWED_UPLOAD_SUFFIXES = frozenset({
    ".md", ".txt", ".pdf", ".xml", ".html", ".htm",
    ".rtf", ".csv", ".json", ".rst",
})


def _safe_upload_name(raw_name: str) -> str | None:
    """return a traversal-safe basename, or None if the name is unsafe.

    strips any path components the client may have supplied, rejects hidden
    files (``.foo``) and empty names, and enforces an explicit extension
    allow-list.
    """
    # strip path components the client may have embedded.
    base = Path(raw_name).name.strip()
    if not base or base in (".", ".."):
        return None
    if base.startswith("."):
        # don't let a client drop .env or .gitignore into raw/.
        return None
    if Path(base).suffix.lower() not in _ALLOWED_UPLOAD_SUFFIXES:
        return None
    return base


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, str]:
    """upload a file to raw/ for later ingestion.

    accepts the extensions listed in ``_ALLOWED_UPLOAD_SUFFIXES``. the
    filename is sanitised (basename only, no traversal, no hidden files)
    and the upload is capped at 50 MB to prevent disk-fill.
    """
    if not file.filename:
        raise HTTPException(400, "Filename is required.")

    safe_name = _safe_upload_name(file.filename)
    if safe_name is None:
        raise HTTPException(400, "Invalid filename or unsupported file type.")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_root = RAW_DIR.resolve()
    dest = (raw_root / safe_name).resolve()

    # final belt-and-braces containment check after resolving symlinks.
    if raw_root != dest.parent:
        raise HTTPException(400, "Invalid upload destination.")

    # stream-read with a running byte cap so a huge body can't OOM the server.
    total = 0
    chunks: list[bytes] = []
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"Upload exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)

    dest.write_bytes(b"".join(chunks))

    return {
        "filename": safe_name,
        "size_bytes": total,
        "message": f"Uploaded to raw/{safe_name}. Ready to ingest.",
    }


@router.post("")
async def start_ingest(body: dict[str, Any]) -> dict[str, Any]:
    """start ingesting a file. returns a task id for progress tracking.

    only one ingest can run at a time. if an ingest is already running,
    returns 409 conflict with the active task info.
    """
    global _active_task

    filename = body.get("filename", "").strip()
    overwrite = body.get("overwrite", False)
    use_embeddings = body.get("use_embeddings", False)

    if not filename:
        raise HTTPException(400, "Filename is required.")

    # reject path components and verify the resolved path is inside RAW_DIR.
    if filename != Path(filename).name:
        raise HTTPException(400, "Invalid filename.")
    raw_root = RAW_DIR.resolve()
    raw_path = (raw_root / filename).resolve()
    if raw_path.parent != raw_root:
        raise HTTPException(400, "Invalid filename.")
    if not raw_path.exists():
        raise HTTPException(404, f"File not found: raw/{filename}")

    # check server.
    if not check_server_health(LLAMA_URL):
        raise HTTPException(
            503,
            "The LLM server is not running. Start it from the Server panel first.",
        )

    with _task_lock:
        if _active_task is not None:
            raise HTTPException(409, {
                "message": "An ingest is already running.",
                "active_task": _active_task,
            })

        task_id = str(uuid.uuid4())[:8]
        _active_task = {
            "task_id": task_id,
            "filename": filename,
            "started_at": time.time(),
        }
        _task_events[task_id] = queue.Queue()
        _task_created_at[task_id] = time.time()
        # clear inside the lock so a cancel request issued between our
        # release and the thread start can't be silently dropped.
        _cancel_flag.clear()

    # launch in background thread.
    thread = threading.Thread(
        target=_run_ingest,
        args=(task_id, filename, overwrite, use_embeddings),
        daemon=True,
    )
    thread.start()

    return {
        "task_id": task_id,
        "filename": filename,
        "message": f"Ingestion started for {filename}. Use /api/ingest/progress/{task_id} to track.",
    }


@router.post("/all")
async def start_ingest_all(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """start ingesting all pending files.

    same concurrency rules as single ingest — only one at a time.
    """
    global _active_task
    body = body or {}
    overwrite = body.get("overwrite", False)
    use_embeddings = body.get("use_embeddings", False)

    if not check_server_health(LLAMA_URL):
        raise HTTPException(
            503,
            "The LLM server is not running. Start it from the Server panel first.",
        )

    with _task_lock:
        if _active_task is not None:
            raise HTTPException(409, {
                "message": "An ingest is already running.",
                "active_task": _active_task,
            })

        task_id = str(uuid.uuid4())[:8]
        _active_task = {
            "task_id": task_id,
            "filename": "(all pending)",
            "started_at": time.time(),
        }
        _task_events[task_id] = queue.Queue()
        _task_created_at[task_id] = time.time()
        # clear inside the lock so a cancel issued between release and
        # thread start can't be silently dropped.
        _cancel_flag.clear()

    def _run_all():
        global _active_task
        event_q = _task_events[task_id]
        t0 = time.time()
        embed_was_off = False

        try:
            if _cancel_flag.is_set():
                event_q.put({"event": "cancelled", "message": "Cancelled."})
                return

            # auto-start embed server if needed.
            if use_embeddings:
                embed_was_off = not check_server_health(EMBED_URL)
                if embed_was_off:
                    event_q.put({"event": "embed_starting",
                                 "message": "Starting embedding server (~2 GB RAM)..."})
                    event_q.put({"event": "progress",
                                 "message": "Starting embedding server (~2 GB RAM)..."})
                else:
                    event_q.put({"event": "embed_was_manual",
                                 "message": "Embedding server already running."})
                if embed_was_off and not _ensure_embed_server():
                    event_q.put({
                        "event": "error",
                        "message": "Embedding server failed to start.",
                        "elapsed_seconds": round(time.time() - t0, 1),
                    })
                    return
                if embed_was_off:
                    event_q.put({"event": "embed_ready",
                                 "message": "Embedding server ready."})

            output_buffer = io.StringIO()
            from ingest import ingest_all as run_ingest_all

            with redirect_stdout(output_buffer):
                run_ingest_all(overwrite=overwrite, use_embeddings=use_embeddings)

            output = output_buffer.getvalue()
            for line in output.splitlines():
                line = line.strip()
                if line:
                    event_q.put({"event": "progress", "message": line})

            elapsed = time.time() - t0
            event_q.put({
                "event": "complete",
                "success": True,
                "elapsed_seconds": round(elapsed, 1),
                "embed_was_manual": use_embeddings and not embed_was_off,
            })
        except Exception as e:
            event_q.put({
                "event": "error",
                "message": f"{type(e).__name__}: {e}",
                "elapsed_seconds": round(time.time() - t0, 1),
            })
        finally:
            with _task_lock:
                _active_task = None
            # auto-stop embed server if we started it.
            if use_embeddings and embed_was_off:
                event_q.put({"event": "embed_stopping",
                             "message": "Stopping embedding server to free RAM."})
                event_q.put({"event": "progress",
                             "message": "Stopping embedding server to free RAM..."})
                _stop_embed_server()

    thread = threading.Thread(target=_run_all, daemon=True)
    thread.start()

    return {
        "task_id": task_id,
        "message": "Batch ingestion started. Use /api/ingest/progress/{task_id} to track.",
    }


@router.post("/batch")
async def start_ingest_batch(body: dict[str, Any]) -> dict[str, Any]:
    """ingest a specific list of files (selected by the user).

    accepts a list of filenames to ingest sequentially. same concurrency
    rules as single ingest — only one task at a time.
    """
    global _active_task

    filenames: list[str] = body.get("filenames", [])
    overwrite = body.get("overwrite", False)
    use_embeddings = body.get("use_embeddings", False)

    if not filenames:
        raise HTTPException(400, "No files selected.")

    # reject path components and verify every resolved path is inside RAW_DIR.
    raw_root = RAW_DIR.resolve()
    for fn in filenames:
        if not isinstance(fn, str) or fn != Path(fn).name:
            raise HTTPException(400, f"Invalid filename: {fn!r}")
        resolved = (raw_root / fn).resolve()
        if resolved.parent != raw_root or not resolved.exists():
            raise HTTPException(404, f"File not found: raw/{fn}")

    if not check_server_health(LLAMA_URL):
        raise HTTPException(
            503,
            "The LLM server is not running. Start it from the Server panel first.",
        )

    with _task_lock:
        if _active_task is not None:
            raise HTTPException(409, {
                "message": "An ingest is already running.",
                "active_task": _active_task,
            })

        task_id = str(uuid.uuid4())[:8]
        _active_task = {
            "task_id": task_id,
            "filename": f"batch ({len(filenames)} files)",
            "started_at": time.time(),
        }
        _task_events[task_id] = queue.Queue()
        _task_created_at[task_id] = time.time()
        # clear inside the lock so a cancel issued between release and
        # thread start can't be silently dropped.
        _cancel_flag.clear()

    def _run_batch():
        global _active_task
        event_q = _task_events[task_id]
        t0 = time.time()
        embed_was_off = False

        try:
            if _cancel_flag.is_set():
                event_q.put({"event": "cancelled", "message": "Cancelled."})
                return

            if use_embeddings:
                embed_was_off = not check_server_health(EMBED_URL)
                if embed_was_off:
                    event_q.put({"event": "embed_starting",
                                 "message": "Starting embedding server (~2 GB RAM)..."})
                    event_q.put({"event": "progress",
                                 "message": "Starting embedding server (~2 GB RAM)..."})
                else:
                    event_q.put({"event": "embed_was_manual",
                                 "message": "Embedding server already running."})
                if embed_was_off and not _ensure_embed_server():
                    event_q.put({
                        "event": "error",
                        "message": "Embedding server failed to start.",
                        "elapsed_seconds": round(time.time() - t0, 1),
                    })
                    return
                if embed_was_off:
                    event_q.put({"event": "embed_ready",
                                 "message": "Embedding server ready."})

            from ingest import ingest as run_ingest

            for i, fn in enumerate(filenames, 1):
                if _cancel_flag.is_set():
                    event_q.put({"event": "cancelled",
                                 "message": f"Cancelled after {i - 1}/{len(filenames)} files."})
                    return
                event_q.put({
                    "event": "progress",
                    "message": f"[{i}/{len(filenames)}] Ingesting {fn}...",
                })

                output_buffer = io.StringIO()
                with redirect_stdout(output_buffer):
                    run_ingest(fn, overwrite=overwrite, use_embeddings=use_embeddings)

                for line in output_buffer.getvalue().splitlines():
                    line = line.strip()
                    if line:
                        event_q.put({"event": "progress", "message": line})

            elapsed = time.time() - t0
            event_q.put({
                "event": "complete",
                "success": True,
                "elapsed_seconds": round(elapsed, 1),
                "files_processed": len(filenames),
                "embed_was_manual": use_embeddings and not embed_was_off,
            })
        except Exception as e:
            event_q.put({
                "event": "error",
                "message": f"{type(e).__name__}: {e}",
                "elapsed_seconds": round(time.time() - t0, 1),
            })
        finally:
            with _task_lock:
                _active_task = None
            if use_embeddings and embed_was_off:
                event_q.put({"event": "embed_stopping",
                             "message": "Stopping embedding server to free RAM."})
                event_q.put({"event": "progress",
                             "message": "Stopping embedding server to free RAM..."})
                _stop_embed_server()

    thread = threading.Thread(target=_run_batch, daemon=True)
    thread.start()

    return {
        "task_id": task_id,
        "filenames": filenames,
        "message": f"Batch ingestion of {len(filenames)} files started.",
    }


def _reap_stale_task_queues() -> None:
    """drop _task_events entries whose task has been idle longer than the ttl.

    holds ``_task_lock`` while mutating the shared dicts so concurrent
    progress requests from different workers can't race each other into
    a half-torn-down entry.
    """
    now = time.time()
    with _task_lock:
        stale = [
            tid for tid, ts in _task_created_at.items()
            if now - ts > _TASK_QUEUE_TTL_SECONDS
        ]
        for tid in stale:
            _task_events.pop(tid, None)
            _task_created_at.pop(tid, None)


@router.get("/progress/{task_id}")
async def ingest_progress(task_id: str) -> StreamingResponse:
    """sse stream of ingest progress events for a task."""
    _reap_stale_task_queues()
    if task_id not in _task_events:
        raise HTTPException(404, f"Unknown task: {task_id}")

    event_q = _task_events[task_id]

    async def event_stream():
        try:
            while True:
                try:
                    event = event_q.get(timeout=1.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("event") in ("complete", "error", "cancelled"):
                        break
                except queue.Empty:
                    # keep-alive.
                    yield ": keepalive\n\n"
        finally:
            # always clean up, whether we exit via terminal event or
            # because the client disconnected mid-stream.
            _task_events.pop(task_id, None)
            _task_created_at.pop(task_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status")
async def ingest_status() -> dict[str, Any]:
    """check if an ingest is currently running."""
    with _task_lock:
        if _active_task is not None:
            elapsed = time.time() - _active_task.get("started_at", 0)
            return {
                "running": True,
                "task_id": _active_task["task_id"],
                "filename": _active_task["filename"],
                "elapsed_seconds": round(elapsed, 1),
            }
    return {"running": False}


@router.post("/cancel")
async def cancel_ingest() -> dict[str, Any]:
    """cancel the currently running ingest task."""
    with _task_lock:
        if _active_task is None:
            return {"cancelled": False, "message": "No active ingest."}
        task_id = _active_task["task_id"]
        _cancel_flag.set()
    return {"cancelled": True, "task_id": task_id}
