#!/usr/bin/env python3
"""llm wiki — unit tests for the ingest within-batch dedup helper.

covers the stem-normalized canonical key that collapses llm surface-form
noise (acronym expansions, trailing punctuation, plurals) without losing
real polysems (supervised vs unsupervised).

run:
    python3 scripts/test_ingest_dedup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import tempfile  # noqa: E402

from ingest import (  # noqa: E402
    _IMAGE_EXTS,
    _compute_file_hash,
    _cross_bucket_dedup,
    _dedup_items,
    _dedup_key,
    _find_source_page_for,
    _read_source_hash,
    _strip_trailing_parens,
    _type_home,
)


# --- _dedup_key: canonical form. ---


def test_dedup_key_strips_trailing_parenthetical():
    # the main fix for "URN" / "URN (Uniform Resource Name)".
    assert _dedup_key("URN") == _dedup_key("URN (Uniform Resource Name)")
    assert _dedup_key("RAG") == _dedup_key("RAG (Retrieval-Augmented Generation)")


def test_dedup_key_strips_trailing_punctuation():
    # "Culex spp." and "Culex spp" must collapse.
    assert _dedup_key("Culex spp.") == _dedup_key("Culex spp")
    assert _dedup_key("Max Sim.") == _dedup_key("Max Sim")


def test_dedup_key_is_case_insensitive():
    assert _dedup_key("Open Weights") == _dedup_key("open weights")
    assert _dedup_key("BERT") == _dedup_key("bert")


def test_dedup_key_collapses_plurals():
    # "Label Embedding" / "Label Embeddings" — classic llm noise.
    assert _dedup_key("Label Embedding") == _dedup_key("Label Embeddings")
    assert _dedup_key("Rigid Designator") == _dedup_key("Rigid Designators")
    assert _dedup_key("Microbiome-based intervention") == _dedup_key(
        "Microbiome-based interventions"
    )


def test_dedup_key_preserves_real_polysems():
    # prefix differences must NOT collapse. these are distinct concepts.
    assert _dedup_key("Supervised Learning") != _dedup_key("Unsupervised Learning")
    assert _dedup_key("Abiotic factors") != _dedup_key("Biotic factors")
    # and entity name collisions with different surface strings survive.
    assert _dedup_key("Python") == _dedup_key("Python")  # identity


def test_dedup_key_empty_name_falls_back():
    # entirely punctuation -> strip leaves empty -> fall back to original.
    # this ensures we never produce an empty key that would silently merge
    # unrelated items into a single bucket.
    assert _dedup_key("").strip() == ""
    # "(foo)" strips to empty, falls back to original lowercase.
    assert _dedup_key("(foo)") == "(foo)"


# --- _dedup_items: batch collation. ---


def test_dedup_items_collapses_acronym_expansion():
    items = [
        {"name": "URN", "type": "method", "description": "Uniform Resource Name."},
        {"name": "URN (Uniform Resource Name)", "type": "method",
         "description": "An identifier scheme for legal documents."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 1
    # richer description wins.
    sole = next(iter(target.values()))
    assert sole["description"] == "An identifier scheme for legal documents."
    # shorter surface name wins.
    assert sole["name"] == "URN"


def test_dedup_items_collapses_plural_variants():
    items = [
        {"name": "Label Embedding", "type": "method",
         "description": "Short form."},
        {"name": "Label Embeddings", "type": "method",
         "description": "A longer description of label embedding methods."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 1
    sole = next(iter(target.values()))
    assert "longer" in sole["description"]
    # shorter form wins as canonical surface name.
    assert sole["name"] == "Label Embedding"


def test_dedup_items_preserves_real_polysems():
    items = [
        {"name": "Supervised Learning", "type": "method",
         "description": "Learns from labeled training data."},
        {"name": "Unsupervised Learning", "type": "method",
         "description": "Learns from unlabeled data via clustering."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 2  # must NOT merge.


def test_dedup_items_case_insensitive_collapse():
    items = [
        {"name": "Open Weights", "type": "concept", "description": "Short."},
        {"name": "open weights", "type": "concept",
         "description": "A model whose parameter tensors are freely downloadable."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 1
    # the richer description won AND kept its name (both are same length).
    sole = next(iter(target.values()))
    assert "freely downloadable" in sole["description"]


def test_dedup_items_trailing_dot_collapse():
    items = [
        {"name": "Culex spp.", "type": "entity", "description": "Mosquito genus."},
        {"name": "Culex spp", "type": "entity",
         "description": "Mosquito genus that vectors several arboviruses."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 1
    sole = next(iter(target.values()))
    assert "arboviruses" in sole["description"]


def test_dedup_items_empty_name_is_skipped():
    items = [
        {"name": "", "type": "entity", "description": "ghost."},
        {"name": "Real", "type": "entity", "description": "actual."},
    ]
    target: dict = {}
    _dedup_items(items, target)
    assert len(target) == 1
    assert next(iter(target.values()))["name"] == "Real"


# --- stacked trailing parentheticals. ---


def test_strip_trailing_parens_single():
    assert _strip_trailing_parens("URN (Uniform Resource Name)") == "URN"


def test_strip_trailing_parens_stacked():
    # "Indoor residual spraying (IRS) (method)" — the llm sometimes stacks
    # qualifiers. BOTH parens must be stripped or the dedup key won't
    # collapse with "Indoor residual spraying (IRS)".
    assert (
        _strip_trailing_parens("Indoor residual spraying (IRS) (method)")
        == "Indoor residual spraying"
    )


def test_strip_trailing_parens_leaves_internal():
    # "Art. 52 (organization)" — internal "Art." has no paren, only the
    # trailing one gets stripped.
    assert _strip_trailing_parens("Art. 52 (organization)") == "Art. 52"


def test_dedup_key_collapses_stacked_parens():
    assert _dedup_key("Knockdown resistance (kdr)") == _dedup_key(
        "Knockdown resistance (kdr) (mechanism)"
    )
    assert _dedup_key("Insecticide-treated nets (ITNs)") == _dedup_key(
        "Insecticide-treated Nets (ITNs) (method)"
    )


# --- cross-bucket routing. ---


def test_type_home_routes_concept_types_to_concepts():
    for t in ("method", "theory", "framework", "pattern", "metric",
              "technique", "algorithm", "architecture"):
        assert _type_home(t) == "concepts", f"{t!r} should route to concepts"


def test_type_home_routes_entity_types_to_entities():
    for t in ("person", "organization", "tool", "dataset", "model", "benchmark"):
        assert _type_home(t) == "entities", f"{t!r} should route to entities"


def test_type_home_unknown_is_empty_string():
    assert _type_home("") == ""
    assert _type_home("concept") == ""  # not in either allowlist.
    assert _type_home(None) == ""  # type: ignore[arg-type]


def test_cross_bucket_dedup_routes_method_collision_to_concepts():
    # the exact pattern behind "Cosine Similarity" appearing in both
    # entities/ and concepts/ with the same tags. the method-typed copy
    # must end up in concepts; entities loses its copy.
    merged = {
        "entities": [
            {"name": "Cosine Similarity", "type": "metric",
             "description": "Short."},
        ],
        "concepts": [
            {"name": "Cosine Similarity", "type": "metric",
             "description": "The mathematical measure of vector closeness."},
        ],
    }
    _cross_bucket_dedup(merged, quiet=True)
    assert len(merged["entities"]) == 0
    assert len(merged["concepts"]) == 1
    # richer description wins.
    assert "mathematical" in merged["concepts"][0]["description"]


def test_cross_bucket_dedup_routes_person_collision_to_entities():
    # the mirror case: someone classified as both a concept and an entity.
    merged = {
        "entities": [
            {"name": "Alan Turing", "type": "person",
             "description": "British mathematician and computer science pioneer."},
        ],
        "concepts": [
            {"name": "Alan Turing", "type": "theory",
             "description": "Short."},
        ],
    }
    _cross_bucket_dedup(merged, quiet=True)
    assert len(merged["concepts"]) == 0
    assert len(merged["entities"]) == 1
    assert "mathematician" in merged["entities"][0]["description"]


def test_cross_bucket_dedup_leaves_non_colliding_items_alone():
    # different names in each bucket -> no collision, no changes.
    merged = {
        "entities": [
            {"name": "PyTorch", "type": "tool", "description": "A library."},
        ],
        "concepts": [
            {"name": "Gradient Descent", "type": "algorithm",
             "description": "An optimization method."},
        ],
    }
    _cross_bucket_dedup(merged, quiet=True)
    assert len(merged["entities"]) == 1
    assert len(merged["concepts"]) == 1


def test_cross_bucket_dedup_prefers_shorter_name_on_collision():
    merged = {
        "entities": [
            {"name": "URN (Uniform Resource Name)", "type": "method",
             "description": "Longer description of the URN scheme."},
        ],
        "concepts": [
            {"name": "URN", "type": "method", "description": "Short."},
        ],
    }
    _cross_bucket_dedup(merged, quiet=True)
    assert len(merged["entities"]) == 0
    assert len(merged["concepts"]) == 1
    # shorter name wins.
    assert merged["concepts"][0]["name"] == "URN"
    # richer description wins.
    assert "Longer" in merged["concepts"][0]["description"]


def test_cross_bucket_dedup_ambiguous_defaults_to_concepts():
    # both types are missing or outside the allowlists.
    # the default is concepts since methods/metrics are the common
    # over-emission case.
    merged = {
        "entities": [
            {"name": "Foo", "type": "", "description": "Desc A."},
        ],
        "concepts": [
            {"name": "Foo", "type": "", "description": "Desc B (longer)."},
        ],
    }
    _cross_bucket_dedup(merged, quiet=True)
    assert len(merged["entities"]) == 0
    assert len(merged["concepts"]) == 1
    assert "longer" in merged["concepts"][0]["description"]


# --- content-hash idempotency. ---


def test_compute_file_hash_is_deterministic():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"hello world")
        path = Path(f.name)
    try:
        h1 = _compute_file_hash(path)
        h2 = _compute_file_hash(path)
        assert h1 == h2
        # sha-256 produces 64 hex chars.
        assert len(h1) == 64
    finally:
        path.unlink(missing_ok=True)


def test_compute_file_hash_changes_on_edit():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"version one")
        path = Path(f.name)
    try:
        h1 = _compute_file_hash(path)
        path.write_bytes(b"version two")
        h2 = _compute_file_hash(path)
        assert h1 != h2
    finally:
        path.unlink(missing_ok=True)


def test_compute_file_hash_missing_file_returns_empty():
    assert _compute_file_hash(Path("/nonexistent/path/xyz.bin")) == ""


def test_read_source_hash_parses_frontmatter():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    ) as f:
        f.write(
            "---\n"
            "type: source\n"
            "tags: [paper]\n"
            "sources: [raw/test.pdf]\n"
            "source_hash: " + "a" * 64 + "\n"
            "---\n"
            "body."
        )
        path = Path(f.name)
    try:
        assert _read_source_hash(path) == "a" * 64
    finally:
        path.unlink(missing_ok=True)


def test_read_source_hash_missing_returns_empty():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    ) as f:
        f.write("---\ntype: source\n---\nbody.")
        path = Path(f.name)
    try:
        assert _read_source_hash(path) == ""
    finally:
        path.unlink(missing_ok=True)


# --- image dispatch. ---


def test_image_extensions_cover_common_formats():
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp",
                ".heic", ".heif", ".bmp", ".tiff", ".tif", ".svg"):
        assert ext in _IMAGE_EXTS, f"{ext} missing from _IMAGE_EXTS"


def test_image_extensions_excludes_text_formats():
    for ext in (".md", ".txt", ".pdf", ".xml", ".json", ".html"):
        assert ext not in _IMAGE_EXTS, f"{ext} should not be in _IMAGE_EXTS"


# --- test runner. ---


def main():
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
        except BaseException as e:
            failed += 1
            failures.append((name, e))
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"  ok    {name}")

    print()
    print(f"  {passed} passed, {failed} failed (of {passed + failed})")
    if failures:
        print()
        name, exc = failures[0]
        print(f"  first failure traceback:\n  {name}:")
        import traceback
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
