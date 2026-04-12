#!/usr/bin/env python3
"""llm wiki — shared llm client and project config.
single source of truth for llama.cpp communication, paths, and constants.
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# --- paths. ---

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "obsidian_vault" / "raw"
WIKI_DIR = BASE_DIR / "obsidian_vault" / "wiki"
DB_PATH = BASE_DIR / "db" / "wiki_search.db"
LLAMA_URL = "http://127.0.0.1:8080"
EMBED_URL = "http://127.0.0.1:8081"

# --- constants. ---

SUBDIRS = ("sources", "entities", "concepts", "synthesis")

# 40k chars ≈ 10-12k tokens. leaves room for the prompt template
# (~500t) and output (2048t) within the 32k per-slot window.
MAX_CONTEXT_CHARS = 40000

# 50k chars ≈ 12-16k tokens. with prompt overhead + output budget,
# fits within the 32k per-slot context limit.
MAX_CHUNK_CHARS = 50000

# matches start_server.sh --parallel. if you bump one, bump the other.
# ingest.extract_chunks_parallel caps ThreadPoolExecutor workers at
# this value so we never queue behind our own server slots.
PARALLEL_SLOTS = 2


# --- shared helpers (hoisted from ingest/resolver to eliminate drift). ---

# yaml frontmatter block at file start: ---\n...\n---\n
FRONTMATTER_RE = re.compile(r"^---\n([\s\S]*?)\n---\n")

# characters that are either unsafe on a filesystem or break obsidian
# wikilink parsing.
_UNSAFE_CHARS_RE = re.compile(r'[<>:"/\\|?*\[\]\x00-\x1f]')


def safe_filename(name: str) -> str:
    """filesystem-safe and wikilink-safe name. keeps spaces for obsidian.

    strips characters that are either unsafe on filesystems or break obsidian
    wikilink parsing: colons, pipes (alias syntax), brackets (nest inside
    [[]]), slashes (path separators), quotes, wildcards, and control chars.
    also prevents path traversal via '..' sequences. trailing punctuation
    is dropped so "Culex spp." and "Culex spp" collapse into one page.

    this used to live in both ingest.py and resolver.py. hoisting here
    prevents the two copies from drifting apart under future edits.
    """
    cleaned = _UNSAFE_CHARS_RE.sub("", name)
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(".,;:!? ")
    if not cleaned:
        cleaned = "Untitled"
    return cleaned[:120].strip() if len(cleaned) > 120 else cleaned


def find_existing_page(subdir: str, name: str) -> Path:
    """case-insensitive page lookup within a wiki subdirectory.

    returns the path of the existing page if one matches (ignoring case),
    otherwise the default path the page would be created at.

    we always iterate the directory rather than trusting ``default.exists()``
    because macos apfs (and windows ntfs) is case-insensitive: a path
    constructed with lowercase 'attention.md' will resolve 'Attention.md'
    on disk but the in-memory Path keeps the wrong case, and downstream
    ``existing_path.stem`` would return the input case instead of the
    canonical on-disk case. iterating is cheap (<1000 files per subdir)
    and guarantees we return the actual filesystem entry.
    """
    safe = safe_filename(name)
    default = WIKI_DIR / subdir / f"{safe}.md"
    target = safe.lower()
    directory = WIKI_DIR / subdir
    if directory.exists():
        for entry in directory.iterdir():
            if entry.suffix == ".md" and entry.stem.lower() == target:
                return entry
    return default


# --- llm interface. ---

class ContextOverflowError(Exception):
    """prompt exceeds the server's per-slot context window."""
    pass


def llm(prompt, system="", max_tokens=4096, temperature=0.3,
        timeout=300, _retries=2):
    """post to llama.cpp /v1/chat/completions.

    server runs with --reasoning off so no thinking tokens are emitted.
    raises ContextOverflowError on HTTP 400 (prompt too large).
    retries transient 5xx errors with exponential backoff.

    args:
        prompt:      user message content.
        system:      optional system message.
        max_tokens:  max completion length.
        temperature: sampling temperature (lower = more deterministic).
        timeout:     http request timeout in seconds.
        _retries:    number of retry attempts for transient errors.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": "gemma-4", "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
        "min_p": 0.05, "repeat_penalty": 1.1,
    }).encode()

    for attempt in range(_retries + 1):
        try:
            req = urllib.request.Request(
                f"{LLAMA_URL}/v1/chat/completions",
                data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"]
            # strip any residual thinking blocks.
            content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
            content = re.sub(r"<think>[\s\S]*$", "", content).strip()
            return content
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass

            # 400 = context overflow. retrying the same payload is pointless.
            if e.code == 400:
                raise ContextOverflowError(
                    f"prompt too large ({len(prompt):,} chars). "
                    f"server: {err_body[:200]}"
                ) from None

            # transient 5xx — retry with backoff.
            if attempt < _retries:
                wait = 3 * (attempt + 1)
                print(f"    (server error {e.code}, retrying in {wait}s...)")
                time.sleep(wait)
            else:
                raise


def require_server():
    """exit if llama.cpp isn't running."""
    try:
        urllib.request.urlopen(f"{LLAMA_URL}/health", timeout=5)
    except Exception:
        print(f"\n  error: llama.cpp server not running at {LLAMA_URL}")
        print("  start it first: bash scripts/start_server.sh\n")
        sys.exit(1)


# --- embedding interface. ---
#
# posts to a second llama.cpp instance running with `--embedding` and a
# bge-m3 model. kept in llm_client so resolver.py can stay stdlib-only
# while still reusing the shared http/retry boilerplate.


class EmbeddingUnavailableError(Exception):
    """embedding server is not reachable or returned an unexpected payload."""
    pass


def embed(text, timeout=30, _retries=2):
    """get a dense vector for a piece of text. returns list[float].

    the server must be running with `--embedding` (see start_embed_server.sh).
    raises EmbeddingUnavailableError on network errors or malformed replies
    so callers can decide whether to fall back to a cheaper signal.

    llama.cpp exposes embeddings at both /v1/embeddings (openai-compatible,
    preferred) and /embedding (native). we hit /v1/embeddings because its
    response shape is stable across llama.cpp releases.
    """
    text = (text or "").strip()
    if not text:
        raise EmbeddingUnavailableError("empty input")

    body = json.dumps({
        "model": "bge-m3",
        "input": text[:8000],  # bge-m3 native max is 8192 tokens; chars is conservative.
    }).encode()

    last_err: Exception | None = None
    for attempt in range(_retries + 1):
        try:
            req = urllib.request.Request(
                f"{EMBED_URL}/v1/embeddings",
                data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except Exception as e:
            last_err = e
            if attempt < _retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise EmbeddingUnavailableError(str(e)) from e

        # openai-style: {"data": [{"embedding": [...]}, ...]}
        data = payload.get("data") or []
        if data and isinstance(data, list):
            vec = data[0].get("embedding")
            if isinstance(vec, list) and vec:
                return vec

        # native llama.cpp: {"embedding": [...]}
        vec = payload.get("embedding")
        if isinstance(vec, list) and vec:
            return vec

        last_err = EmbeddingUnavailableError(
            f"unexpected response shape: {list(payload.keys())}"
        )
        break

    raise EmbeddingUnavailableError(str(last_err or "unknown error"))


def require_embed_server():
    """exit if the bge-m3 embedding server isn't running."""
    try:
        urllib.request.urlopen(f"{EMBED_URL}/health", timeout=5)
    except Exception:
        print(f"\n  error: embedding server not running at {EMBED_URL}")
        print("  start it first: bash scripts/start_embed_server.sh\n")
        sys.exit(1)
