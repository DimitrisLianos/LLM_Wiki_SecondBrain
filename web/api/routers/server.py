"""server control — start, stop, status, config for llama.cpp instances."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from web.api.services import (
    LLAMA_URL,
    EMBED_URL,
    START_SERVER_SH,
    START_EMBED_SH,
    check_server_health,
    check_server_slots,
    parse_server_config,
    update_server_config,
)

router = APIRouter()

# --- log files. ---

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_LOG_DIR = _PROJECT_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_LOG_FILES = {
    "llm": _LOG_DIR / "llm_server.log",
    "embed": _LOG_DIR / "embed_server.log",
}


def _find_server_pid(port: int) -> int | None:
    """find the PID of a process listening on the given port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, timeout=3,
        )
        pids = result.stdout.strip().splitlines()
        return int(pids[0]) if pids else None
    except Exception:
        return None


def _parse_url(url: str) -> tuple[str, int]:
    """extract host and port from a URL like 'http://127.0.0.1:8080'."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return (parsed.hostname or "127.0.0.1", parsed.port or 8080)


def _check_model_loaded(url: str) -> bool:
    """verify a model is actually loaded (not just an empty router)."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(f"{url}/v1/models")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return bool(data.get("data"))
    except Exception:
        return False


@router.get("/status")
async def server_status() -> dict[str, Any]:
    """check health of both llama.cpp servers."""
    llm_health = check_server_health(LLAMA_URL)
    embed_health = check_server_health(EMBED_URL)
    config = parse_server_config()

    # health alone isn't enough — verify model is actually loaded.
    llm_ready = llm_health is not None and _check_model_loaded(LLAMA_URL)
    embed_ready = embed_health is not None

    slots = check_server_slots(LLAMA_URL) if llm_ready else []
    slots_used = sum(1 for s in slots if s.get("is_processing", False))

    llm_host, llm_port = _parse_url(LLAMA_URL)
    embed_host, embed_port = _parse_url(EMBED_URL)

    return {
        "llm_server": {
            "running": llm_ready,
            "health": llm_health,
            "model": "Gemma 4 26B-A4B Q4_K_M (UD)",
            "context_size": config.get("context_size", 0),
            "kv_type_k": config.get("kv_type_k", ""),
            "kv_type_v": config.get("kv_type_v", ""),
            "parallel": config.get("parallel", 0),
            "batch_size": config.get("batch_size", 0),
            "reasoning": config.get("reasoning", "on"),
            "slots_used": slots_used,
            "slots_total": len(slots),
            "host": llm_host,
            "port": llm_port,
            "pid": _find_server_pid(llm_port) if llm_ready else None,
        },
        "embed_server": {
            "running": embed_ready,
            "health": embed_health,
            "model": "BGE-M3 Q4_K_M",
            "host": embed_host,
            "port": embed_port,
            "pid": _find_server_pid(embed_port) if embed_ready else None,
        },
    }


@router.post("/start")
async def start_server(target: str = "llm") -> dict[str, str]:
    """start a llama.cpp server. target: 'llm', 'embed', or 'both'."""
    scripts = []
    if target in ("llm", "both"):
        if not START_SERVER_SH.exists():
            raise HTTPException(404, "start_server.sh not found")
        scripts.append(("LLM", str(START_SERVER_SH)))
    if target in ("embed", "both"):
        if not START_EMBED_SH.exists():
            raise HTTPException(404, "start_embed_server.sh not found")
        scripts.append(("Embed", str(START_EMBED_SH)))

    if not scripts:
        raise HTTPException(400, f"Invalid target: {target}. Use 'llm', 'embed', or 'both'.")

    results = []
    for label, script in scripts:
        target_key = "llm" if "embed" not in script else "embed"
        log_path = _LOG_FILES[target_key]
        try:
            # open, dup into the child via Popen, then close the parent's
            # copy of the fd so the handle can't leak across many start/stop
            # cycles. the child retains its own dup'd fd.
            with open(log_path, "a") as log_fh:
                log_fh.write(f"\n--- Starting {label} server ---\n")
                log_fh.flush()
                subprocess.Popen(
                    ["bash", script, "start"],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            results.append(f"{label} server starting...")
        except Exception as e:
            results.append(f"{label} server failed: {e}")

    return {"message": " | ".join(results)}


@router.post("/stop")
async def stop_server(target: str = "llm") -> dict[str, str]:
    """stop a llama.cpp server. target: 'llm', 'embed', or 'both'."""
    results = []
    if target in ("llm", "both"):
        try:
            subprocess.run(
                ["bash", str(START_SERVER_SH), "stop"],
                capture_output=True, timeout=10,
            )
            results.append("LLM server stopped")
        except Exception as e:
            results.append(f"LLM stop failed: {e}")

    if target in ("embed", "both"):
        try:
            subprocess.run(
                ["bash", str(START_EMBED_SH), "stop"],
                capture_output=True, timeout=10,
            )
            results.append("Embed server stopped")
        except Exception as e:
            results.append(f"Embed stop failed: {e}")

    return {"message": " | ".join(results)}


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """read current server configuration from start_server.sh."""
    return parse_server_config()


@router.post("/config")
async def set_config(updates: dict[str, str | int]) -> dict[str, Any]:
    """update server configuration. requires restart to take effect.

    valid keys: batch_size, context_size, parallel, kv_type_k, kv_type_v, reasoning.
    """
    allowed = {"batch_size", "context_size", "parallel", "kv_type_k", "kv_type_v", "reasoning"}
    results = {}

    for key, value in updates.items():
        if key not in allowed:
            results[key] = f"unknown config key (allowed: {', '.join(sorted(allowed))})"
            continue
        ok = update_server_config(key, str(value))
        results[key] = "updated" if ok else "failed"

    return {
        "results": results,
        "note": "Restart the server for changes to take effect.",
        "config": parse_server_config(),
    }


@router.get("/logs/{target}")
async def get_logs(
    target: str,
    tail: int = Query(default=100, ge=1, le=2000),
) -> dict[str, Any]:
    """return the last N lines of a server's log file.

    target: 'llm' or 'embed'.
    """
    if target not in _LOG_FILES:
        raise HTTPException(400, f"Invalid target: {target}. Use 'llm' or 'embed'.")

    log_path = _LOG_FILES[target]
    if not log_path.exists():
        return {"target": target, "lines": [], "message": "No logs yet."}

    text = log_path.read_text(errors="replace")
    lines = text.splitlines()[-tail:]
    return {"target": target, "lines": lines, "total_lines": len(text.splitlines())}
