#!/usr/bin/env python3
"""llm wiki — query the wiki locally.
asks questions against the wiki using gemma 4 via llama.cpp.
retrieval via sqlite fts5 + wikilink graph + rrf (no llm call for page selection).

usage:
    python3 scripts/query.py "what themes connect these sources?"
    python3 scripts/query.py -i
"""

import argparse
import re
import sys
import time
import urllib.error
from datetime import date
from pathlib import Path

from llm_client import (
    WIKI_DIR, SUBDIRS, MAX_CONTEXT_CHARS,
    ContextOverflowError, llm,
)
from search import WikiSearch


# --- wiki helpers. ---

def get_all_pages():
    """read every wiki page. fallback for empty search results."""
    pages = {}
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        if d.exists():
            for f in d.glob("*.md"):
                pages[f.stem] = f.read_text()
    return pages


def _truncate_context(context, max_chars=MAX_CONTEXT_CHARS):
    """trim context to fit within the context window.

    prioritizes source pages first, then keeps as many entity/concept
    pages as will fit. returns (trimmed_context, was_truncated).
    """
    total = sum(len(v) for v in context.values())
    if total <= max_chars:
        return context, False

    # sort: source pages first (longer, more info), then by size descending.
    def _sort_key(item):
        name, content = item
        is_source = "type: source" in content[:200]
        return (0 if is_source else 1, -len(content))

    trimmed = {}
    chars = 0
    for name, content in sorted(context.items(), key=_sort_key):
        if chars + len(content) > max_chars and trimmed:
            break
        trimmed[name] = content
        chars += len(content)

    return trimmed, True


def retrieve_context(question):
    """find relevant wiki pages via fts5 + wikilink graph + rrf.

    replaces the old llm-based page selection — instant, no tokens spent.
    falls back to loading all pages if search returns nothing.
    """
    with WikiSearch() as ws:
        context, names, was_truncated = ws.search_and_load(
            question, top_k=20, max_chars=MAX_CONTEXT_CHARS,
        )

    if context:
        if was_truncated:
            print(f"    (loaded {len(context)}/{len(names)} pages within context budget)")
        for name in list(context.keys())[:10]:
            print(f"    -> {name}")
        if len(context) > 10:
            print(f"    ... and {len(context) - 10} more")
        return context

    # no fts5 hits — load everything.
    print("    (no search results, loading all pages)")
    return get_all_pages()


def answer_question(question, context):
    """synthesize an answer from wiki context. auto-truncates if too large."""
    context, was_truncated = _truncate_context(context)
    if was_truncated:
        print(f"    (trimmed to {len(context)} pages to fit context window)")

    wiki_text = "\n\n---\n\n".join(
        f"# {name}\n{content}" for name, content in context.items()
    )

    try:
        return llm(
            "answer the question using the wiki pages below. "
            "use [[wikilinks]] when referencing entities or concepts. "
            "if the wiki lacks the information, say so.\n\n"
            f"wiki pages:\n{wiki_text}\n\nquestion: {question}",
            max_tokens=2048, temperature=0.4,
        )
    except ContextOverflowError:
        # still too large even after truncation. halve and retry.
        half = dict(list(context.items())[:len(context) // 2])
        if not half:
            return "(error: even a single wiki page exceeds the context window.)"
        print(f"    (still too large, retrying with {len(half)} pages...)")
        wiki_text = "\n\n---\n\n".join(
            f"# {name}\n{content}" for name, content in half.items()
        )
        return llm(
            "answer the question using the wiki pages below. "
            "use [[wikilinks]] when referencing entities or concepts. "
            "if the wiki lacks the information, say so.\n\n"
            f"wiki pages:\n{wiki_text}\n\nquestion: {question}",
            max_tokens=2048, temperature=0.4,
        )


# --- main. ---

def query(question, save=False):
    print("\n  searching wiki...", flush=True)
    t0 = time.time()
    context = retrieve_context(question)
    search_ms = (time.time() - t0) * 1000

    if not context:
        print("\n  wiki is empty. ingest some sources first.")
        return

    print(f"    ({len(context)} pages, {search_ms:.0f}ms)")
    print("\n  thinking...\n")
    result = answer_question(question, context)
    print(result)

    if save:
        today = date.today().isoformat()
        slug = re.sub(r"[^\w\s]", "", question)[:50].strip().replace(" ", "_")
        page = "\n".join([
            "---", "type: synthesis",
            f"created: {today}", f"updated: {today}",
            f"sources: [{', '.join(context.keys())}]",
            "tags: [query]", "---", "",
            f"# {question}", "", result, "",
        ])
        out = WIKI_DIR / "synthesis" / f"{slug}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
        print(f"\n  saved to: wiki/synthesis/{out.name}")

    print()


def interactive():
    print("\n  llm wiki — interactive query")
    print("  type questions. 'exit' to quit. prefix with /save to file the answer.\n")

    while True:
        try:
            q = input("  ? ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if q.lower() in ("exit", "quit", "q"):
            break
        if not q:
            continue

        save = q.startswith("/save ")
        if save:
            q = q[6:].strip()

        try:
            query(q, save=save)
        except urllib.error.URLError:
            print("\n  error: can't reach the llama.cpp server.")
            print("  is it running? bash scripts/start_server.sh\n")
        except Exception as e:
            print(f"\n  error: {e}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="llm wiki — query")
    parser.add_argument("question", nargs="?", help="question to ask")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-s", "--save", action="store_true",
                        help="save answer as a wiki synthesis page")
    args = parser.parse_args()

    if args.interactive:
        interactive()
    elif args.question:
        query(args.question, save=args.save)
    else:
        parser.print_help()
