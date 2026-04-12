# 6. Runtime View

> **arc42, Section 6.** Dynamic behaviour, how the building blocks from [section 5](05-building-block-view.md) actually cooperate over time. Three scenarios dominate: ingestion, query and entity resolution. A fourth scenario, the auto-split recovery on context overflow, is covered as a cross-cutting runtime concern because it appears in multiple pipelines.

---

## 6.1 Runtime Scenario Overview

| Scenario | Trigger | Cost | Frequency |
|---|---|---|---|
| **Ingestion** ([§ 6.2](#62-ingestion-pipeline)) | `python3 scripts/ingest.py <file>` or `watch.sh` filesystem event | 30 s - 10 min depending on source size | Rare, batch-oriented |
| **Query** ([§ 6.3](#63-query-pipeline)) | `python3 scripts/query.py "…"` | 3 - 10 s (retrieval ≈ 5 ms, synthesis dominates) | Frequent |
| **Entity resolution** ([§ 6.4](#64-entity-resolution-stages-05)) | Called from within ingestion for each extracted entity or concept | 50 µs (stage 0 hit) to ~3 s (stage 4 LLM judge) | Many times per ingest (10-100 items) |
| **Context-overflow recovery** ([§ 6.5](#65-context-overflow-recovery)) | HTTP 400 from llama.cpp when the prompt exceeds per-slot context | Recursive retry up to depth 2 | Triggered on very long chunks |
| **Lint** ([§ 6.6](#66-lint-pass)) | `python3 scripts/lint.py` | < 1 s | Ad-hoc, no LLM calls |

---

## 6.2 Ingestion Pipeline

```mermaid
sequenceDiagram
 autonumber
 actor U as User
 participant I as ingest.py
 participant P as pdftotext<br/>(subprocess)
 participant S as llama-server<br/>127.0.0.1:8080
 participant R as resolver.Resolver
 participant G as aliases gazetteer<br/>(seed + runtime)
 participant W as wiki/*.md<br/>(filesystem)
 participant DB as wiki_search.db<br/>(SQLite FTS5)

 U->>I: python3 scripts/ingest.py article.pdf
 I->>I: resolve path under RAW_DIR<br/>validate containment
 I->>P: subprocess.run(["pdftotext", path, "-"])
 P-->>I: UTF-8 text
 I->>I: chunk_text() &mdash; paragraph split, max 50K chars

 par Parallel extraction across 2 slots
 I->>S: POST /v1/chat/completions<br/>(chunk 1, extraction prompt)
 S-->>I: JSON {entities, concepts, claims}
 and
 I->>S: POST /v1/chat/completions<br/>(chunk 2, extraction prompt)
 S-->>I: JSON {entities, concepts, claims}
 end

 I->>I: merge_extractions() &mdash; dedup across chunks

 opt Context-local descriptions detected
 I->>S: POST /v1/chat/completions<br/>(_canonicalize_descriptions prompt)
 S-->>I: rewritten descriptions
 end

 I->>G: normalise via alias registry
 G-->>I: canonical names + types (or pass-through)

 loop For each entity / concept
 I->>R: resolver.resolve(name, type, desc, source)
 R->>G: stage 0 &mdash; gazetteer lookup
 alt Gazetteer hit
 G-->>R: canonical
 R-->>I: merge (stage 0)
 else Miss
 R->>R: stages 1-4 (exact / type / Jaccard / LLM judge)
 R-->>I: create | merge | fork
 end
 end

 I->>S: POST /v1/chat/completions<br/>(source-page summary prompt)
 S-->>I: 3&ndash;4 paragraph synthesis

 I->>W: write source page
 I->>W: write/update entity pages
 I->>W: write/update concept pages
 I->>W: append to log.md, update index.md
 I->>DB: WikiSearch.build_index() (incremental)
 I-->>U: "Wrote N source + M entity + K concept pages (t = 42 s)"
```

### Key runtime properties

- **Parallelism.** The `ThreadPoolExecutor` in `extract_chunks_parallel` is capped at `PARALLEL_SLOTS = 2` to match the llama.cpp server's `--parallel 2`. Oversubscribing would queue behind our own slots and add no speedup.
- **Idempotency.** The `WikiSearch.find_source_page()` reverse index detects previously-ingested files. Re-ingesting the same PDF finds the existing `wiki/sources/<name>.md`, updates it in place and re-runs resolution for its extracted items (which either no-op or merge).
- **LLM call count per source.** Typical: (number of chunks) + (0 to ~3 canonicalisation calls) + (0 to ~5 stage-4 judge calls for borderline entities) + 1 summary call. A single-chunk article usually costs 2-4 LLM calls total.
- **Failure modes.** Three typed errors, `ContextOverflowError` (HTTP 400, see [§ 6.5](#65-context-overflow-recovery)), `urllib.error.HTTPError` 5xx (retried with exponential backoff in `llm_client.llm()`) and `EmbeddingUnavailableError` (only when `--use-embeddings` is on and `EMBED_URL` is down).

---

## 6.3 Query Pipeline

```mermaid
sequenceDiagram
 autonumber
 actor U as User
 participant Q as query.py
 participant S as search.WikiSearch
 participant DB as wiki_search.db<br/>(FTS5)
 participant W as wiki/*.md
 participant LM as llama-server

 U->>Q: python3 scripts/query.py "what themes connect these sources?"
 Q->>S: search(question, top_k=10)
 S->>DB: SELECT ... FROM pages_fts<br/>WHERE pages_fts MATCH ?<br/>ORDER BY bm25(pages_fts, 10, 3, 5, 1)
 DB-->>S: top 10 BM25-ranked hits
 S->>DB: SELECT target FROM wikilinks<br/>WHERE source IN (top hits)
 DB-->>S: 1-hop adjacency
 S->>S: _rrf() &mdash; fuse (BM25 rank, graph rank)<br/>with k = 60 (Cormack et al. 2009)
 S-->>Q: ranked page list

 Q->>W: read top pages within 40K char budget<br/>(source pages prioritised)
 W-->>Q: context blob

 Q->>LM: POST /v1/chat/completions<br/>(context + question)
 LM-->>Q: answer with [[wikilink]] citations

 alt --save flag
 Q->>W: write synthesis page to wiki/synthesis/
 end

 Q-->>U: answer (stdout)
```

### Retrieval latency breakdown

| Phase | Typical time |
|---|---|
| FTS5 BM25 query | ~2 ms |
| Wikilink graph BFS | ~1 ms |
| RRF fusion | < 1 ms |
| Context assembly (file reads) | ~5 ms |
| LLM synthesis | 3-10 s |

**Retrieval is ~10 ms total; synthesis dominates by three orders of magnitude.** This is what makes the read pipeline feel instantaneous despite running locally on a 26B-parameter model.

The single LLM call operates on the ≤ 40 000-character context budget (≈ 10-12 K tokens), leaving room for the prompt (~500 tokens) and 2 048 output tokens within the 32 K per-slot window. Source pages are prioritised over entity/concept pages because they are more self-contained and cite each other via `[[wikilinks]]` anyway.

### Empty-FTS fallback policy

The initial version of `query.py` fell back to loading *every* wiki page into context when FTS5 returned no hits. This was removed because at 500+ pages it silently blew past the context window and produced truncated answers unrelated to the question. The current policy, documented in [`scripts/query.py`](../../scripts/query.py) lines 56-63, is to return an empty result with a "no matching pages, ingest more sources or rephrase" message. This is a quality-over-liveness choice. See [Appendix A, F-1](appendix-a-academic-retrospective.md#f-1--llm-based-page-selection) for the history.

---

## 6.4 Entity Resolution (Stages 0–5)

The resolver is called from within ingestion for every extracted entity and concept. Its job is to decide, given an incoming mention, whether to (a) create a new page, (b) merge into an existing page, or (c) fork into a disambiguated variant (`Transformer (architecture)` vs `Transformer (device)`).

```mermaid
sequenceDiagram
 autonumber
 participant I as ingest.py
 participant R as resolver.Resolver
 participant G as aliases gazetteer
 participant FS as wiki filesystem
 participant DB as SQLite
 participant LM as llama-server
 participant EM as embed-server<br/>(optional)

 I->>R: resolve(name="ChatGPT", type="tool", desc="...")

 rect rgba(200, 230, 200, 0.25)
 note over R,G: Stage 0 &mdash; Gazetteer anchor (prevention layer)
 R->>G: lookup("ChatGPT")
 alt Gazetteer hit
 G-->>R: canonical="ChatGPT", type="product", blurb="..."
 R-->>I: MERGE to canonical page (short-circuit)
 end
 end

 rect rgba(200, 220, 240, 0.25)
 note over R,FS: Stage 1 &mdash; Exact path check
 R->>FS: find_existing_page(name)
 alt No existing file
 R-->>I: CREATE new page
 end
 end

 rect rgba(240, 220, 220, 0.25)
 note over R: Stage 2 &mdash; Type-constraint fork<br/>(only when descriptions also disagree)
 alt type mismatch AND low Jaccard
 R-->>I: FORK disambiguated
 end
 end

 rect rgba(240, 230, 200, 0.25)
 note over R: Stage 3 &mdash; Stemmed Jaccard
 alt Jaccard &ge; 0,55
 R-->>I: MERGE
 else Jaccard &le; 0,15
 R-->>I: FORK
 end
 end

 rect rgba(230, 210, 240, 0.25)
 note over R,DB: Stage 4 &mdash; LLM judge (cached)
 R->>DB: judge_cache.get((name, existing))
 alt cache hit
 DB-->>R: cached verdict
 else miss
 R->>LM: POST /v1/chat/completions<br/>(pairwise disambiguation prompt)
 LM-->>R: YES / NO verdict + rationale
 R->>DB: judge_cache.put(...)
 end
 end

 rect rgba(220, 240, 230, 0.25)
 note over R,EM: Stage 5 &mdash; bge-m3 cosine (opt-in only)
 alt use_embeddings=True
 R->>EM: POST /v1/embeddings (name + desc)
 EM-->>R: 1024-dim vector
 R->>DB: embed_cache lookup
 R->>R: cosine similarity vs existing<br/>+ F1-tuned threshold
 alt cosine > threshold
 R-->>I: MERGE
 else
 R-->>I: FORK (default on uncertainty)
 end
 end
 end
```

### Stage-by-stage detail

#### Stage 0 — Canonical alias registry (the prevention layer)

Reference: `scripts/resolver.py:_stage_0_alias_anchor` + `scripts/aliases.py`

The two-tier gazetteer short-circuits the entire downstream pipeline for known entities. The seed tier (`scripts/data/seed_aliases.json`, 149 curated entries) is committed to git and read-only at runtime. The runtime tier (`db/alias_registry.json`) auto-populates as wiki pages accumulate ≥ 3 distinct sources. On a hit, the resolver rewrites the mention to the canonical form, replaces the LLM's guessed type and swaps context-local descriptions with the curated blurb. Two guards (subdirectory containment and type-compatibility) prevent cross-bucket matches.

This layer is the fix for the fork epidemic documented in [Appendix A, F-4](appendix-a-academic-retrospective.md#f-4--chatgpt-fork-epidemic). The full design rationale is in [ADR-005](09-architecture-decisions.md#adr-005--six-stage-entity-resolver-with-gazetteer-anchor).

#### Stages 1–2 — Exact path and type constraint

Stage 1 is the trivial case: if no file with this name exists, create. Stage 2 forks on genuine polysems, same name, different types, *and* sufficiently different descriptions. The "and descriptions also disagree" condition was added after the *Aedes aegypti* incident, where the LLM re-classified the same biological species across runs and the naive type-mismatch rule forked `Aedes aegypti` into `Aedes aegypti (model)` on re-ingest. See [Appendix A, F-6](appendix-a-academic-retrospective.md#f-6--aedes-aegypti-fork-from-llm-type-noise).

#### Stage 3 — Stemmed Jaccard similarity

Jaccard similarity over the set of stemmed content words in the two descriptions. Thresholds:

- `SIM_MERGE_THRESHOLD = 0.55`, above this, merge without further checks
- `SIM_FORK_THRESHOLD = 0.15`, below this, fork without further checks
- In between, proceed to stage 4

Stemming is a plain Porter stemmer reimplementation in pure Python. Stop-word removal and lowercasing happen before the token set is built.

#### Stage 4 — LLM pairwise judge

For borderline cases in the `(0.15, 0.55)` band, the resolver issues one LLM call asking "are these two descriptions referring to the same entity?". The verdict is cached in `db/judge_cache.json` keyed by the normalised `(incoming_name, existing_name)` pair, so the same pair never costs two judge calls across runs.

The prompt is narrow, one-shot and typed to return `YES` or `NO` with a one-sentence rationale. The default on LLM disagreement with the Jaccard signal is to *trust the judge*, because the judge has access to more context than a pure bag-of-words measure.

#### Stage 5 — bge-m3 cosine (opt-in)

Only active when `ingest.py` is run with `--use-embeddings` AND `scripts/start_embed_server.sh` has been started on port 8081 with a bge-m3 GGUF model. This stage:

1. Embeds the incoming mention's `(name + description)` via `llm_client.embed()` into a 1024-dimensional vector.
2. Embeds the existing page's same signature.
3. Computes cosine similarity.
4. Compares against either the static `DEFAULT_EMBED_THRESHOLD = 0.75` or an F1-tuned threshold computed from the accumulated `db/resolver_calibration.json` store.
5. Merges above the threshold, forks below, *with a default-to-fork behaviour on borderline uncertainty*.

The F1 threshold tuner has hard sample-count gates that prevent degeneration on imbalanced calibration data; see [ADR-006](09-architecture-decisions.md#adr-006--f1-optimal-threshold-tuning-with-hard-sample-count-gates) for the decision and [Appendix A, F-2](appendix-a-academic-retrospective.md#f-2--f1-threshold-degenerated-on-imbalanced-calibration-cache) for the failure that motivated the gates.

#### Academic grounding

The pipeline is directly informed by the entity-linking literature:

- **Gazetteer-first design**, the spaCy `EntityRuler` pattern and the Stanford CoreNLP `RegexNER` rule layer. Both are used in production systems as a "known names win" short-circuit before any learned model.
- **Surface-form priority**, Wikidata's alias list structure.
- **Layered resolver with similarity then a learned judge**, BLINK ([Wu et al. EMNLP 2020](https://arxiv.org/abs/1911.03814)) uses a bi-encoder for candidate retrieval and a cross-encoder for disambiguation. The same two-stage pattern is mirrored by Jaccard (bi-encoder analogue) + LLM judge (cross-encoder analogue).
- **Cross-lingual embedding re-rank**, ReFinED ([Ayoola et al. NAACL 2022](https://arxiv.org/abs/2207.04108)) and mGENRE ([De Cao et al. TACL 2022](https://arxiv.org/abs/2103.12528)) for multilingual EL; bge-m3 is our nearest open-weights analogue for the Greek + English corpus.
- **TAGME-style surface ambiguity**, Ferragina & Scaiella ([TAGME, CIKM 2010](https://doi.org/10.1145/1871437.1871689)) is the original paper on short-form surface ambiguity over Wikipedia.
- **Age-gap tiebreaker**, Hamilton, Leskovec & Jurafsky ([ACL 2016](https://arxiv.org/abs/1605.09096)) showed semantic drift has a ~10-year plateau, used as a "when borderline AND sources ≥ 10 years apart, fork" tiebreaker. The feature is implemented but gated behind `--use-embeddings` because it composes with stage 5, see [Appendix A, D-2](appendix-a-academic-retrospective.md#d-2--age-gap-tiebreaker-implemented-gated).

---

## 6.5 Context-Overflow Recovery

When a chunk is extracted and the composed prompt exceeds the llama.cpp server's per-slot context window (32 768 tokens), the server returns HTTP 400. The pipeline handles this deterministically rather than retrying the same oversized payload:

```mermaid
sequenceDiagram
 participant I as ingest.py
 participant L as llm_client.llm()
 participant S as llama-server

 I->>L: llm(prompt for chunk [0..50000])
 L->>S: POST /v1/chat/completions
 S-->>L: HTTP 400 &mdash; context overflow
 L-->>I: raise ContextOverflowError

 note over I: catch &rarr; split at nearest<br/>paragraph boundary to midpoint

 par Retry each half in parallel
 I->>L: llm(prompt for chunk [0..25000])
 L->>S: POST /v1/chat/completions
 S-->>L: 200 OK
 and
 I->>L: llm(prompt for chunk [25000..50000])
 L->>S: POST /v1/chat/completions
 S-->>L: 200 OK
 end

 note over I: recurses up to depth 2<br/>(quarter-chunks minimum)
```

The recursion depth is capped at 2, giving a minimum chunk size of 12 500 characters. If a 12 500-character chunk still overflows, the pipeline raises and the ingest fails with a clear error pointing at the offending source. In practice this has not happened on any real corpus.

---

## 6.6 Lint Pass

`python3 scripts/lint.py` runs the following checks over the wiki, with no LLM calls, in under a second on corpora of hundreds of pages:

| Check | Detects | Implemented by |
|---|---|---|
| Broken wikilinks | `[[Foo]]` with no corresponding `Foo.md` | Regex scan + path lookup per subdirectory |
| Orphan pages | Pages with no inbound links | Reverse-index build across all pages |
| Isolated pages | Pages with no outbound links | Forward-link count |
| Missing frontmatter | No YAML block at file start | Regex match on `^---\n...\n---\n` |
| Frontmatter errors | Invalid YAML or missing required keys | Minimal line-based YAML parser (stdlib only) |
| Thin pages | Body < 200 characters | Length check on the body after frontmatter |
| Index inconsistency | Pages in `wiki/*/` missing from `index.md` | Set difference between filesystem and parsed index |

Lint is the "LLM health checks" operation Karpathy describes, see [section 3.3](03-system-scope-and-context.md#33-mapping-to-karpathys-original-gist). It is the human curator's primary oversight tool.

---

## 6.7 Runtime Invariants

These are the things that must always hold at runtime for the system to be sound. They are enforced by code and verified by tests in `scripts/test_resolver.py`, `scripts/test_resolver_scenarios.py`, `scripts/test_aliases.py` and `scripts/test_ingest_dedup.py`.

1. **Every mention that hits the gazetteer short-circuits to the canonical page.** If this invariant breaks, the ChatGPT-fork epidemic returns.
2. **On uncertainty, fork rather than merge.** Silent merges are unrecoverable; forks are recoverable with a `cleanup_dedup.py` pass.
3. **Stage 5 is off by default.** No runtime depends on the embedding server being up unless the user explicitly opts in.
4. **The FTS5 index is always rebuilt after any write to the wiki.** Stale indices are treated as bugs.
5. **`raw/` is read-only for the pipeline.** Any write to `raw/` from code is a bug; `lint.py` does not actively police this but `ingest.py` has no write call with a `RAW_DIR` base.
6. **All LLM calls go through `llm_client.llm()`.** No other module issues its own `urllib.request.urlopen` against `LLAMA_URL`. This gives one place to add retry, timeout and error classification.
