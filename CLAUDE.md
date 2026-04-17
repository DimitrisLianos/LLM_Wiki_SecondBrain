# LLM Wiki, Schema

A personal knowledge base maintained by a local LLM following [Karpathy's LLM Wiki pattern](https://x.com/karpathy/status/2039805659525644595).
**Fully offline**, all processing runs on-device via llama.cpp ([TurboQuant fork](https://github.com/TheTom/llama-cpp-turboquant)) + Gemma 4 26B-A4B (Unsloth Dynamic UD weights, turbo4 KV cache).
No cloud API calls. The LLM writes and maintains all wiki content; the human curates sources and asks questions.

## Architecture

Three directories, three roles:

- **raw/**, immutable source documents. The LLM reads from here but NEVER modifies these files.
- **wiki/**, the LLM's workspace. Create, update and maintain all files here.

```
SecondBrain_POC/
├── obsidian_vault/
│ ├── raw/ # Immutable source documents
│ │ ├── assets/ # Downloaded images and attachments
│ │ └── (source files: xml, md, pdf, txt, etc.)
│ └── wiki/ # LLM-generated and maintained
│ ├── index.md # Master catalog, updated on every ingest
│ ├── log.md # Chronological record, append-only
│ ├── sources/ # One summary page per ingested source
│ ├── entities/ # People, organizations, tools, datasets, models
│ ├── concepts/ # Methods, theories, frameworks, patterns
│ └── synthesis/ # Comparisons, analyses, cross-cutting themes
├── models/ # GGUF model files (gitignored)
├── llama.cpp/ # llama.cpp build (gitignored)
├── db/ # SQLite FTS5 index + alias_registry.json (gitignored)
├── logs/ # Rotating runtime logs (gitignored)
├── scripts/ # Stdlib-only CLI surface
│ ├── llm_client.py # Shared LLM client, paths and constants
│ ├── search.py # SQLite FTS5 + wikilink graph + RRF retrieval
│ ├── start_server.sh # Launch llama.cpp server with optimal settings
│ ├── ingest.py # Ingestion pipeline: raw/ → wiki/
│ ├── query.py # Query the wiki via local LLM
│ ├── resolver.py # Entity resolver (stages 0-5) with gazetteer
│ ├── aliases.py # Canonical alias registry (gazetteer loader)
│ ├── data/ # Curated seed data
│ │ └── seed_aliases.json # 149 canonical AI/tech entries
│ ├── cleanup_dedup.py # Merge duplicate pages (stem + alias-driven)
│ ├── lint.py # Wiki health checker
│ └── watch.sh # Filesystem watcher for auto-ingestion
├── web/ # Optional FastAPI + Lit web UI (adds fastapi + ddgs deps)
│ ├── api/
│ │ ├── app.py # FastAPI entrypoint, CSP + security headers, port 3000
│ │ ├── models.py # Pydantic request / response schemas
│ │ ├── services.py # Shared service layer over scripts/
│ │ └── routers/ # server, search, wiki, query, ingest, lint, dedup, admin
│ └── frontend/
│ ├── src/ # Lit components, DOMPurify + Marked renderers
│ ├── dist/ # Vite production bundle (served by FastAPI)
│ └── package.json # lit, marked, dompurify, vite
├── awake_mac.py # Prevent Mac sleep during long ingests
├── pyproject.toml # Stdlib-only core; optional extras: fastapi, ddgs
└── CLAUDE.md # This file, the wiki schema
```

## Local LLM Stack

- **Model**: Gemma 4 26B-A4B (Q4_K_M Unsloth Dynamic / UD), ~16GB
- **Runtime**: llama.cpp server ([TurboQuant fork](https://github.com/TheTom/llama-cpp-turboquant)), Metal GPU, flash attention, q8_0 K + turbo4 V KV cache, reasoning toggle (default `on` for chat)
- **Context**: 65536 total / 2 parallel slots = 32768 tokens per slot
- **Hardware**: Reference profile is an Apple Silicon MacBook with ≥ 32GB unified RAM (the project was developed on an M5 / 32GB class machine). Anything with comparable memory bandwidth and Metal support should work.
- **API**: llama.cpp HTTP endpoint at `http://127.0.0.1:8080` (`/v1/chat/completions`), embedding server at `http://127.0.0.1:8081`
- **Optional web UI**: FastAPI + Lit app under `web/`, bound to `127.0.0.1:3000`. Launched via `python3 web/api/app.py`.

Start the server: `bash scripts/start_server.sh`

**Reasoning mode**: Gemma 4's `<think>` mode defaults to `on` at the server level for chat quality. The mode is a toggle in `scripts/start_server.sh` (`REASONING="on" | "off"`) and is also exposed in the web UI header. **Ingestion requires `off`**: thinking tokens consume the output budget before the entity-extraction JSON is emitted, producing empty titles. Flip the toggle before each `ingest` run, then flip it back. All `<think>` blocks are automatically stripped from output by `llm_client.py`. Token budgets account for thinking overhead: query generation uses `max_tokens=8192`, classification uses `max_tokens=200`.

## Page Format

Every wiki page MUST include YAML frontmatter:

```markdown
---
type: source | entity | concept | synthesis
tags: [relevant, tags]
sources: [source-page-1, source-page-2]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# Page Title

Content with [[wikilinks]] for all cross-references.
```

## Wikilinks

- Always use `[[Page Name]]` for cross-references (Obsidian-compatible).
- Link liberally, every mention of a known entity or concept should be a wikilink.
- Filenames use spaces to match wikilink targets: `Speculative Cascading.md` matches `[[Speculative Cascading]]`.

## Tags

YAML frontmatter tags for Dataview queries:

**Entities:** `person`, `organization`, `tool`, `dataset`, `model`
**Concepts:** `method`, `theory`, `framework`, `pattern`, `metric`, `technique`
**Sources:** `paper`, `article`, `sms`, `note`
**Synthesis:** `query`, `comparison`, `analysis`

## Index Format

Each entry in `wiki/index.md` is one line:

```
- [[Page Name]], one-line summary (under 120 characters)
```

Organized under category headers: Sources, Entities, Concepts, Synthesis.

## Log Format

Each entry in `wiki/log.md`:

```
## [YYYY-MM-DD] operation | Title
Brief description. Created N new pages, updated M existing pages.
New entities: [[Entity1]], [[Entity2]]. New concepts: [[Concept1]].
```

## Operations

### Ingest (`python scripts/ingest.py <filename>`)
1. Parse the source file from `raw/` and split into chunks (max 50K chars per chunk).
2. For each chunk, extract entities, concepts and key claims via local LLM. Multiple chunks processed in parallel across 2 server slots.
3. If a chunk exceeds the context window (HTTP 400), auto-split in half and retry each piece. Recurses up to 2 levels (quarter-chunks).
4. Merge and deduplicate across chunks. Preserve richest descriptions.
5. Generate a unified summary via local LLM.
6. Write source page with: metadata, summary, key claims, entities/concepts mentioned.
7. Create or **update** entity and concept pages, add new source info, note contradictions.
8. Update `wiki/index.md` and append to `wiki/log.md`.

A single source typically touches 10-50 wiki pages. This is normal.

### Query (`python scripts/query.py "question"`)
1. Search the wiki via SQLite FTS5 full-text search with BM25 ranking.
2. Expand results via wikilink graph traversal (1-hop BFS from top hits).
3. Fuse rankings with Reciprocal Rank Fusion (RRF).
4. Load top-ranked pages within context budget (40K chars).
5. Synthesize an answer via local LLM with `[[wikilink]]` citations.
6. Optionally file the answer as a new synthesis page (`--save` flag).

Page selection is instant (~5ms), no LLM call needed for retrieval.

### Lint (`python scripts/lint.py`)
Checks for: broken wikilinks, orphan pages, missing pages, index consistency, frontmatter issues, thin pages.

### Watch (`bash scripts/watch.sh`)
Monitors `raw/` for new files and auto-ingests them. Optional `--lint` flag runs lint after each ingest.

## Entity Resolution

Entities and concepts extracted from sources are deduplicated through a six-stage resolver (`scripts/resolver.py`) grounded in entity-linking literature (BLINK, ReFinED, TAGME, mGENRE) and industry gazetteer patterns (spaCy EntityRuler, Stanford CoreNLP RegexNER, Wikidata surface forms).

### Stage 0, Canonical alias registry (gazetteer)
Before any similarity math, the incoming mention is looked up in a two-tier canonical alias registry:

- **Seed tier**, `scripts/data/seed_aliases.json`, 149 curated entries for AI labs, models, frameworks, tech companies and core concepts. Committed to git, read-only at runtime.
- **Runtime tier**, `db/alias_registry.json`, self-promoted. After each successful ingest, wiki pages with ≥3 distinct sources and a non-generic description graduate into this tier automatically.

A hit short-circuits the rest of the pipeline: the mention is rewritten to the canonical form, the canonical type replaces the incoming type and thin/context-local descriptions ("mentioned in the context of X") are replaced with the curated blurb. Subdirectory and type-compatibility guards prevent cross-bucket matches (Python language vs. Python snake, Transformer model vs. Transformer architecture).

This layer is the prevention mechanism for the "ChatGPT (tool) vs ChatGPT (model)" fork epidemic: the fork class described in academic EL as "surface form ambiguity with context-local priors".

### Stages 1-5, Fallback pipeline
Mentions that don't hit the registry run through the original layered resolver:
1. **Exact path**, if no file exists with this name, create.
2. **Type constraint**, fork on genuine polysems with disjoint types.
3. **Jaccard**, merge on high description overlap, fork on low.
4. **LLM judge**, a one-shot judge call for edge cases.
5. **bge-m3 embedding cosine**, semantic similarity tiebreaker.

### Cleanup
`python3 scripts/cleanup_dedup.py` merges duplicate pages. The grouping key is composite: registry-canonical when the page hits the gazetteer, stem-based otherwise. Dry run is the default; add `--apply` to write.

## Rules

1. **Never modify files in `raw/`.** They are immutable source material.
2. **Always update `wiki/index.md`** when you create or delete a page.
3. **Always append to `wiki/log.md`** when you perform an operation.
4. **Use `[[wikilinks]]`** for all internal references. Never use raw file paths in page content.
5. **Every wiki page must have YAML frontmatter** with type, tags, sources, created and updated fields.
6. **When new information contradicts existing wiki content**, update the wiki page and note the contradiction with both sources cited.
7. **Keep source summary pages factual.** Save interpretation and synthesis for concept and synthesis pages.
8. **When asked a question, search the wiki first.** Only go to raw sources if the wiki doesn't have the answer.
9. **Prefer updating existing pages over creating new ones.** Only create a new page when the topic is distinct enough to warrant it.
10. **Keep `wiki/index.md` concise**, one line per page, under 120 characters per entry.
