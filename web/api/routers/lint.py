"""wiki lint — health check endpoint."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from web.api.services import SUBDIRS, WIKI_DIR

router = APIRouter()


def _strip_boilerplate(text: str) -> str:
    """strip frontmatter, heading, and 'Mentioned In' sections.

    returns only the substantive body content.
    """
    # strip frontmatter.
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:]

    lines = text.strip().splitlines()
    content_lines: list[str] = []
    in_boilerplate = False

    for line in lines:
        s = line.strip()
        # skip heading.
        if s.startswith("# ") and not content_lines:
            continue
        # detect boilerplate sections.
        if s in ("## Mentioned In", "## Sources", "## References",
                 "## Entities Mentioned", "## Concepts Mentioned",
                 "## Key Claims"):
            in_boilerplate = True
            continue
        if in_boilerplate:
            if s.startswith("## "):
                in_boilerplate = False
            elif not s or s.startswith("- [["):
                continue
            else:
                in_boilerplate = False
        if not in_boilerplate and s:
            content_lines.append(s)

    return "\n".join(content_lines).strip()


@router.post("")
async def run_lint() -> dict[str, Any]:
    """run wiki health checks. returns structured lint results.

    checks for: broken wikilinks, orphan pages, missing-from-index pages,
    frontmatter issues, and thin pages.
    """
    pages: dict[str, tuple[str, str]] = {}  # name -> (subdir, path)
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            pages[f.stem] = (subdir, str(f))

    case_map = {name.lower(): name for name in pages}

    # extract links from all pages.
    outbound: dict[str, set[str]] = {}
    inbound: dict[str, set[str]] = {}
    missing_pages: dict[str, set[str]] = {}

    for name, (subdir, path_str) in pages.items():
        with open(path_str, errors="replace") as fh:
            text = fh.read()
        links = set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text))
        outbound[name] = links

        for link in links:
            resolved = case_map.get(link.lower())
            if resolved:
                inbound.setdefault(resolved, set()).add(name)
            else:
                missing_pages.setdefault(link, set()).add(name)

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    info: list[dict[str, str]] = []

    # errors: broken wikilinks.
    for target in sorted(missing_pages):
        refs = sorted(missing_pages[target])
        errors.append({
            "level": "error",
            "message": f"Broken link [[{target}]] referenced from {', '.join(refs)}",
            "page": refs[0] if refs else "",
            "target": target,
        })

    # errors: stale index entries.
    index_path = WIKI_DIR / "index.md"
    if index_path.exists():
        index_text = index_path.read_text()
        index_links = set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", index_text))
        for link in sorted(index_links):
            if link.lower() not in case_map:
                errors.append({
                    "level": "error",
                    "message": f"Index references missing page [[{link}]]",
                    "page": "index",
                    "target": link,
                })

    # warnings: orphan pages.
    special = {"index", "log"}
    for name in sorted(pages):
        if name not in inbound and name not in special:
            subdir, _ = pages[name]
            warnings.append({
                "level": "warning",
                "message": f"Orphan page: no other page links to [[{name}]]",
                "page": name,
                "target": "",
            })

    # warnings: pages not in index.
    if index_path.exists():
        index_lower = index_path.read_text().lower()
        for name in sorted(pages):
            if f"[[{name.lower()}]]" not in index_lower:
                warnings.append({
                    "level": "warning",
                    "message": f"Page not in index: [[{name}]]",
                    "page": name,
                    "target": "",
                })

    # warnings: frontmatter issues.
    required_fields = ("type", "created", "tags", "sources")
    for name, (subdir, path_str) in sorted(pages.items()):
        with open(path_str, errors="replace") as fh:
            text = fh.read()
        if not text.startswith("---"):
            warnings.append({
                "level": "warning",
                "message": f"Missing YAML frontmatter in [[{name}]]",
                "page": name,
                "target": "",
            })
            continue
        end = text.find("---", 3)
        if end == -1:
            warnings.append({
                "level": "warning",
                "message": f"Unclosed YAML frontmatter in [[{name}]]",
                "page": name,
                "target": "",
            })
            continue
        fm = text[3:end]
        missing = [f for f in required_fields if f not in fm]
        if missing:
            warnings.append({
                "level": "warning",
                "message": f"Frontmatter missing fields ({', '.join(missing)}) in [[{name}]]",
                "page": name,
                "target": "",
            })

    # warnings: body-less pages.
    # sources: flag if "Summary" section is empty (broken ingest from reasoning
    #   consuming the token budget — page is just title + frontmatter).
    # entities/concepts: flag only if truly empty (no description at all).
    #   a valid one-liner like "The Athens Stock Exchange index." is fine.
    bodyless: list[dict[str, str]] = []
    for name, (subdir, path_str) in sorted(pages.items()):
        if subdir not in ("entities", "concepts", "sources"):
            continue
        with open(path_str, errors="replace") as fh:
            text = fh.read()
        body = _strip_boilerplate(text)

        is_bodyless = False
        if subdir == "sources":
            # sources must have real content after stripping boilerplate.
            # a failed ingest leaves only "Summary" header or nothing.
            cleaned = re.sub(r"^##\s+\w+.*$", "", body, flags=re.MULTILINE).strip()
            is_bodyless = len(cleaned) < 20
        else:
            # entities/concepts: only flag if truly empty (no description).
            is_bodyless = len(body) == 0

        if is_bodyless:
            entry = {
                "level": "warning",
                "message": f"Body-less page: [[{name}]] has no substantive content",
                "page": name,
                "subdir": subdir,
                "target": "",
                "bodyless": True,
            }
            warnings.append(entry)
            bodyless.append(entry)

    # info: thin pages (any subdir, raw byte count).
    for name, (subdir, path_str) in sorted(pages.items()):
        with open(path_str, errors="replace") as fh:
            text = fh.read()
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:]
        if len(text.strip()) < 80:
            info.append({
                "level": "info",
                "message": f"Thin page: [[{name}]] has very little content",
                "page": name,
                "target": "",
            })

    # stats.
    stats = {"total_pages": len(pages)}
    for s in SUBDIRS:
        stats[s] = sum(1 for _, (sd, _) in pages.items() if sd == s)

    return {
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "bodyless": bodyless,
        "stats": stats,
        "summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "info_count": len(info),
            "bodyless_count": len(bodyless),
        },
    }


class DeletePagesRequest(BaseModel):
    pages: list[dict[str, str]] = Field(
        ..., description="list of {name, subdir} objects"
    )


@router.post("/delete")
async def delete_pages(body: DeletePagesRequest) -> dict[str, Any]:
    """delete selected wiki pages and remove them from index.md.

    the ``name`` and ``subdir`` values come from the client and must be
    treated as untrusted. every resolved path is required to live under
    ``WIKI_DIR / <subdir>``; anything that escapes (``..``, absolute
    paths, symlinks) is rejected silently to avoid disclosing layout.
    """
    deleted: list[str] = []
    failed: list[str] = []

    wiki_root = WIKI_DIR.resolve()

    for entry in body.pages:
        name = entry.get("name", "")
        subdir = entry.get("subdir", "")
        if not name or subdir not in SUBDIRS:
            failed.append(name)
            continue

        # reject any path separator or traversal token in ``name``.
        if "/" in name or "\\" in name or ".." in name:
            failed.append(name)
            continue

        subdir_root = (wiki_root / subdir).resolve()
        try:
            candidate = (subdir_root / f"{name}.md").resolve()
        except (OSError, ValueError):
            failed.append(name)
            continue

        def _inside(p: Path, base: Path) -> bool:
            try:
                p.relative_to(base)
                return True
            except ValueError:
                return False

        path: Path | None = candidate if candidate.exists() else None
        if path is None:
            # case-insensitive fallback, constrained to the subdir root.
            target = name.lower()
            for f in subdir_root.iterdir():
                if f.suffix == ".md" and f.stem.lower() == target:
                    resolved = f.resolve()
                    if _inside(resolved, subdir_root):
                        path = resolved
                    break

        if path is None or not _inside(path, subdir_root) or not path.is_file():
            failed.append(name)
            continue

        try:
            path.unlink()
            deleted.append(name)
        except OSError:
            failed.append(name)

    # clean deleted pages from index.md.
    index_path = WIKI_DIR / "index.md"
    if deleted and index_path.exists():
        index_text = index_path.read_text()
        deleted_lower = {n.lower() for n in deleted}
        new_lines = []
        for line in index_text.splitlines():
            links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", line)
            if any(l.lower() in deleted_lower for l in links):
                continue
            new_lines.append(line)
        index_path.write_text("\n".join(new_lines))

    return {
        "deleted": deleted,
        "failed": failed,
        "deleted_count": len(deleted),
        "failed_count": len(failed),
    }
