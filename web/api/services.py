"""thin service layer bridging fastapi routers to existing scripts.

adds the scripts/ directory to sys.path once at import time so all
routers can ``from services import ...`` without path manipulation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# --- path setup (one-time). ---

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_PROJECT_DIR = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# re-export the things routers need from existing scripts.
# each import validates that the script is reachable at startup.
from llm_client import (  # noqa: E402
    BASE_DIR,
    DB_PATH,
    EMBED_URL,
    FRONTMATTER_RE,
    LLAMA_URL,
    RAW_DIR,
    SUBDIRS,
    WIKI_DIR,
    safe_filename,
)
from search import WikiSearch  # noqa: E402

# --- server helpers. ---

START_SERVER_SH = _PROJECT_DIR / "scripts" / "start_server.sh"
START_EMBED_SH = _PROJECT_DIR / "scripts" / "start_embed_server.sh"


def check_server_health(url: str, timeout: float = 3.0) -> dict | None:
    """hit /health on a llama.cpp instance. returns json or none.

    connection refused / dns failures are the expected "server is down"
    signal, so the caller gets ``None``. we still log at debug level so
    operators have a breadcrumb when troubleshooting why a running server
    is being reported as stopped (e.g. wrong port, firewall, tls).
    """
    try:
        req = urllib.request.Request(f"{url}/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("health probe %s failed: %s", url, exc)
        return None


def check_server_slots(url: str, timeout: float = 3.0) -> list[dict]:
    """hit /slots on a llama.cpp instance. returns slot list or []."""
    try:
        req = urllib.request.Request(f"{url}/slots")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("slots probe %s failed: %s", url, exc)
        return []


def parse_server_config() -> dict:
    """read shell variables from start_server.sh."""
    config: dict[str, str | int] = {}
    if not START_SERVER_SH.exists():
        return config

    text = START_SERVER_SH.read_text()
    patterns = {
        "batch_size": r'^BATCH=(\d+)',
        "context_size": r'^CONTEXT=(\d+)',
        "parallel": r'^PARALLEL=(\d+)',
        "kv_type_k": r'^KV_TYPE_K="([^"]+)"',
        "kv_type_v": r'^KV_TYPE_V="([^"]+)"',
        "reasoning": r'^REASONING="(on|off)"',
        "threads": r'^THREADS=',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.MULTILINE)
        if m:
            val = m.group(1) if m.lastindex else ""
            config[key] = int(val) if val.isdigit() else val

    # threads uses a subshell; parse the fallback.
    threads_m = re.search(r'THREADS=.*\|\|\s*echo\s+(\d+)', text)
    if threads_m:
        config["threads"] = int(threads_m.group(1))

    return config


# allow-listed shell-side spellings. values not in these sets are rejected
# outright so nothing untrusted ever gets concatenated into start_server.sh.
_NUMERIC_KEYS = {"batch_size", "context_size", "parallel"}
_KV_TYPE_VALUES = frozenset({
    # mainline llama.cpp quantised kv cache types.
    "f16", "f32", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0",
    # turboquant fork additions.
    "turbo2", "turbo3", "turbo4",
})
_REASONING_VALUES = frozenset({"on", "off"})

# hard caps to prevent pathological values from shell-escaping risks and
# also from simply being nonsense (e.g. a 4 GB context).
_NUMERIC_BOUNDS = {
    "batch_size":   (1, 65536),
    "context_size": (512, 1048576),
    "parallel":     (1, 16),
}

_VAR_MAP = {
    "batch_size":   "BATCH",
    "context_size": "CONTEXT",
    "parallel":     "PARALLEL",
    "kv_type_k":    "KV_TYPE_K",
    "kv_type_v":    "KV_TYPE_V",
    "reasoning":    "REASONING",
}


def _validate_config_value(key: str, value: str) -> str | None:
    """return a sanitised value string, or None if invalid.

    every accepted value is checked against an explicit allow-list so the
    regex substitution below can never be coerced into shell injection.
    """
    if key in _NUMERIC_KEYS:
        # accept ints or numeric-looking strings; reject signs and whitespace.
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        lo, hi = _NUMERIC_BOUNDS[key]
        if n < lo or n > hi:
            return None
        return str(n)

    if key in ("kv_type_k", "kv_type_v"):
        v = str(value).strip()
        return v if v in _KV_TYPE_VALUES else None

    if key == "reasoning":
        v = str(value).strip()
        return v if v in _REASONING_VALUES else None

    return None


def update_server_config(key: str, value: str) -> bool:
    """update a single shell variable in start_server.sh. returns success.

    values are strictly allow-listed before being written back to the shell
    script. non-matching keys or values are rejected to prevent any form of
    injection into the start script.
    """
    if not START_SERVER_SH.exists():
        return False

    shell_var = _VAR_MAP.get(key)
    if not shell_var:
        return False

    safe_value = _validate_config_value(key, value)
    if safe_value is None:
        return False

    text = START_SERVER_SH.read_text()

    if key in _NUMERIC_KEYS:
        pattern = rf'^({shell_var})=\d+'
        replacement = rf'\g<1>={safe_value}'
    else:
        pattern = rf'^({shell_var})="[^"]+"'
        replacement = rf'\g<1>="{safe_value}"'

    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count == 0:
        return False

    START_SERVER_SH.write_text(new_text)
    return True


# --- wiki helpers. ---


def parse_frontmatter(text: str) -> dict:
    """extract frontmatter fields from a wiki page."""
    fm: dict[str, str | list[str]] = {}
    match = FRONTMATTER_RE.match(text)
    if not match:
        return fm

    block = match.group(1)
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()

        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            fm[key] = items
        else:
            fm[key] = val

    return fm


def strip_frontmatter(text: str) -> str:
    """remove yaml frontmatter, return body."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return text
    return text[match.end():].strip()


