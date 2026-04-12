# C4 Level 1 — System Context

> **C4 Model, Level 1.** A System Context diagram is the highest-level picture of a software system. It answers a single question: *what are we building and who or what does it talk to?* At this level the system is one box. We do not care yet how it is decomposed internally, [Level 2](L2-container.md) zooms in one step and [Level 3](L3-component.md) zooms in one step further.
>
> This document is the standalone C4 presentation. The same diagram appears inline in [arc42 § 3 (System Scope and Context)](../arc42/03-system-scope-and-context.md) for readers who prefer the arc42 presentation. The two must agree, if you change one, change both.

---

## System in scope

**Name:** LLM Wiki, a fully local, Obsidian-compatible knowledge base compiled by an on-device LLM from a folder of personal source documents.

**Purpose:** Let one human ingest and then query their own documents, emails, SMS exports, PDFs, Markdown notes, research papers, without trusting any third party with the content.

**Pattern origin:** [Andrej Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), April 2026.

**Boundary rule:** Everything that crosses the boundary in either direction is pictured below. Nothing else is expected to cross it. If a new edge would appear, that is an architectural decision and must be documented in an ADR before code lands.

---

## System Context diagram

```mermaid
graph TB
 user(("User<br/>single human<br/>at one MacBook"))

 subgraph SYS ["LLM Wiki &mdash; system in scope"]
 app["Python CLI scripts<br/>+<br/>llama.cpp inference servers<br/>+<br/>SQLite FTS5 index<br/>+<br/>Obsidian vault"]
 end

 fs[("Local filesystem<br/><i>raw/, wiki/, db/, models/</i>")]
 poppler["Poppler<br/><i>pdftotext / pdfinfo<br/>subprocess, list form</i>"]
 obs["Obsidian<br/><i>desktop app, optional,<br/>read-only renderer</i>"]
 hf[("Hugging Face<br/><i>one-time GGUF download</i>")]
 gh[("GitHub<br/><i>one-time fork clone</i>")]

 user -- "CLI commands:<br/>ingest &middot; query &middot; lint &middot; watch" --> app
 app -- "synthesized answer<br/>with [[wikilink]] citations<br/>(stdout)" --> user
 user -- "drops source files" --> fs
 app -- "reads / writes<br/>(filesystem syscalls)" --> fs
 app -- "subprocess<br/>(shell=False, list form)" --> poppler
 obs -- "reads<br/>(filesystem)" --> fs
 hf -. "initial setup only" .-> fs
 gh -. "initial setup only" .-> fs

 style SYS fill:#e1f5d4,stroke:#5aa02c,color:#000
 style user fill:#dae8fc,stroke:#2980b9,color:#000
 style fs fill:#fff2cc,stroke:#d6b656,color:#000
 style poppler fill:#fdebd0,stroke:#e67e22,color:#000
 style obs fill:#e1d5e7,stroke:#9673a6,color:#000
 style hf fill:#f5f5f5,stroke:#999,color:#000,stroke-dasharray: 5 5
 style gh fill:#f5f5f5,stroke:#999,color:#000,stroke-dasharray: 5 5
```

---

## Element catalogue

### Person — User

