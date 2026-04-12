#!/usr/bin/env python3
"""llm wiki — unit tests for the canonical alias registry.

stdlib-only tests. no pytest, no mocks library, no network - only the
pure functions that can run without the llama.cpp or bge-m3 servers.

run:
    python3 scripts/test_aliases.py

each test is a plain function returning None on success. on failure the
inline assert raises and the runner prints the exception with a
traceback. follows the same runner shape as test_resolver.py.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# allow running from the project root.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aliases import (  # noqa: E402
    MAX_DESCRIPTION_LENGTH,
    MIN_DESCRIPTION_LENGTH,
    MIN_SOURCES_FOR_PROMOTION,
    AliasEntry,
    AliasRegistry,
    default_registry,
    normalize_alias_key,
)


# --- normalize_alias_key. ---


def test_normalize_strips_parentheticals():
    assert normalize_alias_key("ChatGPT (model)") == "chatgpt"
    assert normalize_alias_key("URN (Uniform Resource Name)") == "urn"
    # stacked trailing parens.
    assert normalize_alias_key("Python (lang) (interpreter)") == "python"


def test_normalize_lowercases():
    assert normalize_alias_key("ChatGPT") == "chatgpt"
    assert normalize_alias_key("OpenAI") == "openai"
    assert normalize_alias_key("GPT-4") == "gpt-4"


def test_normalize_strips_punctuation():
    # punctuation other than word chars collapses to spaces.
    assert normalize_alias_key("OpenAI, Inc.") == "openai inc"
    assert normalize_alias_key("Google, LLC") == "google llc"


def test_normalize_collapses_whitespace():
    assert normalize_alias_key("  ChatGPT   Model  ") == "chatgpt model"
    assert normalize_alias_key("ChatGPT\nmodel") == "chatgpt model"


def test_normalize_empty_returns_empty():
    assert normalize_alias_key("") == ""
    assert normalize_alias_key("   ") == ""
    # a string that collapses to nothing after punctuation stripping.
    assert normalize_alias_key("...") == ""


def test_normalize_chatgpt_fork_collapses():
    """the specific fork case that motivated the registry."""
    a = normalize_alias_key("ChatGPT")
    b = normalize_alias_key("ChatGPT (model)")
    assert a == b == "chatgpt"


# --- AliasEntry.is_type_compatible. ---


def _sample_entry(
    canonical="ChatGPT",
    canonical_type="model",
    compatible_types=("model", "tool", "product"),
    subdir="entities",
    aliases=("chat gpt",),
    description=(
        "OpenAI's conversational AI product powered by the GPT family "
        "of large language models."
    ),
    source="seed",
) -> AliasEntry:
    return AliasEntry(
        canonical_name=canonical,
        canonical_type=canonical_type,
        compatible_types=frozenset(compatible_types),
        description=description,
        aliases=tuple(aliases),
        subdir=subdir,
        source=source,
    )


def test_type_compat_exact_match():
    entry = _sample_entry()
    assert entry.is_type_compatible("model") is True
    assert entry.is_type_compatible("tool") is True


def test_type_compat_case_insensitive():
    entry = _sample_entry()
    assert entry.is_type_compatible("MODEL") is True
    assert entry.is_type_compatible("Tool") is True


def test_type_compat_empty_is_allowed():
    entry = _sample_entry()
    # missing type hint should never block a stage 0 anchor.
    assert entry.is_type_compatible("") is True
    assert entry.is_type_compatible("   ") is True


def test_type_compat_mismatch_returns_false():
    entry = _sample_entry()
    assert entry.is_type_compatible("person") is False
    assert entry.is_type_compatible("benchmark") is False


# --- AliasRegistry.lookup. ---


def test_lookup_hits_canonical_name():
    reg = AliasRegistry()
    reg.add(_sample_entry())
    entry = reg.lookup("ChatGPT", type_hint="tool")
    assert entry is not None
    assert entry.canonical_name == "ChatGPT"


def test_lookup_hits_alias():
    reg = AliasRegistry()
    reg.add(_sample_entry(aliases=("chat gpt", "chatgpt model")))
    entry = reg.lookup("chat gpt", type_hint="tool")
    assert entry is not None
    assert entry.canonical_name == "ChatGPT"


def test_lookup_collapses_parens():
    reg = AliasRegistry()
    reg.add(_sample_entry())
    entry = reg.lookup("ChatGPT (model)", type_hint="model")
    assert entry is not None
    assert entry.canonical_name == "ChatGPT"


def test_lookup_returns_none_on_miss():
    reg = AliasRegistry()
    reg.add(_sample_entry())
    assert reg.lookup("Anthropic", type_hint="organization") is None
    assert reg.lookup("", type_hint="model") is None


def test_lookup_returns_none_on_type_mismatch():
    reg = AliasRegistry()
    reg.add(_sample_entry())
    # 'person' is not in compatible_types -> no anchor.
    assert reg.lookup("ChatGPT", type_hint="person") is None


def test_lookup_empty_type_hint_matches():
    reg = AliasRegistry()
    reg.add(_sample_entry())
    entry = reg.lookup("ChatGPT", type_hint="")
    assert entry is not None


# --- AliasRegistry.add / override. ---


def test_add_does_not_overwrite_by_default():
    reg = AliasRegistry()
    reg.add(_sample_entry(canonical_type="model"))
    reg.add(_sample_entry(canonical_type="tool"), overwrite=False)
    entry = reg.lookup("ChatGPT", type_hint="tool")
    assert entry is not None
    assert entry.canonical_type == "model"  # first write wins.


def test_add_overwrite_replaces_entry():
    reg = AliasRegistry()
    reg.add(_sample_entry(canonical_type="model"))
    reg.add(_sample_entry(canonical_type="tool"), overwrite=True)
    entry = reg.lookup("ChatGPT", type_hint="tool")
    assert entry is not None
    assert entry.canonical_type == "tool"


def test_alias_never_shadows_canonical():
    """an alias registration cannot overwrite an existing canonical key."""
    reg = AliasRegistry()
    reg.add(_sample_entry(canonical="Claude", canonical_type="model"))
    # register a different entity whose alias is "Claude" - must not
    # silently reroute lookups for Claude to this new entity.
    reg.add(
        _sample_entry(
            canonical="Anthropic Claude",
            canonical_type="product",
            aliases=("claude",),
        )
    )
    entry = reg.lookup("Claude", type_hint="model")
    assert entry is not None
    assert entry.canonical_name == "Claude"


# --- default_registry / seed loading. ---


def test_default_registry_has_seed_entries():
    reg = default_registry()
    # the seed file ships with ~150 entries. any non-zero count proves
    # the seed loader is wired up and the json file is parseable.
    assert len(reg) > 100


def test_default_registry_has_chatgpt():
    reg = default_registry()
    entry = reg.lookup("ChatGPT", type_hint="model")
    assert entry is not None
    assert entry.canonical_name == "ChatGPT"
    assert entry.source == "seed"


def test_default_registry_fork_case_collapses():
    """the exact failure mode that motivated the whole system."""
    reg = default_registry()
    tool_side = reg.lookup("ChatGPT", type_hint="tool")
    model_side = reg.lookup("ChatGPT (model)", type_hint="model")
    assert tool_side is not None
    assert model_side is not None
    assert tool_side.canonical_name == model_side.canonical_name == "ChatGPT"


def test_default_registry_openai_aliases():
    reg = default_registry()
    for name in ("OpenAI", "OpenAI, Inc.", "openai"):
        entry = reg.lookup(name, type_hint="organization")
        assert entry is not None, f"no hit for {name!r}"
        assert entry.canonical_name == "OpenAI"


def test_default_registry_python_polysem_type_guard():
    """Python the language must not resolve to an entry tagged as animal.

    this is the academic EL canonical example of type-constraint
    disambiguation. if our seed tier only registers Python as a
    programming language, a 'Python' mention with type 'animal' must
    fall through (return None) so the 5-stage pipeline can fork it.
    """
    reg = default_registry()
    entry = reg.lookup("Python", type_hint="animal")
    # either we have no python entry at all, or we have one whose
    # compatible_types does not include 'animal'. either way, the
    # lookup must refuse to anchor it.
    assert entry is None


# --- registry serialization roundtrip. ---


def test_alias_entry_json_roundtrip():
    original = _sample_entry()
    roundtripped = AliasEntry.from_json(original.to_json(), source="seed")
    assert roundtripped.canonical_name == original.canonical_name
    assert roundtripped.canonical_type == original.canonical_type
    assert roundtripped.compatible_types == original.compatible_types
    assert roundtripped.description == original.description
    assert roundtripped.aliases == original.aliases
    assert roundtripped.subdir == original.subdir


def test_save_runtime_only_writes_promoted():
    reg = AliasRegistry()
    reg.add(_sample_entry(canonical="ChatGPT", source="seed"))
    reg.add(
        _sample_entry(canonical="GPT-4", source="promoted"),
        overwrite=True,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "runtime.json"
        reg.save_runtime(path)
        data = json.loads(path.read_text())
    assert data["version"] == 1
    names = [e["canonical_name"] for e in data["entries"]]
    assert "GPT-4" in names
    assert "ChatGPT" not in names  # seed entries are never written.


# --- promote_from_wiki. ---


def _write_page(
    directory: Path,
    stem: str,
    tags: list[str],
    sources: list[str],
    body: str,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.md"
    src_list = ", ".join(sources)
    tag_list = ", ".join(tags)
    path.write_text(
        "---\n"
        f"type: entity\n"
        f"tags: [{tag_list}]\n"
        f"sources: [{src_list}]\n"
        "created: 2024-01-01\n"
        "updated: 2024-01-01\n"
        "---\n"
        "\n"
        f"# {stem}\n"
        "\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def test_promote_from_wiki_skips_thin_pages():
    """a single-source page must NOT promote."""
    reg = AliasRegistry()
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki = Path(tmpdir)
        _write_page(
            wiki / "entities",
            "NewTool",
            tags=["tool"],
            sources=["Paper A"],
            body=(
                "A distinctive new tool mentioned in a single paper "
                "with a rich, specific description that is clearly "
                "not context-local."
            ),
        )
        added = reg.promote_from_wiki(wiki, min_sources=3)
    assert added == 0


def test_promote_from_wiki_promotes_stable_pages():
    """a page with enough sources and a rich description promotes."""
    reg = AliasRegistry()
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki = Path(tmpdir)
        _write_page(
            wiki / "entities",
            "StableTool",
            tags=["tool"],
            sources=["Paper A", "Paper B", "Paper C"],
            body=(
                "A well-established tool used across many recent papers "
                "for large-scale entity linking and canonical name "
                "disambiguation in knowledge bases."
            ),
        )
        added = reg.promote_from_wiki(wiki, min_sources=3)
    assert added == 1
    entry = reg.lookup("StableTool", type_hint="tool")
    assert entry is not None
    assert entry.source == "promoted"


def test_promote_from_wiki_respects_min_sources_gate():
    """the default gate is 3 sources; 2 must not promote."""
    reg = AliasRegistry()
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki = Path(tmpdir)
        _write_page(
            wiki / "entities",
            "TwoSourceTool",
            tags=["tool"],
            sources=["Paper A", "Paper B"],
            body=(
                "A rich and specific description of a tool that is "
                "clearly not context-local and has enough text to be "
                "considered informative by any reasonable reader."
            ),
        )
        added = reg.promote_from_wiki(wiki, min_sources=MIN_SOURCES_FOR_PROMOTION)
    assert added == 0


def test_promote_from_wiki_never_overrides_seed():
    """a wiki page with the canonical name of a seed entry must not
    overwrite the seed tier even if it has more sources."""
    reg = default_registry()
    original_entry = reg.lookup("ChatGPT", type_hint="model")
    assert original_entry is not None
    assert original_entry.source == "seed"
    with tempfile.TemporaryDirectory() as tmpdir:
        wiki = Path(tmpdir)
        # write a page that would otherwise qualify for promotion.
        _write_page(
            wiki / "entities",
            "ChatGPT",
            tags=["tool"],
            sources=["Paper A", "Paper B", "Paper C", "Paper D"],
            body=(
                "A locally-defined version of ChatGPT that should not "
                "be allowed to clobber the curated seed entry from the "
                "registry. This description is deliberately long enough "
                "to pass the promotion gate."
            ),
        )
        reg.promote_from_wiki(wiki, min_sources=3)
    # seed entry must still be present unchanged.
    entry = reg.lookup("ChatGPT", type_hint="model")
    assert entry is not None
    assert entry.source == "seed"
    assert entry.description == original_entry.description


# --- test runner. ---


def main() -> int:
    tests = [
        v for k, v in globals().items()
        if k.startswith("test_") and callable(v)
    ]
    passed = failed = 0
    failures: list[tuple[str, BaseException]] = []

    for fn in tests:
        name = fn.__name__
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 — test runner.
            failed += 1
            failures.append((name, e))
            print(f"  FAIL  {name}: {e}")
        else:
            passed += 1
            print(f"  ok    {name}")

    print()
    print(f"  {passed} passed, {failed} failed (of {passed + failed})")

    if failures:
        print()
        print("  first failure traceback:")
        import traceback
        name, exc = failures[0]
        print(f"  {name}:")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
