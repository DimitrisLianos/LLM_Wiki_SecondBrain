"""dedup endpoint — plan and apply duplicate page merges."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from web.api.services import WIKI_DIR, strip_frontmatter

router = APIRouter()
logger = logging.getLogger(__name__)

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"

# guard against concurrent wiki mutations (ingest, dedup, etc.).
_merge_lock = asyncio.Lock()


# --- request schemas. ---

class ClusterSelection(BaseModel):
    canonical: str
    merge_from: list[str]

    @field_validator("canonical", "merge_from", mode="before")
    @classmethod
    def _sanitise_names(cls, v: str | list[str]) -> str | list[str]:
        """reject path-traversal and illegal characters in page names."""
        names = [v] if isinstance(v, str) else v
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Page name must be a non-empty string.")
            if ".." in name or "/" in name or "\\" in name:
                raise ValueError(f"Invalid page name: {name!r}")
        return v


class ApplySelectedRequest(BaseModel):
    clusters: list[ClusterSelection]


# --- endpoints. ---

@router.post("/plan")
async def dedup_plan() -> dict[str, Any]:
    """run cleanup_dedup.py in dry-run mode and return the merge plan.

    shows which pages would be merged, the canonical survivor for each
    cluster, and the total number of deletions. no changes are written.
    each cluster includes a 'candidates' list so the user can choose
    which page to keep.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "cleanup_dedup.py")],
        capture_output=True, text=True, timeout=120,
        cwd=str(_SCRIPTS_DIR.parent),
    )

    output = result.stdout + result.stderr
    clusters = _parse_dedup_output(output)

    # add candidates list (all pages) for user selection in the ui.
    for c in clusters:
        c["candidates"] = [c["canonical"]] + c["merge_from"]

    total_merges = sum(len(c["merge_from"]) for c in clusters)

    return {
        "clusters": clusters,
        "total_clusters": len(clusters),
        "total_merges": total_merges,
        "raw_output": output,
    }


@router.post("/apply")
async def dedup_apply() -> dict[str, Any]:
    """run cleanup_dedup.py with --apply to execute merges.

    this is destructive: merged pages are deleted, wikilinks are rewritten.
    always run /plan first to review what will change.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "cleanup_dedup.py"), "--apply"],
        capture_output=True, text=True, timeout=300,
        cwd=str(_SCRIPTS_DIR.parent),
    )

    output = result.stdout + result.stderr
    clusters = _parse_dedup_output(output)
    total_merges = sum(len(c["merge_from"]) for c in clusters)

    # extract file rewrite count.
    files_rewritten = 0
    m = re.search(r"wikilink rewrite:\s*(\d+)\s*file", output)
    if m:
        files_rewritten = int(m.group(1))

    success = result.returncode == 0

    return {
        "success": success,
        "clusters_merged": len(clusters),
        "pages_deleted": total_merges,
        "files_rewritten": files_rewritten,
        "raw_output": output,
        "message": (
            f"Merged {total_merges} pages across {len(clusters)} clusters. "
            f"{files_rewritten} files had wikilinks updated."
            if success
            else f"Dedup failed: {output[-500:]}"
        ),
    }


@router.post("/apply-selected")
async def dedup_apply_selected(body: ApplySelectedRequest) -> dict[str, Any]:
    """apply merges with user-selected canonicals.

    accepts a list of clusters where the user may have chosen a different
    canonical than the script's default. each cluster must have:
      canonical: str — the page to keep
      merge_from: list[str] — pages to merge into canonical

    this endpoint does the merge directly (no subprocess) so user
    selections are respected. uses an asyncio lock to prevent concurrent
    wiki mutations.
    """
    if not body.clusters:
        raise HTTPException(400, "No clusters provided.")

    async with _merge_lock:
        merged_count = 0
        deleted_count = 0
        links_rewritten = 0
        errors: list[str] = []

        for cluster in body.clusters:
            canonical_path = _find_wiki_page(cluster.canonical)
            if not canonical_path:
                errors.append(f"Canonical page not found: {cluster.canonical}")
                continue

            canonical_text = canonical_path.read_text(encoding="utf-8")

            for name in cluster.merge_from:
                dup_path = _find_wiki_page(name)
                if not dup_path or not dup_path.exists():
                    errors.append(f"Duplicate page not found: {name}")
                    continue

                dup_text = dup_path.read_text(encoding="utf-8")
                body_text = strip_frontmatter(dup_text).strip()

                if body_text:
                    canonical_text += f"\n\n---\n> *Merged from {name}*\n\n{body_text}"

                dup_path.unlink()
                deleted_count += 1

            canonical_path.write_text(canonical_text, encoding="utf-8")
            merged_count += 1

            # rewrite wikilinks across all wiki pages.
            for md_file in WIKI_DIR.rglob("*.md"):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    modified = text
                    for name in cluster.merge_from:
                        modified = modified.replace(f"[[{name}]]", f"[[{cluster.canonical}]]")
                    if modified != text:
                        md_file.write_text(modified, encoding="utf-8")
                        links_rewritten += 1
                except (OSError, UnicodeDecodeError) as exc:
                    logger.warning("failed to rewrite wikilinks in %s: %s", md_file, exc)
                    errors.append(f"Failed to rewrite {md_file.name}: {exc}")

        success = deleted_count > 0 or (merged_count > 0 and not errors)

        return {
            "success": success,
            "clusters_merged": merged_count,
            "pages_deleted": deleted_count,
            "files_rewritten": links_rewritten,
            "errors": errors if errors else None,
            "message": (
                f"Merged {deleted_count} pages across {merged_count} clusters. "
                f"{links_rewritten} files had wikilinks updated."
                + (f" {len(errors)} error(s) occurred." if errors else "")
            ),
        }


# --- helpers. ---

def _find_wiki_page(name: str) -> Path | None:
    """find a wiki page by name across all subdirectories.

    guards against path traversal by validating the resolved path
    stays within WIKI_DIR.
    """
    wiki_resolved = WIKI_DIR.resolve()

    for subdir in ("sources", "entities", "concepts", "synthesis"):
        path = WIKI_DIR / subdir / f"{name}.md"
        if path.resolve().is_relative_to(wiki_resolved) and path.exists():
            return path

    # try without .md (name might already include it).
    for subdir in ("sources", "entities", "concepts", "synthesis"):
        path = WIKI_DIR / subdir / name
        if path.resolve().is_relative_to(wiki_resolved) and path.exists():
            return path

    return None


def _parse_dedup_output(output: str) -> list[dict[str, Any]]:
    """parse cleanup_dedup.py output into structured clusters."""
    clusters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in output.splitlines():
        line = line.strip()

        # group header: [entities] group 'alias::xxx':
        group_match = re.match(
            r"\[(\w+(?:\+\w+)?)\]\s+group\s+'([^']+)':",
            line,
        )
        if group_match:
            if current:
                clusters.append(current)
            current = {
                "subdirs": group_match.group(1),
                "group_key": group_match.group(2),
                "canonical": "",
                "merge_from": [],
            }
            continue

        if current is None:
            continue

        # keep line: keep:   [entities] PageName
        keep_match = re.match(r"keep:\s+\[\w+\]\s+(.+)", line)
        if keep_match:
            current["canonical"] = keep_match.group(1).strip()
            continue

        # merge line: merge:  [entities] PageName
        merge_match = re.match(r"merge:\s+\[\w+\]\s+(.+)", line)
        if merge_match:
            current["merge_from"].append(merge_match.group(1).strip())
            continue

    if current:
        clusters.append(current)

    return clusters
