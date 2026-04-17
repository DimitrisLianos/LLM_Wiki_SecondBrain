"""admin operations — destructive resets, factory restore.

exposes two reset modes so users can recover from a corrupt wiki state or
start over from scratch:

  * ``mode="wiki"`` — wipes the generated wiki (sources/, entities/,
    concepts/, synthesis/, index.md, log.md), the search index, and the
    runtime caches/registries. raw/ source files are kept so they can be
    re-ingested into a clean wiki.
  * ``mode="full"`` — does everything ``wiki`` does AND deletes every file
    inside raw/ (assets/ subdirectory is recreated empty). nothing left.

both endpoints require a typed confirmation string and refuse to run while
an ingest task is in flight.
"""

from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from web.api.services import RAW_DIR, SUBDIRS, WIKI_DIR

router = APIRouter()
logger = logging.getLogger(__name__)

# --- constants. ---

# typed confirmation token the client must supply on POST /reset to
# acknowledge that they understand the operation is destructive.
_CONFIRM_TOKEN = "RESET"

# project root, used to locate db/ and to recreate empty raw/assets/ after a
# full reset. resolved once at import time.
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_DB_DIR = _PROJECT_DIR / "db"
_RAW_ASSETS = RAW_DIR / "assets"

# files inside db/ that are part of the runtime state and should be wiped
# on either reset mode. seed_aliases.json lives in scripts/data/ and is
# never touched — it is curated, version-controlled seed data.
_DB_RUNTIME_FILES = (
    "wiki_search.db",
    "wiki.db",
    "alias_registry.json",
    "judge_cache.json",
    "embed_cache.json",
    "resolver_calibration.json",
)

ResetMode = Literal["wiki", "full"]


# --- request / response models. ---

class ResetRequest(BaseModel):
    mode: ResetMode
    confirm: str = Field(
        ...,
        description=f"must equal {_CONFIRM_TOKEN!r} to acknowledge the destructive action.",
    )


# --- helpers. ---

def _is_ingest_running() -> bool:
    """check whether an ingest task is currently active.

    imports inside the function to avoid circular dependencies (the ingest
    router imports from services, and we don't want services to import
    routers). returns false on any introspection failure — better to allow
    the reset and let it fail loudly than to block on a stale lock.
    """
    try:
        from web.api.routers import ingest as ingest_router
        with ingest_router._task_lock:
            return ingest_router._active_task is not None
    except Exception:
        logger.exception("failed to check ingest state — allowing reset")
        return False


def _count_files(directory: Path, recursive: bool = False) -> int:
    """count files in a directory, ignoring dotfiles. zero if missing."""
    if not directory.exists():
        return 0
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    return sum(
        1 for f in iterator
        if f.is_file() and not f.name.startswith(".")
    )


def _count_wiki_pages() -> dict[str, int]:
    """per-subdir page counts under wiki/."""
    return {sub: _count_files(WIKI_DIR / sub) for sub in SUBDIRS}


def _count_raw_files() -> int:
    """top-level raw/ files (excluding the assets/ subdirectory)."""
    if not RAW_DIR.exists():
        return 0
    return sum(
        1 for f in RAW_DIR.iterdir()
        if f.is_file() and not f.name.startswith(".")
    )


def _count_raw_assets() -> int:
    """files inside raw/assets/ (recursive)."""
    return _count_files(_RAW_ASSETS, recursive=True)


def _count_db_files() -> dict[str, bool]:
    """which runtime db/ files currently exist."""
    return {
        name: (_DB_DIR / name).exists()
        for name in _DB_RUNTIME_FILES
    }


def _bytes_in(directory: Path, recursive: bool = True) -> int:
    """sum of file sizes in a directory; 0 if missing."""
    if not directory.exists():
        return 0
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    total = 0
    for f in iterator:
        if f.is_file() and not f.name.startswith("."):
            try:
                total += f.stat().st_size
            except OSError:
                continue
    return total


def _wipe_dir_contents(directory: Path) -> int:
    """delete every file/subdir inside ``directory`` but keep directory itself.

    returns the number of top-level entries removed. silently no-ops when
    the directory does not exist.
    """
    if not directory.exists():
        return 0
    removed = 0
    for entry in directory.iterdir():
        if entry.name.startswith("."):
            # leave hidden files alone (.DS_Store, .gitkeep, etc).
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError:
            logger.exception("failed to delete %s", entry)
    return removed


