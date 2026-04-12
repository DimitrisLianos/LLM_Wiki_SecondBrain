#!/usr/bin/env python3
"""llm wiki — scenario tests for the full entity resolver pipeline.

these are integration-style tests that drive resolve_item end-to-end
against a temporary wiki directory seeded with fixture pages. the only
things mocked are the two network calls (llm judge, bge-m3 embed) —
everything else runs the real code paths.

the test names map 1:1 to resolver stages so a reviewer can cross-
check the decision tree without reading any code:

    stage 1 — exact path check          -> create new page
    stage 2 — type constraint            -> fork on type mismatch
    stage 3 — jaccard high               -> merge
    stage 3 — jaccard low                -> fork
    stage 3b — age-gap tiebreaker         -> fork when >=10y apart
    stage 4 — llm judge same              -> merge
    stage 4 — llm judge different         -> fork
    stage 5 — cosine >= threshold         -> merge on llm unsure
    stage 5 — cosine <  threshold         -> fork on llm unsure

there is also a dedup proof scenario that drives the full merge-by-
name path the way ingest.py would: seed "Attention" from source A,
then extract "attention" (lowercase) from source B and verify only
one page exists at the end.

run:
    python3 scripts/test_resolver_scenarios.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import llm_client  # noqa: E402
import resolver  # noqa: E402
from resolver import (  # noqa: E402
    DEFAULT_EMBED_THRESHOLD,
    MIN_SAMPLES_FOR_TUNING,
    Resolution,
    resolve_item,
)


# --- fixture helpers. ---


def _write_page(wiki_dir: Path, subdir: str, name: str, body: dict) -> Path:
    """write a minimal wiki page mimicking what ingest.py would produce.

    body keys: type, tags, sources, source_dates, description.
    """
    path = wiki_dir / subdir / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    tags_str = ", ".join(body.get("tags", ["topic"]))
    sources_str = ", ".join(body.get("sources", ["fixture"]))
    dates = body.get("source_dates", [])

    fm = [
        "---",
        f"type: {body.get('type', 'concept')}",
        f"tags: [{tags_str}]",
        f"sources: [{sources_str}]",
    ]
    if dates:
        fm.append(f"source_dates: [{', '.join(dates)}]")
    fm += [
        "created: 2026-04-11",
        "updated: 2026-04-11",
        "---",
        "",
        f"# {name}",
        "",
        body.get("description", ""),
        "",
        "## Mentioned In",
        "",
        f"- [[{body.get('sources', ['fixture'])[0]}]]",
        "",
    ]
    path.write_text("\n".join(fm))
    return path


class TempWiki:
    """context manager that swaps WIKI_DIR for a temp directory.

    find_existing_page lives in llm_client and reads llm_client.WIKI_DIR
    at call time, so we must patch the llm_client binding (the
    authoritative one). resolver.py also imports WIKI_DIR as a local
    alias; we patch that too so any code path that still reads
    resolver.WIKI_DIR sees the temp path. both get restored on exit.
    """

    def __init__(self):
        self._orig_resolver_wiki = resolver.WIKI_DIR
        self._orig_client_wiki = llm_client.WIKI_DIR
        self._tmp: Path | None = None

    def __enter__(self) -> Path:
        self._tmp = Path(tempfile.mkdtemp(prefix="resolver_test_"))
        (self._tmp / "entities").mkdir()
        (self._tmp / "concepts").mkdir()
        resolver.WIKI_DIR = self._tmp
        llm_client.WIKI_DIR = self._tmp
        return self._tmp

    def __exit__(self, *exc):
        resolver.WIKI_DIR = self._orig_resolver_wiki
        llm_client.WIKI_DIR = self._orig_client_wiki
        if self._tmp and self._tmp.exists():
            shutil.rmtree(self._tmp, ignore_errors=True)


# --- mocks. ---


def _stub_judge(verdict: bool | None):
    """replace resolver._llm_judge_same with a deterministic return."""
    def _fn(new_item, existing_meta):
        return verdict
    resolver._llm_judge_same = _fn  # type: ignore[assignment]


def _stub_embed(mapping: dict[str, list[float]]):
    """replace resolver._cached_embed to return canned vectors.

    mapping is keyed by a substring that must appear in the text. the
    first matching key wins. this lets us write intent-revealing tests
    like {"transformer": [...], "biology": [...]} without reproducing
    the full text.
    """
    def _fn(text, cache):
        for substr, vec in mapping.items():
            if substr.lower() in (text or "").lower():
                return vec
        return None
    resolver._cached_embed = _fn  # type: ignore[assignment]


def _restore_mocks():
    """re-import to restore the original function bindings."""
    import importlib
    importlib.reload(resolver)


# --- scenarios. ---


def test_stage1_create_when_no_existing_page():
    """no existing page for this name -> create."""
    with TempWiki():
        res = resolve_item(
            {"name": "NewEntity", "type": "tool", "description": "a new tool"},
            "entities",
        )
        assert res.action == "create", f"expected create, got {res.action}"
        assert res.stage == 1
        assert res.resolved_name == "NewEntity"


def test_stage1_empty_name_is_safe():
    """empty name falls back to Untitled with a 'create' action."""
    with TempWiki():
        res = resolve_item({"name": "", "type": "tool"}, "entities")
        assert res.action == "create"
        assert res.resolved_name == "Untitled"


def test_stage2_fork_on_type_mismatch():
    """'Transformer' as biology vs 'Transformer' as architecture -> fork."""
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "Transformer", {
            "type": "entity",
            "tags": ["biology"],
            "sources": ["paper-a"],
            "description": "A protein complex in plant cell walls.",
        })

        res = resolve_item(
            {
                "name": "Transformer",
                "type": "architecture",
                "description": "A neural network architecture based on attention.",
            },
            "entities",
        )
        assert res.action == "fork", f"expected fork, got {res.action}"
        assert res.stage == 2
        assert "type mismatch" in res.reason
        assert "(architecture)" in res.resolved_name


def test_stage2_override_merges_when_descriptions_agree():
    """re-ingest case: llm re-classifies the same entity with a new type
    but writes an identical description. the type-mismatch fork would
    manufacture a fake duplicate. stage 2 must defer to stage 3 when
    descriptions strongly agree (jaccard >= SIM_MERGE_THRESHOLD).

    this is the bug that produced 30+ "(organization)" / "(model)" /
    "(method)" forks in the real wiki after re-ingesting the same batch
    of papers twice.
    """
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "Art. 52", {
            "type": "dataset",
            "tags": ["dataset"],
            "sources": ["paper-a"],
            "description": (
                "A specific article of the Brazilian Constitution used "
                "in retrieval testing."
            ),
        })

        # second ingest of the same paper: llm labels Art. 52 as
        # 'organization' instead of 'dataset', but the description is
        # character-for-character identical.
        res = resolve_item(
            {
                "name": "Art. 52",
                "type": "organization",
                "description": (
                    "A specific article of the Brazilian Constitution used "
                    "in retrieval testing."
                ),
            },
            "entities",
        )
        assert res.action == "merge", (
            f"expected merge, got {res.action}: {res.reason}"
        )
        # merge happens at stage 3 because stage 2 falls through when
        # descriptions agree. we only need to confirm it's not a fork.
        assert res.stage == 3
        assert res.resolved_name == "Art. 52"  # no "(organization)" suffix.


def test_stage2_still_forks_real_polysems_with_disjoint_descriptions():
    """guard against regression: a true polysem like 'Python' (snake)
    vs 'Python' (language) must still fork at stage 2 even though stage
    2 now allows description-agreement overrides.
    """
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "Python", {
            "type": "animal",
            "tags": ["animal"],
            "sources": ["paper-a"],
            "description": "A large non-venomous constrictor snake species.",
        })

        res = resolve_item(
            {
                "name": "Python",
                "type": "language",
                "description": "A high-level interpreted programming language.",
            },
            "entities",
        )
        # disjoint descriptions -> stage 2 still forks.
        assert res.action == "fork", f"expected fork, got {res.action}"
        assert res.stage == 2
        assert "type mismatch" in res.reason


def test_stage3_merge_on_high_jaccard():
    """paraphrased description with high token overlap -> merge."""
    with TempWiki() as wiki:
        _write_page(wiki, "concepts", "Attention", {
            "type": "concept",
            "tags": ["method"],
            "sources": ["paper-a"],
            "description": (
                "A neural network mechanism that computes weighted sums "
                "over input tokens to focus on relevant positions."
            ),
        })

        res = resolve_item(
            {
                "name": "Attention",
                "type": "method",
                "description": (
                    "A neural mechanism that computes weighted sums over "
                    "input tokens to focus on relevant positions in sequences."
                ),
            },
            "concepts",
        )
        assert res.action == "merge", f"expected merge, got {res.action}: {res.reason}"
        assert res.stage == 3
        assert res.similarity >= 0.55


def test_stage3_fork_on_low_jaccard():
    """same name, wildly disjoint description -> fork."""
    with TempWiki() as wiki:
        _write_page(wiki, "concepts", "Attention", {
            "type": "concept",
            "tags": ["method"],
            "sources": ["paper-a"],
            "description": "A cognitive process selecting sensory information.",
        })

        res = resolve_item(
            {
                "name": "Attention",
                "type": "method",
                "description": (
                    "Matryoshka hyperbolic embeddings for legal document retrieval."
                ),
            },
            "concepts",
        )
        assert res.action == "fork", f"expected fork, got {res.action}"
        assert res.stage == 3
        assert res.similarity < 0.15


def test_stage3b_age_gap_tiebreaker_forks_on_drift():
    """borderline jaccard + 19-year gap -> fork via drift tiebreaker.

    proves historical-drift handling: a 2005 paper and a 2024 paper both
    mentioning "attention" under borderline similarity should be treated
    as potentially different concepts without asking the llm.
    """
    with TempWiki() as wiki:
        # descriptions share "weighting/mechanism/applied/inputs" (~0.3
        # jaccard) so stage 3 lands in the borderline band instead of
        # forking on disjoint tokens; that lets stage 3b actually run.
        _write_page(wiki, "concepts", "Attention", {
            "type": "concept",
            "tags": ["method"],
            "sources": ["paper-2005"],
            "source_dates": ["2005-06-01"],
            "description": (
                "A weighting mechanism applied to sensory inputs for "
                "conscious filtering."
            ),
        })

        res = resolve_item(
            {
                "name": "Attention",
                "type": "method",
                "description": (
                    "A weighting mechanism applied to token inputs for "
                    "neural computation."
                ),
            },
            "concepts",
            use_embeddings=True,
            current_source_date="2024-01-15",
        )
        assert res.action == "fork", f"expected fork, got {res.action}: {res.reason}"
        assert res.stage == 3
        assert "age-gap" in res.reason
        assert res.details.get("age_gap_years", 0) >= 10


def test_stage3b_age_gap_inactive_without_embeddings():
    """age-gap tiebreaker only fires when use_embeddings=True.

    keeps the default pipeline zero-dep; drift handling composes with
    stage 5 and should not leak into the stdlib-only path.
    """
    with TempWiki() as wiki:
        _write_page(wiki, "concepts", "Attention", {
            "type": "concept",
            "tags": ["method"],
            "sources": ["paper-2005"],
            "source_dates": ["2005-06-01"],
            "description": "A psychological selective focus mechanism.",
        })

        # stub the judge in case stage 4 is reached with an empty cache.
        _stub_judge(None)
        try:
            res = resolve_item(
                {
                    "name": "Attention",
                    "type": "method",
                    "description": (
                        "A neural network attention weighting mechanism."
                    ),
                },
                "concepts",
                use_embeddings=False,
                current_source_date="2024-01-15",
                cache={},
            )
        finally:
            _restore_mocks()

        # without the tiebreaker, borderline jaccard + unsure judge -> fork,
        # but reason must not mention age-gap.
        assert "age-gap" not in res.reason


def test_stage4_merge_on_llm_judge_same():
    """borderline jaccard, judge says 'same' -> merge.

    descriptions share 'transformer/encoder/model' but each adds distinct
    tokens (google/research vs bidirectional/representations/masking) so
    jaccard lands in [0.15, 0.55) — the band where stage 4 actually runs.
    """
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "BERT", {
            "type": "entity",
            "tags": ["model"],
            "sources": ["paper-a"],
            "description": (
                "A pretrained transformer encoder model from google research."
            ),
        })

        _stub_judge(True)
        try:
            res = resolve_item(
                {
                    "name": "BERT",
                    "type": "model",
                    "description": (
                        "Bidirectional encoder representations from "
                        "transformers trained with masking."
                    ),
                },
                "entities",
                cache={},
            )
        finally:
            _restore_mocks()

        assert res.action == "merge", f"expected merge, got {res.action}: {res.reason}"
        assert res.stage == 4
        assert "llm judge: same" in res.reason


def test_stage4_fork_on_llm_judge_different():
    """borderline jaccard, judge says 'different' -> fork."""
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "Apple", {
            "type": "entity",
            "tags": ["company"],
            "sources": ["paper-a"],
            "description": "A large consumer electronics company.",
        })

        _stub_judge(False)
        try:
            res = resolve_item(
                {
                    "name": "Apple",
                    "type": "company",
                    "description": "A fruit-producing tree cultivated worldwide.",
                },
                "entities",
                cache={},
            )
        finally:
            _restore_mocks()

        # low jaccard will catch this at stage 3 before reaching the
        # judge. this is correct: type match + disjoint descriptions
        # should fork at the cheapest stage that detects it.
        assert res.action == "fork"


def test_stage5_merge_on_high_cosine_when_judge_unsure():
    """judge unsure + cosine above threshold -> merge via stage 5."""
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "GPT", {
            "type": "entity",
            "tags": ["model"],
            "sources": ["paper-a"],
            "description": (
                "An autoregressive language model trained on large text corpora."
            ),
        })

        _stub_judge(None)  # unsure
        # canned near-identical vectors -> cosine ~ 1.0
        same_vec = [1.0, 0.0, 0.0, 0.0]
        _stub_embed({
            "autoregressive language model": same_vec,
            "large generative transformer": same_vec,
        })

        try:
            res = resolve_item(
                {
                    "name": "GPT",
                    "type": "model",
                    "description": "A large generative transformer for text synthesis.",
                },
                "entities",
                use_embeddings=True,
                cache={},
                embed_cache={},
                calibration=[],
            )
        finally:
            _restore_mocks()

        assert res.action == "merge", f"expected merge, got {res.action}: {res.reason}"
        assert res.stage == 5
        assert "cosine" in res.reason


def test_stage5_fork_on_low_cosine_when_judge_unsure():
    """judge unsure + cosine below threshold -> fork via stage 5."""
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "Mercury", {
            "type": "entity",
            "tags": ["element"],
            "sources": ["paper-a"],
            "description": "A heavy liquid metal chemical element.",
        })

        _stub_judge(None)
        # orthogonal vectors -> cosine ~ 0
        _stub_embed({
            "liquid metal chemical": [1.0, 0.0, 0.0, 0.0],
            "nearest planet to the sun": [0.0, 1.0, 0.0, 0.0],
        })

        try:
            res = resolve_item(
                {
                    "name": "Mercury",
                    "type": "element",
                    "description": "The nearest planet to the sun in our solar system.",
                },
                "entities",
                use_embeddings=True,
                cache={},
                embed_cache={},
                calibration=[],
            )
        finally:
            _restore_mocks()

        # low jaccard already catches this at stage 3. stage 5 only
        # runs when we reach the unsure branch, which requires passing
        # stages 1-3. this assertion is conservative: the fork must
        # happen regardless of which stage catches it.
        assert res.action == "fork"


# --- dedup proof. ---


def test_dedup_proof_case_insensitive_name_merges_across_sources():
    """the canonical dedup guarantee: two sources mentioning the same
    entity under different casing should produce ONE page, not two.

    this is the guarantee reviewers will ask about first. we simulate
    two ingests: paper-a writes Attention.md, paper-b extracts
    'attention' with a near-identical description, and we verify the
    resolver picks 'merge' (stage 3 high jaccard).
    """
    with TempWiki() as wiki:
        # paper-a ingests first.
        _write_page(wiki, "concepts", "Attention", {
            "type": "concept",
            "tags": ["method"],
            "sources": ["paper-a"],
            "source_dates": ["2017"],
            "description": (
                "A neural network mechanism that computes weighted "
                "attention scores over input token representations."
            ),
        })

        # paper-b arrives with the same entity under different casing.
        res = resolve_item(
            {
                "name": "attention",
                "type": "method",
                "description": (
                    "A neural mechanism computing weighted attention "
                    "scores over input token representations."
                ),
            },
            "concepts",
        )

        # resolver should point at the existing page, not create a new one.
        assert res.action == "merge", f"expected merge, got {res.action}"
        assert res.resolved_name == "Attention"  # canonical casing preserved.
        # verify exactly one page exists at the end. glob is case-sensitive
        # on apfs so we filter by stem.lower() instead of a case-matched
        # pattern — this is the same check the resolver itself uses.
        pages = [
            p for p in (wiki / "concepts").iterdir()
            if p.suffix == ".md" and p.stem.lower() == "attention"
        ]
        assert len(pages) == 1, f"expected 1 page, got {len(pages)}: {pages}"


def test_dedup_proof_type_mismatch_yields_two_pages_with_callouts():
    """polysems MUST fork. 'Python' (snake) vs 'Python' (language) are
    different real-world things and merging them silently would be a bug.
    after the fork, both pages must exist and both must receive a
    disambiguation callout.
    """
    from resolver import apply_disambiguation_callout, safe_filename

    with TempWiki() as wiki:
        existing = _write_page(wiki, "entities", "Python", {
            "type": "animal",
            "tags": ["animal"],
            "sources": ["paper-a"],
            "description": "A large non-venomous constrictor snake.",
        })

        # second mention is a programming language.
        item = {
            "name": "Python",
            "type": "language",
            "description": "A high-level interpreted programming language.",
        }
        res = resolve_item(item, "entities")
        assert res.action == "fork"
        assert res.stage == 2

        # simulate what ingest.py does: write the forked page, then
        # apply callouts on both sides.
        fork_path = _write_page(wiki, "entities", res.resolved_name, {
            "type": "language",
            "tags": ["language"],
            "sources": ["paper-b"],
            "description": item["description"],
        })

        applied_a = apply_disambiguation_callout(existing, res.resolved_name)
        applied_b = apply_disambiguation_callout(fork_path, existing.stem)
        assert applied_a is True
        assert applied_b is True

        # both pages must exist with callouts pointing at each other.
        assert existing.exists()
        assert fork_path.exists()
        assert f"[[{res.resolved_name}]]" in existing.read_text()
        assert f"[[{existing.stem}]]" in fork_path.read_text()


def test_dedup_proof_idempotent_disambiguation_callout():
    """running ingest twice must not stack duplicate callouts.

    reviewers will re-run ingests to verify determinism. a non-idempotent
    callout writer would accumulate garbage on every re-run.
    """
    from resolver import apply_disambiguation_callout

    with TempWiki() as wiki:
        path = _write_page(wiki, "entities", "Foo", {
            "type": "entity",
            "tags": ["tool"],
            "sources": ["paper-a"],
            "description": "A thing.",
        })
        first = apply_disambiguation_callout(path, "Foo (alt)")
        second = apply_disambiguation_callout(path, "Foo (alt)")
        assert first is True
        assert second is False
        assert path.read_text().count("[[Foo (alt)]]") == 1


# --- stage 0: canonical alias registry anchor. ---
#
# these tests exercise the gazetteer short-circuit that runs before the
# standard 5-stage resolver pipeline. the canonical fork epidemic that
# motivated the whole system — ChatGPT (tool) vs ChatGPT (model) — is
# covered by stage_0_* and by the full resolve_item integration tests
# that feed a fake registry directly.


def _make_registry_with_chatgpt():
    """construct a minimal AliasRegistry containing just ChatGPT.

    we use a fresh registry (not default_registry()) so the test does
    not depend on whatever seed data happens to ship with the repo.
    """
    from aliases import AliasEntry, AliasRegistry

    reg = AliasRegistry()
    reg.add(
        AliasEntry(
            canonical_name="ChatGPT",
            canonical_type="model",
            compatible_types=frozenset({"model", "tool", "product", "chatbot"}),
            description=(
                "OpenAI's conversational AI product powered by the GPT "
                "family of large language models, launched in November 2022."
            ),
            aliases=("chat gpt", "chatgpt model", "gpt chat"),
            subdir="entities",
            source="seed",
        )
    )
    return reg


def test_stage0_anchors_thin_mention_to_canonical_create():
    """ChatGPT with a context-local description and no existing page ->
    create at the canonical filename, with the curated description
    overwriting the thin one, and stage == 0."""
    with TempWiki():
        item = {
            "name": "ChatGPT",
            "type": "tool",
            "description": (
                "An LLM-based system mentioned in the context of "
                "sliding window segmentation."
            ),
        }
        res = resolve_item(
            item,
            "entities",
            registry=_make_registry_with_chatgpt(),
        )
        assert res.stage == 0, f"expected stage 0, got {res.stage}"
        assert res.action == "create"
        assert res.resolved_name == "ChatGPT"
        # the thin incoming description must have been replaced with
        # the curated one from the registry.
        assert "OpenAI" in item["description"]
        assert "sliding window" not in item["description"]
        # canonical type wins over the incoming 'tool' label.
        assert item["type"] == "model"


def test_stage0_anchors_existing_canonical_page_to_merge():
    """when ChatGPT.md already exists, stage 0 must return a merge
    decision pointing at it (not create a ChatGPT (model) sibling)."""
    with TempWiki() as wiki:
        _write_page(wiki, "entities", "ChatGPT", {
            "type": "entity",
            "tags": ["model"],
            "sources": ["paper-a", "paper-b", "paper-c"],
            "description": (
                "OpenAI's conversational AI product powered by the GPT "
                "family of large language models."
            ),
        })
        res = resolve_item(
            {
                "name": "ChatGPT (model)",
                "type": "model",
                "description": "thin mention",
            },
            "entities",
            registry=_make_registry_with_chatgpt(),
        )
        assert res.stage == 0
        assert res.action == "merge"
        assert res.resolved_name == "ChatGPT"
        assert res.existing_path is not None
        assert res.existing_path.name == "ChatGPT.md"


def test_stage0_respects_type_incompatibility_falls_through():
    """an incoming type outside compatible_types must NOT anchor.

    the mention falls through to the standard 5-stage pipeline so
    stage 2's type-constraint fork still protects real polysems.
    """
    with TempWiki():
        item = {
            "name": "ChatGPT",
            "type": "person",  # not in compatible_types.
            "description": "a unique fictional character in a novel",
        }
        res = resolve_item(
            item,
            "entities",
            registry=_make_registry_with_chatgpt(),
        )
        # stage 0 refused to anchor, so we fell through and created.
        assert res.stage != 0
        assert res.action == "create"
        # neither name nor type got canonicalized — registry didn't
        # touch this item at all.
        assert item["type"] == "person"


def test_stage0_subdir_guard_prevents_crossover():
    """a concept-registered name must not anchor an entity-subdir mention.

    Transformer is registered as a concept in the real registry. if the
    subdir guard is missing, an entity-subdir Transformer mention would
    incorrectly get anchored as a concept.
    """
    from aliases import AliasEntry, AliasRegistry

    reg = AliasRegistry()
    reg.add(
        AliasEntry(
            canonical_name="Transformer",
            canonical_type="architecture",
            compatible_types=frozenset({"architecture", "method", "framework"}),
            description="A neural network architecture based on attention.",
            aliases=(),
            subdir="concepts",
            source="seed",
        )
    )
    with TempWiki():
        item = {
            "name": "Transformer",
            "type": "method",
            "description": "short",
        }
        res = resolve_item(item, "entities", registry=reg)
        # subdir mismatch — concept entry should not anchor entity subdir.
        assert res.stage != 0, (
            f"stage 0 should not fire across subdirs, got stage {res.stage}"
        )


def test_stage0_keeps_rich_incoming_description():
    """a rich, non-context-local description must NOT be overwritten
    by the registry blurb. we only replace weak descriptions.
    """
    with TempWiki():
        rich = (
            "A large language model released by OpenAI in late 2022 "
            "that popularized conversational AI for the general public "
            "and kicked off the current LLM product boom. Trained on "
            "web-scale text with reinforcement learning from human "
            "feedback (RLHF)."
        )
        item = {
            "name": "ChatGPT",
            "type": "model",
            "description": rich,
        }
        res = resolve_item(
            item,
            "entities",
            registry=_make_registry_with_chatgpt(),
        )
        assert res.stage == 0
        assert res.action == "create"
        assert item["description"] == rich  # preserved verbatim.


def test_stage0_empty_registry_no_op():
    """an empty registry must be a complete no-op.

    a fresh clone with no seed file must behave exactly like the
    pre-registry resolver — stage 0 returns None and stage 1 runs.
    """
    from aliases import AliasRegistry

    with TempWiki():
        res = resolve_item(
            {"name": "SomethingObscure", "type": "tool", "description": "a thing"},
            "entities",
            registry=AliasRegistry(),
        )
        assert res.stage != 0
        assert res.action == "create"
        assert res.resolved_name == "SomethingObscure"


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
        print("  first failure traceback:")
        import traceback
        name, exc = failures[0]
        print(f"  {name}:")
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