| Attribute | Value |
|---|---|
| **Type** | Person (C4 notation) |
| **Cardinality** | 1 |
| **Description** | A single human operator on their own MacBook. The user drops source files into `raw/`, issues CLI commands and reads answers on stdout. |
| **Trust level** | Full trust, same UID as the scripts. The security model is *local privilege*, not *multi-user*. |
| **Authentication** | None. This is a single-user system and making it multi-user is an explicit non-goal (see [arc42 § 10, non-goals](../arc42/10-quality-requirements.md#105-non-goals)). |

### Software system — LLM Wiki (the system in scope)

| Attribute | Value |
|---|---|
| **Type** | Software system |
| **Technology summary** | Python 3.12+ (stdlib only), llama.cpp with Metal, Gemma 4 26B-A4B MoE, SQLite FTS5, Obsidian vault on filesystem |
| **Responsibility** | Ingest source documents → compile them into a cross-linked Markdown wiki → let the user query the wiki in natural language → file answers back into the wiki as synthesis pages |
| **Decomposition** | Opened at [Level 2 (Container view)](L2-container.md) |
| **Runtime location** | Exactly one process tree on one Apple Silicon MacBook. Loopback-only HTTP on `127.0.0.1:8080` and `127.0.0.1:8081`. |

### External software system — Local filesystem

| Attribute | Value |
|---|---|
| **Type** | External software system (OS filesystem) |
| **Interaction** | The only durable persistence mechanism. There is no database server, no message queue, no cloud bucket. |
| **Layout** | `obsidian_vault/raw/` (immutable source files), `obsidian_vault/wiki/` (LLM-generated Markdown), `db/` (SQLite + JSON caches), `models/` (GGUF weights) |
| **Notes** | Reads and writes go through `safe_filename()` and `find_existing_page()` helpers in [`scripts/llm_client.py`](../../scripts/llm_client.py), which enforce path-containment. See [arc42 § 11.1 SEC-2](../arc42/11-risks-and-technical-debt.md#111-security-posture). |

### External software system — Poppler

| Attribute | Value |
|---|---|
| **Type** | External executable dependency |
| **Binary** | [Poppler](https://poppler.freedesktop.org/), `pdftotext` and `pdfinfo` |
| **Interaction** | Called by `scripts/ingest.py` via `subprocess.run([...], shell=False)` on a resolved `Path` object |
| **Why not a library** | Python-native PDF libraries (PyMuPDF, pdfplumber, pypdf) would add a runtime dependency, which violates [ADR-001 (zero dependencies)](../arc42/09-architecture-decisions.md#adr-001--zero-external-python-dependencies). Poppler is installed system-wide via Homebrew and invoked as a subprocess. |
| **Data flow** | Inbound only (PDF text extraction). No data ever flows back to Poppler. |

### External software system — Obsidian

| Attribute | Value |
|---|---|
| **Type** | External desktop application, optional |
| **Interaction** | Obsidian watches `obsidian_vault/` and renders the Markdown files with graph view, backlinks, Dataview queries. It is **read-only from Obsidian's side**: Obsidian does not run the pipeline, does not write back to the vault and is not required for any script in this repository to work. |
| **Why optional** | The pipeline produces plain Markdown + YAML frontmatter. You could read it in any text editor. Obsidian is the ergonomic default because the pattern the system implements, [Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), uses it explicitly. |

### External software system — Hugging Face (dashed)

| Attribute | Value |
|---|---|
| **Type** | External software system, **one-time setup only** |
| **Interaction** | The user runs `huggingface-cli download unsloth/gemma-3-27b-it-GGUF` **once** at setup to place the GGUF weights in `models/`. After that the system never talks to Hugging Face again. |
| **Why dashed** | The dashed edge signals that this is not part of the normal runtime path. It is present in the picture because the reproducibility procedure in [arc42 § 7.5](../arc42/07-deployment-view.md) requires it; it is dashed because it is a [one-time setup step, not an operational dependency](../arc42/07-deployment-view.md). |
| **Air-gap compatibility** | A prepared laptop can be taken fully offline; the edge never needs to activate again. |

### External software system — GitHub (dashed)

| Attribute | Value |
|---|---|
| **Type** | External software system, **one-time setup only** |
| **Interaction** | The user runs `git clone https://github.com/TheTom/llama-cpp-turboquant.git llama.cpp` **once** at setup to fetch the inference runtime fork. After that the `.git` directory is unused and can be deleted. |
| **Why dashed** | Same rationale as Hugging Face, setup-time edge, not a runtime dependency. The fork is pinned for the [specific TurboQuant KV-cache semantics documented in ADR-004](../arc42/09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k). |

---

## Relationship catalogue

| # | From | To | Direction | Protocol | Data | Rate |
|---|---|---|---|---|---|---|
| 1 | User | LLM Wiki (app) | → | POSIX CLI (`argv`, stdin, stdout, stderr) | Commands: `ingest`, `query`, `lint`, `watch`, `cleanup_dedup` | Human-paced, a few per session |
| 2 | LLM Wiki (app) | User | → | Stdout text | Synthesized answers with `[[wikilink]]` citations; progress output; error messages | One response per CLI invocation |
| 3 | User | Local filesystem | → | Filesystem (Finder, `cp`, `mv`) | Source documents dropped into `obsidian_vault/raw/` | Occasional |
| 4 | LLM Wiki (app) | Local filesystem | ↔ | Filesystem syscalls (`open`, `read`, `write`, `rename`, `stat`) | Markdown, YAML, SQLite, JSON | Many per second during ingest; read-heavy during query |
| 5 | LLM Wiki (app) | Poppler | → | `subprocess.run(list, shell=False)` | Absolute file path of a PDF in `raw/` | Once per PDF ingest |
| 6 | Poppler | LLM Wiki (app) | → | Stdout capture | Extracted UTF-8 text and metadata | Once per PDF ingest |
| 7 | Obsidian | Local filesystem | → | Filesystem (FS events, file reads) | Reads `wiki/*.md`; renders graph view and backlinks | Continuous when the app is open |
| 8 | Hugging Face | Local filesystem | ⇢ (dashed) | HTTPS (`huggingface-cli`) | GGUF weights | **One-off, setup only** |
| 9 | GitHub | Local filesystem | ⇢ (dashed) | HTTPS (`git clone`) | `llama-cpp-turboquant` source code | **One-off, setup only** |

Relationships 1-7 are the steady state. Relationships 8-9 exist only during initial setup and are never reactivated during normal operation.

---

## What is deliberately **not** on this diagram

This section is load-bearing. The absence of these edges is a direct expression of [Quality Goal Q1 (privacy)](../arc42/01-introduction-and-goals.md#12-quality-goals):

| Missing edge | Why it is missing |
|---|---|
| LLM Wiki → OpenAI / Anthropic / any LLM API | All inference is on-device. `grep -R "https://" scripts/` returns only documentation comments, never a live request target. |
| LLM Wiki → telemetry service | No telemetry collector exists. There is no crash reporter, no usage metric, no update check. |
| LLM Wiki → vector database (Pinecone, Weaviate, Qdrant, …) | SQLite FTS5 + wikilink graph is the retrieval stack. Rationale in [ADR-003](../arc42/09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search). |
| LLM Wiki → message queue (Redis, RabbitMQ, SQS, …) | There is no long-running broker. Concurrency is one `ThreadPoolExecutor` inside one Python process. |
| LLM Wiki → auth provider (OAuth, Auth0, …) | Single-user by design. Multi-user is an explicit non-goal ([arc42 § 10.8](../arc42/10-quality-requirements.md#105-non-goals)). |
| LLM Wiki → external web scrape / search engine | Sources are dropped into `raw/` manually. The system never fetches anything from the open web. |
| LLM Wiki → backup service | Backups are the user's responsibility. The repo is *intended* to live under a user-managed backup (Time Machine, rsync, etc.); it does not bring its own. |

Every one of these absences would be a new edge in this diagram if reintroduced. By policy, adding any such edge is a breaking change to Q1 and requires an ADR.

---

## Boundary invariants

Three invariants follow mechanically from the diagram above. They are enforceable with `grep` and should be checked before any release:

1. **No outbound HTTPS.** `grep -R "https?://" scripts/` may match only comments and docstrings, never a live `urllib.request.urlopen`, `http.client`, `socket.create_connection`, `requests.get`, or equivalent. Violation = Q1 broken.
2. **Loopback-only server binding.** Both llama.cpp servers bind to `127.0.0.1`. Checked in [`scripts/start_server.sh`](../../scripts/start_server.sh) and [`scripts/start_embed_server.sh`](../../scripts/start_embed_server.sh). Violation = server exposed on LAN.
3. **One-way raw/.** `raw/` is read but never written by any script in this repository. Checked by inspecting `scripts/*.py` for any `open(... "w")` under `RAW_DIR`. Violation = immutability of source material broken ([CLAUDE.md Rule 1](../../CLAUDE.md)).

These three checks together are the minimal mechanical proof that the Level 1 boundary is intact.

---

## Where to go next

- **[C4 Level 2, Container view](L2-container.md)**, opens the `LLM Wiki` box into its five containers: CLI scripts, inference servers, vault, derived state, seed gazetteer.
- **[C4 Level 3, Component view](L3-component.md)**, opens the most complex containers (`ingest.py`, `query.py` + `search.py`, `resolver.py`) into their internal components.
- **[arc42 § 3, System Scope and Context](../arc42/03-system-scope-and-context.md)**, the same diagram with additional narrative on the mapping to Karpathy's original gist.
- **[arc42 § 1, Introduction and Goals](../arc42/01-introduction-and-goals.md)**, why the boundary is drawn this way (the quality goals).
- **[arc42 § 9, Architecture Decisions](../arc42/09-architecture-decisions.md)**, the ADRs that justify every missing edge above.