def _recreate_empty_wiki() -> None:
    """rebuild the empty wiki skeleton so the app stays usable post-reset."""
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (WIKI_DIR / sub).mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    (WIKI_DIR / "index.md").write_text(
        "# Index\n\n"
        "_Wiki index will appear here as you ingest sources._\n"
    )
    (WIKI_DIR / "log.md").write_text(
        f"# Log\n\n"
        f"## [{today}] reset | wiki cleared\n"
        f"Wiki state was reset to an empty baseline.\n"
    )


def _delete_db_runtime_files() -> dict[str, str]:
    """remove all runtime db files. returns {filename: status} for each."""
    results: dict[str, str] = {}
    for name in _DB_RUNTIME_FILES:
        path = _DB_DIR / name
        if not path.exists():
            results[name] = "absent"
            continue
        try:
            path.unlink()
            results[name] = "deleted"
        except OSError as exc:
            logger.exception("failed to delete %s", path)
            results[name] = f"failed: {exc}"
    return results


# --- endpoints. ---

@router.get("/reset/preview")
async def reset_preview(
    mode: ResetMode = Query(..., description="reset mode: wiki|full"),
) -> dict[str, Any]:
    """report what *would* be deleted, without doing anything.

    used by the ui to render an accurate "this will delete N pages, M raw
    files, …" summary before the user confirms.
    """
    wiki_pages = _count_wiki_pages()
    wiki_total = sum(wiki_pages.values())
    raw_top = _count_raw_files()
    raw_assets = _count_raw_assets()

    db_files = _count_db_files()
    db_present = sum(1 for present in db_files.values() if present)

    payload: dict[str, Any] = {
        "mode": mode,
        "ingest_running": _is_ingest_running(),
        "wiki": {
            "pages_per_subdir": wiki_pages,
            "pages_total": wiki_total,
            "bytes": _bytes_in(WIKI_DIR),
            "will_delete": True,
        },
        "db": {
            "files": db_files,
            "files_present": db_present,
            "will_delete": True,
        },
        "raw": {
            "files": raw_top,
            "assets": raw_assets,
            "bytes": _bytes_in(RAW_DIR),
            "will_delete": mode == "full",
        },
    }
    return payload


@router.post("/reset")
async def reset(body: ResetRequest) -> dict[str, Any]:
    """perform the requested reset. requires ``confirm == "RESET"``.

    refuses (409) if an ingest task is currently running. always recreates
    the empty wiki skeleton afterwards so the ui has a valid state to
    render.
    """
    if body.confirm != _CONFIRM_TOKEN:
        raise HTTPException(
            400,
            f"Confirmation token mismatch. Send {{\"confirm\": \"{_CONFIRM_TOKEN}\"}}.",
        )

    if _is_ingest_running():
        raise HTTPException(
            409,
            "An ingest is in progress. Cancel it from the Ingest panel first.",
        )

    summary: dict[str, Any] = {"mode": body.mode}

    # 1. wipe each wiki subdir (keep the directory shells).
    wiki_removed: dict[str, int] = {}
    for sub in SUBDIRS:
        wiki_removed[sub] = _wipe_dir_contents(WIKI_DIR / sub)
    # 2. wipe top-level wiki files (index.md, log.md, etc.).
    for entry in list(WIKI_DIR.iterdir()):
        if entry.name in SUBDIRS:
            continue
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            logger.exception("failed to delete wiki top-level entry %s", entry)

    summary["wiki_removed"] = wiki_removed

    # 3. wipe runtime db files (search index, caches, runtime alias registry).
    summary["db_files"] = _delete_db_runtime_files()

    # 4. for full mode, also clear raw/ (keep raw/assets/ as an empty dir).
    if body.mode == "full":
        raw_removed = _wipe_dir_contents(RAW_DIR)
        # ensure raw/ and raw/assets/ still exist after the wipe so the
        # uploader and ingest watcher have a target.
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        _RAW_ASSETS.mkdir(parents=True, exist_ok=True)
        summary["raw_entries_removed"] = raw_removed
    else:
        summary["raw_entries_removed"] = 0

    # 5. rebuild empty wiki skeleton so the rest of the app stays sane.
    _recreate_empty_wiki()

    summary["message"] = (
        "Wiki reset complete. Raw sources kept — re-ingest to rebuild."
        if body.mode == "wiki"
        else "Full reset complete. Raw sources and wiki cleared."
    )
    logger.warning("reset complete: mode=%s summary=%s", body.mode, summary)
    return summary
