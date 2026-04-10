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

from llm_client import WIKI_DIR, DB_PATH, SUBDIRS, MAX_CONTEXT_CHARS

# bm25 column weights: name 10x, type 3x, tags 5x, content 1x.
BM25_WEIGHTS = (10.0, 3.0, 5.0, 1.0)


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
        """rebuild the fts5 index from all wiki pages. <1s for hundreds of pages."""
        conn = self._connect()

        # drop and recreate for clean rebuild.
        conn.execute("DROP TABLE IF EXISTS wiki_fts")
        conn.execute("DROP TABLE IF EXISTS wiki_pages")

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

        conn.commit()
        return count

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
    if not text.startswith("---"):
        return "", ""
    end = text.find("---", 3)
    if end == -1:
        return "", ""
    fm = text[3:end]

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
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].strip()


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