def get_all_pages() -> list[dict]:
    """list all wiki pages with metadata."""
    pages = []
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            text = f.read_text(errors="replace")
            fm = parse_frontmatter(text)
            pages.append({
                "name": f.stem,
                "subdir": subdir,
                "page_type": fm.get("type", ""),
                "tags": fm.get("tags", []),
                "created": fm.get("created", ""),
                "updated": fm.get("updated", ""),
            })
    return pages


def get_page(subdir: str, name: str) -> dict | None:
    """load a single wiki page. returns none if not found.

    both ``subdir`` and ``name`` are treated as untrusted input: we validate
    the subdir against a fixed whitelist and ensure the resolved file path
    stays inside ``WIKI_DIR/subdir``.
    """
    # subdir must be on the fixed whitelist.
    if subdir not in SUBDIRS:
        return None

    # name must not contain any path components. reject silently.
    if not name or name != Path(name).name or name in (".", ".."):
        return None

    subdir_root = (WIKI_DIR / subdir).resolve()
    if not subdir_root.exists():
        return None

    path = (subdir_root / f"{name}.md").resolve()
    if path.parent != subdir_root:
        return None

    if not path.exists():
        # case-insensitive fallback, still constrained to this subdir.
        target = name.lower()
        for f in subdir_root.iterdir():
            if f.suffix == ".md" and f.stem.lower() == target:
                path = f
                break
        else:
            return None

    text = path.read_text(errors="replace")
    fm = parse_frontmatter(text)

    # extract wikilinks for link panel.
    outbound = list(set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text)))

    return {
        "name": path.stem,
        "subdir": subdir,
        "content": text,
        "page_type": fm.get("type", ""),
        "tags": fm.get("tags", []),
        "created": fm.get("created", ""),
        "updated": fm.get("updated", ""),
        "outbound_links": sorted(outbound),
    }


def build_wiki_graph() -> dict:
    """build the full wikilink graph for visualization."""
    with WikiSearch() as ws:
        ws._ensure_index()
        conn = ws._connect()

        # all pages.
        rows = conn.execute("SELECT name, subdir FROM wiki_pages").fetchall()
        name_to_subdir = {name: subdir for name, subdir in rows}

        # all content for link extraction.
        content_rows = conn.execute("SELECT name, content FROM wiki_pages").fetchall()
        name_set = {n.lower(): n for n in name_to_subdir}

        edges = []
        inbound_count: dict[str, int] = {n: 0 for n in name_to_subdir}

        for name, content in content_rows:
            links = set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content))
            for link in links:
                resolved = name_set.get(link.lower())
                if resolved and resolved != name:
                    edges.append({"source": name, "target": resolved})
                    inbound_count[resolved] = inbound_count.get(resolved, 0) + 1

    nodes = [
        {
            "name": name,
            "subdir": subdir,
            "link_count": inbound_count.get(name, 0),
        }
        for name, subdir in name_to_subdir.items()
    ]

    return {"nodes": nodes, "edges": edges}


# --- raw file helpers. ---


def list_raw_files() -> list[dict]:
    """list files in raw/ with ingestion status."""
    if not RAW_DIR.exists():
        return []

    with WikiSearch() as ws:
        ws._ensure_index()
        files = []
        for f in sorted(RAW_DIR.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue

            source_page = ws.find_source_page(f.name)
            if source_page:
                # check if content changed via hash.
                current_hash = hashlib.sha256(
                    f.read_bytes()
                ).hexdigest()
                stored_hash = ws.read_source_hash(f.name)
                status = "changed" if (stored_hash and current_hash != stored_hash) else "ingested"
            else:
                status = "pending"

            files.append({
                "filename": f.name,
                "status": status,
                "size_bytes": f.stat().st_size,
            })

    return files
