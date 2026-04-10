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

# --- constants. ---

SUBDIRS = ("sources", "entities", "concepts", "synthesis")

# 40k chars ≈ 10-12k tokens. leaves room for the prompt template
# (~500t) and output (2048t) within the 32k per-slot window.
MAX_CONTEXT_CHARS = 40000

# 50k chars ≈ 12-16k tokens. with prompt overhead + output budget,
# fits within the 32k per-slot context limit.
MAX_CHUNK_CHARS = 50000


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
