#!/usr/bin/env python3
"""llm wiki — local ingestion pipeline.
processes source files from raw/ into wiki pages using gemma 4 via llama.cpp.

usage:
    python3 scripts/ingest.py <filename>        # ingest a single file.
    python3 scripts/ingest.py --all             # ingest all pending files.
    python3 scripts/ingest.py --list            # list available sources.
    python3 scripts/ingest.py --reprocess <f>   # overwrite existing pages.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from aliases import AliasRegistry, default_registry, normalize_alias_key
from llm_client import (
    BASE_DIR, FRONTMATTER_RE, MAX_CHUNK_CHARS, PARALLEL_SLOTS, RAW_DIR,
    WIKI_DIR, ContextOverflowError, find_existing_page, llm,
    require_embed_server, require_server, safe_filename,
)
from resolver import (
    _looks_context_local,
    _stem as _resolver_stem,
    apply_disambiguation_callout,
    load_calibration_cache,
    load_embed_cache,
    load_judge_cache,
    resolve_item,
    save_calibration_cache,
    save_embed_cache,
    save_judge_cache,
)
from search import WikiSearch

MAX_SMS_PER_CHUNK = 300

# --- generic-reference patterns for the canonicalization pass. ---
#
# descriptions that boil down to "the model" or "our system" carry no
# disambiguation signal and collapse into each other across sources.
# we detect them with a small regex allowlist and ask the llm to
# rewrite them using the source context. the regex is intentionally
# narrow — false positives here would burn tokens on already-good
# descriptions without improving resolution quality.

_GENERIC_REFERENCE_RE = re.compile(
    r"""^\s*(?:the|our|this|that|a|an)\s+
        (?:model|system|approach|method|framework|technique|tool|
           dataset|benchmark|paper|work|study|implementation|library|
           pipeline|architecture|algorithm)\b""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_generic_description(desc: str, name: str = "") -> bool:
    """true when a description is too generic to disambiguate an entity.

    two failure modes are caught here:

    1. determiner + generic noun ("the model", "our framework") —
       short strings that tell us nothing about the real-world thing.
    2. context-local phrasing ("mentioned in the context of X") —
       a description that merely situates the entity inside one source
       instead of identifying it. these are the primary cause of fork
       epidemics because they kill Jaccard similarity during dedup.
    """
    if not desc:
        return False
    stripped = desc.strip()
    if _looks_context_local(stripped, name):
        return True
    if len(stripped) > 200:
        return False
    return bool(_GENERIC_REFERENCE_RE.search(stripped))


# --- source parsers. ---

def detect_and_parse(path):
    """read a source file. returns (file_type, full_text, chunks)."""
    suffix = path.suffix.lower()

    if suffix == ".xml":
        tree = ET.parse(path)
        root = tree.getroot()
        if root.findall("sms"):
            return "sms", *_parse_sms_xml(root)

    if suffix == ".pdf":
        text = _parse_pdf(path)
        return "pdf", text, _chunk_text(text)

    text = path.read_text(encoding="utf-8", errors="replace")
    return "text", text, _chunk_text(text)


def _parse_pdf(path):
    """extract text via pdftotext (poppler). falls back to raw string extraction."""
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except FileNotFoundError:
        print("  error: pdftotext not found. install: brew install poppler")
        sys.exit(1)

    # fallback for scanned pdfs.
    raw = path.read_bytes()
    parts = [
        m.group(1).decode("utf-8", errors="ignore")
        for m in re.finditer(rb"\(([^)]+)\)", raw)
    ]
    if parts:
        return " ".join(parts)

    print(f"  warning: no text from {path.name}. may be image-only pdf.")
    return ""


def _parse_sms_xml(root):
    """convert sms xml into timestamped lines, pre-chunked."""
    all_sms = root.findall("sms")
    lines, chunks = [], []

    for i, sms in enumerate(all_sms):
        ts = int(sms.get("date", "0"))
        dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
        name = sms.get("contact_name", "Unknown")
        body = sms.get("body", "").strip()
        direction = "SENT" if sms.get("type") == "2" else "RECV"
        lines.append(f"[{dt}] {direction} {name}: {body}")

        if (i + 1) % MAX_SMS_PER_CHUNK == 0:
            chunks.append("\n".join(lines[-MAX_SMS_PER_CHUNK:]))

    remainder = len(all_sms) % MAX_SMS_PER_CHUNK
    if remainder:
        chunks.append("\n".join(lines[-remainder:]))

    return "\n".join(lines), chunks


def _chunk_text(text):
    """split at paragraph boundaries. most files fit in one chunk."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]

    chunks, current, current_len = [], [], 0
    for para in text.split("\n\n"):
        if current_len + len(para) > MAX_CHUNK_CHARS and current:
            chunks.append("\n\n".join(current))
            current, current_len = [para], len(para)
        else:
            current.append(para)
            current_len += len(para) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks


# --- source date extraction. ---
#
# the historical-drift tiebreaker needs to know when a source was
# authored, not when it was ingested. we look for signals in this
# order of reliability:
#   1. a YYYY-MM-DD or YYYY prefix in the filename.
#   2. pdf metadata (CreationDate) via pdfinfo.
#   3. a YYYY in the first 2000 chars of text (publication year in
#      the header of most papers).
#   4. filesystem mtime as a last-resort fallback.

_FILENAME_DATE_RE = re.compile(r"(?:^|[^\d])(\d{4})(?:[-_](\d{2}))?(?:[-_](\d{2}))?")
# accept any 19xx/20xx year; the old upper bound (2039) was a hardcoded
# future cutoff that would have quietly broken in 2040. we still reject
# 18xx and earlier so stray "1850" in a historical citation can't become
# the source_date. any match past "today" is later filtered by callers.
_TEXT_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _extract_source_date(path, file_type, full_text):
    """best-effort iso date (YYYY-MM-DD or YYYY) for a source file.

    returns an empty string when nothing plausible is found. the
    resolver only needs a year for the age-gap tiebreaker, so partial
    dates are acceptable.
    """
    # 1. filename prefix: common for academic papers and notes.
    name_match = _FILENAME_DATE_RE.match(path.name)
    if name_match:
        year = int(name_match.group(1))
        if 1980 <= year <= 2100:
            parts = [name_match.group(1)]
            if name_match.group(2):
                parts.append(name_match.group(2))
                if name_match.group(3):
                    parts.append(name_match.group(3))
            return "-".join(parts)

    # 2. pdf metadata.
    if file_type == "pdf":
        try:
            result = subprocess.run(
                ["pdfinfo", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("CreationDate:"):
                        year_match = _TEXT_YEAR_RE.search(line)
                        if year_match:
                            return year_match.group(1)
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    # 3. first 2000 chars of text — catches publication years in paper headers.
    if full_text:
        head = full_text[:2000]
        year_match = _TEXT_YEAR_RE.search(head)
        if year_match:
            return year_match.group(1)

    # 4. filesystem mtime as last resort.
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return mtime.strftime("%Y-%m-%d")
    except OSError:
        return ""


# --- second-pass canonicalization (generic -> specific). ---

_CANONICALIZE_PROMPT = """You are fixing a generic entity description extracted from a document.

Source title: "{source_title}"
Entity name: "{name}"
Entity type: {entity_type}
Current generic description: "{generic_desc}"

Source context (first ~3000 chars):
\"\"\"{context}\"\"\"

Rewrite the description in ONE concrete sentence that identifies what {name} actually IS, based on the source. Do NOT use phrases like "the model", "our system", "this approach". Name the real thing. If the source truly does not specify, reply with exactly the word "unknown".

