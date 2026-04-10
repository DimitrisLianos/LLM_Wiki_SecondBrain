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
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from llm_client import (
    BASE_DIR, RAW_DIR, WIKI_DIR, MAX_CHUNK_CHARS,
    ContextOverflowError, llm, require_server,
)
from search import WikiSearch

MAX_SMS_PER_CHUNK = 300


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

    with ThreadPoolExecutor(max_workers=min(total, 2)) as pool:
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
                if not quiet:
                    print(f"      chunk {idx + 1}/{total} failed: {e}")
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


def _dedup_items(items, target):
    """keep the item with the longest description per name.

    dedup key is case-insensitive so 'Open Weights' and 'Open weights'
    merge into one entry. the version with the longer description wins.
    """
    for item in items:
        name = item.get("name", "").strip()
        if not name:
            continue
        key = name.lower()
        existing = target.get(key)
        if not existing or len(item.get("description", "")) > len(
            existing.get("description", "")
        ):
            target[key] = item


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


def safe_filename(name):
    """filesystem-safe and wikilink-safe name. keeps spaces for obsidian resolution.

    strips characters that are either unsafe on filesystems or break obsidian
    wikilink parsing: colons, pipes (alias syntax), brackets (nest inside [[]]),
    slashes (path separators), quotes, wildcards, and control chars.
    also prevents path traversal via '..' sequences.
    """
    name = re.sub(r'[<>:"/\\|?*\[\]\x00-\x1f]', "", name)
    while ".." in name:
        name = name.replace("..", "")
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "Untitled"
    return name[:120].strip() if len(name) > 120 else name


def make_source_page(title, filename, summary, entities, concepts, key_claims, tags):
    """create a source summary page."""
    lines = [
        "---", "type: source",
        f"tags: [{', '.join(tags)}]",
        f"sources: [raw/{filename}]",
        f"created: {_today()}", f"updated: {_today()}",
        "---", "",
        f"# {title}", "",
        f"**Source:** `raw/{filename}`",
        f"**Date ingested:** {_today()}",
        f"**Type:** {tags[0] if tags else 'article'}", "",
        "## Summary", "", summary, "",
    ]

    if key_claims:
        lines += ["## Key Claims", ""] + [f"- {c}" for c in key_claims] + [""]

    for heading, items in [("## Entities Mentioned", entities),
                           ("## Concepts Covered", concepts)]:
        if items:
            lines += [heading, ""]
            for item in items:
                desc = item.get("description", "")
                desc = desc.split(".")[0] + "." if desc else ""
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


def _find_existing_page(subdir, name):
    """case-insensitive page lookup. returns path if found, else default path.

    the llm might extract 'Open Weights' from one source and 'Open weights'
    from another. both should resolve to the same wiki page.
    """
    safe = safe_filename(name)
    exact = WIKI_DIR / subdir / f"{safe}.md"
    if exact.exists():
        return exact
    # case-insensitive search.
    target = safe.lower()
    d = WIKI_DIR / subdir
    if d.exists():
        for f in d.iterdir():
            if f.suffix == ".md" and f.stem.lower() == target:
                return f
    return exact  # not found — return the default path for creation.


def write_or_update_page(subdir, name, description, source_title,
                         entity_type="", tags=None, overwrite=False):
    """create or update a wiki page. returns 'created', 'updated', or 'exists'."""
    path = _find_existing_page(subdir, name)

    if path.exists() and not overwrite:
        content = path.read_text()
        if f"[[{source_title}]]" in content:
            return "exists"

        # add new source reference.
        content = re.sub(
            r"(sources: \[)([^\]]*)(\])",
            lambda m: f"{m.group(1)}{m.group(2)}, {source_title}{m.group(3)}",
            content,
        )
        content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {_today()}", content)

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

    content = "\n".join([
        "---", f"type: {page_type}",
        f"tags: [{', '.join(tag_list)}]",
        f"sources: [{source_title}]",
        f"created: {_today()}", f"updated: {_today()}",
        "---", "",
        f"# {name}", "", description, "",
        "## Mentioned In", "", f"- [[{source_title}]]", "",
    ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return "created"


# --- index and log. ---

def update_index(source_title, source_desc, entities, concepts):
    """append new entries to the wiki index."""
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
    """rebuild the sqlite fts5 search index."""
    try:
        with WikiSearch() as ws:
            count = ws.build_index()
        if not quiet:
            print(f"    search index rebuilt ({count} pages)")
    except Exception as e:
        if not quiet:
            print(f"    warning: search index rebuild failed: {e}")


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


def _write_item_pages(items, subdir, title, overwrite):
    """write entity or concept pages. returns (created, updated, created_names)."""
    created = updated = 0
    created_names = []
    for item in items:
        itype = item.get("type", "entity" if subdir == "entities" else "topic")
        kwargs = {"entity_type": itype} if subdir == "entities" else {"tags": [itype]}
        result = write_or_update_page(
            subdir, item["name"], item.get("description", ""),
            title, overwrite=overwrite, **kwargs,
        )
        if result == "created":
            created += 1
            created_names.append(item["name"])
        elif result == "updated":
            updated += 1
    return created, updated, created_names


def ingest(filename, overwrite=False, quiet=False, _skip_index_rebuild=False):
    """parse, extract, and write wiki pages for a single source file."""
    source_path = RAW_DIR / filename
    if not source_path.exists():
        print(f"  error: raw/{filename} not found.")
        return False

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

    # extract and merge.
    extractions = extract_chunks_parallel(chunks, quiet=quiet)
    merged = merge_extractions(extractions)

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

    # write source page.
    tag_map = {"sms": "sms", "pdf": "paper", "text": "article"}
    tags = [tag_map.get(file_type, "article")]
    write_page("sources", title, make_source_page(
        title, filename, overview,
        merged["entities"], merged["concepts"],
        merged["key_claims"], tags,
    ), overwrite=overwrite)

    # write entity and concept pages.
    ent_created, ent_updated, ent_names = _write_item_pages(
        merged["entities"], "entities", title, overwrite)
    con_created, con_updated, con_names = _write_item_pages(
        merged["concepts"], "concepts", title, overwrite)

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


def ingest_all(overwrite=False):
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
                       _skip_index_rebuild=True):
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
    args = parser.parse_args()

    if args.list:
        list_sources()
    elif args.all:
        require_server()
        ingest_all()
    elif args.reprocess_all:
        require_server()
        ingest_all(overwrite=True)
    elif args.reprocess:
        require_server()
        ingest(args.reprocess, overwrite=True)
    elif args.filename:
        require_server()
        ingest(args.filename)
    else:
        parser.print_help()
