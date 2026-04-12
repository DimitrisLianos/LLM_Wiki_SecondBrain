# 5. Building Block View

> **arc42, Section 5.** Static decomposition. This is where the C4 model's Level 2 (Container) and Level 3 (Component) diagrams live, because arc42 and C4 are complementary and their Level 2/3 views answer the same questions arc42 expects in section 5.

Standalone C4 documents with the same diagrams and additional detail are also available at [`docs/c4/L2-container.md`](../c4/L2-container.md) and [`docs/c4/L3-component.md`](../c4/L3-component.md).

---

## 5.1 Whitebox Overall System — C4 Level 2 (Container View)

Opening the black box from [section 3](03-system-scope-and-context.md#32-technical-context--c4-level-1-system-context), the LLM Wiki system is composed of five *containers*, independently runnable or inspectable units of software and data.

```mermaid
graph TB
 user(("User"))

 subgraph SYS ["LLM Wiki system"]
 subgraph CLI ["Container -- CLI scripts<br/>Python 3.12+, stdlib only"]
 ingest["ingest.py<br/><i>write pipeline</i>"]
 query["query.py<br/><i>read pipeline</i>"]
 search_c["search.py<br/><i>retrieval engine</i>"]
 lint["lint.py<br/><i>health checks</i>"]
 cleanup["cleanup_dedup.py<br/><i>offline merge</i>"]
 watch["watch.sh<br/><i>filesystem trigger</i>"]
 end

 subgraph INF ["Container -- Inference servers<br/>llama.cpp (TurboQuant fork)"]
 gen["Generation server<br/><i>Gemma 4 26B-A4B<br/>127.0.0.1:8080</i>"]
 emb["Embedding server<br/><i>bge-m3 (optional)<br/>127.0.0.1:8081</i>"]
 end

 subgraph VAULT ["Container -- Obsidian vault<br/>filesystem, Markdown + YAML"]
 raw["raw/<br/><i>immutable</i>"]
 wiki["wiki/<br/><i>sources, entities,<br/>concepts, synthesis</i>"]
 idxlog["wiki/index.md<br/>wiki/log.md"]
 end

 subgraph SIDE ["Container -- Derived state<br/>regeneratable, gitignored"]
 db["db/wiki_search.db<br/><i>SQLite FTS5</i>"]
 reg["db/alias_registry.json<br/><i>runtime gazetteer</i>"]
 calib["db/judge_cache.json<br/>db/embed_cache.json<br/>db/resolver_calibration.json"]
 end

 subgraph GAZ ["Container -- Seed gazetteer<br/>git-tracked, read-only"]
 seed["scripts/data/<br/>seed_aliases.json<br/><i>149 curated entries</i>"]
 end
 end

 user --> ingest
 user --> query
 user --> lint
 user --> cleanup
 user --> watch

 ingest -- "HTTP /v1/chat/completions" --> gen
 query -- "HTTP /v1/chat/completions" --> gen
 ingest -. "HTTP /v1/embeddings<br/>(opt-in)" .-> emb

 watch --> ingest
 ingest --> raw
 ingest --> wiki
 ingest --> idxlog
 ingest --> db
 ingest --> reg
 ingest --> calib
 ingest --> seed

 query --> search_c
 search_c --> db
 search_c --> wiki
 query --> wiki

 lint --> wiki
 cleanup --> wiki
 cleanup --> seed
 cleanup --> reg

 style CLI fill:#fef9e7,stroke:#f39c12,color:#000
 style INF fill:#fdebd0,stroke:#e67e22,color:#000
 style VAULT fill:#eafaf1,stroke:#27ae60,color:#000
 style SIDE fill:#f5eef8,stroke:#8e44ad,color:#000
 style GAZ fill:#e8f4f8,stroke:#2980b9,color:#000
 style SYS fill:none,stroke:#2c3e50,color:#000
 style user fill:#dae8fc,stroke:#2980b9,color:#000
```

### Containers

| Container | Technology | Responsibility | Lifecycle |
|---|---|---|---|
| **CLI scripts** | Python 3.12+, stdlib only | User-facing commands. Everything the user interacts with on the terminal lives here. | Short-lived per command |
| **Inference servers** | [llama.cpp](https://github.com/TheTom/llama-cpp-turboquant) with Metal, Gemma 4 26B-A4B UD Q4_K_M | Text generation (mandatory); embeddings for entity resolution stage 5 (optional) | Long-running daemons, started on demand |
| **Obsidian vault** | Filesystem (Markdown + YAML frontmatter) | Persistent source and generated content. The only durable, human-facing state. | Permanent, managed by the user |
| **Derived state** | Filesystem (SQLite + JSON) | Regeneratable indices, caches and calibration data. `.gitignore`-d. | Rebuilt by `search.py --rebuild` or automatically after each ingest |
| **Seed gazetteer** | JSON committed to git | 149 curated canonical alias entries covering major AI/tech entities. Read-only at runtime. | Updated via hand edits + code review |

### Container-level interfaces

| Consumer → Producer | Interface | Notes |
|---|---|---|
| `ingest.py` → generation server | HTTP POST `/v1/chat/completions` | OpenAI-compatible shape. `urllib.request` stdlib only. |
| `query.py` → generation server | same | One call per query (answer synthesis). |
| `ingest.py` → embedding server | HTTP POST `/v1/embeddings` | Optional, behind `--use-embeddings` flag. Only used by resolver stage 5. |
| `search.py` → SQLite FTS5 | `sqlite3` stdlib, parameterised queries | Column weights `(10.0, 3.0, 5.0, 1.0)` for `(name, type, tags, content)` passed via `?` placeholders. |
| `ingest.py` / `query.py` → wiki | Filesystem writes/reads under a `safe_filename()` discipline | Path traversal defences in [`llm_client.safe_filename()`](../../scripts/llm_client.py). |
| `resolver.py` → seed + runtime gazetteer | JSON file reads with on-access normalisation | Seed tier is read-only; runtime tier is append-only via `aliases.promote()`. |

---

## 5.2 Whitebox `ingest.py` — C4 Level 3 (Component View)

The ingestion pipeline is the single largest module. `scripts/ingest.py` is roughly 1 850 lines organised into cohesive component groups. Decomposed at the component level:

```mermaid
graph TB
 subgraph INGEST ["ingest.py -- components"]
 direction LR

 subgraph READ ["Read layer"]
 parse["detect_and_parse()<br/><i>per-type parsers</i>"]
 pdf["_parse_pdf()<br/><i>pdftotext subprocess</i>"]
 xml["_parse_sms_xml()<br/><i>ElementTree</i>"]
 date["_extract_source_date()<br/><i>pdfinfo + regex</i>"]
 end

 subgraph CHUNK ["Chunking layer"]
 split["chunk_text()<br/><i>paragraph-boundary split<br/>max 50K chars</i>"]
 end

 subgraph EXTRACT ["Extraction layer"]
 parallel["extract_chunks_parallel()<br/><i>ThreadPoolExecutor<br/>PARALLEL_SLOTS=2</i>"]
 one["extract_chunk()<br/><i>LLM JSON extraction<br/>auto-split on overflow</i>"]
 parse_json["_parse_json()<br/><i>tolerant JSON parser</i>"]
 end

 subgraph MERGE ["Merge + canonicalize layer"]
 merge_fn["merge_extractions()<br/><i>dedup, richest desc wins</i>"]
 canon["_canonicalize_descriptions()<br/><i>fix 'the model' / 'our framework'<br/>via targeted LLM calls</i>"]
 norm["_normalize_via_aliases()<br/><i>gazetteer short-circuit</i>"]
 end

 subgraph RESOLVE ["Resolve layer"]
 resolve["resolver.resolve()<br/><i>stages 0-5</i>"]
 end

 subgraph WRITE ["Write layer"]
 write_source["_write_source_page()"]
 write_items["_write_item_pages()<br/><i>entities + concepts</i>"]
 update_idx["_update_index_and_log()"]
 rebuild["WikiSearch.build_index()<br/><i>FTS5 rebuild</i>"]
 end

 parse --> split
 pdf --> parse
 xml --> parse
 date --> parse
 split --> parallel
 parallel --> one
 one --> parse_json
 parse_json --> merge_fn
 merge_fn --> canon
 canon --> norm
 norm --> resolve
 resolve --> write_source
 resolve --> write_items
 write_source --> update_idx
 write_items --> update_idx
 update_idx --> rebuild
 end

 style READ fill:#fff2cc,stroke:#d6b656,color:#000
 style CHUNK fill:#d5e8d4,stroke:#82b366,color:#000
 style EXTRACT fill:#dae8fc,stroke:#6c8ebf,color:#000
 style MERGE fill:#e1d5e7,stroke:#9673a6,color:#000
 style RESOLVE fill:#f8cecc,stroke:#b85450,color:#000
 style WRITE fill:#fdebd0,stroke:#e67e22,color:#000
```

Each layer is one responsibility and the handoffs are the only seams. Layers are:

| Layer | Components | Responsibility |
|---|---|---|
| **Read** | `detect_and_parse`, `_parse_pdf`, `_parse_sms_xml`, `_extract_source_date` | Turn a file in `raw/` into UTF-8 text and an optional source date. PDF via Poppler subprocess. SMS XML via `xml.etree.ElementTree` (not vulnerable to XXE, Python's ET parser does not expand external entities, see [section 11.1, SEC-3](11-risks-and-technical-debt.md#111-security-posture)). |
| **Chunking** | `chunk_text` | Paragraph-boundary splits at ≤ 50 000 chars per chunk. Most single articles fit in one chunk; long PDFs split into 2-8. |
| **Extraction** | `extract_chunks_parallel`, `extract_chunk`, `_parse_json` | Parallel LLM calls across the two llama.cpp slots. Structured prompt asks for `{title, summary, key_claims, entities, concepts}`. Tolerant JSON parser recovers from the LLM's occasional half-valid output. Context-overflow errors auto-split the chunk and retry recursively (up to depth 2). |
| **Merge + canonicalize** | `merge_extractions`, `_canonicalize_descriptions`, `_normalize_via_aliases` | Deduplicate across chunks; richest description wins. Then a second LLM pass rewrites context-local descriptions ("the model", "our framework") into stand-alone ones, this is essential for the resolver's Jaccard stage to work. Finally the alias gazetteer short-circuits known entities. |
| **Resolve** | `resolver.resolve()` | Six stages 0-5. See [section 6.4](06-runtime-view.md#64-entity-resolution-stages-05) for the runtime view. |
| **Write** | `_write_source_page`, `_write_item_pages`, `_update_index_and_log`, `WikiSearch.build_index` | Write the source summary page, create-or-update entity and concept pages, update `wiki/index.md` and `wiki/log.md`, rebuild the FTS5 index. |

---

## 5.3 Whitebox `search.py` and `query.py` — retrieval and synthesis

The read pipeline is intentionally much thinner than the write pipeline.

```mermaid
graph TB
 subgraph READ_CTRL ["query.py -- control"]
 answer["answer_question()"]
 retrieve_ctx["retrieve_context()"]
 synth["synthesise_prompt()"]
 end

 subgraph SEARCH_LIB ["search.py -- WikiSearch"]
 build["build_index()"]
 search["search()<br/><i>FTS5 + BM25</i>"]
 graph_exp["_graph_expand()<br/><i>1-hop BFS on<br/>wikilink adjacency</i>"]
 rrf["_rrf()<br/><i>Cormack et al. 2009</i>"]
 find_src["find_source_page()<br/><i>idempotency reverse index</i>"]
 end

 subgraph LM_IF ["llm_client.py"]
 llm_call["llm()<br/><i>/v1/chat/completions</i>"]
 end

 DB[("SQLite<br/>wiki_search.db")]
 FS[("wiki/*.md")]

 answer --> retrieve_ctx
 retrieve_ctx --> search
 search --> DB
 search --> graph_exp
 graph_exp --> DB
 search --> rrf
 retrieve_ctx --> FS
 retrieve_ctx --> synth
 synth --> llm_call
 build --> DB
 build --> FS
 find_src --> DB

 style READ_CTRL fill:#fef9e7,stroke:#f39c12,color:#000
 style SEARCH_LIB fill:#e1f5d4,stroke:#5aa02c,color:#000
 style LM_IF fill:#fdebd0,stroke:#e67e22,color:#000
```

`WikiSearch` (in [`scripts/search.py`](../../scripts/search.py)) is the reusable retrieval library. It holds all the SQL, all the BM25 weights (`(10.0, 3.0, 5.0, 1.0)` for `(name, type, tags, content)`), all the graph-expansion logic and the `_rrf` fusion primitive. `query.py` is the thin controller: it calls `WikiSearch.search()`, assembles the context within the 40 000-char budget and issues one LLM call for synthesis.

The `find_source_page()` method on `WikiSearch` is load-bearing for idempotency: when a source is re-ingested, the pipeline asks the reverse index (a `source_files` table in SQLite) for its previous page stem, avoiding an O(N) linear scan across all source pages on disk. This replaced an earlier per-ingest directory scan, see [Appendix A, section A.2.F](appendix-a-academic-retrospective.md#a2-succeeded-and-fits-purpose).

---

## 5.4 Whitebox `resolver.py` — the entity resolution pipeline

The resolver is the single most complex module. Its internal structure is covered in [section 6.4 (Runtime View, stages 0-5)](06-runtime-view.md#64-entity-resolution-stages-05). At the *static* building-block level, the components are:

| Component | Lines (approx.) | Responsibility |
|---|---|---|
| `Resolution` dataclass | 20 | Immutable return type carrying action (`create`, `merge`, `fork`), resolved name, reason and the stage that produced the verdict |
| `Resolver` class | 600 | Stateful pipeline holding the gazetteer, judge cache, embed cache and calibration store |
| `_stage_0_alias_anchor()` | 110 | Gazetteer short-circuit, canonical alias registry lookup ([`scripts/aliases.py`](../../scripts/aliases.py)) |
| `_stage_1_exact_path()` | 30 | Direct path check, if no file exists, create |
| `_stage_2_type_constraint()` | 70 | Fork on type mismatch *only when descriptions also disagree* (narrowed after the Aedes aegypti incident, see [Appendix A, F-6](appendix-a-academic-retrospective.md#f-6--aedes-aegypti-fork-from-llm-type-noise)) |
| `_stage_3_jaccard()` | 50 | Stemmed Jaccard similarity over descriptions with `MERGE=0.55` and `FORK=0.15` thresholds |
| `_stage_4_llm_judge()` | 90 | Pairwise LLM judge call for borderline cases, verdict cached in `db/judge_cache.json` |
| `_stage_5_embed_cosine()` | 130 | Optional bge-m3 cosine similarity with F1-tuned threshold, gated behind `use_embeddings` flag |
| `_f1_optimal_threshold()` | 60 | Fawcett (2006) precision-recall sweep with `MIN_SAMPLES_FOR_TUNING=20`, `MIN_NEGATIVES=5`, `MIN_POSITIVES=5` gates |

The `aliases.py` sidecar (544 lines) manages the two-tier gazetteer: the committed seed tier in `scripts/data/seed_aliases.json` (149 entries) and the runtime-promoted tier in `db/alias_registry.json`. Promotion happens automatically when a wiki page accumulates ≥ 3 distinct sources and a non-generic description.

---

## 5.5 `llm_client.py` — the shared foundation

The smallest and most load-bearing module. Every other script imports from it. Its job is to be the single source of truth for three things:

1. **Paths** (`BASE_DIR`, `RAW_DIR`, `WIKI_DIR`, `DB_PATH`, `LLAMA_URL`, `EMBED_URL`), so nothing else in the tree hardcodes a filesystem location.
2. **HTTP boilerplate** (`llm()`, `embed()`, `require_server()`, `require_embed_server()`), so `ingest.py`, `query.py` and `resolver.py` all go through the same retry/timeout/error-handling path.
3. **Safe filesystem helpers** (`safe_filename()`, `find_existing_page()`), consolidated here after a duplicated-implementation incident (see [Appendix A, A.2.H](appendix-a-academic-retrospective.md#a2-succeeded-and-fits-purpose)).

The two typed exceptions, `ContextOverflowError` and `EmbeddingUnavailableError`, also live here, so callers do not need to parse `urllib.error.HTTPError` bodies themselves.

---

## 5.6 Why this decomposition

The modules are organised around three orthogonal axes:

- **The write/read split.** `ingest.py` and everything it calls (the resolver, the aliases module) are write-path. `query.py` and `search.py` are read-path. `llm_client.py` is shared infrastructure.
- **Pure functions vs. stateful classes.** `search.py` exposes a `WikiSearch` class because the SQLite connection is its state. `resolver.py` exposes a `Resolver` class because the gazetteer + caches are its state. Everything else is plain functions.
- **Stdlib-first, optional second-server second.** The core pipeline runs on `llama-server` only. The embedding server is a second, optional llama.cpp instance used only for resolver stage 5 with `--use-embeddings`. Nothing else in the system knows or cares whether it is running.

This structure is the direct result of the "zero dependencies" and "fork-on-uncertainty" strategic choices from [section 4](04-solution-strategy.md#42-key-strategic-decisions). Were either of those relaxed, the decomposition would be different, for example, a typical RAG project would have a `VectorStore` container here instead of `WikiSearch`.