Reply with only the rewritten sentence — no prefix, no quotes, no explanation."""


def _canonicalize_one(item, context, source_title):
    """llm call for a single generic item. returns the rewritten
    description on success, or None on any failure / reject path.

    isolated so _canonicalize_descriptions can dispatch it through a
    ThreadPoolExecutor. kept a plain function (not a closure) so the
    futures can be submitted by key, not by capturing loop state.
    """
    try:
        reply = llm(
            _CANONICALIZE_PROMPT.format(
                source_title=source_title,
                name=item.get("name", "?"),
                entity_type=item.get("type", "unknown"),
                generic_desc=item.get("description", ""),
                context=context,
            ),
            max_tokens=120,
            temperature=0.1,
            timeout=60,
        )
    except ContextOverflowError:
        return None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return ("network", f"{type(e).__name__}: {e}")
    except (json.JSONDecodeError, KeyError) as e:
        return ("parse", f"{type(e).__name__}: {e}")

    new_desc = (reply or "").strip().strip('"').strip("'")
    if not new_desc or new_desc.lower().startswith("unknown"):
        return None
    # sanity bounds: drop runaway replies that drifted off-task.
    if len(new_desc) < 10 or len(new_desc) > 400:
        return None
    return new_desc


def _canonicalize_descriptions(items, source_text, source_title, quiet=False):
    """rewrite generic descriptions ("the model") into concrete ones.

    iterates every extracted item; for those flagged as generic, calls
    the llm with a tight prompt and the first ~3000 chars of source
    context. mutates items in place. on failure keeps the original
    description so we never lose information.

    calls are dispatched through a thread pool sized to PARALLEL_SLOTS
    so we keep all llama.cpp slots warm. the individual calls are
    independent (same context, different item) so pool parallelism is
    a strict win — wall-clock drops from N * latency to
    ceil(N/PARALLEL_SLOTS) * latency.
    """
    if not items or not source_text:
        return
    context = source_text[:3000]

    generic_items = [
        item for item in items
        if _is_generic_description(item.get("description", ""), item.get("name", ""))
    ]
    if not generic_items:
        return

    rewritten = 0
    with ThreadPoolExecutor(max_workers=PARALLEL_SLOTS) as pool:
        futures = {
            pool.submit(_canonicalize_one, item, context, source_title): item
            for item in generic_items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                result = future.result()
            except Exception as e:
                # worker should handle its own exceptions. this is
                # belt-and-braces — log class and keep original desc.
                if not quiet:
                    print(
                        f"    (canonicalize worker crashed for "
                        f"{item.get('name','?')}: {type(e).__name__}: {e})"
                    )
                continue

            # tuple results are error markers; None means reject; str is success.
            if result is None:
                continue
            if isinstance(result, tuple):
                kind, detail = result
                if not quiet:
                    print(
                        f"    (canonicalize {kind} error for "
                        f"{item.get('name','?')}: {detail})"
                    )
                continue
            item["description"] = result
            rewritten += 1

    if rewritten and not quiet:
        print(f"    canonicalized {rewritten} generic description(s)")


# --- canonical alias registry normalization (stage 0 preprocessing). ---
#
# runs AFTER _canonicalize_descriptions but BEFORE _resolve_items so the
# resolver only sees already-canonicalized proper nouns. this layer is
# the industry-standard "gazetteer" pattern (spaCy EntityRuler, Stanford
# CoreNLP RegexNER, Apple/Google Knowledge Graph canonical alias tables)
# grounded in the entity-linking literature (BLINK, ReFinED, TAGME).
#
# why it matters: the fork epidemic we saw — ChatGPT (tool, 1 src) vs
# ChatGPT (model, 4 srcs) — is exactly the failure mode academic EL
# research calls "surface form ambiguity with context-local priors".
# fixing it requires an external anchor (the gazetteer) rather than
# relying on stage 1-5 dedup similarity. stage 0 in resolver.py applies
# the anchor at resolve time; this function applies it at extraction
# time so the same canonical form is used throughout the pipeline
# (page writes, source page citations, log entries, index updates).


def _looks_thin_description(desc: str) -> bool:
    """true when a description is too short or too generic to be trusted.

    used only by _normalize_via_aliases to decide whether to overwrite
    the incoming description with the curated one from the registry.
    deliberately more aggressive than _is_generic_description — we want
    the curated wikidata-style blurb unless the extracted one is clearly
    richer.
    """
    if not desc:
        return True
    stripped = desc.strip()
    if len(stripped) < 30:
        return True
    return False


def _normalize_via_aliases(
    items: list[dict],
    registry: AliasRegistry,
    subdir: str,
    quiet: bool = False,
) -> None:
    """apply the canonical alias registry to a batch of extracted items.

    for every item whose name hits the registry with a compatible type
    and a matching subdir:
      - rewrite item['name'] to the canonical form
      - normalize item['type'] to the canonical type
      - replace item['description'] with the curated one when the
        incoming description is thin or context-local

    then dedupe items that collapse to the same canonical key so the
    resolver does not see within-source duplicates. when two items
    merge, keep the richer description and union the aliases/evidence.

    mutates `items` in place (both the list membership and the dicts).
    """
    if not items or registry is None or len(registry) == 0:
        return

    anchored = 0
    merged = 0
    seen: dict[str, dict] = {}
    kept: list[dict] = []

    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            kept.append(item)
            continue

        type_hint = (item.get("type") or "").strip().lower()
        entry = registry.lookup(name, type_hint=type_hint)

        # subdir guard: never cross entities/concepts even on a hit.
        # a word like "Transformer" is a concept in our taxonomy even
        # though a newer source might tag it as a tool/model.
        if entry is not None and entry.subdir and entry.subdir != subdir:
            entry = None

        if entry is not None:
            # 1. canonical name wins.
            if item.get("name") != entry.canonical_name:
                item["name"] = entry.canonical_name
            # 2. canonical type wins when we have one.
            if entry.canonical_type:
                item["type"] = entry.canonical_type
            # 3. curated description wins when the incoming is weak.
            incoming_desc = (item.get("description") or "").strip()
            if entry.description and (
                _looks_thin_description(incoming_desc)
                or _looks_context_local(incoming_desc, entry.canonical_name)
            ):
                item["description"] = entry.description
            anchored += 1

        # dedupe by canonical key. a single chunk can emit both "ChatGPT"
        # and "ChatGPT (model)"; after normalization both collapse to
        # the same key and must not be written as two separate items.
        key = normalize_alias_key(item.get("name", ""))
        if not key:
            kept.append(item)
            continue

        prior = seen.get(key)
        if prior is None:
            seen[key] = item
            kept.append(item)
            continue

        # merge: prefer the longer, non-context-local description.
        prior_desc = (prior.get("description") or "").strip()
        new_desc = (item.get("description") or "").strip()
        prior_weak = _looks_thin_description(prior_desc) or _looks_context_local(
            prior_desc, prior.get("name", "")
        )
        new_weak = _looks_thin_description(new_desc) or _looks_context_local(
            new_desc, item.get("name", "")
        )
        if prior_weak and not new_weak:
            prior["description"] = new_desc
        elif not prior_weak and not new_weak and len(new_desc) > len(prior_desc):
            prior["description"] = new_desc
        # type: keep the more specific one if both are set and differ.
        if not prior.get("type") and item.get("type"):
            prior["type"] = item["type"]
        merged += 1

    # write back the deduped list.
    if merged:
        items[:] = kept

    if not quiet and (anchored or merged):
        parts = []
        if anchored:
            parts.append(f"{anchored} alias-anchored")
        if merged:
            parts.append(f"{merged} within-source duplicate(s) merged")
        print(f"    registry: {', '.join(parts)}")


# --- extraction. ---

_EXTRACTION_PROMPT = """Analyze this document (section {idx}/{total}). Return ONLY valid JSON:

