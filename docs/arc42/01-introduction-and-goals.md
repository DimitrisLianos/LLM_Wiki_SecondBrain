# 1. Introduction and Goals

> **arc42, Section 1.** This section captures the project's essence: what it is, why it exists, which qualities matter and who cares. It is the entry point for any reader encountering the architecture for the first time.

---

## 1.1 Requirements Overview

`LLM Wiki` is a fully local, fully offline implementation of [Karpathy's LLM Knowledge Base pattern](https://x.com/karpathy/status/2039805659525644595), where a local Large Language Model reads raw source documents, extracts entities and concepts and writes and maintains an interlinked Markdown knowledge base without any cloud API calls.

The functional requirements are intentionally narrow:

| ID | Requirement | Rationale |
|----|-------------|-----------|
| F1 | Ingest arbitrary source documents (PDF, Markdown, plain text, XML) into a per-source summary page plus linked entity and concept pages | This is the pattern described in Karpathy's [architecture gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) |
| F2 | Accumulate knowledge across sources, the same entity mentioned in multiple documents produces one page enriched incrementally, not many orphan copies | Entity accumulation is the *compounding* property that distinguishes LLM Wiki from conventional RAG |
| F3 | Answer natural-language questions about the corpus, with citations back to specific wiki pages via `[[wikilinks]]` | Queries are the primary user interaction, retrieval quality is the dominant quality driver |
| F4 | Remain fully operational without any network connection after the initial model download | Offline-first is a hard constraint (see section 2) |
| F5 | Produce output that is byte-compatible with [Obsidian](https://obsidian.md), YAML frontmatter, `[[wikilinks]]`, folder-per-type layout | Obsidian is the intended human-facing browser; the wiki must look native inside it |
| F6 | Provide a health-check operation that detects broken links, orphan pages, frontmatter errors and thin pages | Karpathy explicitly calls out "LLM health checks" as part of the pattern |
| F7 | Deduplicate entities across ingests using a layered, academically grounded resolver, not a naive filename match | Cross-document entity linking is the *hard* problem RAG avoids by not attempting it at all |

The non-functional requirements, the ones that actually drove the architecture, are documented in [section 10 (Quality Requirements)](10-quality-requirements.md). The short version is: **zero external dependencies, zero cloud calls, zero hardcoded secrets and retrieval latency well under one second on a single MacBook.**

---

## 1.2 Quality Goals

The three dominant quality attributes, in priority order:

| Priority | Quality | Scenario | Target |
|---|---|---|---|
| 1 | **Privacy / data sovereignty** | The operator ingests private notes, personal archives, or confidential work documents. No byte of source text or derived wiki content is transmitted to any external service, ever. | 0 outbound connections to any host other than `127.0.0.1`. `scripts/` contains no `urlopen`, `requests`, or `httpx` call. |
| 2 | **Reproducibility** | A fresh clone on a fresh machine runs end-to-end with only the Python standard library plus `pdftotext` (Poppler) plus a locally-downloaded GGUF model. | `pyproject.toml` declares `dependencies = []`. No `pip install`, no virtualenv, no `requirements.txt`. |
| 3 | **Retrieval quality under zero dependencies** | Retrieval must be fast and accurate enough to beat the LLM-based page-selection approach we started with, using only what ships with CPython. | < 10 ms page selection via [SQLite FTS5](https://www.sqlite.org/fts5.html), BM25 ranking, wikilink graph expansion and [Reciprocal Rank Fusion](https://dl.acm.org/doi/10.1145/1571941.1572114) (Cormack et al. SIGIR 2009). |

These priorities inform every architectural choice in this document. When privacy conflicts with a convenience feature, privacy wins. When reproducibility conflicts with a nice library, the library is dropped. When retrieval quality conflicts with zero-dependency purity, we negotiate (see [ADR-003](09-architecture-decisions.md#adr-003--fts5--wikilink-graph--rrf-over-vector-search)).

---

## 1.3 Stakeholders

| Role | Concern | Expectation from this documentation |
|------|---------|-------------------------------------|
| **The operator** | The system must produce a useful personal knowledge base from private documents without leaking any of it. | Architecture decisions must be traceable back to observed failures in real use. |
| **The academic reader** | The project is offered as a case study in how the 2026 open-weights stack (Gemma 4, Unsloth Dynamic, TurboQuant, llama.cpp) composes into something usable on a single laptop. | Citations must resolve to real papers and real repositories. Claims must be falsifiable. |
| **The re-user / forker** | Someone wants to build the same thing for their own corpus and needs to know what works, what does not and what they will inherit as technical debt. | The retrospective ([Appendix A](appendix-a-academic-retrospective.md)) must be honest about the things that failed. |
| **The security reviewer** | The project advertises "fully local" as a security posture. This claim must survive scrutiny. | The [README "Security and privacy posture"](../../README.md#security-and-privacy-posture) section enumerates the concrete design properties (no outbound network, loopback-only binding, parameterised SQL, path-containment, list-form subprocess calls, web-UI CSP) that back the claim. |
| **The Obsidian community** | Users expect the wiki output to look native inside Obsidian, graph view, backlinks, Dataview, the usual. | The page format (YAML frontmatter + `[[wikilinks]]`) is specified in [`CLAUDE.md`](../../CLAUDE.md) and enforced by `scripts/lint.py`. |
| **Karpathy's original description** | This is a concrete implementation of a pattern he sketched. The architecture gist describes *what* to build; this document is one answer to *how*. | [Section 3 (System Context)](03-system-scope-and-context.md) explicitly maps the design back to that gist, point by point. |

---

## 1.4 Positioning Relative to Prior Work

This project is not a novel contribution to information retrieval, entity linking, or knowledge-graph construction. It is an *integration*, a working assembly of four recent developments into one end-to-end system that fits on a 2025-era MacBook:

1. **[Karpathy's LLM Wiki pattern](https://x.com/karpathy/status/2039805659525644595)** (April 2026), the organising idea. Raw data is *compiled* into interlinked Markdown by an LLM, rather than chunked-and-embedded for one-shot retrieval.
2. **[Gemma 4 26B-A4B](https://ai.google.dev/gemma/docs/core/model_card_4)** (April 2026), Google DeepMind's open-weights MoE. 25,2 B total / 3,8 B active per token across 128 experts. 27B-class quality at ≈ 4B-class inference cost. Apache 2.0.
3. **[Unsloth Dynamic 2.0 (UD)](https://unsloth.ai/blog/dynamic-v2)**, per-layer importance-weighted GGUF quantization ([docs](https://unsloth.ai/docs/basics/unsloth-dynamic-2.0-ggufs)). Same ≈ 16 GB file size as vanilla Q4_K_M, measurably better quality.
4. **[TurboQuant](https://arxiv.org/abs/2504.19874)** (Zandieh et al. ICLR 2026), online KV cache compression via rotated random projections, shipped in the [`llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant) fork. We use the asymmetric `q8_0` K + `turbo4` V configuration (see [ADR-004](09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k)).

The wiki itself, the retrieval pipeline, the entity resolver and the hardening around Gemma 4's thinking tokens are all novel contributions in the sense of "had to be built to make the above four things work together", but none of them claim research novelty. Where they stand on the shoulders of prior work, BM25, RRF, BLINK, ReFinED, TAGME, mGENRE, GraphRAG, SubgraphRAG, BEIR, that work is cited in the relevant sections.

The full academic retrospective, what worked, what failed, what succeeded but did not fit the project's purpose, lives in [Appendix A](appendix-a-academic-retrospective.md). It is the most important section of this document for anyone considering re-using the approach.
