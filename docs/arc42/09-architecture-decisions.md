# 9. Architecture Decisions

> **arc42, Section 9.** Architectural Decision Records (ADRs). Each ADR captures one non-trivial design choice: its context, the alternatives considered, the chosen option and the consequences, positive and negative. ADRs are immutable historical records; when a decision changes, a new ADR supersedes the old one rather than editing it in place.

The format is a lightweight variant of [Michael Nygard's ADR template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locations/nygard/index.md). Status is one of `Proposed`, `Accepted`, `Superseded`, `Deprecated`.

Earlier sections reference these ADRs by their ID. The set below covers the five load-bearing decisions referenced from [section 4.2](04-solution-strategy.md#42-key-strategic-decisions) plus two smaller decisions that influenced the resolver's shape.

---

## ADR-001 — Zero external Python dependencies

- **Status:** Accepted
- **Date:** 2026-04-07
- **Deciders:** author
- **Consulted:** none (single-author POC)

### Context

The project is a personal knowledge base POC. It has to run, today, on a fresh MacBook with nothing but Python 3.12 from the system installer. It must also be auditable in one sitting, a reader who opens `scripts/` should be able to understand every import and every external call in 20 minutes.

Typical RAG stacks (LangChain, LlamaIndex, Haystack) reach several hundred transitive dependencies after one `pip install`. Each dependency adds:

- A compatibility dimension the reader has to keep in mind.
- A potential supply-chain attack surface ([OWASP A06:2021](https://owasp.org/Top10/A06_2021-Vulnerable_and_Outdated_Components/)).
- A reproducibility failure mode (lockfile drift, interpreter mismatch, platform wheel unavailability).
- A piece of prior art that obscures the actual design choices in the project, the reader cannot tell which decisions are ours and which are the framework's defaults.

### Decision

**The core runtime pipeline uses only the Python 3.12+ standard library.** Specifically:

- HTTP: `urllib.request` + `json`
- XML: `xml.etree.ElementTree`
- Storage: `sqlite3` with FTS5
- Concurrency: `concurrent.futures.ThreadPoolExecutor`
- Subprocess: `subprocess.run` in list form
- CLI: `argparse`
- Paths: `pathlib.Path`

No `pip install` is required. No `requirements.txt` exists. No `venv` is created. `pyproject.toml` exists only for metadata and IDE hints; it declares zero runtime dependencies.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| LangChain + ChromaDB + PyPDF | ~ 400 transitive deps. Obscures the design. Supply-chain surface is larger than the project itself. |
| LlamaIndex | Same. |
| Minimal pinned set (`requests`, `pypdf`, `chromadb`) | Still a lockfile to maintain; the stdlib versions of each are adequate. |
| Nix-style reproducible environment | Reproducible but still a dependency graph; fails the "auditable in 20 minutes" goal. |

### Consequences

**Positive:**

- Cloning the repo is the install step. Nothing else.
- Every import statement in the tree names a stdlib module. A reviewer can verify the closed-world property with `grep -R "^import\|^from" scripts/`.
- SSRF, XXE, deserialisation and pickling attacks are absent-by-construction ([§ 11.1](11-risks-and-technical-debt.md#111-security-posture)).
- Python 3.12 has enough stdlib to cover the whole surface (FTS5 arrived in Python's bundled SQLite a few versions back and `concurrent.futures` has been stable for a decade).

**Negative:**

- A tolerant JSON parser, a stemmed Jaccard, a minimal YAML reader and a few other small utilities live in this repo rather than in `pip install`. These are bounded, well-tested and each under 100 lines.
- Some third-party improvements are not available (e.g. `pydantic` validation, `rich` tables). The workarounds are explicit.
- If a new requirement appears that genuinely needs a dependency (e.g. Arabic text normalisation), the constraint is the first thing to renegotiate. Until then, it holds.

### Related

- [TC-1](02-architecture-constraints.md#21-technical-constraints)
- [§ 8.7 (Stdlib-only as a crosscutting constraint)](08-crosscutting-concepts.md#87-stdlib-only-as-a-cross-cutting-constraint)

---

## ADR-002 — Fork on uncertainty, never silently merge

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** author

### Context

Entity resolution is a lossy operation. When the system is deciding whether two mentions `Transformer` and `Transformer` refer to the same thing, it has only local evidence: two descriptions, two types and possibly a similarity score. That evidence is insufficient to be certain in the hard cases:

- `Transformer` as a deep-learning architecture vs `Transformer` as an electrical device.
- `Python` as a programming language vs `Python` as a snake species.
- `ChatGPT` as OpenAI's product vs `ChatGPT` as generic noun for any LLM.

The cost of a **wrong merge** is catastrophic and unrecoverable: two distinct entities are glued together, their descriptions are interleaved and the wikilink graph is silently corrupted. A human curator reading the page has no signal that a merge happened.

The cost of a **wrong fork** is recoverable: two pages exist that should be one. `cleanup_dedup.py` can merge them in a batch pass. The user sees both pages in Obsidian's graph view and can decide.

### Decision

**Fork is the default on uncertainty.** Every ambiguous case in `scripts/resolver.py` routes to fork unless the evidence is strong enough for merge. Specifically:

- Stage 3 (Jaccard): merge requires ≥ 0,55 similarity; fork at ≤ 0,15; otherwise proceed to stage 4.
- Stage 4 (LLM judge): merge requires an explicit YES verdict; on anything else (NO, ambiguous, parse failure), fork.
- Stage 5 (embedding cosine): merge requires cosine above the F1-tuned threshold; on borderline cases, fork.

This is the opposite of a typical entity-linking system, which tries hard to merge. For a personal knowledge base where the user is actively curating, "fork and clean up later" is the right default.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| Merge by default when similarity > 0,5 | Produces silent data loss on borderline cases. Unrecoverable without old backups. |
| Ask the user interactively | Doesn't scale past a few dozen ambiguous items per ingest. Defeats the "run overnight" use case. |
| Log ambiguous cases and do nothing | Leaves the wiki in an inconsistent state until the user acts. |

### Consequences

**Positive:**

- No wiki page is ever silently corrupted.
- `cleanup_dedup.py` exists as a recovery pass and actually does the merging when the user is ready.
- Debugging is easier, when a fork happens, both sides are visible in Obsidian.

**Negative:**

- The wiki initially has more pages than it "should", up to 10-20 % fork rate on a fresh corpus before running `cleanup_dedup.py`.
- `cleanup_dedup.py` has to be idempotent and correctness-critical (it is and there is a dedicated test file `scripts/test_ingest_dedup.py`).
- Stage 0 (gazetteer anchor) had to be added later to prevent forks on *known* entities. See [ADR-005](#adr-005--six-stage-entity-resolver-with-gazetteer-anchor).

### Related

- [§ 6.4 (Entity resolution stages 0-5)](06-runtime-view.md#64-entity-resolution-stages-05)
- [Appendix A, F-4 (ChatGPT fork epidemic)](appendix-a-academic-retrospective.md#f-4--chatgpt-fork-epidemic)

---

## ADR-003 — FTS5 + wikilink graph + RRF over vector search

- **Status:** Accepted
- **Date:** 2026-04-08
- **Deciders:** author

### Context

The query pipeline has to retrieve relevant pages from a growing corpus of Markdown files. The default choice in 2026 would be a vector database (ChromaDB, Qdrant, Weaviate, or Pinecone) with dense embeddings of every page.

But this project has three structural advantages that make the default choice a poor fit:

1. **The wiki is already structured.** Every page has a title, a type, tags and a body. Column weights on a traditional search index can exploit that structure directly.
2. **The wikilink graph already encodes semantic neighbourhood.** `[[X]] → [[Y]]` is an explicit statement that X and Y are related. Dense embeddings try to recover this signal from the text; the wiki hands it to us as a graph.
3. **The extraction step canonicalises descriptions.** Content words are not noisy LLM output; they are cleaned and deduplicated. BM25 over clean text is stronger than BM25 over raw text.

The research literature confirms that BM25 is a surprisingly strong baseline in well-constructed corpora. [Rosa et al. (2021)](https://arxiv.org/abs/2105.05686) show that properly-tuned BM25 matches or beats naive dense retrieval on MS MARCO; [Thakur et al. (BEIR, NeurIPS 2021)](https://arxiv.org/abs/2104.08663) show that dense retrievers are often brittle out-of-domain while BM25 generalises.

A first attempt used an LLM to pick pages from an index. It failed at ~ 500 pages for context-window reasons, see [Appendix A, F-1](appendix-a-academic-retrospective.md#f-1--llm-based-page-selection).

### Decision

**Retrieval is SQLite FTS5 + BM25 + wikilink graph expansion + Reciprocal Rank Fusion**, with no vector store and no LLM call on the retrieval path.

Specifically:

1. `search.py` issues an FTS5 query with column weights `(10.0, 3.0, 5.0, 1.0)` for `(name, type, tags, content)`.
2. The top-K BM25 hits seed a 1-hop BFS over the `wikilinks` adjacency table.
3. The two ranked lists (BM25 + graph) are fused via Reciprocal Rank Fusion ([Cormack, Clarke, Büttcher, SIGIR 2009](https://doi.org/10.1145/1571941.1572114)) with `k = 60`.
4. The fused top-M pages are loaded within the 40 000-char context budget.
5. One LLM call synthesises the answer.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| ChromaDB + sentence-transformers | Extra process, extra dependency, violates [ADR-001](#adr-001--zero-external-python-dependencies). Empirically unnecessary on a structured wiki. |
| Hybrid BM25 + dense with SPLADE | Complexity without measurable benefit on this corpus; SPLADE needs a server of its own. |
| LLM picks pages from an index | Failed at ~ 500 pages due to context-window truncation. [Appendix A, F-1](appendix-a-academic-retrospective.md#f-1--llm-based-page-selection). |
| GraphRAG / SubgraphRAG style | The wiki's wikilink graph is already a usable retrieval structure without the GraphRAG query-expansion pass; the simpler approach measured similarly on test queries. |

### Consequences

**Positive:**

- Retrieval is ~ 5 ms, no LLM call on the hot path.
- One stdlib module (`sqlite3`) replaces what would otherwise be a separate server.
- BM25 column weights are a dial the author can actually turn and measure.
- The wikilink graph is used directly instead of approximated.

**Negative:**

- Semantically-similar-but-lexically-different queries are weaker than a dense retriever would be. The author has not yet seen a real query where this matters, but it is a known limitation.
- FTS5 does not index on update automatically; the pipeline has to rebuild the index after every ingest. This takes < 1 s so it is free in practice, but it is a discipline point.
- No multilingual magic; the FTS5 Porter stemmer is English-specific. Greek sources are indexed, but Greek→English lexical matching is not automatic. (Stage 5 embeddings with bge-m3 handle cross-lingual entity matching inside the resolver; query-time cross-lingual retrieval is a known gap.)

### Related

- [§ 4.2 (Key strategic decisions)](04-solution-strategy.md#42-key-strategic-decisions)
- [§ 5.3 (search.py and query.py)](05-building-block-view.md#53-whitebox-searchpy-and-querypy--retrieval-and-synthesis)
- [§ 6.3 (Query pipeline)](06-runtime-view.md#63-query-pipeline)
- [Appendix A, F-1](appendix-a-academic-retrospective.md#f-1--llm-based-page-selection)

---

## ADR-004 — TurboQuant turbo4 V only, q8_0 K

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** author
- **Supersedes:** none (initial decision)

### Context

The 32 GB memory budget is tight. At mainline llama.cpp with `q8_0` KV cache for both K and V, the 2-slot × 32 K-token KV cache costs ~ 5 GB, leaving ~ 6 GB of nominal headroom that macOS starts encroaching on under load. TurboQuant ([Zandieh et al. ICLR 2026](https://arxiv.org/abs/2504.19874)) offers four modes:

- `turbo2`, 2-bit values (extreme compression)
- `turbo3`, 3-bit values (aggressive)
- `turbo4`, 4-bit values (moderate)
- asymmetric combinations (`q8_0` K + `turboN` V)

Community benchmarks in the [`TheTom/turboquant_plus`](https://github.com/TheTom/turboquant_plus) research repo report:

- On many models (Llama 3, Qwen 2.5, Phi 3), `turbo3` is safe and gives the best savings.
- **On Gemma 4 Q4_K_M, `turbo3` catastrophically blows up perplexity** (measured PPL > 100 000 on a held-out set). The interaction between Unsloth Dynamic quantization of the weights and `turbo3`'s 3-bit rotation of the values destabilises attention routing beyond recovery.
- `turbo4` on Gemma 4 Q4_K_M passes 37/37 quality tests and 8/8 Needle-in-a-Haystack tests in the same community benchmark.

The separate question of whether to compress K is empirically clear: on Gemma 4, *any* K compression (`q4`, `q5`, `turbo4`, `turbo3`) hurts attention routing accuracy. Keys must stay at `q8_0` full precision.

### Decision

**Asymmetric: `q8_0` for K, `turbo4` for V.** Both hard-coded in `scripts/start_server.sh`:

```bash
KV_TYPE_K="q8_0" # full precision keys (attention routing)
KV_TYPE_V="turbo4" # TurboQuant 4-bit values (3,8x compression)
```

`turbo3` is explicitly called out as unsafe for Gemma 4 in the script's comment block.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| `q8_0` K + `q8_0` V (mainline) | Safe but uses ~ 5 GB KV cache; leaves insufficient headroom. Falls back here on 16 GB or when the fork is unavailable, see [§ 7.3](07-deployment-view.md#73-fallback-configurations). |
| `q8_0` K + `turbo3` V | Catastrophic PPL blowup on Gemma 4 Q4_K_M. Observed across multiple test prompts. |
| `turbo3` K + `turbo4` V | K compression hurts attention routing independently. Rejected by the same benchmark set. |
| `turbo2` anything on Gemma 4 | More aggressive than `turbo3`, already unsafe. |

### Consequences

**Positive:**

- KV cache drops from ~ 5 GB to ~ 3 GB, freeing ~ 2 GB of headroom on 32 GB machines.
- 65 536-token total context (2 × 32 K) is feasible without starving macOS.
- The asymmetric split is the only one that is simultaneously safe and space-saving on Gemma 4.

**Negative:**

- Ties the project to a specific llama.cpp fork ([TC-4](02-architecture-constraints.md#21-technical-constraints)). Fallback to mainline requires reverting V to `q8_0`.
- Benchmarks for new Gemma versions must be re-run before changing the cache type.
- The decision is model-specific; migrating to a different base model (say, Qwen 3 or Llama 4) would require re-evaluating whether `turbo3` is safe on that architecture.

### Related

- [Pillar 4 (TurboQuant KV cache)](04-solution-strategy.md#pillar-4--the-runtime-turboquant-kv-cache)
- [§ 7.2 (Memory budget)](07-deployment-view.md#72-memory-budget)
- [Appendix A, F-5](appendix-a-academic-retrospective.md#f-5--turbo3-on-gemma-4-q4_k_m)

---

## ADR-005 — Six-stage entity resolver with gazetteer anchor

- **Status:** Accepted
- **Date:** 2026-04-10
- **Deciders:** author
- **Supersedes:** an earlier four-stage resolver without Stage 0

### Context

The initial four-stage resolver (exact path → type constraint → Jaccard → LLM judge) produced a *cross-document proper-noun fork epidemic* on re-ingestion, the full symptom and root-cause analysis is in [Appendix A, F-4](appendix-a-academic-retrospective.md#f-4--chatgpt-fork-epidemic). In short: the resolver re-decided "is this the same ChatGPT?" on every ingest from per-source context-local descriptions and Jaccard similarity between two context-local descriptions is unreliable.

The standard fix in the entity-linking literature is a **gazetteer** placed before any similarity math (spaCy `EntityRuler`, Stanford CoreNLP `RegexNER`, Wikidata aliases). Known entities are routed to canonical pages without ever reaching the Jaccard or judge stages.

### Decision

**The resolver has six stages, numbered 0 through 5.** Stage 0 is a two-tier canonical alias registry (gazetteer); stages 1-4 are the original fallback; stage 5 is an opt-in bge-m3 cosine re-ranker. Specifically:

- **Stage 0, Gazetteer anchor.** Consults `scripts/data/seed_aliases.json` (149 curated entries) and `db/alias_registry.json` (runtime tier, auto-promoted). On a hit, rewrite the mention to the canonical form, replace the LLM's guessed type with the canonical type and replace thin context-local descriptions with the curated blurb. Short-circuits the rest of the pipeline.
- **Stages 1-4.** Exact path check → type-constraint fork (narrowed to require Jaccard disagreement too, after the [Aedes aegypti incident](appendix-a-academic-retrospective.md#f-6--aedes-aegypti-fork-from-llm-type-noise)) → stemmed Jaccard with `MERGE=0.55`/`FORK=0.15` thresholds → LLM pairwise judge (cached).
- **Stage 5, bge-m3 cosine (opt-in).** Only active with `--use-embeddings` and the embedding server up. F1-tuned threshold with hard gates (`MIN_SAMPLES_FOR_TUNING=20`, `MIN_NEGATIVES=5`, `MIN_POSITIVES=5`).

Seed tier entries are curated by hand and committed to git under review. Runtime tier entries are self-promoted: wiki pages with ≥ 3 distinct sources and a non-generic description graduate into `db/alias_registry.json` automatically after each successful ingest. Two guards, subdirectory containment and type-compatibility, prevent cross-bucket matches.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| Keep the four-stage resolver, raise Jaccard merge threshold | Doesn't fix the root cause; just delays the fork symptom to cleaner cases |
| Re-rank every mention with bge-m3 embeddings | Needs the embedding server up *always*, violating [§ 7.3](07-deployment-view.md#73-fallback-configurations) fallback story. Also slower per mention than a hash lookup. |
| Reuse a public gazetteer (Wikidata / DBpedia) | Huge, mostly irrelevant and would violate [ADR-001](#adr-001--zero-external-python-dependencies). A small curated list covers the 80% case. |
| Use the LLM to decide "is this a known entity?" before resolving | Circular, the LLM just extracted the mention, asking it again doesn't add information and costs another call. |

### Consequences

**Positive:**

- Fork epidemic eliminated for known entities. Stage 0 hits short-circuit ~ 70% of mentions on real corpora, measured over a 200-source test set.
- The seed list is tiny (149 entries) and is a one-time curation cost.
- Runtime promotion means the gazetteer compounds, the more the user ingests, the more entities skip the slow path.
- The rest of the resolver is unchanged; stage 0 is a pure addition rather than a rewrite.

**Negative:**

- The seed list has to be maintained; adding a new major AI lab requires a code review on `seed_aliases.json`.
- Promotion rules are tuneable and have edge cases (what if one source reuses the same generic description three times?). Current heuristic is "3 distinct sources, non-generic description length, non-template opening sentence".
- Stage 0 hits hide the stage 1-4 logic on most mentions, which means coverage testing has to exercise non-gazetteer paths explicitly.
- Three tests (`test_aliases.py`, `test_resolver.py`, `test_resolver_scenarios.py`) now cover the interaction surface. It is a surface.

### Related

- [§ 5.4 (resolver.py whitebox)](05-building-block-view.md#54-whitebox-resolverpy--the-entity-resolution-pipeline)
- [§ 6.4 (Entity resolution stages 0-5)](06-runtime-view.md#64-entity-resolution-stages-05)
- [Appendix A, F-4 (ChatGPT fork epidemic)](appendix-a-academic-retrospective.md#f-4--chatgpt-fork-epidemic)
- [Appendix A, F-6 (Aedes aegypti incident)](appendix-a-academic-retrospective.md#f-6--aedes-aegypti-fork-from-llm-type-noise)

---

## ADR-006 — F1-optimal threshold tuning with hard sample-count gates

- **Status:** Accepted
- **Date:** 2026-04-11
- **Deciders:** author
- **Supersedes:** a naive F1 tuner that degenerated on imbalanced data

### Context

Stage 5 of the resolver (bge-m3 cosine similarity) needs a threshold. A fixed threshold (0,75) works on toy data but is suboptimal as the calibration cache accumulates labelled pairs from real ingests. The natural upgrade is to compute the F1-optimal threshold from the accumulated positive/negative pairs on the fly, which is a textbook operation ([Fawcett, 2006](https://doi.org/10.1016/j.patrec.2005.10.010)).

The naive version, "as soon as there are any pairs, compute F1-optimal threshold", degenerated in practice when the cache hit 51 positives / 1 negative. The sweep picked a near-zero threshold and accepted everything, silently breaking the resolver's ability to fork.

### Decision

**The F1 tuner activates only when three sample-count gates are all satisfied.** All of the following must hold before the tuner replaces the default threshold:

```python
MIN_SAMPLES_FOR_TUNING = 20 # total (positive + negative)
MIN_NEGATIVES = 5 # to prevent accept-everything degeneracy
MIN_POSITIVES = 5 # to prevent reject-everything degeneracy
```

If any gate fails, stage 5 uses the static `DEFAULT_EMBED_THRESHOLD = 0.75`. The gate logic is in `resolver.Resolver._f1_optimal_threshold()` with inline comments linking back to the incident.

### Alternatives considered

| Alternative | Rejected because |
|---|---|
| Remove the F1 tuner entirely | Loses the measurable-improvement case when the cache becomes balanced. |
| Use only a ratio gate (e.g. negatives/total ≥ 0,2) | Fails at small absolute sample sizes. 1 negative / 4 positives is still statistically meaningless. |
| Use a Bayesian prior instead of F1 | Adds complexity ([ADR-001](#adr-001--zero-external-python-dependencies) pressure) with no clear advantage on this small-data regime. |

### Consequences

**Positive:**

- The accept-everything failure mode is prevented by construction.
- The default threshold is still used when there isn't enough data to tune, which is the safe choice.
- The gates are named constants at the top of the file, so they are easy to find and adjust.

**Negative:**

- The tuner is idle for longer than it could be with a loose gate. On a fresh corpus, this can mean hundreds of ingests before the F1 tuner kicks in. In practice the default threshold is fine during this window.
- There is a transition boundary: the first ingest that crosses the gates causes a visible threshold change, which shows up as a difference in stage-5 behaviour from one ingest to the next. This is logged.

### Related

- [§ 6.4 (Stage 5, bge-m3 cosine)](06-runtime-view.md#stage-5--bge-m3-cosine-opt-in)
- [Appendix A, F-2 (F1 threshold degenerated on imbalanced calibration cache)](appendix-a-academic-retrospective.md#f-2--f1-threshold-degenerated-on-imbalanced-calibration-cache)

---

## ADR-007 — Reverse-index (`source_files`) for idempotent re-ingestion

- **Status:** Accepted
- **Date:** 2026-04-09
- **Deciders:** author
- **Supersedes:** per-ingest O(N) directory scan

### Context

When a file in `raw/` is re-ingested (e.g. after correcting an OCR issue, or because `watch.sh` re-triggered), the pipeline must find the existing `wiki/sources/<stem>.md` and update it in place. The initial implementation did this by scanning every file in `wiki/sources/` on each ingest and comparing frontmatter `sources` fields. This is O(N) per ingest and grew linearly with the corpus.

A simpler primitive exists: a reverse index mapping the raw filename to the source page stem. SQLite already holds the FTS5 index; one more small table is free.

### Decision

**A `source_files` table in `wiki_search.db` maps `(original_filename) → (source_page_stem)`.** It is populated on every ingest as part of `WikiSearch.build_index()` and consulted by `WikiSearch.find_source_page(filename)` before any directory scan.

The table is also used by `cleanup_dedup.py` to detect when a source page has been merged away and its reverse-index entry needs updating.

### Consequences

**Positive:**

- Re-ingestion is O(1) lookup instead of O(N) scan.
- Idempotency is provably correct: the reverse index is the single source of truth for "which source page was produced by which raw file".
- `cleanup_dedup.py` can update the reverse index when it merges pages, preserving correctness across cleanup passes.

**Negative:**

- Adds one more table to the schema, which means `search.py --rebuild` has to know to populate it.
- If the reverse index falls out of sync with the filesystem (e.g. a user manually deletes a source page), the next re-ingest will create a duplicate. `lint.py` has a check for this.

### Related

- [§ 5.3 (`find_source_page()` is load-bearing for idempotency)](05-building-block-view.md#53-whitebox-searchpy-and-querypy--retrieval-and-synthesis)
- [Appendix A, A.2 (succeeded and fits purpose)](appendix-a-academic-retrospective.md#a2-succeeded-and-fits-purpose)

---

## ADR Log

| ID | Title | Status | Supersedes |
|---|---|---|---|
| ADR-001 | Zero external Python dependencies | Accepted | - |
| ADR-002 | Fork on uncertainty, never silently merge | Accepted | - |
| ADR-003 | FTS5 + wikilink graph + RRF over vector search | Accepted | - |
| ADR-004 | TurboQuant turbo4 V only, q8_0 K | Accepted | - |
| ADR-005 | Six-stage entity resolver with gazetteer anchor | Accepted | four-stage resolver |
| ADR-006 | F1-optimal threshold tuning with hard sample-count gates | Accepted | naive F1 tuner |
| ADR-007 | Reverse-index for idempotent re-ingestion | Accepted | O(N) directory scan |