{{"title":"Full paper/document title (not filename or arxiv id)",
"summary":"One detailed paragraph: motivation, methodology, key findings, implications.",
"key_claims":["Specific finding with metrics","Another finding with numbers"],
"entities":[{{"name":"Full Name","type":"person|organization|tool|dataset|model|benchmark","description":"One sentence: who/what and their role."}}],
"concepts":[{{"name":"Name","type":"method|theory|framework|pattern|metric|technique|algorithm|architecture","description":"One sentence: what it is and why it matters."}}]}}

IMPORTANT: Be exhaustive. Extract EVERY person, organization, tool, dataset, model, benchmark, method, algorithm, technique, metric, and framework mentioned. Aim for 15+ entities and 15+ concepts. Keep each description to ONE concise sentence. Key claims MUST cite specific numbers or results.

Text:
\"\"\"{text}\"\"\""""


def _empty_extraction():
    """blank extraction result for skipped or failed chunks."""
    return {"summary": "", "entities": [], "concepts": [], "key_claims": []}


def extract_chunk(text, idx, total, _depth=0):
    """extract structured info from a text chunk.

    on context overflow: splits in half and merges. max 2 levels deep.
    """
    try:
        raw = llm(_EXTRACTION_PROMPT.format(idx=idx, total=total, text=text),
                  max_tokens=2048, temperature=0.2, timeout=600)
        return _parse_json(raw)
    except ContextOverflowError:
        if _depth >= 2:
            print(f"      chunk {idx} still too large after splitting "
                  f"({len(text):,} chars). skipping.")
            return _empty_extraction()

        # split at nearest paragraph boundary to midpoint.
        mid = len(text) // 2
        split_at = text.rfind("\n\n", 0, mid + 2000)
        if split_at < mid // 2:
            split_at = mid

        half_a, half_b = text[:split_at].rstrip(), text[split_at:].lstrip()
        print(f"      chunk {idx} too large ({len(text):,} chars). "
              f"splitting → {len(half_a):,} + {len(half_b):,} chars")

        return merge_extractions([
            extract_chunk(half_a, idx, total, _depth + 1),
            extract_chunk(half_b, idx, total, _depth + 1),
        ])


