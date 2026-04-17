"""fts5 full-text search endpoint."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from web.api.services import WikiSearch, strip_frontmatter

router = APIRouter()


@router.get("")
async def search_wiki(q: str = "", top_k: int = 20) -> dict[str, Any]:
    """search wiki pages via sqlite fts5 + wikilink graph + rrf.

    returns ranked results with name, subdir, score, and snippet.
    response is instant (~5ms) — no llm call involved.
    """
    if not q.strip():
        return {"results": [], "query": q, "elapsed_ms": 0, "total": 0}

    t0 = time.time()

    with WikiSearch() as ws:
        ranked = ws.search(q.strip(), top_k=top_k)
        if not ranked:
            elapsed = (time.time() - t0) * 1000
            return {"results": [], "query": q, "elapsed_ms": elapsed, "total": 0}

        # load content for snippets.
        conn = ws._connect()
        results = []
        for name, score in ranked:
            row = conn.execute(
                "SELECT subdir, content FROM wiki_pages WHERE name = ?",
                (name,),
            ).fetchone()
            if row:
                subdir, content = row
                body = strip_frontmatter(content)
                snippet = body[:200].replace("\n", " ").strip()
                if len(body) > 200:
                    snippet += "..."
                results.append({
                    "name": name,
                    "subdir": subdir,
                    "score": round(score, 4),
                    "snippet": snippet,
                })

    elapsed = (time.time() - t0) * 1000
    return {
        "results": results,
        "query": q,
        "elapsed_ms": round(elapsed, 1),
        "total": len(results),
    }


@router.post("/rebuild")
async def rebuild_index() -> dict[str, Any]:
    """rebuild the sqlite fts5 search index from disk."""
    t0 = time.time()
    with WikiSearch() as ws:
        count = ws.build_index()
    elapsed = (time.time() - t0) * 1000

    return {
        "pages_indexed": count,
        "elapsed_ms": round(elapsed, 1),
        "message": f"Index rebuilt: {count} pages indexed.",
    }
