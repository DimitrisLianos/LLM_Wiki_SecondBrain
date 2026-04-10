#!/usr/bin/env python3
"""llm wiki - wiki lint.
checks for broken links, orphan pages, missing pages, index consistency,
and frontmatter issues.

usage:
    python3 scripts/lint.py
"""

import re

from llm_client import WIKI_DIR, SUBDIRS
REQUIRED_FIELDS = ("type", "created", "tags", "sources")


def get_all_pages():
    """collect all wiki pages as display_name -> path."""
    pages = {}
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        if d.exists():
            for f in d.glob("*.md"):
                pages[f.stem] = f
    return pages


def _build_case_map(pages):
    """map lowercased page names to actual names for case-insensitive resolution."""
    return {name.lower(): name for name in pages}


def extract_wikilinks(text):
    """pull all [[target]] links, ignoring display aliases."""
    return set(re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", text))


def check_frontmatter(path):
    """verify yaml frontmatter has required fields."""
    issues = []
    text = path.read_text()
    if not text.startswith("---"):
        return ["missing yaml frontmatter"]

    end = text.find("---", 3)
    if end == -1:
        return ["unclosed yaml frontmatter"]

    fm = text[3:end]
    issues = [f"missing '{f}'" for f in REQUIRED_FIELDS if f not in fm]
    return issues


def check_index_consistency(pages):
    """returns (missing_from_index, stale_entries). case-insensitive matching."""
    index_path = WIKI_DIR / "index.md"
    if not index_path.exists():
        return [], []

    index_text = index_path.read_text()
    index_lower = index_text.lower()
    index_links = extract_wikilinks(index_text)
    case_map = _build_case_map(pages)

    missing = [n for n in pages if f"[[{n.lower()}]]" not in index_lower]
    stale = [link for link in index_links if link.lower() not in case_map]
    return missing, stale


def lint():
    pages = get_all_pages()

    print(f"\n  wiki lint report")
    print(f"  {'=' * 50}")

    # page counts.
    for subdir in SUBDIRS:
        d = WIKI_DIR / subdir
        count = len(list(d.glob("*.md"))) if d.exists() else 0
        print(f"    {subdir}: {count}")
    print(f"    total: {len(pages)}")

    errors, warnings, info = [], [], []

    # build link graph. case-insensitive resolution since the llm may
    # extract the same name with different casing across sources.
    case_map = _build_case_map(pages)
    all_outbound, all_inbound, missing_pages = {}, {}, {}
    for name, path in pages.items():
        text = path.read_text()
        links = extract_wikilinks(text)
        all_outbound[name] = links
        for link in links:
            resolved = case_map.get(link.lower())
            if resolved:
                all_inbound.setdefault(resolved, set()).add(name)
            else:
                missing_pages.setdefault(link, set()).add(name)

    # errors: broken wikilinks.
    for target in sorted(missing_pages):
        refs = ", ".join(sorted(missing_pages[target]))
        errors.append(f"broken link [[{target}]] <- {refs}")

    # errors: index references missing pages.
    missing_from_index, stale_entries = check_index_consistency(pages)
    for name in stale_entries:
        errors.append(f"index references missing page [[{name}]]")

    # warnings: orphan pages (no inbound links).
    special = {"index", "log"}
    for name in sorted(n for n in pages if n not in all_inbound and n not in special):
        warnings.append(f"orphan page: {name} (no inbound links)")

    # warnings: pages missing from index.
    for name in sorted(missing_from_index):
        warnings.append(f"page not in index: {name}")

    # warnings: frontmatter issues.
    for name, path in sorted(pages.items()):
        problems = check_frontmatter(path)
        if problems:
            warnings.append(f"frontmatter [{name}]: {', '.join(problems)}")

    # info: isolated pages (no outbound links).
    for name in sorted(n for n, links in all_outbound.items() if not links):
        info.append(f"isolated page: {name} (no outbound links)")

    # info: nearly empty pages.
    for name, path in sorted(pages.items()):
        text = path.read_text()
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:]
        if len(text.strip()) < 80:
            info.append(f"thin page: {name} (very little content)")

    # report.
    for label, items in [("ERRORS", errors), ("WARNINGS", warnings), ("INFO", info)]:
        if items:
            print(f"\n  {label} ({len(items)}):")
            for item in items:
                print(f"    {item}")

    total = len(errors) + len(warnings) + len(info)
    if total == 0:
        print(f"\n  all clear.")
    else:
        print(f"\n  {len(errors)} errors, {len(warnings)} warnings, {len(info)} info.")

    print(f"  {'=' * 50}\n")


if __name__ == "__main__":
    lint()
