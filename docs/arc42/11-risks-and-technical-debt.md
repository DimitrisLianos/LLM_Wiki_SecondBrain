# 11. Risks and Technical Debt

> **arc42, Section 11.** Honest accounting of what the POC does *not* do, what it has deferred, and what could trip it up at scale. Knowing what you are inheriting matters as much as knowing how the system works.

---

## 11.1 Known Limitations

Limitations accepted for the POC. Each is numbered for cross-reference from other sections.

### L-1, No automated relevance evaluation

The retrieval quality scenario [QS-8](10-quality-requirements.md#qs-8--the-relevant-page-appears-in-the-top-5-retrieval-hits) is verified by hand on a small set of test queries. There is no labelled evaluation set, no precision@k / recall@k metrics, no regression alarm. A change to BM25 column weights or RRF's `k` constant could silently degrade retrieval, with the operator noticing only through worse answers.

**Workaround.** Build a labelled test set and a `scripts/evaluate_retrieval.py` harness. Out of scope for the POC.

### L-2, No cross-lingual query expansion

FTS5 with the Porter stemmer indexes Greek content but does not bridge Greek↔English at query time. A query in English for a Greek-language source page will miss unless the source page itself contains English keywords (which it often does because the LLM extracted them during ingest, but not always).

**Workaround.** Stage 5 embeddings (`bge-m3`) handle cross-lingual matching inside the resolver, but not at query time. A query-time embedding-enriched retrieval would fix this but violates [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search).

### L-3, No semantic-similarity retrieval for novel phrasings

By design (see [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search)), retrieval is lexical. A query asking "what is the current state of the art in efficient inference for 30B-class models?" will miss a page titled "Mixture-of-Experts routing in Gemma 4" unless the query contains the right keywords.

**Workaround.** The wikilink graph helps here: a hit on "Gemma 4" pulls in its neighbours via 1-hop BFS, which often include MoE-adjacent pages. This is a partial mitigation, not a full solution.

### L-4, No incremental re-indexing

Every ingest rebuilds the FTS5 index from scratch via `WikiSearch.build_index()`. For hundreds of pages this takes under a second. For tens of thousands of pages the rebuild would become expensive.

**Workaround.** FTS5 supports incremental updates; the code currently does not use them for simplicity. At ≥ 5 000 pages this becomes worth addressing. Not an issue for the POC scale.

### L-5, No automated backup of `obsidian_vault/`

Personal wiki content is gitignored (correctly), which means the system does not back it up on the operator's behalf. If `obsidian_vault/` is deleted, everything in it is gone.

**Workaround.** Time Machine on macOS, or any filesystem-level backup the operator configures. Not the system's responsibility.

### L-6, Greek Porter stemmer is an imperfect fit

FTS5's default Porter stemmer is English-only. For Greek content, tokens are lowercased but not properly stemmed, so morphologically-related forms of the same word are indexed as distinct terms. This degrades BM25 relevance on Greek queries.

**Workaround.** The [Snowball Greek stemmer](https://snowballstem.org/algorithms/greek/stemmer.html) could be plugged in as a custom FTS5 tokeniser, but requires either a C extension or a pure-Python reimplementation. Out of scope for the POC.

### L-7, OCR of scanned PDFs is not supported

`pdftotext` extracts only the text layer. A scanned PDF with no text layer produces empty output and the ingest aborts. No OCR fallback exists.

**Workaround.** Run `ocrmypdf` out-of-band before placing the file in `raw/`. Adding OCR to the pipeline would require either a C dependency or a model-based solution, both of which violate [TC-1](02-architecture-constraints.md#21-technical-constraints).

---

## 11.2 Technical Debt

Tracked debt items not yet paid down. Each item has an estimated effort and a justification for its current "not yet" status.

| ID | Debt | Effort | Why not yet |
|---|---|---:|---|
| TD-1 | Extract `_validate_raw_path` helper into `llm_client.py` | 1 h | The inline path-containment check currently works; refactor is cosmetic. |
| TD-2 | Profile harness for QS-7 / QS-10 / QS-11 | 4 h | Manual measurement has been adequate; no regression observed. |
| TD-3 | Automated relevance test set (L-1) | 1 day | Requires labelled data; not yet curated. |
| TD-4 | Custom FTS5 tokeniser for Greek (L-6) | 1 day | Keyword match is adequate for current workloads. |
| TD-5 | Incremental FTS5 updates (L-4) | 4 h | Premature until corpus exceeds ~ 5 000 pages. |
| TD-6 | Splitting `ingest.py` into smaller modules (QS-13) | 1 day | `ingest.py` is ~ 1 850 lines; the layered decomposition in [§ 5.2](05-building-block-view.md#52-whitebox-ingestpy--c4-level-3-component-view) is internal. A split would reduce the "single biggest file" count but not improve readability much. |
| TD-7 | Centralised configuration instead of per-script constants | 4 h | Explicit rejection, constants at the top of each script are simpler than any config system. Listed for completeness. |
| TD-8 | Atomic write for `judge_cache.json` and friends | 1 h | Crash during write is recoverable; the cache rebuilds. |
| TD-9 | Unicode confusable normalisation in `safe_filename()` | 2 h | Single-user threat model; not exploitable. |

### What is not debt

Explicitly, the following are *not* debt even though they might look like it to an outside reader:

- **Zero external Python dependencies**, this is a quality goal, not a shortfall. See [ADR-001](09-architecture-decisions.md#adr-001--zero-external-python-dependencies).
- **No vector database**, this is a deliberate architectural choice. See [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search).
- **Manual rather than automated measurement of operational metrics**, intentional for a single-user POC; would be debt in a production system.

---

## 11.3 Risk Register

Risks that are not yet issues but could become issues. Probability × impact assessed qualitatively.

| ID | Risk | Probability | Impact | Mitigation |
|---|---|:---:|:---:|---|
| R-1 | TurboQuant fork diverges from mainline llama.cpp; fork becomes unmaintained | LOW | HIGH | Fallback to mainline documented in [§ 7.3](07-deployment-view.md#73-fallback-configurations) and [ADR-004](09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k). Costs ~ 2 GB memory, no code changes. |
| R-2 | Gemma 4 is superseded by a model that does not tolerate `--reasoning off` | MEDIUM | MEDIUM | Failure mode is visible (truncated outputs), not silent. Would require prompt and server flag re-tuning. |
| R-3 | Python 3.12 stdlib deprecates one of the APIs we rely on | LOW | LOW | `urllib.request` and `sqlite3` are stable APIs. `concurrent.futures` is stable. |
| R-4 | A new Unicode vulnerability in `safe_filename()` surfaces | LOW | MEDIUM | Test coverage on `safe_filename` is good; a new attack class would need to bypass both the control-char strip and the path separator strip. |
| R-5 | The user's raw corpus grows past ~ 10 000 pages and retrieval slows | LOW | MEDIUM | FTS5 scales to millions of rows; the bottleneck would be LLM synthesis, which is independent of corpus size (only the retrieved context matters). |
| R-6 | Obsidian changes its graph view to require a proprietary format | LOW | HIGH | Unlikely given Obsidian's stance; the wiki is still valid Markdown + YAML, so a different Markdown viewer would work. |
| R-7 | A cosmic-ray bit flip corrupts `wiki_search.db` mid-query | VERY LOW | LOW | SQLite has integrity checks; a rebuild from the Markdown source of truth is `search.py --rebuild`. |
| R-8 | Resolver regression re-introduces the ChatGPT fork epidemic | LOW | MEDIUM | `test_resolver_scenarios.py` specifically tests the ChatGPT case and the Aedes aegypti case. A regression would break a test. |
| R-9 | A future GGUF format is incompatible with the fork | MEDIUM | LOW | Re-clone mainline, accept the KV cache cost, update the `start_server.sh` defaults. |
| R-10 | Obsidian vault path changes and breaks the `BASE_DIR` assumption | LOW | LOW | `llm_client.py` resolves `BASE_DIR` relative to the script file, not the CWD. |

**None of the risks are in the HIGH-probability / HIGH-impact quadrant.** The most consequential risk (R-1) has a clean fallback path. The rest are low-probability or low-impact or both.

---

## 11.4 What Would Break First at Scale?

A thought experiment: if the user ingested 10× their current corpus overnight, which component would break first? The answer is instructive for anyone thinking about scaling the POC.

| Corpus size | First bottleneck | Mitigation |
|---|---|---|
| 10 × current (~ 5 000 pages) | FTS5 rebuild time from sub-second to ~ 5 s per ingest | Incremental index updates (TD-5) |
| 100 × current (~ 50 000 pages) | `lint.py` runtime (currently O(N) in page count) | Cached reverse-index for lint |
| 1 000 × current (~ 500 000 pages) | SQLite single-file size, potential FTS5 query latency | At this point a real search engine (Elasticsearch, Typesense) becomes justified |
| 10 000 × current (~ 5 M pages) | No longer a personal knowledge base | Different system |

The POC is comfortable up to ~ 1 000 pages and functional to ~ 5 000. Beyond that, it would need a real search engine and multi-process ingest. Neither is in scope.
