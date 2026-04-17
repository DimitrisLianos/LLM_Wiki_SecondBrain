# 12. Glossary

> **arc42, Section 12.** Terms, acronyms and proper nouns that appear across the documentation, with one-sentence definitions and cross-references. Ordered alphabetically.

---

## A

**ADR, Architecture Decision Record.** A short document capturing one architectural decision with context, alternatives, choice and consequences. See [section 9](09-architecture-decisions.md).

**Aedes aegypti incident.** A resolver regression where the LLM re-classified the same biological species with different `type` values across ingests and the naive type-mismatch rule forked the same entity into disambiguated variants. Fixed by requiring Jaccard disagreement in addition to type mismatch. See [Appendix A, F-6](appendix-a-academic-retrospective.md#f-6--aedes-aegypti-fork-from-llm-type-noise).

**Alias registry.** Synonym for *gazetteer* in this project. The two-tier canonical entity store consisting of `scripts/data/seed_aliases.json` (seed tier) and `db/alias_registry.json` (runtime tier). See [section 6.4](06-runtime-view.md#stage-0--canonical-alias-registry-the-prevention-layer).

**Apple Silicon.** The Arm64 SoC family from Apple (M1 through M5). Relevant because unified memory and Metal GPU acceleration are first-class assumptions in this project. See [section 7.1](07-deployment-view.md#71-infrastructure).

**arc42.** The software architecture template this documentation follows. 12 sections covering requirements, constraints, context, strategy, structure, runtime, deployment, cross-cutting concepts, decisions, quality, risks and glossary. See [arc42.org](https://arc42.org).

## B

**BEIR.** A benchmark for zero-shot information retrieval across 18 datasets. Used to establish that BM25 is a strong baseline across domains. [Thakur et al. NeurIPS 2021](https://arxiv.org/abs/2104.08663).

**bge-m3.** A multilingual dense embedding model (BAAI, 2024). 1024-dimensional output, 8 192-token context, 100+ languages. Used in resolver stage 5 (opt-in) for cross-lingual entity matching. [Chen et al. 2024](https://arxiv.org/abs/2402.03216). [Model card](https://huggingface.co/BAAI/bge-m3).

**BLINK.** A neural entity-linking model using a bi-encoder for candidate retrieval and a cross-encoder for disambiguation. The two-stage pattern is mirrored in this project by Jaccard similarity (bi-encoder analogue) + LLM judge (cross-encoder analogue). [Wu et al. EMNLP 2020](https://arxiv.org/abs/1911.03814).

**BM25.** Best Match 25, the ranking function used by FTS5. Weights terms by corpus statistics (inverse document frequency) and document length normalisation. The de-facto strong baseline for lexical retrieval. [Robertson & Zaragoza, 2009](https://www.nowpublishers.com/article/Details/INR-019).

## C

**C4 model.** A four-level architecture visualisation technique (System Context → Container → Component → Code) by Simon Brown. Complements arc42; Level 1 sits in [arc42 section 3](03-system-scope-and-context.md), Level 2 and Level 3 sit in [arc42 section 5](05-building-block-view.md). Standalone copies in [`docs/c4/`](../c4/). See [c4model.com](https://c4model.com/).

**CLAUDE.md.** A top-level project file consumed by Claude Code and similar agentic tools. Contains the wiki schema, directory layout, operation contracts and agent-facing rules. See [`CLAUDE.md`](../../CLAUDE.md).

**Context overflow.** The runtime condition where the composed prompt for an LLM call exceeds the server's per-slot context window. The pipeline handles it deterministically by splitting the chunk in half and recursing up to depth 2. See [section 6.5](06-runtime-view.md#65-context-overflow-recovery) and `ContextOverflowError` in [`scripts/llm_client.py`](../../scripts/llm_client.py).

## D

**Dataview.** An Obsidian community plugin that treats YAML frontmatter as a queryable database. The project's frontmatter schema is Dataview-compatible by construction, though Dataview is not required for any pipeline operation.

**Derived state.** State that can be rebuilt from the wiki and the code. Includes `db/*.db`, `db/*.json` and the llama.cpp build tree. Gitignored. See [section 7.4](07-deployment-view.md#74-repository-hygiene-and-rebuildable-state).

**Disambiguation page.** A wiki page produced when the resolver forks an ambiguous entity. Example: `Transformer (architecture)` and `Transformer (device)` with a stub `Transformer` page pointing at both.

## E

**Embedding server.** The optional second `llama-server` instance running `bge-m3` on `127.0.0.1:8081`. Used only by resolver stage 5 with `--use-embeddings`. See [`scripts/start_embed_server.sh`](../../scripts/start_embed_server.sh) and [section 7.1](07-deployment-view.md#71-infrastructure).

**Entity page.** A wiki page of `type: entity`, representing a person, organisation, tool, dataset, or model. Stored in `wiki/entities/`.

**Entity resolution.** The process of deciding, given an incoming mention, whether it refers to an existing entity (merge), a novel one (create), or a disambiguated variant of an existing one (fork). The central correctness concern of the ingest pipeline. See [section 6.4](06-runtime-view.md#64-entity-resolution-stages-05).

## F

**F1-optimal threshold.** The decision threshold that maximises the harmonic mean of precision and recall. Computed by sweeping candidate thresholds over a labelled calibration set. Used in resolver stage 5 with hard sample-count gates. [Fawcett, 2006](https://doi.org/10.1016/j.patrec.2005.10.010). See [ADR-006](09-architecture-decisions.md#adr-006--f1-optimal-threshold-tuning-with-hard-sample-count-gates).

**Fork (resolver action).** The decision to create a disambiguated variant page instead of merging into an existing one. Preferred over silent merge on any uncertainty. See [ADR-002](09-architecture-decisions.md#adr-002--fork-on-uncertainty-never-silently-merge).

**FTS5.** SQLite's full-text search module. Tokenisation, BM25 ranking and column weights are first-class features. Used here as the sole retrieval index (no vector store). [SQLite FTS5 docs](https://www.sqlite.org/fts5.html).

## G

**Gazetteer.** A pre-populated registry of known entities with canonical names, aliases and types. Standard concept in entity linking (spaCy `EntityRuler`, Stanford `RegexNER`, Wikidata). In this project: the two-tier alias registry. See [ADR-005](09-architecture-decisions.md#adr-005--six-stage-entity-resolver-with-gazetteer-anchor).

**Gemma 4.** Google DeepMind's April 2026 open-weights model family. The 26B-A4B variant is an MoE with 25,2 B total parameters and 3,8 B active per token. Used as the sole generation model in this project. [Model card](https://ai.google.dev/gemma/docs/core/model_card_4).

**GGUF.** The quantised model file format used by llama.cpp. A single file containing weights, tokeniser and metadata. Loaded via `mmap` into unified memory. [Specification](https://github.com/ggml-org/ggml/blob/master/docs/gguf.md).

**GraphRAG.** Microsoft Research's retrieval-augmented generation approach that builds a knowledge graph from source documents and queries via graph traversal. A cousin of this project's approach; see [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search) for why we use a simpler wikilink graph.

## H

**Hugging Face.** The ML model registry where Unsloth publishes the Gemma 4 GGUF weights. Reached exactly once during setup; never during runtime. [huggingface.co](https://huggingface.co).

## I

**Idempotency (of ingest).** The property that re-ingesting the same file updates the existing source page in place rather than creating a duplicate. Implemented via the `source_files` reverse-index table. See [ADR-007](09-architecture-decisions.md#adr-007--reverse-index-source_files-for-idempotent-re-ingestion).

**Index (wiki/index.md).** The master catalog of the wiki, one line per page, under 120 characters per entry, grouped under Sources / Entities / Concepts / Synthesis. Updated on every ingest.

**Ingest.** The write-path pipeline: raw file → chunked → extracted → merged → resolved → written as a source page with linked entity and concept pages. See [section 6.2](06-runtime-view.md#62-ingestion-pipeline).

## J

**Jaccard similarity.** The size of the intersection divided by the size of the union of two sets. Used on stemmed token sets of entity descriptions to decide merge/fork in resolver stage 3. Thresholds: merge ≥ 0,55; fork ≤ 0,15.

**Judge cache.** `db/judge_cache.json`. Stores the verdict of resolver stage 4 (LLM pairwise disambiguation) keyed by the normalised `(incoming_name, existing_name)` pair. Prevents re-running the judge on the same pair across ingests.

## K

**Karpathy's LLM Wiki pattern.** The April 2026 pattern that inspired this project: an LLM compiles a wiki from raw source documents, rather than chunking for one-shot retrieval. Described in [this tweet](https://x.com/karpathy/status/2039805659525644595) and the [architecture gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

**KV cache.** The key/value tensors held by a transformer decoder for all previously-generated tokens. In llama.cpp, configurable per-type (K and V separately). Asymmetric `q8_0` K + `turbo4` V is the load-bearing choice of this project. See [ADR-004](09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k).

## L

**llama.cpp.** The open-source C++ implementation of LLM inference with GGUF support, Metal acceleration on Apple Silicon and an OpenAI-compatible HTTP server. [Repository](https://github.com/ggml-org/llama.cpp). This project uses a specific fork, see *TurboQuant fork*.

**Lint.** The health-check pass run by `scripts/lint.py`. Detects broken wikilinks, orphan pages, isolated pages, missing or malformed frontmatter, thin pages and index inconsistencies. See [section 6.6](06-runtime-view.md#66-lint-pass).

**Log (wiki/log.md).** An append-only chronological record of operations. Each entry names the operation, the source and the number of pages created/updated.

## M

**Metal.** Apple's GPU API. llama.cpp uses Metal for GPU acceleration on Apple Silicon; on M-series chips this means using the unified-memory GPU partition without host-to-device copies. See [section 7.1](07-deployment-view.md#71-infrastructure).

**mGENRE.** A multilingual autoregressive entity-linking model. Relevant as prior art for the cross-lingual component of the resolver. [De Cao et al. TACL 2022](https://arxiv.org/abs/2103.12528).

**MMLU Pro.** A harder variant of the MMLU knowledge benchmark. Used as a reference quality metric for Gemma 4's extraction capability. Gemma 4 26B-A4B scores 82,6 % ([model card](https://ai.google.dev/gemma/docs/core/model_card_4)).

**MoE, Mixture of Experts.** A transformer architecture where each layer has many expert feed-forward networks and a learned router activates a small subset per token. Gemma 4 26B-A4B uses 128 experts with 8 active (+ 1 shared) per token, giving 3,8 B active out of 25,2 B total parameters.

## N

**Needle in a Haystack (NIAH).** A long-context evaluation task: hide a fact in a long document and query for it. Used by the [TurboQuant+ benchmarks](https://github.com/TheTom/turboquant_plus) to verify that `turbo4` preserves retrieval accuracy.

## O

**Obsidian.** A Markdown-based personal knowledge-base application. Used here as a read-only browser of the generated wiki. Graph view, backlinks and Dataview queries all work on the output by construction.

## P

**Parallel slots.** llama.cpp's `--parallel N` flag splits the context window across N concurrent request slots. This project uses `--parallel 2` with a 65 536-token total context, giving 32 768 tokens per slot.

**PDF extraction.** Delegated to `pdftotext` (Poppler) via a subprocess. No Python PDF library is used, per [TC-1](02-architecture-constraints.md#21-technical-constraints). See [section 3.2](03-system-scope-and-context.md#32-technical-context--c4-level-1-system-context).

**PII.** Personally Identifiable Information. The `.gitignore` rules covering personal content (`obsidian_vault/raw/`, generated wiki subfolders, `db/`, logs, `models/`) are described in [§ 7.4 (Repository hygiene)](07-deployment-view.md#74-repository-hygiene-and-rebuildable-state).

**Poppler.** A PDF rendering library; `pdftotext` and `pdfinfo` are its command-line utilities. Installed via `brew install poppler` on macOS.

**Porter stemmer.** A classic English stemming algorithm (Porter, 1980). Reimplemented in pure Python for the Jaccard stage of the resolver. Also used indirectly via SQLite FTS5's Porter tokeniser.

**Promotion (of alias).** The process by which a wiki page with ≥ 3 distinct sources and a non-generic description graduates into the runtime alias registry. See [section 6.4](06-runtime-view.md#stage-0--canonical-alias-registry-the-prevention-layer).

## Q

**Q4_K_M.** A GGUF quantization scheme that stores most weights at 4 bits with a mix of higher-precision layers. Produces ~ 16 GB files for 26B-parameter models. Combined with Unsloth Dynamic's per-layer importance weighting, gives better quality than vanilla Q4_K_M at the same file size.

**Quality goal.** A non-functional requirement that drove architectural decisions. The three in this project: privacy, reproducibility, retrieval quality. See [section 1.2](01-introduction-and-goals.md#12-quality-goals).

**Query synthesis.** The LLM call in the read pipeline that produces a prose answer from retrieved context. See [section 6.3](06-runtime-view.md#63-query-pipeline).

## R

**Raw directory.** `obsidian_vault/raw/`. Immutable source documents. The pipeline only reads from here; it never writes. Gitignored except for `.gitkeep`.

**ReFinED.** A neural entity-linking model optimised for efficient inference. Relevant prior art for the resolver; also cross-lingual via mBERT. [Ayoola et al. NAACL 2022](https://arxiv.org/abs/2207.04108).

**Reciprocal Rank Fusion (RRF).** A rank-fusion technique: for each candidate, sum `1 / (k + rank)` across all input ranked lists. Simple, hyperparameter-light (just `k`) and strong. Used to fuse BM25 and graph expansion scores in `search.py._rrf()`. [Cormack, Clarke, Büttcher, SIGIR 2009](https://doi.org/10.1145/1571941.1572114).

**Resolver.** The six-stage entity resolution pipeline in `scripts/resolver.py`. See [section 6.4](06-runtime-view.md#64-entity-resolution-stages-05) and [ADR-005](09-architecture-decisions.md#adr-005--six-stage-entity-resolver-with-gazetteer-anchor).

**Reverse index.** The `source_files` table in `wiki_search.db` that maps raw filename → source page stem. Used for O(1) idempotency check on re-ingest. See [ADR-007](09-architecture-decisions.md#adr-007--reverse-index-source_files-for-idempotent-re-ingestion).

## S

**Seed tier.** The curated alias-registry tier stored in `scripts/data/seed_aliases.json`. 149 entries covering major AI labs, models, frameworks, languages and core concepts. Committed to git, read-only at runtime.

**Source page.** A wiki page of `type: source`, one per ingested file. Contains metadata, summary, key claims and links to extracted entities/concepts. Stored in `wiki/sources/`.

**SSRF, Server-Side Request Forgery.** An attack class where an attacker induces a server to make HTTP requests on their behalf. Absent by construction here because `LLAMA_URL` and `EMBED_URL` are hardcoded constants with no user-overridable override.

**Stage 0 … Stage 5.** The six stages of the resolver pipeline: gazetteer anchor → exact path → type constraint → Jaccard → LLM judge → embedding cosine. See [section 6.4](06-runtime-view.md#64-entity-resolution-stages-05).

**Synthesis page.** A wiki page of `type: synthesis`, produced by `query.py --save`. Contains a question, an answer and citations. Stored in `wiki/synthesis/`.

## T

**TAGME.** An early entity-linking system using topic-based coherence. [Ferragina & Scaiella, CIKM 2010](https://doi.org/10.1145/1871437.1871689). Listed here as prior art for surface-form ambiguity.

**Temperature.** The softmax temperature applied to LLM logits during sampling. Lower is more deterministic. Per-call values listed in [section 8.6](08-crosscutting-concepts.md#86-prompt-discipline).

**Thin page.** A wiki page with body content below a minimum length (200 characters in `lint.py`). Flagged as a quality issue, usually the resolver's sign that extraction was shallow.

**TurboQuant.** A KV-cache compression technique using rotated random projections (PolarQuant + Walsh-Hadamard rotation). [Zandieh et al. ICLR 2026](https://arxiv.org/abs/2504.19874). Shipped in the [`llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant) fork.

**TurboQuant fork.** [`TheTom/llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant), the fork of mainline llama.cpp that adds `turbo2/3/4` cache types with validated Metal/Apple Silicon support. Required for the `turbo4` V-cache configuration.

**`turbo4`.** The moderate TurboQuant setting: 4-bit compression for the V cache. Safe on Gemma 4 Q4_K_M. Not to be confused with `turbo3`, which is catastrophic on Gemma 4. See [ADR-004](09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k) and [Appendix A, F-5](appendix-a-academic-retrospective.md#f-5--turbo3-on-gemma-4-q4_k_m).

**Type constraint.** A resolver stage (stage 2) that forks entities when their types disagree *and* their descriptions also disagree. The "and descriptions" condition was added after the Aedes aegypti incident.

## U

**Unified memory.** The shared CPU/GPU address space on Apple Silicon. Model weights in GGUF are `mmap`-ed once and visible to both CPU code and Metal kernels without copying. See [section 7.1](07-deployment-view.md#71-infrastructure).

**Unsloth Dynamic 2.0 (UD).** Per-layer importance-weighted GGUF quantization. Attention-heavy layers retain higher precision; less-critical layers are more aggressively quantized. Same file size as vanilla Q4_K_M, measurably better quality. [Docs](https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs), [blog post](https://unsloth.ai/blog/dynamic-v2).

## V

**Vault.** The `obsidian_vault/` directory containing `raw/` and `wiki/`. Named for its role as an Obsidian vault when Obsidian is used as the browser.

**Vector database.** The entity that does **not** exist in this project. See [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search) for why.

## W

**Watch.** `scripts/watch.sh`. A filesystem watcher (using `fswatch`) that triggers `ingest.py` on new files in `raw/`. Optional with `--lint` to run `lint.py` after each ingest.

**Whitebox view.** An architectural diagram that opens up a previously-opaque component to show its internal structure. Used for `ingest.py`, `search.py`, `query.py` and `resolver.py` in [section 5](05-building-block-view.md).

**Wiki.** `obsidian_vault/wiki/`. The LLM's workspace. Contains `sources/`, `entities/`, `concepts/`, `synthesis/`, `index.md` and `log.md`. Gitignored except for `.gitkeep` markers.

**Wiki search (WikiSearch).** The retrieval library in `scripts/search.py`. Holds the SQLite connection, BM25 weights, graph-expansion logic and RRF fusion primitive. See [section 5.3](05-building-block-view.md#53-whitebox-searchpy-and-querypy--retrieval-and-synthesis).

**Wikilink.** An Obsidian-style inline link: `[[Target Page]]`. The only inter-page reference mechanism. Resolved at read time by `search.py._graph_expand()`.

**Wikidata alias.** Each Wikidata entity has a canonical label and a list of aliases. The pattern inspired the two-tier gazetteer in this project. See [ADR-005](09-architecture-decisions.md#adr-005--six-stage-entity-resolver-with-gazetteer-anchor).

## X

**XXE, XML External Entity.** An attack class where XML parsers expand external entities from a URL. Not reachable in this project because `xml.etree.ElementTree` in Python 3.12 does not expand external entities by default.

## Y

**YAML frontmatter.** The `---\nkey: value\n---` block at the top of every wiki page. Contains `type`, `tags`, `sources`, `created`, `updated`. Parsed by a minimal hand-written reader in `lint.py` (stdlib only). See [section 8.1](08-crosscutting-concepts.md#81-domain-model).

## Z

*(Nothing under Z. Included for completeness.)*
