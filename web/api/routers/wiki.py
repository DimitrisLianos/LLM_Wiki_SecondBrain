"""wiki browsing — pages, metadata, graph data."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from web.api.services import (
    SUBDIRS,
    WIKI_DIR,
    build_wiki_graph,
    get_all_pages,
    get_page,
)

router = APIRouter()

# compile once at import time — these run on every page / stats request.
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _iter_all_wiki_files():
    """yield every .md file under WIKI_DIR/<subdir>/ exactly once."""
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            yield f


@router.get("/pages")
async def list_pages(subdir: str = "") -> dict[str, Any]:
    """list all wiki pages, optionally filtered by subdirectory.

    each page includes: name, subdir, page_type, tags, created, updated.
    """
    pages = get_all_pages()

    if subdir:
        if subdir not in SUBDIRS:
            raise HTTPException(400, f"Invalid subdir. Use: {', '.join(SUBDIRS)}")
        pages = [p for p in pages if p["subdir"] == subdir]

    return {
        "pages": pages,
        "total": len(pages),
        "subdirs": {
            s: sum(1 for p in pages if p["subdir"] == s)
            for s in SUBDIRS
        },
    }


@router.get("/page/{subdir}/{name}")
async def read_page(subdir: str, name: str) -> dict[str, Any]:
    """load a single wiki page with full content and link data.

    returns raw markdown, parsed frontmatter, and inbound/outbound wikilinks.
    inbound-link resolution does a single pass over all .md files rather
    than nesting a subdir loop inside a per-link loop.
    """
    if subdir not in SUBDIRS:
        raise HTTPException(400, f"Invalid subdir. Use: {', '.join(SUBDIRS)}")

    page = get_page(subdir, name)
    if not page:
        raise HTTPException(404, f"Page not found: {subdir}/{name}")

    # find inbound links (pages that link to this one).
    # single-pass scan: read each .md at most once, short-circuit as soon as
    # we find a link pointing at the target page in that file.
    target_lower = page["name"].lower()
    inbound: list[str] = []
    for f in _iter_all_wiki_files():
        if f.stem.lower() == target_lower:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for link in _WIKILINK_RE.finditer(text):
            if link.group(1).lower() == target_lower:
                inbound.append(f.stem)
                break

    page["inbound_links"] = sorted(set(inbound))
    return page


@router.get("/graph")
async def wiki_graph() -> dict[str, Any]:
    """return the full wikilink graph for visualization.

    nodes have: name, subdir, link_count (inbound).
    edges have: source, target.
    """
    return build_wiki_graph()


@router.get("/stats")
async def wiki_stats() -> dict[str, Any]:
    """aggregate wiki statistics.

    a single filesystem pass gathers page counts and link counts together;
    previously this function walked the wiki twice.
    """
    pages = get_all_pages()
    page_names_lower = {p["name"].lower() for p in pages}

    per_subdir = {s: 0 for s in SUBDIRS}
    for p in pages:
        per_subdir[p["subdir"]] = per_subdir.get(p["subdir"], 0) + 1

    total_links = 0
    broken_links = 0
    for f in _iter_all_wiki_files():
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_RE.finditer(text):
            total_links += 1
            if m.group(1).lower() not in page_names_lower:
                broken_links += 1

    return {
        "total_pages": len(pages),
        "per_subdir": per_subdir,
        "total_wikilinks": total_links,
        "broken_links": broken_links,
    }
