#!/usr/bin/env python3
"""llm wiki — fast wiki search.
sqlite fts5 full-text search + wikilink graph traversal + reciprocal rank fusion.
zero dependencies beyond python stdlib.

usage:
    python3 scripts/search.py "transformer attention"   # test search.
    python3 scripts/search.py --rebuild                 # rebuild index.

    from search import WikiSearch
    ws = WikiSearch()
    ws.build_index()
    context, names, truncated = ws.search_and_load("kv cache quantization")
"""

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from llm_client import (
    DB_PATH, FRONTMATTER_RE, MAX_CONTEXT_CHARS, SUBDIRS, WIKI_DIR,
)

# bm25 column weights: name 10x, type 3x, tags 5x, content 1x.
BM25_WEIGHTS = (10.0, 3.0, 5.0, 1.0)

# extracts the "`raw/<filename>`" marker we embed in every source page.
# used by build_index to populate the reverse index that maps raw
# filenames back to the source page they were ingested into.
_SOURCE_MARKER_RE = re.compile(r"`raw/([^`]+)`")

# extracts the source_hash frontmatter value so idempotency checks
# can read the hash from sqlite instead of opening each source page.
_SOURCE_HASH_RE = re.compile(
    r"^source_hash:\s*([a-f0-9]{64})\s*$",
    re.MULTILINE,
)