def extract_chunks_parallel(chunks, quiet=False):
    """extract from all chunks concurrently via server's --parallel slots."""
    total = len(chunks)
    if total == 1:
        if not quiet:
            print("    [1/1] extracting... ", end="", flush=True)
        result = extract_chunk(chunks[0], 1, 1)
        if not quiet:
            ne, nc = len(result.get("entities", [])), len(result.get("concepts", []))
            print(f"{ne} entities, {nc} concepts")
        return [result]

    results = [None] * total
    if not quiet:
        print(f"    extracting {total} chunks in parallel...", flush=True)

    with ThreadPoolExecutor(max_workers=min(total, PARALLEL_SLOTS)) as pool:
        futures = {
            pool.submit(extract_chunk, chunk, i + 1, total): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results[idx] = result
                if not quiet:
                    ne = len(result.get("entities", []))
                    nc = len(result.get("concepts", []))
                    print(f"      chunk {idx + 1}/{total} done — "
                          f"{ne} entities, {nc} concepts")
            except Exception as e:
                # broad catch is intentional: a single failed chunk
                # must not abort the rest of the parallel batch.
                # logging the class lets us distinguish network errors
                # from parse errors from llm context overflows.
                if not quiet:
                    print(
                        f"      chunk {idx + 1}/{total} failed: "
                        f"{type(e).__name__}: {e}"
                    )
                results[idx] = _empty_extraction()

    return results


def generate_summary(title, chunk_summaries, entities, concepts):
    """synthesize a unified overview from chunk extractions."""
    ent_names = ", ".join(e["name"] for e in entities[:20])
    con_names = ", ".join(c["name"] for c in concepts[:15])

    def _prompt(sums):
        text = "\n\n".join(f"Section {i+1}: {s}" for i, s in enumerate(sums) if s)
        return (
            f'Write a comprehensive 3-4 paragraph overview of "{title}".\n\n'
            f"Section summaries:\n{text}\n\n"
            f"Key entities: {ent_names}\nKey concepts: {con_names}\n\n"
            "Synthesize the sections into a cohesive narrative. Cover the motivation, "
            "methodology, key findings, and implications. Reference entities and concepts "
            "by name. Write clear, detailed prose — no bullet points or headers."
        )

    try:
        return llm(_prompt(chunk_summaries), max_tokens=2048, temperature=0.4)
    except ContextOverflowError:
        # too many chunk summaries. truncate to first half and retry.
        print("      (summary too large, truncating input...)")
        return llm(_prompt(chunk_summaries[:len(chunk_summaries) // 2]),
                    max_tokens=2048, temperature=0.4)


# --- json parsing. ---

def _parse_json(text):
    """handle markdown fences, truncation, and other llm quirks."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    # full parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # outermost json object.
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # truncation recovery: close open brackets/braces.
    start = text.find("{")
    if start >= 0:
        partial = text[start:]
        partial = re.sub(r',\s*"[^"]*$', "", partial)       # trailing key.
        partial = re.sub(r',\s*$', "", partial)              # trailing comma.
        partial = re.sub(r':\s*"[^"]*$', ': ""', partial)   # truncated value.
        opens = partial.count("[") - partial.count("]")
        partial += "]" * max(opens, 0)
        opens = partial.count("{") - partial.count("}")
        partial += "}" * max(opens, 0)
        try:
            result = json.loads(partial)
            print("      (recovered truncated json)")
            return result
        except json.JSONDecodeError:
            pass

    return _empty_extraction()


_PAREN_SUFFIX_RE = re.compile(r"\s*\([^)]+\)\s*$")


def _strip_trailing_parens(name: str) -> str:
    """strip every trailing ``(...)`` group, not just the last one.

    the llm sometimes stacks qualifiers: ``Indoor residual spraying (IRS)
    (method)``. a single ``re.sub`` only strips ``(method)`` and leaves
    ``(IRS)`` behind, so the dedup key still disagrees with the cleaner
    ``Indoor residual spraying (IRS)`` form. looping until no paren
    remains fixes both nested and stacked variants.
    """
    prev = None
    while prev != name:
        prev = name
        name = _PAREN_SUFFIX_RE.sub("", name).rstrip()
    return name


def _dedup_key(name: str) -> str:
    """canonical dedup key for within-batch collation.

    collapses four kinds of llm surface-form noise:

    - trailing parentheticals: ``URN`` vs ``URN (Uniform Resource Name)``
      (strip every trailing ``(...)`` group, including stacked ones like
      ``(IRS) (method)``).
    - trailing punctuation:    ``Culex spp.`` vs ``Culex spp``
      (strip via rstrip).
    - case variation:          ``Open Weights`` vs ``open weights``
      (lowercase).
    - singular/plural / verb forms: ``Label Embedding`` vs ``Label
      Embeddings`` (apply the resolver's stemmer to each word and join).

    the stemmer is applied twice because ``embeddings -> embedding ->
    embed`` only collapses after the second pass ('s' strips before 'ing'
    in the suffix list and the first pass would otherwise undershoot).

    does not collapse real polysems: ``Supervised Learning`` vs
    ``Unsupervised Learning`` keep distinct keys because the prefix
    differs, and ``Python`` snake vs ``Python`` language would be caught
    later by the resolver's stage 2 type check (they have different
    types and disjoint descriptions).
    """
    base = _strip_trailing_parens(name).strip()
    base = base.rstrip(".,;:!? ").lower()
    if not base:
        return name.lower()
    # double-stem each word: "embeddings" -> "embedding" -> "embed".
    stems = [_resolver_stem(_resolver_stem(w)) for w in base.split()]
    return " ".join(stems)


# --- cross-bucket routing. ---
#
# the llm extraction prompt lists six "entity" types and eight "concept"
# types. the model is usually correct but it occasionally emits the same
# thing into *both* buckets in a single chunk (e.g. "Cosine Similarity"
# shows up as a metric under concepts AND as a metric under entities).
# within-bucket dedup can't catch these — we need a second pass that
# collapses the collision and routes the winner to the subdirectory
# that matches its declared type.

_CONCEPT_TYPES = frozenset({
    "method", "theory", "framework", "pattern", "metric",
    "technique", "algorithm", "architecture",
})
_ENTITY_TYPES = frozenset({
    "person", "organization", "tool", "dataset", "model", "benchmark",
})


def _type_home(type_field: str) -> str:
    """return ``'concepts'`` or ``'entities'`` for a declared item type.

    ambiguous or missing types default to ``'entities'`` only when we
    have no other signal — callers provide both sides' types to disambiguate.
    """
    t = (type_field or "").strip().lower()
    if t in _CONCEPT_TYPES:
        return "concepts"
    if t in _ENTITY_TYPES:
        return "entities"
    return ""


def _cross_bucket_dedup(merged: dict, quiet: bool = False) -> None:
    """collapse items that appear in both entities and concepts buckets.

    for each (dedup_key) collision:
      1. look at both sides' declared ``type`` fields.
      2. route the collision to the bucket that matches the type
         (concepts for method/metric/technique/…, entities for
         person/organization/tool/…).
      3. merge the richer description and shorter surface name into
         the survivor.
      4. drop the loser from its bucket.

    runs after within-bucket dedup. mutates ``merged`` in place.
    """
    ents = merged.get("entities", [])
    cons = merged.get("concepts", [])
    if not ents or not cons:
        return

    cons_index = {_dedup_key(c["name"]): i for i, c in enumerate(cons) if c.get("name")}
    kept_ents: list = []
    dropped_con_indices: set[int] = set()
    collapsed = 0

    for ent in ents:
        name = ent.get("name", "")
        if not name:
            kept_ents.append(ent)
            continue
        key = _dedup_key(name)
        con_idx = cons_index.get(key)
        if con_idx is None or con_idx in dropped_con_indices:
            kept_ents.append(ent)
            continue

        con = cons[con_idx]
        ent_type = ent.get("type", "")
        con_type = con.get("type", "")
        ent_home = _type_home(ent_type)
        con_home = _type_home(con_type)

        # winner_bucket = which subdir keeps the merged item.
        if ent_home == "entities" and con_home != "entities":
            winner_bucket = "entities"
        elif con_home == "concepts" and ent_home != "concepts":
            winner_bucket = "concepts"
        elif ent_home == "concepts" and con_home == "":
            winner_bucket = "concepts"
        elif con_home == "entities" and ent_home == "":
            winner_bucket = "entities"
        else:
            # both agree, both disagree, or truly ambiguous. default
            # to concepts since methods/metrics are the common case.
            winner_bucket = "concepts"

        # merge description (prefer the longer one) and surface name
        # (prefer the shorter one).
        ent_desc = ent.get("description", "")
        con_desc = con.get("description", "")
        richer_desc = ent_desc if len(ent_desc) > len(con_desc) else con_desc
        shorter_name = ent["name"] if len(ent["name"]) < len(con["name"]) else con["name"]

        if winner_bucket == "entities":
            ent["description"] = richer_desc
            ent["name"] = shorter_name
            kept_ents.append(ent)
            dropped_con_indices.add(con_idx)
        else:
            con["description"] = richer_desc
            con["name"] = shorter_name
            # ent is dropped by not appending to kept_ents.

        collapsed += 1

    merged["entities"] = kept_ents
    merged["concepts"] = [c for i, c in enumerate(cons) if i not in dropped_con_indices]

    if collapsed and not quiet:
        print(f"    cross-bucket dedup: collapsed {collapsed} collision(s)")


def _dedup_items(items, target):
    """keep the richest description per canonical name within a batch.

    dedup key is the stem-normalized base name (see ``_dedup_key``) so
    acronym expansions, punctuation variants, and simple plurals all
    collapse. when a collision occurs, the item with the longer
    description wins — and if the loser has a shorter surface name, the
    winner adopts that shorter name so the canonical page uses the
    clean form rather than the verbose one.
    """
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue
        key = _dedup_key(name)
        existing = target.get(key)
        if existing is None:
            target[key] = item
            continue

        # prefer the richer description. on tie, prefer the shorter name.
        new_desc_len = len(item.get("description", ""))
        old_desc_len = len(existing.get("description", ""))
        if new_desc_len > old_desc_len:
            # rich new description wins. keep the shorter surface form
            # so "URN" beats "URN (Uniform Resource Name)".
            if len(name) > len(existing["name"]):
                item["name"] = existing["name"]
            target[key] = item
        elif len(name) < len(existing["name"]):
            # same/shorter description but shorter surface name -> just
            # rename in place. preserves the richer description.
            existing["name"] = name


def merge_extractions(extractions):
    """deduplicate entities and concepts across chunks. keep richest descriptions."""
    entities, concepts, claims, summaries, title = {}, {}, [], [], ""

    for ext in extractions:
        if not title and ext.get("title"):
            title = ext["title"]
        summaries.append(ext.get("summary", ""))
        claims.extend(ext.get("key_claims", ext.get("key_facts", [])))
        _dedup_items(ext.get("entities", []), entities)
        _dedup_items(ext.get("concepts", []), concepts)

    return {
        "title": title,
        "summaries": [s for s in summaries if s],
        "entities": list(entities.values()),
        "concepts": list(concepts.values()),
        "key_claims": list(dict.fromkeys(claims)),
    }


# --- wiki page generators. ---

def _today():
    return date.today().isoformat()


# ``safe_filename`` now lives in llm_client so ingest and resolver
# share a single implementation.


# --- image support. ---
#
# images can be uploaded to raw/ alongside papers and notes. the text-only
# llm backend cannot extract structured facts from a jpeg, so we create a
# thin source page that registers the image and embeds it via obsidian's
# attachment syntax. the page is fully lint-clean (required frontmatter,
# wikilink-safe) and participates in wiki navigation like any other source.
#
# if a vision-capable llm is wired up later, the extraction path can be
# switched on here without changing the public interface.

_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".heic", ".heif", ".bmp", ".tiff", ".tif", ".svg",
})


def _ingest_image(source_path: Path, overwrite: bool = False,
                  quiet: bool = False) -> bool:
    """register an image as a source page without llm extraction.

    creates ``wiki/sources/<Title>.md`` with:
      - type/source/tags frontmatter (tags: [image])
      - source_hash for re-upload idempotency
      - an ``![[filename]]`` obsidian embed so the image renders in preview
      - a notes stub the user can extend manually

    idempotent: re-running on the same unchanged image is a no-op.
    """
    filename = source_path.name

    # idempotency: unchanged re-upload is a no-op.
    current_hash = _compute_file_hash(source_path)
    existing = _find_source_page_for(filename)
    if existing is not None and not overwrite:
        existing_hash = _read_source_hash(existing)
        if existing_hash and existing_hash == current_hash:
            if not quiet:
                print(f"\n  skip: raw/{filename} already registered "
                      f"(image unchanged).")
            return True

    # derive a human-readable title from the filename stem.
    stem = source_path.stem.replace("_", " ").replace("-", " ").strip()
    title = safe_filename(stem.title() or "Image")

    source_date = _extract_source_date(source_path, "image", "")

    lines = [
        "---",
        "type: source",
        "tags: [image]",
        f"sources: [raw/{filename}]",
    ]
    if source_date:
        lines.append(f"source_date: {source_date}")
    if current_hash:
        lines.append(f"source_hash: {current_hash}")
    lines += [
        f"created: {_today()}",
        f"updated: {_today()}",
        "---",
        "",
        f"# {title}",
        "",
        f"**Source:** `raw/{filename}`",
        f"**Date ingested:** {_today()}",
        "**Type:** image",
    ]
    if source_date:
        lines.append(f"**Source date:** {source_date}")
    lines += [
        "",
        "## Preview",
        "",
        f"![[{filename}]]",
        "",
        "## Notes",
        "",
        "_Image registered as a source without automatic extraction._",
        "_Add observations here, or embed this image elsewhere with_ "
        f"`![[{filename}]]`.",
        "",
    ]
    content = "\n".join(lines)

    target_path = WIKI_DIR / "sources" / f"{title}.md"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content)

    if not quiet:
        print(f"\n{'=' * 60}")
        print(f"  INGEST (image): {filename}")
        print(f"{'=' * 60}")
        print(f"  registered: wiki/sources/{target_path.name}")

    # index entry.
    index_path = WIKI_DIR / "index.md"
    if index_path.exists():
        index_text = index_path.read_text()
        if f"[[{title}]]" not in index_text:
            new_lines: list[str] = []
            inserted = False
            for line in index_text.split("\n"):
                new_lines.append(line)
                if not inserted and line.strip() == "## Sources":
                    new_lines.append(f"- [[{title}]] — image source")
                    inserted = True
            if inserted:
                index_path.write_text("\n".join(new_lines))

    # log entry.
    log_path = WIKI_DIR / "log.md"
    if log_path.exists():
        entry = (
            f"\n## [{_today()}] ingest (image) | {title}\n\n"
            f"Registered image `raw/{filename}` as a source page. "
            f"No automatic extraction performed (text-only llm backend).\n"
        )
        log_path.write_text(log_path.read_text() + entry)

    return True


# --- content-hash idempotency for raw files. ---
#
# a naive re-ingest of an unchanged raw file would burn minutes of llm
# time re-extracting identical entities. we write a sha-256 of the raw
# bytes into the source page frontmatter on first ingest and compare on
# subsequent runs. equal hash -> skip. different hash -> update flow.
#
# the hash is cheap (streamed 64k chunks) and lets us also catch
# silent edits to a raw file that's already been ingested.

_SOURCE_HASH_RE = re.compile(r"^source_hash:\s*([a-f0-9]{64})\s*$", re.MULTILINE)


def _compute_file_hash(path: Path) -> str:
    """sha-256 hex digest of a raw file's bytes. streams 64k at a time."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _find_source_page_for(filename: str) -> Path | None:
    """locate the source page, if any, written for this raw filename.

    fast path: query the sqlite ``source_files`` reverse index built by
    search.WikiSearch.build_index. the index maps raw filename ->
    source-page stem, so a lookup is one indexed SELECT instead of a
    linear scan across every source page.

    slow path: if the index is missing (first ingest before the db
    exists, or a schema mismatch on older dbs), fall back to the
    original linear scan over ``wiki/sources/*.md``. the fallback also
    keeps unit tests that run without a db happy.

    returns ``None`` if no source page exists for this filename.
    """
    sources_dir = WIKI_DIR / "sources"
    if not sources_dir.exists():
        return None

    # fast path — sqlite reverse index.
    try:
        with WikiSearch() as ws:
            stem = ws.find_source_page(filename)
    except sqlite3.OperationalError:
        # index missing or schema mismatch — fall through to linear scan.
        stem = None
    if stem:
        candidate = sources_dir / f"{stem}.md"
        if candidate.exists():
            return candidate
        # stem in index but file missing — index is stale. fall through.

    # slow path — linear scan. O(N) but reliable bootstrap path.
    needle = f"`raw/{filename}`"
    for f in sources_dir.glob("*.md"):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if needle in text:
            return f
    return None


def _read_source_hash(source_page: Path) -> str:
    """return the source_hash frontmatter value, or empty string."""
    try:
        text = source_page.read_text()
    except OSError:
        return ""
    match = _SOURCE_HASH_RE.search(text)
    return match.group(1) if match else ""


# abbreviations that must not terminate a sentence. the naive
# split("."")[0] strategy amputated descriptions on the first "e.g." it
# found ("a method, e.g" instead of "a method, e.g. greedy decoding").
# we normalize these before splitting.
_SENTENCE_ABBREVIATIONS = (
    "e.g.", "i.e.", "etc.", "vs.", "cf.", "al.",  # latin shortforms
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Jr.", "Sr.", "St.",  # titles
    "U.S.", "U.K.", "E.U.", "U.N.",  # country shortforms
    "Fig.", "Eq.", "No.", "vol.", "ch.", "p.", "pp.",  # citation shortforms
)

# cheap regex-free end-of-sentence detector. splits on the first "."
# "?" or "!" followed by whitespace that isn't an abbreviation.
def _first_sentence(text: str) -> str:
    """return the first sentence of ``text`` (trailing terminator kept).

    handles common abbreviations by masking their dots before splitting
    and restoring them after. returns the original text if no sentence
    terminator is found. safe on empty input.
    """
    if not text:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""

    # mask abbreviation dots with a sentinel the scanner won't treat
    # as a terminator, then restore after we find the split point.
    masked = stripped
    for abbrev in _SENTENCE_ABBREVIATIONS:
        masked = masked.replace(abbrev, abbrev.replace(".", "\x00"))

    # first occurrence of [.!?] followed by whitespace-or-end.
    terminators = (".", "!", "?")
    best = -1
    for i, ch in enumerate(masked):
        if ch in terminators:
            if i == len(masked) - 1 or masked[i + 1].isspace():
                best = i
                break
    if best == -1:
        return stripped

    # unmask and return.
    sentence = masked[: best + 1].replace("\x00", ".")
    return sentence


def make_source_page(title, filename, summary, entities, concepts,
                     key_claims, tags, source_date="", source_hash=""):
    """create a source summary page."""
    lines = [
        "---", "type: source",
        f"tags: [{', '.join(tags)}]",
        f"sources: [raw/{filename}]",
    ]
    if source_date:
        lines.append(f"source_date: {source_date}")
    if source_hash:
        lines.append(f"source_hash: {source_hash}")
    lines += [
        f"created: {_today()}", f"updated: {_today()}",
        "---", "",
        f"# {title}", "",
        f"**Source:** `raw/{filename}`",
        f"**Date ingested:** {_today()}",
        f"**Type:** {tags[0] if tags else 'article'}",
    ]
    if source_date:
        lines.append(f"**Source date:** {source_date}")
    lines += ["", "## Summary", "", summary, ""]

    if key_claims:
        lines += ["## Key Claims", ""] + [f"- {c}" for c in key_claims] + [""]

    for heading, items in [("## Entities Mentioned", entities),
                           ("## Concepts Covered", concepts)]:
        if items:
            lines += [heading, ""]
            for item in items:
                desc = item.get("description", "")
                desc = _first_sentence(desc) if desc else ""
                lines.append(f"- [[{item['name']}]] — {desc}")
            lines.append("")

    return "\n".join(lines)


def write_page(subdir, name, content, overwrite=False):
    """write a wiki page. returns true if created."""
    path = WIKI_DIR / subdir / f"{safe_filename(name)}.md"
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


# ``_find_existing_page`` was identical to llm_client.find_existing_page.
# the local duplicate has been removed; callers use the hoisted version.


def _append_source_date(content, source_date):
    """add source_date to the source_dates list in frontmatter.

    creates the field if missing. deduplicates so re-ingests don't
    stack identical dates. returns the modified content.
    """
    if not source_date:
        return content

    def _replace(match):
        body = match.group(1)
        existing = re.search(r"^source_dates:\s*\[([^\]]*)\]\s*$", body, re.MULTILINE)
        if existing:
            items = [d.strip() for d in existing.group(1).split(",") if d.strip()]
            if source_date in items:
                return match.group(0)
            items.append(source_date)
            new_line = f"source_dates: [{', '.join(items)}]"
            new_body = body[:existing.start()] + new_line + body[existing.end():]
        else:
            new_body = body.rstrip() + f"\nsource_dates: [{source_date}]\n"
        return f"---\n{new_body}\n---\n"

    return re.sub(r"^---\n([\s\S]*?)\n---\n", _replace, content, count=1)


def write_or_update_page(subdir, name, description, source_title,
                         entity_type="", tags=None, overwrite=False,
                         source_date=""):
    """create or update a wiki page. returns 'created', 'updated', or 'exists'."""
    path = find_existing_page(subdir, name)

    if path.exists() and not overwrite:
        content = path.read_text()
        if f"[[{source_title}]]" in content:
            # even on a no-op source reference we still record the date
            # so the resolver sees a fresh timestamp next time.
            if source_date:
                updated = _append_source_date(content, source_date)
                if updated != content:
                    path.write_text(updated)
            return "exists"

        # add new source reference.
        content = re.sub(
            r"(sources: \[)([^\]]*)(\])",
            lambda m: f"{m.group(1)}{m.group(2)}, {source_title}{m.group(3)}",
            content,
        )
        content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {_today()}", content)
        content = _append_source_date(content, source_date)

        # insert description before mentioned-in section.
        if description and "## Mentioned In" in content:
            insert = f"\n### From [[{source_title}]]\n\n{description}\n"
            content = content.replace("## Mentioned In", f"{insert}\n## Mentioned In")

        # append to mentioned-in list.
        mention = f"- [[{source_title}]]"
        if mention not in content:
            content = content.rstrip() + f"\n{mention}\n"

        path.write_text(content)
        return "updated"

    # create new page.
    page_type = "entity" if subdir == "entities" else "concept"
    tag_list = tags or ([entity_type] if entity_type else ["topic"])

    frontmatter_lines = [
        "---", f"type: {page_type}",
        f"tags: [{', '.join(tag_list)}]",
        f"sources: [{source_title}]",
    ]
    if source_date:
        frontmatter_lines.append(f"source_dates: [{source_date}]")
    frontmatter_lines += [
        f"created: {_today()}", f"updated: {_today()}",
        "---", "",
    ]

    content = "\n".join(frontmatter_lines + [
        f"# {name}", "", description, "",
        "## Mentioned In", "", f"- [[{source_title}]]", "",
    ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return "created"


# --- index and log. ---

# seed templates used when index.md / log.md don't exist yet. keeps
# the "fresh clone, first ingest" path working: a brand new repo has
# the folder structure (via .gitkeep) but no catalog files. without
# this bootstrap, update_index / append_log would silently no-op on
# every ingest and the catalog would never populate.
_INDEX_SEED = """\
---
type: index
tags: [catalog]
created: {today}
updated: {today}
---

# Wiki Index

Master catalog of all wiki pages. Updated automatically on every ingest.

## Sources

## Entities

## Concepts

## Synthesis
"""

_LOG_SEED = """\
---
type: log
tags: [history]
created: {today}
updated: {today}
---

# Wiki Log

Chronological record of ingestion and synthesis operations. Append-only.
"""


def _bootstrap_index():
    """create wiki/index.md from the seed template if missing."""
    index_path = WIKI_DIR / "index.md"
    if index_path.exists():
        return
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(_INDEX_SEED.format(today=_today()))


def _bootstrap_log():
    """create wiki/log.md from the seed template if missing."""
    log_path = WIKI_DIR / "log.md"
    if log_path.exists():
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(_LOG_SEED.format(today=_today()))


def update_index(source_title, source_desc, entities, concepts):
    """append new entries to the wiki index."""
    _bootstrap_index()
    index_path = WIKI_DIR / "index.md"
    if not index_path.exists():
        return

    content = index_path.read_text()
    content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {_today()}", content)
    lines = content.split("\n")

    # find section header positions.
    sections = {
        line.strip(): i for i, line in enumerate(lines)
        if line.strip().startswith("## ")
    }

    insertions = []
    content_lower = content.lower()

    # source entry.
    if f"[[{source_title.lower()}]]" not in content_lower and "## Sources" in sections:
        desc = source_desc[:120].replace("\n", " ").strip()
        insertions.append((sections["## Sources"], f"- [[{source_title}]] — {desc}"))

    # entity and concept entries.
    for section, items in [("## Entities", entities), ("## Concepts", concepts)]:
        if section in sections:
            for item in items:
                if f"[[{item['name'].lower()}]]" not in content_lower:
                    desc = item.get("description", "")[:120].replace("\n", " ").strip()
                    insertions.append(
                        (sections[section], f"- [[{item['name']}]] — {desc}")
                    )

    # apply in reverse order to preserve line numbers.
    for pos, text in sorted(insertions, key=lambda x: -x[0]):
        lines.insert(pos + 1, text)

    index_path.write_text("\n".join(lines))


def append_log(source_title, filename, ent_created, ent_updated,
               con_created, con_updated, entity_names, concept_names):
    """append a timestamped entry to the wiki log."""
    _bootstrap_log()
    log_path = WIKI_DIR / "log.md"
    if not log_path.exists():
        return

    new_ents = ", ".join(f"[[{n}]]" for n in entity_names[:10]) or "none"
    new_cons = ", ".join(f"[[{n}]]" for n in concept_names[:10]) or "none"

    entry = (
        f"\n## [{_today()}] ingest | {source_title}\n\n"
        f"Processed `raw/{filename}`. "
        f"Created {ent_created + con_created} new pages, "
        f"updated {ent_updated + con_updated} existing pages.\n"
        f"New entities: {new_ents}. New concepts: {new_cons}.\n"
    )

    content = log_path.read_text()
    content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {_today()}", content)
    log_path.write_text(content + entry)


# --- search index. ---

def _rebuild_search_index(quiet=False):
    """rebuild the sqlite fts5 search index.

    broad catch is intentional: search index rebuild is best-effort —
    a failed rebuild must never abort an otherwise successful ingest.
    we log the class so sqlite lock errors are distinguishable from
    disk-full or permission errors.
    """
    try:
        with WikiSearch() as ws:
            count = ws.build_index()
        if not quiet:
            print(f"    search index rebuilt ({count} pages)")
    except Exception as e:
        if not quiet:
            print(
                f"    warning: search index rebuild failed: "
                f"{type(e).__name__}: {e}"
            )


# --- main workflow. ---

def get_ingested_filenames():
    """scan source pages to find which raw files have been ingested."""
    ingested = set()
    sources_dir = WIKI_DIR / "sources"
    if sources_dir.exists():
        for f in sources_dir.glob("*.md"):
            # match backtick-wrapped path: `raw/filename with spaces.md`
            match = re.search(r"`raw/([^`]+)`", f.read_text(errors="replace"))
            if match:
                ingested.add(match.group(1))
    return ingested


def _resolve_items(items, subdir, judge_cache, quiet,
                   use_embeddings=False, source_date="",
                   embed_cache=None, calibration=None,
                   registry: AliasRegistry | None = None):
    """run the layered resolver over every item in a subdir.

    mutates each item's 'name' field to the resolved (possibly forked)
    name and returns a parallel list of Resolution objects. forks and
    non-trivial decisions are logged at info level unless quiet=True.

    the alias registry is threaded through so stage 0 can short-circuit
    dedup for canonical proper nouns without reloading the json file on
    every call.
    """
    resolutions = []
    fork_count = merge_count = 0
    for item in items:
        res = resolve_item(
            item, subdir, cache=judge_cache,
            use_embeddings=use_embeddings,
            current_source_date=source_date,
            embed_cache=embed_cache,
            calibration=calibration,
            registry=registry,
        )
        resolutions.append(res)
        if res.resolved_name != item["name"]:
            item["name"] = res.resolved_name
        if res.action == "fork":
            fork_count += 1
        elif res.action == "merge":
            merge_count += 1

    if not quiet and (fork_count or merge_count):
        print(
            f"    resolved {subdir}: "
            f"{merge_count} merged, {fork_count} forked "
            f"(of {len(items)})"
        )
        # show the forks by name so reviewers can spot bad calls.
        for item, res in zip(items, resolutions):
            if res.action == "fork":
                print(f"      fork: {res.original_name!r} -> "
                      f"{res.resolved_name!r} ({res.reason})")
    return resolutions


def _apply_fork_callouts(items, resolutions, subdir):
    """inject 'see also' callouts into both halves of every fork.

    for a fork, the existing page gets a pointer at the new fork, and
    the new fork gets a pointer at the existing page. both callouts
    are idempotent — re-running ingest won't duplicate them.
    """
    for item, res in zip(items, resolutions):
        if res.action != "fork" or res.existing_path is None:
            continue

        new_path = WIKI_DIR / subdir / f"{safe_filename(item['name'])}.md"

        # original page -> points at the new fork.
        apply_disambiguation_callout(res.existing_path, item["name"])
        # new fork page -> points back at the original.
        apply_disambiguation_callout(new_path, res.existing_path.stem)


def _write_item_pages(items, subdir, title, overwrite, source_date=""):
    """write entity or concept pages. returns (created, updated, created_names)."""
    created = updated = 0
    created_names = []
    for item in items:
        itype = item.get("type", "entity" if subdir == "entities" else "topic")
        kwargs = {"entity_type": itype} if subdir == "entities" else {"tags": [itype]}
        result = write_or_update_page(
            subdir, item["name"], item.get("description", ""),
            title, overwrite=overwrite, source_date=source_date, **kwargs,
        )
        if result == "created":
            created += 1
            created_names.append(item["name"])
        elif result == "updated":
            updated += 1
    return created, updated, created_names


def ingest(filename, overwrite=False, quiet=False, _skip_index_rebuild=False,
           use_embeddings=False):
    """parse, extract, and write wiki pages for a single source file."""
    source_path = RAW_DIR / filename
    if not source_path.exists():
        print(f"  error: raw/{filename} not found.")
        return False

    # image files: register a thin source page and skip llm extraction.
    # the text-only llm cannot describe images, and feeding image bytes
    # to the extractor would waste tokens on garbage.
    if source_path.suffix.lower() in _IMAGE_EXTS:
        return _ingest_image(source_path, overwrite=overwrite, quiet=quiet)

    # content-hash idempotency: if the raw file is byte-identical to what
    # we already ingested, skip entirely. changed content falls through
    # and re-runs the pipeline so updates are picked up.
    current_hash = _compute_file_hash(source_path)
    if not overwrite and current_hash:
        existing_source = _find_source_page_for(filename)
        if existing_source is not None:
            existing_hash = _read_source_hash(existing_source)
            if existing_hash and existing_hash == current_hash:
                if not quiet:
                    print(f"\n  skip: raw/{filename} already ingested "
                          f"(content unchanged).")
                    print(f"        use --reprocess to force re-extraction.")
                return True
            if existing_hash and existing_hash != current_hash and not quiet:
                print(f"\n  note: raw/{filename} content changed since last "
                      f"ingest — updating wiki.")

    t0 = time.time()
    if not quiet:
        print(f"\n{'=' * 60}")
        print(f"  INGEST: {filename}")
        print(f"{'=' * 60}")

    # parse and chunk.
    file_type, full_text, chunks = detect_and_parse(source_path)
    if not full_text.strip():
        print(f"  error: no text extracted from {filename}.")
        return False
    if not quiet:
        print(f"  type: {file_type} — {len(full_text):,} chars — {len(chunks)} chunk(s)")

    # derive a source date for the historical-drift tiebreaker. this is
    # cheap (regex + optional pdfinfo) and runs before extraction so it
    # can be threaded into the resolver.
    source_date = _extract_source_date(source_path, file_type, full_text)
    if source_date and not quiet:
        print(f"  source_date: {source_date}")

    # extract and merge.
    extractions = extract_chunks_parallel(chunks, quiet=quiet)
    merged = merge_extractions(extractions)

    # cross-bucket dedup: collapse items the llm emitted into both
    # entities and concepts. routes each collision to the subdirectory
    # that matches its declared type so methods/metrics land in concepts
    # and people/organizations land in entities.
    _cross_bucket_dedup(merged, quiet=quiet)

    # derive title. prefer llm-extracted, fall back to filename.
    title = merged.get("title", "").strip()
    if not title or title == filename or len(title) < 5:
        title = filename.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()

    # sanitize title and entity/concept names so wikilink targets always
    # match filenames. without this, chars like | : / [] get stripped from
    # filenames but stay in [[wikilinks]], creating phantom nodes in obsidian.
    title = safe_filename(title)
    for item in merged["entities"] + merged["concepts"]:
        item["name"] = safe_filename(item["name"])

    if not quiet:
        print(f"  title: {title}")

    # second-pass canonicalization: rewrite "the model"/"our system"
    # descriptions into concrete ones using the source context. this
    # runs before resolution so the resolver sees canonical text.
    _canonicalize_descriptions(merged["entities"], full_text, title, quiet=quiet)
    _canonicalize_descriptions(merged["concepts"], full_text, title, quiet=quiet)

    # stage 0 preprocessing: canonical alias registry. rewrites proper
    # nouns to their registered canonical form (ChatGPT, OpenAI, etc.)
    # and replaces context-local descriptions with curated ones. this
    # is the prevention layer — it eliminates the ChatGPT / ChatGPT
    # (model) fork class before the resolver ever sees it.
    registry = default_registry()
    _normalize_via_aliases(merged["entities"], registry, "entities", quiet=quiet)
    _normalize_via_aliases(merged["concepts"], registry, "concepts", quiet=quiet)

    # resolve entities and concepts against the existing wiki BEFORE any
    # page write. the resolver may rename items (fork polysems) or leave
    # them unchanged (merge coreferent mentions). mutating item["name"]
    # in place back-propagates renames into the source page citations,
    # the overview prompt, and the index/log — no second rewrite pass.
    judge_cache = load_judge_cache()
    embed_cache = load_embed_cache() if use_embeddings else None
    calibration = load_calibration_cache() if use_embeddings else None

    entity_resolutions = _resolve_items(
        merged["entities"], "entities", judge_cache, quiet,
        use_embeddings=use_embeddings, source_date=source_date,
        embed_cache=embed_cache, calibration=calibration,
        registry=registry,
    )
    concept_resolutions = _resolve_items(
        merged["concepts"], "concepts", judge_cache, quiet,
        use_embeddings=use_embeddings, source_date=source_date,
        embed_cache=embed_cache, calibration=calibration,
        registry=registry,
    )
    save_judge_cache(judge_cache)
    if embed_cache is not None:
        save_embed_cache(embed_cache)
    if calibration is not None:
        save_calibration_cache(calibration)

    # generate unified summary for multi-chunk files.
    if len(chunks) > 2:
        if not quiet:
            print("    synthesizing overview...", flush=True)
        overview = generate_summary(
            title, merged["summaries"],
            merged["entities"], merged["concepts"],
        )
    elif len(merged["summaries"]) > 1:
        overview = "\n\n".join(merged["summaries"])
    else:
        overview = merged["summaries"][0] if merged["summaries"] else ""

    # write entity and concept pages first. the source page is written
    # afterwards so it can cite the resolved (possibly forked) names.
    ent_created, ent_updated, ent_names = _write_item_pages(
        merged["entities"], "entities", title, overwrite, source_date=source_date)
    con_created, con_updated, con_names = _write_item_pages(
        merged["concepts"], "concepts", title, overwrite, source_date=source_date)

    # inject disambiguation callouts on both sides of every fork. this
    # runs after page writes so the fork page exists on disk and can
    # receive its own "see also" pointing back at the original.
    _apply_fork_callouts(merged["entities"], entity_resolutions, "entities")
    _apply_fork_callouts(merged["concepts"], concept_resolutions, "concepts")

    # write source page (uses resolved names via merged["entities"]/["concepts"]).
    tag_map = {"sms": "sms", "pdf": "paper", "text": "article"}
    tags = [tag_map.get(file_type, "article")]
    write_page("sources", title, make_source_page(
        title, filename, overview,
        merged["entities"], merged["concepts"],
        merged["key_claims"], tags, source_date=source_date,
        source_hash=current_hash,
    ), overwrite=True)  # always rewrite source page so hash updates.

    # update index and log.
    first_summary = merged["summaries"][0] if merged["summaries"] else ""
    desc = first_summary.split(".")[0].strip() if first_summary else title
    update_index(title, desc, merged["entities"], merged["concepts"])
    append_log(title, filename,
               ent_created, ent_updated, con_created, con_updated,
               ent_names, con_names)

    # rebuild search index so queries see new pages immediately.
    if not _skip_index_rebuild:
        _rebuild_search_index(quiet=quiet)

    # self-promote stable wiki entities into the runtime registry.
    # a page with >=3 sources and a non-generic description earns a
    # promoted alias entry that persists across runs. this grows the
    # gazetteer from the wiki itself — no manual curation required
    # after the initial seed tier. promotion is cheap (filesystem scan)
    # and side-effect free if nothing new is stable.
    try:
        promoted = registry.promote_from_wiki(WIKI_DIR, min_sources=3)
        if promoted and not quiet:
            print(f"    registry: promoted {promoted} new alias entry/entries")
        if promoted:
            registry.save_runtime()
    except Exception as e:  # noqa: BLE001 — promotion must never fail ingest.
        if not quiet:
            print(
                f"    warning: alias promotion failed: "
                f"{type(e).__name__}: {e}"
            )

    elapsed = time.time() - t0
    if not quiet:
        total_pages = 1 + ent_created + ent_updated + con_created + con_updated
        print(f"\n  done in {elapsed:.0f}s — {total_pages} pages touched:")
        print(f"    1 source, {ent_created} entities created, "
              f"{ent_updated} updated, {con_created} concepts created, "
              f"{con_updated} updated")
    else:
        print(f"  [{elapsed:5.0f}s] {filename} -> "
              f"{ent_created}+{ent_updated}e {con_created}+{con_updated}c")

    return True


def ingest_all(overwrite=False, use_embeddings=False):
    """process every un-ingested file in raw/."""
    ingested = get_ingested_filenames()
    pending = [
        f.name for f in sorted(RAW_DIR.iterdir())
        if f.is_file() and not f.name.startswith(".")
        and (overwrite or f.name not in ingested)
    ]

    if not pending:
        print("\n  nothing to ingest. all files already processed.")
        print("  use --reprocess-all to overwrite existing pages.\n")
        return

    print(f"\n  ingesting {len(pending)} files...\n")
    t0 = time.time()
    success = 0

    for i, filename in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] ", end="", flush=True)
        try:
            if ingest(filename, overwrite=overwrite, quiet=True,
                       _skip_index_rebuild=True,
                       use_embeddings=use_embeddings):
                success += 1
        except Exception as e:
            print(f"  [error] {filename}: {e}")

    # one index rebuild at the end of batch, not per-file.
    _rebuild_search_index(quiet=False)

    elapsed = time.time() - t0
    print(f"\n  batch complete: {success}/{len(pending)} files in {elapsed:.0f}s")
    print(f"  average: {elapsed / max(success, 1):.0f}s per file\n")


def list_sources():
    """show all raw files and their ingest status."""
    print("\n  raw sources (obsidian_vault/raw/):\n")

    ingested = get_ingested_filenames()
    pending = done = 0

    for f in sorted(RAW_DIR.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            size = f.stat().st_size
            if size > 1_000_000:
                s = f"{size / 1_000_000:.1f} MB"
            elif size > 1000:
                s = f"{size / 1000:.1f} KB"
            else:
                s = f"{size} B"

            if f.name in ingested:
                marker, status = "+", "ingested"
                done += 1
            else:
                marker, status = " ", "pending"
                pending += 1

            print(f"  [{marker}] {f.name:45s} {s:>10s}  ({status})")

    print(f"\n  total: {done + pending} files ({done} ingested, {pending} pending)\n")


# --- cli. ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="llm wiki — local ingestion pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 scripts/ingest.py --list                      # see what's available.
  python3 scripts/ingest.py --all                       # ingest everything pending.
  python3 scripts/ingest.py my-article.md               # ingest one file.
  python3 scripts/ingest.py --reprocess my-article.md   # re-ingest, overwrite pages.
  python3 scripts/ingest.py --reprocess-all             # re-ingest everything.
""",
    )
    parser.add_argument("filename", nargs="?", help="filename in raw/ to ingest")
    parser.add_argument("--list", action="store_true", help="list raw sources")
    parser.add_argument("--all", action="store_true", help="ingest all pending files")
    parser.add_argument("--reprocess", metavar="FILE", help="re-ingest one file")
    parser.add_argument("--reprocess-all", action="store_true", help="re-ingest everything")
    parser.add_argument(
        "--use-embeddings", action="store_true",
        help="enable stage 5 entity resolution (requires bge-m3 server on :8081)",
    )
    args = parser.parse_args()

    if args.list:
        list_sources()
        sys.exit(0)

    require_server()
    if args.use_embeddings:
        require_embed_server()

    if args.all:
        ingest_all(use_embeddings=args.use_embeddings)
    elif args.reprocess_all:
        ingest_all(overwrite=True, use_embeddings=args.use_embeddings)
    elif args.reprocess:
        ingest(args.reprocess, overwrite=True, use_embeddings=args.use_embeddings)
    elif args.filename:
        ingest(args.filename, use_embeddings=args.use_embeddings)
    else:
        parser.print_help()
