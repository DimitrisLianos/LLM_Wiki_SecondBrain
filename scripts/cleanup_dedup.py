#!/usr/bin/env python3
"""llm wiki — cleanup for duplicate pages produced by the resolver.

usage:
    python3 scripts/cleanup_dedup.py               # dry run, print a plan.
    python3 scripts/cleanup_dedup.py --apply       # actually merge.
    python3 scripts/cleanup_dedup.py --no-registry # skip alias-driven grouping.

what it does
------------
groups pages in wiki/entities/ and wiki/concepts/ into duplicate
clusters using two complementary signals, then merges each cluster
into a single canonical survivor.

grouping signals (a page joins a cluster when either signal hits):

    A. stem-normalized dedup key — collapses "URN" vs "URN (Uniform
       Resource Name)", "Label Embeddings" vs "Label Embedding",
       "Culex spp." vs "Culex spp". this is the original behavior
       and catches surface-form variants.

    B. canonical alias registry — collapses "ChatGPT" vs "ChatGPT
       (model)", "OpenAI" vs "OpenAI, Inc.", "GPT-4" vs "gpt-4"
       vs "gpt four". driven by scripts/data/seed_aliases.json
       plus runtime promotions in db/alias_registry.json. this is
       the layer-3 cleanup for the entity-linking system; it catches
       the forks academic literature calls "surface form ambiguity
       with context-local priors" (BLINK, ReFinED, TAGME).

merging (per cluster):

1. pick a canonical survivor (registry-preferred canonical name wins;
   then pages that route to the correct subdirectory for their declared
   type; then shortest stem without trailing parentheticals; then
   longest description).
2. merge the other pages' frontmatter fields (sources, source_dates,
   tags) into the survivor.
3. remove disambiguation callouts that pointed at the now-merged
   sibling.
4. delete the merged-in pages.
5. rewrite [[wikilinks]] across ALL wiki pages so source citations and
   cross-links follow the new canonical name.
6. update wiki/index.md via the same wikilink rewrite.

safety
------
dry run is the default. --apply is required to write. every cluster
gets logged so a reviewer can grep the output and spot bad merges
before committing. --no-registry disables the registry pass if a
seeded entry is producing unwanted merges.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aliases import (  # noqa: E402
    AliasEntry,
    AliasRegistry,
    default_registry,
    normalize_alias_key,
)
from ingest import _CONCEPT_TYPES, _ENTITY_TYPES, _dedup_key  # noqa: E402
from llm_client import FRONTMATTER_RE, WIKI_DIR, safe_filename  # noqa: E402

SUBDIRS = ("entities", "concepts")

_LIST_FIELD_RE = re.compile(r"^(sources|source_dates|tags):\s*\[([^\]]*)\]\s*$")
_DISAMBIG_RE = re.compile(
    r"^>\s*\*\*Disambiguation:\*\*\s*see also \[\[(.+?)\]\].*?\n\n",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PageInfo:
    path: Path
    subdir: str
    stem: str
    description: str
    sources: list[str]
    source_dates: list[str]
    tags: list[str]
    has_parens: bool
    alias_entry: AliasEntry | None = None

    @property
    def routes_correctly(self) -> bool:
        """true when this page lives in the subdirectory that matches its
        declared tags (method/metric/… -> concepts, person/tool/… -> entities).

        used to pick the canonical survivor for cross-directory duplicates.
        """
        lowered = {t.lower() for t in self.tags}
        if self.subdir == "concepts":
            return bool(lowered & _CONCEPT_TYPES)
        if self.subdir == "entities":
            # must match an entity type AND not also match a concept type,
            # otherwise "method" tagged pages that ended up in entities/
            # would look correctly routed.
            return bool(lowered & _ENTITY_TYPES) and not (lowered & _CONCEPT_TYPES)
        return False

    @property
    def matches_alias_canonical(self) -> bool:
        """true when this page's stem is already the registry's canonical form.

        used by _pick_survivor to prefer pages that already match the
        gazetteer — these are the highest-quality survivors because
        they align with the long-lived canonical name.
        """
        if self.alias_entry is None:
            return False
        canonical_stem = safe_filename(self.alias_entry.canonical_name)
        return self.stem == canonical_stem


# --- parsing. ---


def _parse_list_field(line: str) -> list[str] | None:
    match = _LIST_FIELD_RE.match(line.strip())
    if not match:
        return None
    raw = match.group(2)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_page(
    path: Path,
    subdir: str,
    registry: AliasRegistry | None = None,
) -> PageInfo:
    text = path.read_text()
    fm_match = FRONTMATTER_RE.match(text)
    sources: list[str] = []
    source_dates: list[str] = []
    tags: list[str] = []
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            parsed = _parse_list_field(line)
            if parsed is None:
                continue
            key = line.split(":", 1)[0].strip()
            if key == "sources":
                sources = parsed
            elif key == "source_dates":
                source_dates = parsed
            elif key == "tags":
                tags = parsed

    # first non-heading non-blockquote non-empty paragraph -> description.
    body = text[fm_match.end():] if fm_match else text
    description = ""
    for para in re.split(r"\n\n+", body):
        stripped = para.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        description = stripped
        break

    # look up the page name in the alias registry. the type hint is
    # the first frontmatter tag (tools/method/model/etc.) — the same
    # mechanism the ingest pipeline uses. subdir is used as a guard so
    # a concept-registered name like "Transformer" cannot accidentally
    # match an entity page of the same name.
    alias_entry: AliasEntry | None = None
    if registry is not None and len(registry) > 0:
        type_hint = tags[0].lower() if tags else ""
        candidate = registry.lookup(path.stem, type_hint=type_hint)
        if candidate is not None and (
            not candidate.subdir or candidate.subdir == subdir
        ):
            alias_entry = candidate

    return PageInfo(
        path=path,
        subdir=subdir,
        stem=path.stem,
        description=description,
        sources=sources,
        source_dates=source_dates,
        tags=tags,
        has_parens="(" in path.stem,
        alias_entry=alias_entry,
    )


# --- canonical survivor selection. ---


def _pick_survivor(pages: list[PageInfo]) -> PageInfo:
    """pick the best page to keep from a duplicate group.

    preferences (in order):
      1. page whose stem already matches the registry's canonical
         form (e.g. "ChatGPT" beats "ChatGPT (model)" because the
         registry has "ChatGPT" as canonical).
      2. lives in the subdirectory that matches its declared type
         (method -> concepts, tool -> entities). this is what routes
         cross-directory collisions to the correct home.
      3. no trailing parenthetical qualifier (cleaner surface form).
      4. shortest name (the canonical "URN" over "URN (Uniform...)").
      5. longest description (most informative).

    if no page routes correctly, the rule collapses to rules 3-5 over
    all candidates. this preserves the pre-fix behavior for groups
    that live entirely in one subdirectory.
    """
    canonical_matches = [p for p in pages if p.matches_alias_canonical]
    if canonical_matches:
        pool = canonical_matches
    else:
        correctly_routed = [p for p in pages if p.routes_correctly]
        pool = correctly_routed if correctly_routed else pages

    def sort_key(p: PageInfo) -> tuple:
        return (
            1 if p.has_parens else 0,
            len(p.stem),
            -len(p.description),
        )
    return sorted(pool, key=sort_key)[0]


# --- merging frontmatter. ---


def _merge_list(a: list[str], b: list[str]) -> list[str]:
    """union preserving order; dedup by lowercased trimmed value."""
    seen: set[str] = set()
    out: list[str] = []
    for item in list(a) + list(b):
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


def _rewrite_frontmatter(text: str, merged: PageInfo) -> str:
    """replace sources / source_dates / tags lines with merged values."""
    fm_match = FRONTMATTER_RE.match(text)
    if not fm_match:
        return text

    fm_text = fm_match.group(1)
    new_lines: list[str] = []
    seen_sources = seen_dates = seen_tags = False
    for line in fm_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("sources:"):
            new_lines.append(f"sources: [{', '.join(merged.sources)}]")
            seen_sources = True
        elif stripped.startswith("source_dates:"):
            if merged.source_dates:
                new_lines.append(
                    f"source_dates: [{', '.join(merged.source_dates)}]"
                )
            seen_dates = True
        elif stripped.startswith("tags:"):
            new_lines.append(f"tags: [{', '.join(merged.tags)}]")
            seen_tags = True
        else:
            new_lines.append(line)

    # if source_dates wasn't originally present but we now have values,
    # inject it after the sources line.
    if not seen_dates and merged.source_dates:
        out: list[str] = []
        for line in new_lines:
            out.append(line)
            if line.strip().startswith("sources:"):
                out.append(
                    f"source_dates: [{', '.join(merged.source_dates)}]"
                )
        new_lines = out

    if not seen_sources:
        new_lines.insert(0, f"sources: [{', '.join(merged.sources)}]")
    if not seen_tags:
        new_lines.insert(0, f"tags: [{', '.join(merged.tags)}]")

    return "---\n" + "\n".join(new_lines) + "\n---\n" + text[fm_match.end():]


def _strip_disambig_referring_to(text: str, sibling_stems: set[str]) -> str:
    """remove disambiguation callouts pointing at any of the merged siblings.

    leaves callouts pointing at pages that still exist. the `[[X]]` form
    matches the sibling_name exactly (case-sensitive on content, we
    lowercase for comparison).
    """
    def replace(match: re.Match) -> str:
        target = match.group(1).strip().lower()
        if target in sibling_stems:
            return ""
        return match.group(0)

    return _DISAMBIG_RE.sub(replace, text)


def _append_mentioned_in(text: str, new_sources: list[str]) -> str:
    """append new sources to the Mentioned In list, preserving order."""
    if "## Mentioned In" not in text:
        return text
    head, _, tail = text.partition("## Mentioned In")
    existing_lines = tail.split("\n")
    existing_links = {
        re.search(r"\[\[(.+?)\]\]", line).group(1).strip().lower()
        for line in existing_lines
        if re.search(r"\[\[(.+?)\]\]", line)
    }
    additions: list[str] = []
    for src in new_sources:
        key = src.strip().lower()
        if key and key not in existing_links:
            additions.append(f"- [[{src}]]")
            existing_links.add(key)
    if not additions:
        return text
    # insert after the blank line following the heading.
    return head + "## Mentioned In\n\n" + "\n".join(
        [line for line in tail.split("\n") if line.strip()] + additions
    ) + "\n"


# --- wikilink rewrite across all pages. ---


def _rewrite_wikilinks(root: Path, rename_map: dict[str, str], apply: bool) -> int:
    """rewrite [[old]] -> [[new]] across every .md file under root.

    rename_map keys/values are stems (no .md). matches are exact on the
    link target, not substring — we don't touch partial words.
    """
    changed = 0
    if not rename_map:
        return 0

    # compile alternation once.
    escaped = "|".join(re.escape(old) for old in rename_map.keys())
    pattern = re.compile(rf"\[\[({escaped})\]\]")

    def replace(match: re.Match) -> str:
        return f"[[{rename_map[match.group(1)]}]]"

    for path in root.rglob("*.md"):
        try:
            text = path.read_text()
        except OSError:
            continue
        if not pattern.search(text):
            continue
        new_text = pattern.sub(replace, text)
        if new_text != text:
            changed += 1
            if apply:
                path.write_text(new_text)
    return changed


# --- main cleanup. ---


def _composite_group_key(page: PageInfo) -> str:
    """return the cluster key a page should be grouped under.

    preference order:
      1. if the page hits the alias registry, use the canonical form
         normalized through normalize_alias_key. this is the strongest
         signal because the registry is curated + validated.
      2. otherwise fall back to _dedup_key (stem-based surface form
         collation).

    the two keys live in disjoint namespaces by construction —
    normalize_alias_key strips parentheticals and punctuation but
    keeps the original word order + case-folded form, while _dedup_key
    also stems every word (embeddings -> embed) and thus produces
    keys that look different even for pages that hit the registry.
    prefixing the registry case prevents accidental collisions.
    """
    if page.alias_entry is not None:
        return f"alias::{normalize_alias_key(page.alias_entry.canonical_name)}"
    return f"stem::{_dedup_key(page.stem)}"


def cleanup(apply: bool, use_registry: bool = True) -> int:
    total_groups = 0
    total_merged = 0
    total_deleted = 0
    rename_map: dict[str, str] = {}

    registry: AliasRegistry | None = None
    if use_registry:
        registry = default_registry()
        print(f"alias registry: {len(registry)} entries loaded")
    else:
        print("alias registry: disabled (--no-registry)")

    # build ONE combined group map across both subdirectories. this is
    # the key change from v1 — cross-directory collisions (same item
    # written into both entities/ and concepts/) now show up as normal
    # duplicate groups and get collapsed by _pick_survivor's type-routing
    # rule.
    #
    # the group key is composite: registry-canonical when available,
    # stem-based otherwise. this catches both surface-form variants
    # ("URN" vs "URN (Uniform Resource Name)") and alias-level forks
    # ("ChatGPT" vs "ChatGPT (model)") in a single pass.
    groups: dict[str, list[PageInfo]] = defaultdict(list)
    for subdir in SUBDIRS:
        directory = WIKI_DIR / subdir
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            page = _read_page(path, subdir, registry=registry)
            groups[_composite_group_key(page)].append(page)

    for key, pages in sorted(groups.items()):
        if len(pages) < 2:
            continue
        total_groups += 1

        survivor = _pick_survivor(pages)
        losers = [p for p in pages if p.path != survivor.path]

        merged_sources = survivor.sources[:]
        merged_dates = survivor.source_dates[:]
        merged_tags = survivor.tags[:]
        for loser in losers:
            merged_sources = _merge_list(merged_sources, loser.sources)
            merged_dates = _merge_list(merged_dates, loser.source_dates)
            merged_tags = _merge_list(merged_tags, loser.tags)

        merged_info = PageInfo(
            path=survivor.path,
            subdir=survivor.subdir,
            stem=survivor.stem,
            description=survivor.description,
            sources=merged_sources,
            source_dates=merged_dates,
            tags=merged_tags,
            has_parens=survivor.has_parens,
        )

        # build the new survivor text.
        survivor_text = survivor.path.read_text()
        survivor_text = _rewrite_frontmatter(survivor_text, merged_info)
        survivor_text = _strip_disambig_referring_to(
            survivor_text,
            {loser.stem.lower() for loser in losers},
        )
        # add any sources the losers had but survivor didn't.
        new_source_refs = [
            s for s in merged_sources
            if s not in survivor.sources
        ]
        survivor_text = _append_mentioned_in(survivor_text, new_source_refs)

        # label the group with whichever subdirs it spans so cross-dir
        # cleanups are visible in the plan.
        span = sorted({p.subdir for p in pages})
        label = "+".join(span)
        print(f"\n[{label}] group {key!r}:")
        print(f"  keep:   [{survivor.subdir}] {survivor.stem}")
        for loser in losers:
            print(f"  merge:  [{loser.subdir}] {loser.stem}")
            # rename_map only needs unique stems; collisions between
            # same-stem pages across subdirs just rewrite once.
            rename_map[loser.stem] = survivor.stem

        if apply:
            survivor.path.write_text(survivor_text)
            for loser in losers:
                loser.path.unlink(missing_ok=True)

        total_merged += len(losers)
        total_deleted += len(losers)

    # rewrite wikilinks across the whole wiki to follow renames.
    if rename_map:
        changed = _rewrite_wikilinks(WIKI_DIR, rename_map, apply)
        print(f"\nwikilink rewrite: {changed} file(s) updated")

    print(
        f"\nsummary: {total_groups} duplicate group(s), "
        f"{total_merged} page(s) merged, "
        f"{total_deleted} page(s) to be deleted."
    )
    if not apply:
        print("(dry run — re-run with --apply to commit changes.)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write changes (default is dry run).",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help=(
            "disable the alias registry pass. only stem-based dedup "
            "(the pre-registry behavior) runs. use this if a seeded "
            "entry is producing unwanted merges."
        ),
    )
    args = parser.parse_args()
    return cleanup(apply=args.apply, use_registry=not args.no_registry)


if __name__ == "__main__":
    sys.exit(main())