class WikiSearch:
    """fast wiki retrieval: fts5 keyword search + wikilink graph expansion + rrf."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _connect(self):
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- index building. ---

    def build_index(self):
        r"""rebuild the fts5 index from all wiki pages. <1s for hundreds of pages.

        also populates the ``source_files`` reverse index: every source
        page embeds a ``\`raw/<filename>\``` marker during ingest, so we
        can build a filename -> (source_page, source_hash) map without
        any extra disk i/o beyond what we already do for fts.

        the reverse index replaces the O(N) linear scan that
        ingest._find_source_page_for used to run on every ingest. on a
        wiki with hundreds of sources that drops idempotency check cost
        from "open every file" to a single indexed sqlite SELECT.
        """
        conn = self._connect()

        # drop and recreate for clean rebuild.
        conn.execute("DROP TABLE IF EXISTS wiki_fts")
        conn.execute("DROP TABLE IF EXISTS wiki_pages")
        conn.execute("DROP TABLE IF EXISTS source_files")

        # raw content table for full page retrieval.
        conn.execute("""
            CREATE TABLE wiki_pages (
                name TEXT PRIMARY KEY,
                subdir TEXT,
                content TEXT
            )
        """)

        # fts5 virtual table. porter tokenizer for stemming.
        conn.execute("""
            CREATE VIRTUAL TABLE wiki_fts USING fts5(
                name, type, tags, content,
                tokenize='porter unicode61'
            )
        """)

        # reverse index: raw filename -> source page name + sha256 hash.
        # keyed on filename (the stable identifier) so re-uploads update
        # the row in place. source_page stores the page stem (not the
        # full path) so the index survives WIKI_DIR moves.
        conn.execute("""
            CREATE TABLE source_files (
                filename    TEXT PRIMARY KEY,
                source_page TEXT NOT NULL,
                source_hash TEXT NOT NULL DEFAULT ''
            )
        """)

        count = 0
        for subdir in SUBDIRS:
            d = WIKI_DIR / subdir
            if not d.exists():
                continue
            for f in d.glob("*.md"):
                text = f.read_text(errors="replace")
                name = f.stem
                page_type, tags = _extract_frontmatter(text)
                body = _strip_frontmatter(text)

                conn.execute(
                    "INSERT OR REPLACE INTO wiki_pages VALUES (?, ?, ?)",
                    (name, subdir, text),
                )
                conn.execute(
                    "INSERT INTO wiki_fts VALUES (?, ?, ?, ?)",
                    (name, page_type, tags, body),
                )
                count += 1

                # reverse index population — only source pages have
                # `raw/<filename>` markers, so the regex short-circuits
                # cleanly for entities/concepts/synthesis.
                if subdir == "sources":
                    for match in _SOURCE_MARKER_RE.finditer(text):
                        filename = match.group(1).strip()
                        if not filename:
                            continue
                        hash_match = _SOURCE_HASH_RE.search(text)
                        source_hash = hash_match.group(1) if hash_match else ""
                        conn.execute(
                            "INSERT OR REPLACE INTO source_files "
                            "VALUES (?, ?, ?)",
                            (filename, name, source_hash),
                        )

        conn.commit()
        return count

    # --- reverse index queries. ---

    def find_source_page(self, filename):
        """return the source-page stem for a raw filename, or None.

        this is the fast replacement for ingest._find_source_page_for's
        linear scan. the caller still has to resolve (stem -> Path) if
        they need the on-disk location; we only return the stem here so
        the index stays WIKI_DIR-relocation safe.
        """
        self._ensure_index()
        conn = self._connect()
        row = conn.execute(
            "SELECT source_page FROM source_files WHERE filename = ?",
            (filename,),
        ).fetchone()
        return row[0] if row else None

    def read_source_hash(self, filename):
        """return the cached sha256 source_hash for a raw filename.

        cheaper than reading + parsing the source page on every idempotency
        check. empty string if we have no row OR the row has no hash.
        """
        self._ensure_index()
        conn = self._connect()
        row = conn.execute(
            "SELECT source_hash FROM source_files WHERE filename = ?",
            (filename,),
        ).fetchone()
        return row[0] if row and row[0] else ""

    def _ensure_index(self):
        """auto-build index if it doesn't exist yet."""
        conn = self._connect()
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "wiki_fts" not in tables:
            self.build_index()

    # --- search. ---

    def search(self, query, top_k=20, expand_graph=True):
        """search the wiki. returns ranked list of (name, score) tuples.

        combines fts5 bm25 ranking with wikilink graph expansion via rrf.
        """
        self._ensure_index()

        # fts5 keyword search.
        fts_results = self._fts_search(query, top_k=top_k * 2)
        if not fts_results:
            return []

        if not expand_graph:
            return fts_results[:top_k]

        # wikilink graph expansion from top fts hits.
        seeds = [name for name, _ in fts_results[:10]]
        graph_results = self._graph_expand(seeds)

        # fuse with rrf.
        fts_ranking = [name for name, _ in fts_results]
        graph_ranking = [name for name, _ in graph_results]
        fused = _rrf([fts_ranking, graph_ranking])

        return fused[:top_k]

    def _fts_search(self, query, top_k=40):
        """bm25-ranked fts5 search. returns [(name, score), ...]."""
        conn = self._connect()

        # clean query: extract words, join with OR for broad recall.
        terms = re.findall(r"\w+", query.lower())
        if not terms:
            return []

        fts_query = " OR ".join(terms)

        try:
            rows = conn.execute(
                "SELECT name, bm25(wiki_fts, ?, ?, ?, ?) as score "
                "FROM wiki_fts WHERE wiki_fts MATCH ? "
                "ORDER BY score LIMIT ?",
                (*BM25_WEIGHTS, fts_query, top_k),
            ).fetchall()
            # bm25 returns negative scores (more negative = better match).
            return [(name, -score) for name, score in rows]
        except sqlite3.OperationalError:
            # malformed fts query fallback: try each term individually.
            all_results = {}
            for term in terms:
                try:
                    rows = conn.execute(
                        "SELECT name, bm25(wiki_fts, ?, ?, ?, ?) as score "
                        "FROM wiki_fts WHERE wiki_fts MATCH ? "
                        "ORDER BY score LIMIT ?",
                        (*BM25_WEIGHTS, term, top_k),
                    ).fetchall()
                    for name, score in rows:
                        if name not in all_results or -score > all_results[name]:
                            all_results[name] = -score
                except sqlite3.OperationalError:
                    continue
            return sorted(all_results.items(), key=lambda x: -x[1])

    def _graph_expand(self, seeds, hops=1):
        """1-hop bfs from seed pages via wikilink adjacency.

        pages linked by multiple seeds score higher.
        """
        conn = self._connect()
        graph = _build_link_graph(conn)

        # count how many seeds link to each neighbor.
        neighbor_hits = defaultdict(int)
        for seed in seeds:
            for neighbor in graph.get(seed, set()):
                if neighbor not in seeds:
                    neighbor_hits[neighbor] += 1

        return sorted(neighbor_hits.items(), key=lambda x: -x[1])

    # --- context assembly. ---

    def get_context(self, ranked_names, max_chars=MAX_CONTEXT_CHARS):
        """load page content for ranked results within char budget.

        prioritizes source pages first, then entities/concepts in rank order.
        returns dict of {name: content}.
        """
        conn = self._connect()
        context = {}
        chars = 0

        # separate sources from others, preserving rank order within each group.
        sources, others = [], []
        for name in ranked_names:
            row = conn.execute(
                "SELECT subdir, content FROM wiki_pages WHERE name = ?", (name,)
            ).fetchone()
            if row:
                subdir, content = row
                if subdir == "sources":
                    sources.append((name, content))
                else:
                    others.append((name, content))

        # load sources first (richest context), then others.
        for name, content in sources + others:
            if chars + len(content) > max_chars and context:
                break
            context[name] = content
            chars += len(content)

        return context

    def search_and_load(self, query, top_k=20, max_chars=MAX_CONTEXT_CHARS):
        """convenience: search + load context in one call.

        returns (context_dict, ranked_names, was_truncated).
        """
        ranked = self.search(query, top_k=top_k)
        if not ranked:
            return {}, [], False

        names = [name for name, *_ in ranked]
        context = self.get_context(names, max_chars=max_chars)
        was_truncated = len(context) < len(names)
        return context, names, was_truncated


# --- helpers (module-level, stateless). ---

def _extract_frontmatter(text):
    """pull type and tags from yaml frontmatter."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return "", ""
    fm = match.group(1)

    page_type = ""
    m = re.search(r"type:\s*(.+)", fm)
    if m:
        page_type = m.group(1).strip()

    tags = ""
    m = re.search(r"tags:\s*\[([^\]]*)\]", fm)
    if m:
        tags = m.group(1).strip()

    return page_type, tags


def _strip_frontmatter(text):
    """remove yaml frontmatter, return body text only."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return text
    return text[match.end():].strip()


def _build_link_graph(conn):
    """build bidirectional adjacency dict from wikilinks in all pages."""
    graph = defaultdict(set)

    rows = conn.execute("SELECT name, content FROM wiki_pages").fetchall()
    name_set = {name.lower(): name for name, _ in rows}

    for name, content in rows:
        links = set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content))
        for link in links:
            resolved = name_set.get(link.lower())
            if resolved and resolved != name:
                graph[name].add(resolved)
                graph[resolved].add(name)

    return dict(graph)


def _rrf(rankings, k=60):
    """reciprocal rank fusion. merges multiple ranked lists.

    score(doc) = sum(1 / (k + rank)) across all lists containing doc.
    """
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, name in enumerate(ranking, 1):
            scores[name] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


# --- cli for testing. ---

if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="llm wiki — search")
    parser.add_argument("query", nargs="?", help="search query")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the index")
    parser.add_argument("--top", type=int, default=10, help="number of results")
    args = parser.parse_args()

    with WikiSearch() as ws:
        if args.rebuild or not DB_PATH.exists():
            t0 = time.time()
            count = ws.build_index()
            print(f"\n  indexed {count} pages in {time.time() - t0:.2f}s\n")

        if args.query:
            t0 = time.time()
            results = ws.search(args.query, top_k=args.top)
            elapsed = (time.time() - t0) * 1000

            print(f"\n  results for: \"{args.query}\" ({elapsed:.0f}ms)\n")
            for i, (name, score) in enumerate(results, 1):
                print(f"  {i:2d}. [{score:.4f}] {name}")

            if results:
                context = ws.get_context([n for n, _ in results])
                total_chars = sum(len(v) for v in context.values())
                print(f"\n  context: {len(context)} pages, {total_chars:,} chars")
            print()
