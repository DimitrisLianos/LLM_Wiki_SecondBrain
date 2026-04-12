# 2. Architecture Constraints

> **arc42, Section 2.** Constraints are the rules that were not up for negotiation. They shape every subsequent decision: what cannot be used, what must be preserved, what environment is assumed.

---

## 2.1 Technical Constraints

| ID | Constraint | Source | Implication |
|----|-----------|--------|-------------|
| TC-1 | **Python 3.12+ standard library only** for the core pipeline | Author decision, see [ADR-001](09-architecture-decisions.md#adr-001--zero-external-python-dependencies) | No `pip`, no `venv`, no `requirements.txt`. HTTP via `urllib.request`; JSON via `json`; XML via `xml.etree.ElementTree`; search via `sqlite3` with FTS5; concurrency via `concurrent.futures`. |
| TC-2 | **All inference is local, on a single Apple Silicon Mac**, via [llama.cpp](https://github.com/ggml-org/llama.cpp) with Metal GPU | Privacy quality goal (Q1) | The model must fit in ~ 16 GB of unified memory with enough headroom for a 65 K-token context window and two parallel slots. See [section 7 (Deployment)](07-deployment-view.md) for the memory budget. |
| TC-3 | **No outbound network connections** after model download | Privacy quality goal (Q1) | Both `LLAMA_URL` and `EMBED_URL` are hard-coded to `http://127.0.0.1:8080` / `:8081` in [`scripts/llm_client.py`](../../scripts/llm_client.py) lines 19-20. There is no user-overridable URL anywhere, eliminating SSRF as an attack class. |
| TC-4 | **llama.cpp must be a specific fork**, [`TheTom/llama-cpp-turboquant`](https://github.com/TheTom/llama-cpp-turboquant), not mainline | TurboQuant KV cache compression ([Zandieh et al. 2026](https://arxiv.org/abs/2504.19874)) is not yet merged into mainline llama.cpp | `scripts/start_server.sh` expects `turbo4` as a valid `--cache-type-v` argument. A fallback to mainline is documented in [section 7](07-deployment-view.md#73-fallback-configurations) and requires reverting the V-cache type to `q8_0`. |
| TC-5 | **Gemma 4 must run with `--reasoning off`** at the server level | Observed failure, see [Appendix A, Failure F-3](appendix-a-academic-retrospective.md#f-3--thinking-tokens-consume-the-output-budget) | Without this flag, invisible thinking tokens consume the 2,048-token output budget and truncate visible content to zero. The [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4) documents that non-E2B/E4B variants skip the thinking phase entirely when the flag is set. |
| TC-6 | **KV cache must use `q8_0` K + `turbo4` V** (asymmetric), never `turbo3` for any axis | Observed quality failure on Gemma 4 Q4_K_M | Community benchmarks in the [TurboQuant+ research repo](https://github.com/TheTom/turboquant_plus) show `turbo3` symmetric causes catastrophic perplexity blow-up (PPL > 100 000) on Gemma 4. See [ADR-004](09-architecture-decisions.md#adr-004--turboquant-turbo4-v-only-q8_0-k). |
| TC-7 | **Source documents in `obsidian_vault/raw/` are immutable** | Pattern constraint from Karpathy's gist | `scripts/ingest.py` only reads from `raw/`; it never writes. The wiki workspace is `obsidian_vault/wiki/`; this is the only place code is allowed to write content. |
| TC-8 | **Page format is YAML frontmatter + `[[wikilinks]]` + Markdown body** | Obsidian compatibility | Any schema drift breaks Obsidian's graph view and backlinks panel. `scripts/lint.py` enforces frontmatter presence and wikilink integrity. |
| TC-9 | **PDF parsing delegates to `pdftotext`** (Poppler) via `subprocess.run` with the list form | No `PyPDF2`/`pdfminer` allowed under TC-1 | `subprocess.run` uses `shell=False` (the list form) with no shell interpolation, confirmed by the [security audit (Verified Safe #2)](11-risks-and-technical-debt.md#111-security-posture). |

## 2.2 Organizational Constraints

| ID | Constraint | Rationale |
|----|-----------|-----------|
| OC-1 | **Single-author, single-machine deployment** | This is a personal-knowledge-base POC. There is no multi-tenant story, no auth system, no user management, no RBAC. The threat model (see [section 11](11-risks-and-technical-debt.md)) is explicitly single-user. |
| OC-2 | **MIT license**, the code is shipped to be reused, forked and re-composed | See [`LICENSE`](../../LICENSE). |
| OC-3 | **English and Greek source corpora** are both in scope | The resolver's optional [bge-m3](https://huggingface.co/BAAI/bge-m3) embedding stage (see [section 6.4](06-runtime-view.md#64-entity-resolution-stages-05)) was chosen specifically for its cross-lingual coverage. |
| OC-4 | **No secrets in git history** (past or future) | Enforced by `.gitignore` (see [section 7.4](07-deployment-view.md#74-repository-hygiene-and-rebuildable-state)) and confirmed by the [PII audit](11-risks-and-technical-debt.md#112-pii-and-privacy-audit). |
| OC-5 | **Documentation must be self-contained**, someone reading only `docs/` should be able to rebuild understanding without having to read all 6 000+ lines of Python | This is why arc42 was chosen over a single monolithic README. |

## 2.3 Conventions

| Convention | Rule |
|-----------|------|
| Filenames for wiki pages | Keep spaces (Obsidian-friendly wikilinks): `Speculative Cascading.md` matches `[[Speculative Cascading]]`. Enforced by [`llm_client.safe_filename()`](../../scripts/llm_client.py). |
| Decimal notation | European comma notation (`82,6 %`, `3,8 B`) throughout documentation. |
| Inline citations | Inline Markdown links to the primary source (paper, repo, docs page). Full bibliography in each section's trailing references block when volume warrants one. |
| Mermaid diagrams | All architecture diagrams are expressed as Mermaid so they render both in Obsidian and on GitHub. Colored subgraphs distinguish layers. |
| Code citations | File:line references where load-bearing, e.g. `scripts/resolver.py:824`. |
| Frontmatter schema | Every wiki page and this documentation system itself, uses YAML frontmatter with at least `type`, `created`, `updated`. |

## 2.4 What Is Explicitly Out of Scope

Listing the non-goals is as important as listing the goals:

- **Multi-user collaboration.** No auth, no sharing, no per-user state. If two people want to share a knowledge base, they share the Markdown folder.
- **Cloud fallback.** There is no "call OpenAI if the local model fails" path. The local model is the only path.
- **Real-time streaming.** Ingestion is batch-oriented. `watch.sh` triggers on filesystem events, but each file still runs the full synchronous pipeline.
- **Fine-tuning.** The local model is used as-shipped. No LoRA, no QLoRA, no continued pre-training.
- **OCR of scanned PDFs.** `pdftotext` handles text PDFs; image-only PDFs produce empty extraction and abort. See [section 11.3, limitation L-7](11-risks-and-technical-debt.md#113-known-limitations).
- **A web UI.** The interface is the CLI for operations and Obsidian for browsing. There is no HTTP service layer on top of the scripts.
- **General-purpose entity linking.** The resolver targets the specific failure modes observed in real use, cross-document proper-noun forks, polysemy within a single document, not arbitrary EL at BLINK/ReFinED generality.

Anything in this list could be added later without violating the constraints above. Until then, it is out.
