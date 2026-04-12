#!/usr/bin/env python3
"""llm wiki — unit tests for the resolver module.

stdlib-only tests. no pytest, no mocks library, no network — only the
pure functions that can run without the llama.cpp or bge-m3 servers.

run:
    python3 scripts/test_resolver.py

each test is a plain function returning None on success. on failure the
inline assert raises and the runner prints the exception with a
traceback.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

# allow running from the project root.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from resolver import (  # noqa: E402
    DEFAULT_EMBED_THRESHOLD,
    MIN_SAMPLES_FOR_TUNING,
    AGE_GAP_YEARS,
    _age_gap_years,
    _f1_optimal_threshold,
    _fork_name,
    _stem,
    _tokenize,
    _year_from_date,
    cosine_similarity,
    description_similarity,
    read_page_meta,
    safe_filename,
)


# --- stem / tokenize. ---


def test_stem_drops_common_suffixes():
    assert _stem("running") == "runn"
    assert _stem("quantized") == "quantiz"
    assert _stem("cats") == "cat"
    assert _stem("ies") == "ies"  # too short to strip.
    assert _stem("hi") == "hi"  # too short to strip anything.


def test_tokenize_drops_stopwords_and_shorts():
    tokens = _tokenize("The quick brown fox is on the mat")
    # 'the', 'is', 'on' are stopwords; length >= 3 kept; stemmed.
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens
    assert "the" not in tokens
    assert "is" not in tokens
    # 'mat' is 3 chars and not a stopword -> kept.
    assert "mat" in tokens


def test_tokenize_is_case_insensitive():
    a = _tokenize("Transformer Architecture")
    b = _tokenize("transformer architecture")
    assert a == b


def test_tokenize_empty():
    assert _tokenize("") == frozenset()
    assert _tokenize(None) == frozenset()  # type: ignore[arg-type]


# --- description_similarity. ---


def test_description_similarity_identical():
    s = description_similarity(
        "A deep learning architecture for sequence modelling.",
        "A deep learning architecture for sequence modelling.",
    )
    assert s == 1.0


def test_description_similarity_disjoint():
    s = description_similarity(
        "A vegetable grown in cold climates.",
        "A microchip manufacturing process.",
    )
    assert s == 0.0


def test_description_similarity_partial_overlap():
    s = description_similarity(
        "A deep learning model for text classification.",
        "A deep learning framework for sequence prediction.",
    )
    assert 0.0 < s < 1.0


def test_description_similarity_empty_returns_zero():
    assert description_similarity("", "anything") == 0.0
    assert description_similarity("anything", "") == 0.0


# --- cosine_similarity. ---


def test_cosine_identical_vectors():
    v = [0.1, 0.2, 0.3, 0.4]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_orthogonal_vectors():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(a, b)) < 1e-9


def test_cosine_opposite_vectors():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-9


def test_cosine_length_mismatch_returns_zero():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_empty_returns_zero():
    assert cosine_similarity([], [1.0, 0.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], []) == 0.0


def test_cosine_zero_vector_returns_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# --- safe_filename / fork_name. ---


def test_safe_filename_strips_unsafe_chars():
    assert safe_filename("foo/bar") == "foobar"
    assert safe_filename("foo:bar?") == "foobar"
    assert safe_filename('foo"bar*') == "foobar"


def test_safe_filename_prevents_traversal():
    assert ".." not in safe_filename("..foo..bar..")


def test_safe_filename_empty_falls_back_to_untitled():
    assert safe_filename("") == "Untitled"
    assert safe_filename("???") == "Untitled"


def test_safe_filename_length_cap():
    long_name = "x" * 500
    assert len(safe_filename(long_name)) <= 120


def test_safe_filename_strips_trailing_punctuation():
    # "Culex spp." / "Culex spp" must collapse to one page. internal dots
    # ("Art. 52") are preserved because rstrip only touches trailing chars.
    assert safe_filename("Culex spp.") == "Culex spp"
    assert safe_filename("Culex spp") == "Culex spp"
    assert safe_filename("Max Sim.") == "Max Sim"
    assert safe_filename("Art. 52") == "Art. 52"
    assert safe_filename("Foo, ") == "Foo"
    assert safe_filename("Bar!") == "Bar"


def test_fork_name_adds_qualifier():
    assert _fork_name("Transformer", "architecture") == "Transformer (architecture)"
    assert _fork_name("Attention", "ML") == "Attention (ml)"


def test_fork_name_default_qualifier():
    assert _fork_name("Foo", "") == "Foo (alt)"


# --- year / age-gap helpers. ---


def test_year_from_date_iso():
    assert _year_from_date("2016-05-01") == 2016
    assert _year_from_date("2016") == 2016


def test_year_from_date_freeform():
    assert _year_from_date("May 2016") == 2016
    assert _year_from_date("published in the year 1999") == 1999


def test_year_from_date_invalid():
    assert _year_from_date("") is None
    assert _year_from_date("no digits") is None
    # 1899 is outside the 1900-2100 window.
    assert _year_from_date("1899-01-01") is None


def test_age_gap_matches_10y_plateau():
    gap = _age_gap_years(["2005-06-01"], ["2016-01-01"])
    assert gap == 11
    assert gap >= AGE_GAP_YEARS


def test_age_gap_single_year_no_drift():
    gap = _age_gap_years(["2020-01-01"], ["2021-06-01"])
    assert gap == 1
    assert gap < AGE_GAP_YEARS


def test_age_gap_empty_returns_zero():
    assert _age_gap_years([], ["2020"]) == 0
    assert _age_gap_years(["2020"], []) == 0


# --- f1-optimal threshold tuner. ---


def test_f1_tuning_below_minimum_uses_default():
    # fewer than MIN_SAMPLES_FOR_TUNING -> default.
    few = [{"cosine": 0.9, "same": True}, {"cosine": 0.3, "same": False}]
    assert _f1_optimal_threshold(few) == DEFAULT_EMBED_THRESHOLD


def test_f1_tuning_perfectly_separable():
    # clean split at 0.5.
    samples = []
    for i in range(MIN_SAMPLES_FOR_TUNING):
        samples.append({"cosine": 0.9, "same": True})
        samples.append({"cosine": 0.1, "same": False})
    t = _f1_optimal_threshold(samples)
    # should pick a threshold that places all positives above and all
    # negatives below -> the smallest positive value (0.9) wins.
    assert 0.1 < t <= 0.9


def test_f1_tuning_all_positive_uses_default():
    samples = [{"cosine": 0.9, "same": True}] * MIN_SAMPLES_FOR_TUNING
    assert _f1_optimal_threshold(samples) == DEFAULT_EMBED_THRESHOLD


def test_f1_tuning_all_negative_uses_default():
    samples = [{"cosine": 0.3, "same": False}] * MIN_SAMPLES_FOR_TUNING
    assert _f1_optimal_threshold(samples) == DEFAULT_EMBED_THRESHOLD


# --- read_page_meta with source_dates. ---


def test_read_page_meta_parses_source_dates():
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    )
    try:
        tmp.write(
            "---\n"
            "type: entity\n"
            "tags: [model]\n"
            "sources: [paper-a, paper-b]\n"
            "source_dates: [2005-06-01, 2024-03-15]\n"
            "---\n"
            "\n"
            "# Transformer\n"
            "\n"
            "A neural network architecture for sequence modelling.\n"
        )
        tmp.close()
        meta = read_page_meta(Path(tmp.name))
        assert meta["type"] == "model"
        assert meta["source_dates"] == ["2005-06-01", "2024-03-15"]
        assert "neural network" in meta["description"]
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def test_read_page_meta_missing_source_dates_field():
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8",
    )
    try:
        tmp.write(
            "---\n"
            "type: entity\n"
            "tags: [model]\n"
            "sources: [paper-a]\n"
            "---\n"
            "\n"
            "# Foo\n"
            "\n"
            "A description.\n"
        )
        tmp.close()
        meta = read_page_meta(Path(tmp.name))
        assert meta["source_dates"] == []
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def test_read_page_meta_nonexistent():
    assert read_page_meta(Path("/nonexistent/path.md")) == {}


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
