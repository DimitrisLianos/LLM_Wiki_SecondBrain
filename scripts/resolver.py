#!/usr/bin/env python3
"""llm wiki — layered entity/concept resolver.

cross-document entity coreference: when extraction surfaces a name that
already lives in the wiki, we must decide whether the two mentions refer
to the same real-world thing (merge) or to distinct things that happen to
share a name (fork). raw case-insensitive name matching quietly collapses
polysems like "transformer" (the ml architecture) and "transformer" (the
biology term) into a single incoherent page.

this module implements a 5-stage pipeline. cheap deterministic checks
run first; the llm is only consulted in a narrow borderline band; an
optional embedding pass over bge-m3 is gated behind --use-embeddings.

stages:
    1. exact path check   — no existing page -> create.
    2. type constraint    — types differ -> fork.
    3. description jaccard— high sim -> merge, low sim -> fork.
       also: age-gap tiebreaker (hamilton et al. acl 2016, 10y plateau).
    4. llm pairwise judge — borderline only, cached to disk.
    5. embedding re-rank  — bge-m3 cosine with f1-tuned threshold.

the resolver is stdlib-only by default. bge-m3 is reached over http so
no python bindings are required. stages 1-4 are zero-dep.

academic grounding:
    - lesk (1986) — gloss-overlap word sense disambiguation.
    - otsu (1979) — automatic thresholding from bimodal histograms.
    - fawcett (2006) — roc analysis / f1-optimal threshold sweep.
    - ferragina & scaiella (cikm 2010) — tagme entity linking.
    - hoffart et al. (emnlp 2011) — aida graph-based disambiguation.
    - hamilton, leskovec, jurafsky (acl 2016) — diachronic word embeddings
      show semantic drift plateaus at ~10 years for content words.
    - botha, shan, gillick (emnlp 2020) — entity linking in 100+ languages.
    - wu et al. (emnlp 2020) — blink biencoder entity linking.
    - de cao et al. (tacl 2022) — mgenre multilingual entity linking.
    - narayan et al. (vldb 2022) — llm-as-entity-resolution.
    - sevgili et al. (semantic web 2022) — neural entity linking survey.
    - chen et al. (2024) — bge-m3 multilingual, multi-granularity embeddings.
    - edge et al. (2024) — graphrag element summarization.

the resolver does not claim to solve cross-document coreference in the
general case. it aims to prevent the obvious failures (type collisions,
disjoint descriptions, cross-lingual / historical-drift coreference)
without paying the infrastructure cost of a full entity linking system.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path

from aliases import AliasEntry, AliasRegistry, default_registry, normalize_alias_key
from llm_client import (
    BASE_DIR, FRONTMATTER_RE, WIKI_DIR, ContextOverflowError,
    EmbeddingUnavailableError, embed, find_existing_page, llm, safe_filename,
)

# --- thresholds. ---

# jaccard >= this: treat as the same entity without asking the llm.
SIM_MERGE_THRESHOLD = 0.55

# jaccard < this: treat as distinct without asking the llm.
SIM_FORK_THRESHOLD = 0.15

# anything in [SIM_FORK_THRESHOLD, SIM_MERGE_THRESHOLD) goes to stage 4.

# cap description length sent to the llm judge.
JUDGE_DESC_CHARS = 500

# cap tokens returned by the judge (single-word reply expected).
JUDGE_MAX_TOKENS = 16

# --- stage 5 (embedding) constants. ---
#
# default cosine floor for "same thing" when we have no labeled data yet.
# 0.75 is a documented bge-m3 operating point for paraphrase detection
# (chen et al. 2024, table 6) and matches the typical sts-benchmark cut.
# once we collect MIN_SAMPLES_FOR_TUNING labeled pairs from stage 4, we
# switch to f1-optimal tuning (fawcett 2006).
DEFAULT_EMBED_THRESHOLD = 0.75

# minimum labeled samples before tuning kicks in. below this we use
# the static default; f1 sweeps over a handful of points produce
# misleading optima. bumped from 10 -> 20 after observing the real
# cache degenerate to 51 positives / 1 negative (imbalanced f1 picks
# a near-zero threshold that accepts everything).
MIN_SAMPLES_FOR_TUNING = 20

# separate floor on the rarer class. without at least this many
# negatives the roc curve is a single point and f1 is meaningless.
# we also require at least this many positives for symmetry. this
# is the gate that protects us from the "all positives" pathology
# that the total-sample gate alone lets through.
MIN_NEGATIVES = 5
MIN_POSITIVES = 5

# hamilton, leskovec, jurafsky (acl 2016) report that semantic drift for
# content words plateaus around 10-20 years. we use the low end as a
# conservative tiebreaker: when jaccard is borderline AND the two
# sources are >= 10 years apart, fork without calling the llm.
AGE_GAP_YEARS = 10

JUDGE_CACHE_PATH = BASE_DIR / "db" / "judge_cache.json"
EMBED_CACHE_PATH = BASE_DIR / "db" / "embed_cache.json"
CALIBRATION_CACHE_PATH = BASE_DIR / "db" / "resolver_calibration.json"


# --- tokenization. ---

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "that", "this", "these", "those", "it", "its",
    "he", "she", "they", "we", "you", "his", "her", "their", "our",
    "my", "me", "us", "them", "him", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "can",
    "not", "no", "yes", "which", "who", "what", "when", "where", "why",
    "how", "about", "into", "than", "then", "also", "such", "one", "two",
})

# ordered by length; first match wins so longer suffixes strip before
# shorter ones (e.g. "izing" before "ing").
_SUFFIXES = (
    "ational", "tional", "ization", "iveness", "fulness",
    "ousness", "ement", "ation", "ities", "ative",
    "tion", "ness", "ment", "ing", "ies", "ied", "ize", "ly",
    "ed", "es", "s",
)


def _stem(word: str) -> str:
    """naive english stemmer. drops common noun/verb suffixes only.

    this is not porter. it is a 20-line heuristic that exists so jaccard
    matches "routing" to "route" and "quantized" to "quantize" without
    pulling in a dependency. errs on the side of under-stemming.
    """
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)]
    return word


def _tokenize(text: str) -> frozenset[str]:
    """lowercase, split on non-alpha, drop stopwords and short tokens, stem."""
    if not text:
        return frozenset()
    words = re.findall(r"[a-zA-Z]+", text.lower())
    stems = {
        _stem(w) for w in words
        if len(w) >= 3 and w not in _STOP_WORDS
    }
    return frozenset(stems)


def description_similarity(a: str, b: str) -> float:
    """jaccard over stemmed content tokens. 0.0 = disjoint, 1.0 = identical.

    jaccard is insensitive to sentence length and stable under paraphrase
    because it treats the description as a bag of topic words. it is
    weak at synonymy (cf. stage 4 llm judge) but strong at separating
    obviously different domains.
    """
    tokens_a = _tokenize(a)
    tokens_b = _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """cosine similarity between two dense vectors.

    returns 0.0 on empty input or length mismatch rather than raising,
    so the resolver can degrade gracefully when the embed server is
    flaky. bge-m3 is already l2-normalized on output, so in practice
    this is equivalent to a dot product, but we compute the full form
    for robustness against future model changes.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for va, vb in zip(a, b):
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


# --- embed cache. ---
#
# text -> vector is a pure function of the model, so caching by sha-1
# of the normalized text is sound. bge-m3 vectors are 1024 floats;
# a few thousand entries fit comfortably in json.

def _load_embed_cache() -> dict:
    """read the disk-persisted embed cache. empty dict on miss."""
    if not EMBED_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(EMBED_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_embed_cache(cache: dict) -> None:
    """write the embed cache back to disk (best-effort)."""
    try:
        EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EMBED_CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _embed_key(text: str) -> str:
    """stable cache key over the normalized text."""
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _cached_embed(text: str, cache: dict) -> list[float] | None:
    """fetch a vector from cache or call the embed server.

    returns None on embedding server failure so stage 5 can fall back
    to a safe default instead of crashing the whole ingest.
    """
    text = (text or "").strip()
    if not text:
        return None
    key = _embed_key(text)
    hit = cache.get(key)
    if isinstance(hit, list) and hit:
        return hit
    try:
        vec = embed(text)
    except EmbeddingUnavailableError:
        return None
    cache[key] = vec
    return vec


# --- calibration cache. ---
#
# every time stage 4 (llm judge) reaches a confident verdict AND we
# have a bge-m3 cosine score for the same pair, we store (cosine,
# verdict) as a labeled sample. once MIN_SAMPLES_FOR_TUNING samples
# accumulate, stage 5 switches from the static DEFAULT_EMBED_THRESHOLD
# to an f1-optimal threshold (fawcett 2006).
#
# the calibration cache is an append-only list because removing samples
# would require a rationale the poc doesn't need. the in-memory size is
# tiny — each sample is two numbers.

def _load_calibration_cache() -> list[dict]:
    """read labeled (cosine, verdict) pairs. empty list on miss."""
    if not CALIBRATION_CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CALIBRATION_CACHE_PATH.read_text())
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_calibration_cache(samples: list[dict]) -> None:
    """write labeled samples back to disk (best-effort)."""
    try:
        CALIBRATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CALIBRATION_CACHE_PATH.write_text(json.dumps(samples, indent=2))
    except OSError:
        pass


def _append_calibration_sample(
    samples: list[dict],
    cosine: float,
    verdict: bool,
) -> None:
    """add a labeled pair to the in-memory list. caller persists later."""
    samples.append({"cosine": float(cosine), "same": bool(verdict)})


def _f1_optimal_threshold(samples: list[dict]) -> float:
    """sweep candidate thresholds and pick the f1-maximizing one.

    this is the textbook approach from fawcett (2006) — for each unique
    cosine value, compute precision/recall treating "predict same if
    cosine >= t", and keep the t that maximizes f1. scikit-learn's
    precision_recall_curve uses the same construction.

    returns DEFAULT_EMBED_THRESHOLD when we don't have enough positive
    AND negative samples to compute a meaningful sweep. we enforce
    three gates before tuning:
      1. total samples >= MIN_SAMPLES_FOR_TUNING (volume).
      2. negatives >= MIN_NEGATIVES (rarer class floor).
      3. positives >= MIN_POSITIVES (symmetric floor).
    gate 2 is the one that protects us from the "51 positives /
    1 negative" pathology the real calibration cache hit — f1 on a
    single-negative sample is undefined and the sweep picks a near-
    zero threshold that accepts everything.
    """
    if len(samples) < MIN_SAMPLES_FOR_TUNING:
        return DEFAULT_EMBED_THRESHOLD
    positives = [s["cosine"] for s in samples if s.get("same")]
    negatives = [s["cosine"] for s in samples if not s.get("same")]
    if len(positives) < MIN_POSITIVES or len(negatives) < MIN_NEGATIVES:
        return DEFAULT_EMBED_THRESHOLD

    candidates = sorted({s["cosine"] for s in samples})
    best_threshold = DEFAULT_EMBED_THRESHOLD
    best_f1 = -1.0
    for t in candidates:
        tp = sum(1 for c in positives if c >= t)
        fp = sum(1 for c in negatives if c >= t)
        fn = sum(1 for c in positives if c < t)
        if tp == 0:
            continue
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        if precision + recall <= 0:
            continue
        f1 = 2 * precision * recall / (precision + recall)
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = t
    return best_threshold


def _compute_adaptive_threshold() -> float:
    """load calibration samples and compute the f1-optimal cut."""
    return _f1_optimal_threshold(_load_calibration_cache())


# --- page metadata reader. ---

_TAGS_RE = re.compile(r"\[([^\]]*)\]")
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _year_from_date(value: str) -> int | None:
    """pull a 4-digit year out of a date string. tolerates iso and free-form."""
    if not value:
        return None
    match = _YEAR_RE.search(value)
    if match:
        year = int(match.group(0))
        if 1900 <= year <= 2100:
            return year
    return None


def _age_gap_years(dates_a: list[str], dates_b: list[str]) -> int:
    """largest year gap between two sets of source dates. 0 if undetermined.

    we use max-gap (not min-gap) because one document citing two eras
    should still trigger the drift check — e.g. a retrospective paper
    comparing 2005 and 2025 "attention" genuinely does span 20 years.
    """
    years_a = [y for y in (_year_from_date(d) for d in dates_a) if y is not None]
    years_b = [y for y in (_year_from_date(d) for d in dates_b) if y is not None]
    if not years_a or not years_b:
        return 0
    gap = max(abs(a - b) for a in years_a for b in years_b)
    return gap


def read_page_meta(path: Path) -> dict:
    """parse yaml frontmatter and grab the first content paragraph.

    returns a dict with keys: name, type, description, source_dates.
    source_dates is a list of strings pulled from the 'source_dates'
    frontmatter field (one per ingested source). missing fields are
    empty strings / empty lists.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}

    meta: dict = {"name": path.stem, "source_dates": []}

    # strip frontmatter and pull fields we care about.
    fm_match = FRONTMATTER_RE.match(text)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
        text = text[fm_match.end():]

    # parse source_dates as a list if present.
    raw_dates = meta.get("source_dates", "")
    if isinstance(raw_dates, str) and raw_dates.strip():
        list_match = _TAGS_RE.search(raw_dates)
        if list_match:
            meta["source_dates"] = [
                d.strip() for d in list_match.group(1).split(",") if d.strip()
            ]
        else:
            meta["source_dates"] = [raw_dates.strip()]
    else:
        meta["source_dates"] = []

    # derive type from the first tag (ingest.py writes [entity_type] or [topic]).
    tags_str = meta.get("tags", "")
    tag_match = _TAGS_RE.search(tags_str)
    if tag_match:
        first_tag = tag_match.group(1).split(",")[0].strip()
        meta["type"] = first_tag

    # first non-heading, non-blockquote paragraph is the description.
    for para in re.split(r"\n\n+", text):
        stripped = para.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith(">"):
            continue
        meta["description"] = stripped
        break
    meta.setdefault("description", "")
    return meta


# --- llm pairwise judge. ---

_JUDGE_PROMPT = """You are disambiguating whether two extracted items refer to the same real-world entity or concept.

Item A (already in the wiki):
- name: {name_a}
- type: {type_a}
- description: {desc_a}

Item B (newly extracted):
- name: {name_b}
- type: {type_b}
- description: {desc_b}

Reply with exactly ONE word:
- "same"      — they refer to the same real-world thing.
- "different" — they are distinct things that happen to share a name.
- "unsure"    — there is not enough information to decide.
"""


def _llm_judge_same(
    new_item: dict,
    existing_meta: dict,
) -> bool | None:
    """ask the llm whether two items are coreferent.

    returns True (same), False (different), or None (unsure / error).
    temperature is 0 for determinism; max_tokens is tiny because we only
    need a single word.
    """
    try:
        reply = llm(
            _JUDGE_PROMPT.format(
                name_a=existing_meta.get("name", ""),
                type_a=existing_meta.get("type", "unknown"),
                desc_a=(existing_meta.get("description", "") or "")[:JUDGE_DESC_CHARS],
                name_b=new_item.get("name", ""),
                type_b=new_item.get("type", "unknown"),
                desc_b=(new_item.get("description", "") or "")[:JUDGE_DESC_CHARS],
            ),
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=0.0,
            timeout=60,
        )
    except ContextOverflowError:
        # oversized prompt — no recovery, defer to later stages.
        return None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # transient network issue. log and defer; don't crash the resolver.
        print(f"    (llm judge network error: {type(e).__name__}: {e})")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        # malformed llama.cpp response. log and defer.
        print(f"    (llm judge parse error: {type(e).__name__}: {e})")
        return None

    reply = (reply or "").strip().lower()
    if reply.startswith("same"):
        return True
    if reply.startswith("different"):
        return False
    return None


# --- judge cache (sqlite would be overkill for a sidecar json file). ---

def load_judge_cache() -> dict:
    """load the disk-persisted judge cache. returns empty dict on miss."""
    if not JUDGE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(JUDGE_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_judge_cache(cache: dict) -> None:
    """write the judge cache back to disk."""
    try:
        JUDGE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        JUDGE_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))
    except OSError:
        pass  # the cache is an optimization, not correctness-critical.


def _judge_cache_key(new_item: dict, existing_meta: dict) -> str:
    """stable order-insensitive hash over (name, type, truncated description).

    we hash a sorted pair so that resolve(A, B) and resolve(B, A) hit the
    same cache entry. descriptions are truncated to keep the key bounded.
    """
    def _norm(item: dict) -> tuple[str, str, str]:
        return (
            (item.get("name", "") or "").strip().lower(),
            (item.get("type", "") or "").strip().lower(),
            (item.get("description", "") or "").strip().lower()[:200],
        )

    pair = tuple(sorted([_norm(new_item), _norm(existing_meta)]))
    return hashlib.sha1(repr(pair).encode("utf-8")).hexdigest()


# --- filename / lookup helpers. ---
# ``safe_filename`` and ``find_existing_page`` moved to llm_client so
# ingest and resolver share a single implementation. they used to be
# duplicated here.


def _fork_name(base: str, qualifier: str) -> str:
    """generate a disambiguated page name. 'Transformer' + 'architecture'
    -> 'Transformer (architecture)'. repeated forks get a numeric suffix
    rather than stacking parenthetical qualifiers.
    """
    qualifier = (qualifier or "alt").strip().lower()
    candidate = f"{base} ({qualifier})"
    return safe_filename(candidate)


# --- resolution decision object. ---

@dataclass(frozen=True)
class Resolution:
    """outcome of resolving a single extracted item.

    action:
        'create' — no existing page, write a new one at resolved_name.
        'merge'  — existing page at existing_path, add the new source ref.
        'fork'   — existing page is a different entity; write a new page
                   at resolved_name and add a disambiguation callout to
                   both pages.
    """
    action: str
    resolved_name: str
    original_name: str
    existing_path: Path | None = None
    reason: str = ""
    similarity: float = 0.0
    stage: int = 0
    details: dict = field(default_factory=dict)


# --- stage 0: canonical alias anchor. ---
#
# stage 0 runs before any similarity math. it consults a gazetteer of
# curated + auto-promoted canonical entities (see aliases.py) and, if
# the incoming mention normalizes to a known entry with a compatible
# type, short-circuits the resolver to the canonical page. this is the
# core fix for the cross-document proper-noun fork epidemic: without
# an anchor, the resolver re-decides "is this the same ChatGPT?" on
# every ingest from per-source evidence alone, and thin context-local
# descriptions reliably produce forks.
#
# academic grounding:
#   - wu et al. (emnlp 2020) — blink: entity linking via fixed catalog.
#   - ayoola et al. (naacl 2022) — refined: typed entity embeddings
#     anchored to wikidata.
#   - bunescu & pasca (eacl 2006) — encyclopedic knowledge for ned.
#   - ferragina & scaiella (cikm 2010) — tagme (already cited above).
#
# design notes:
#   - the type compatibility check is soft: a missing incoming type
#     still anchors, but a type conflict (e.g. 'Python' tagged 'person'
#     hitting the language entry) falls through to the 5-stage pipeline
#     so stage 2 can fork real polysems.
#   - if the canonical page doesn't exist yet on disk we return a
#     create-with-canonical-name resolution, which is how we migrate a
#     forked name like 'ChatGPT (model)' into the canonical 'ChatGPT'
#     on the next ingest.
#   - if the canonical page already exists we return a merge. the
#     description override and mention injection are handled by the
#     caller (ingest._write_item_pages) as they already are for
#     stage-3/4 merges. stage 0 only decides which page to target.


def _stage_0_alias_anchor(
    name: str,
    type_hint: str,
    subdir: str,
    item: dict,
    registry: AliasRegistry,
) -> Resolution | None:
    """check the alias registry and short-circuit to a canonical page.

    returns a Resolution when the mention resolves to a known entry,
    or None when the caller should fall through to stages 1-5.
    """
    if registry is None or len(registry) == 0:
        return None

    entry = registry.lookup(name, type_hint=type_hint)
    if entry is None:
        return None

    # the registry has one subdir per entry. if the caller is resolving
    # against a different subdir we cannot anchor: the entry lives
    # somewhere else on disk and we'd be creating a phantom page.
    if entry.subdir and entry.subdir != subdir:
        return None

    canonical_name = entry.canonical_name
    canonical_safe = safe_filename(canonical_name)
    canonical_path = WIKI_DIR / subdir / f"{canonical_safe}.md"

    # when the extracted description is thin/generic the registry's
    # curated description is a strict upgrade. we mutate the item in
    # place so downstream page writes get the canonical text. this is
    # consistent with how the rest of resolve_item treats item['name'].
    incoming_desc = (item.get("description") or "").strip()
    if entry.description and (
        len(incoming_desc) < 30
        or _looks_context_local(incoming_desc, item.get("name", ""))
    ):
        item["description"] = entry.description

    # normalize the type too - the registry's canonical_type is the
    # ground truth, and using it here prevents a downstream Stage 2
    # type-mismatch fork if a later ingest comes in with a different type.
    if entry.canonical_type:
        item["type"] = entry.canonical_type

    if canonical_path.exists():
        return Resolution(
            action="merge",
            resolved_name=canonical_safe,
            original_name=name,
            existing_path=canonical_path,
            similarity=1.0,
            reason=f"stage 0: alias anchor -> {canonical_name!r} ({entry.source})",
            stage=0,
            details={"alias_source": entry.source},
        )

    # canonical page doesn't exist yet. create it at the canonical
    # name rather than at whatever surface form the extractor produced.
    return Resolution(
        action="create",
        resolved_name=canonical_safe,
        original_name=name,
        similarity=1.0,
        reason=f"stage 0: alias anchor -> create {canonical_name!r} ({entry.source})",
        stage=0,
        details={"alias_source": entry.source},
    )


_CONTEXT_LOCAL_PATTERNS = (
    re.compile(r"\bmentioned\s+(?:in|as|for|when)\b", re.IGNORECASE),
    re.compile(r"\breferenced\s+(?:in|as|for|when)\b", re.IGNORECASE),
    re.compile(r"\bused\s+(?:in|as)\s+(?:the|a|an)\s+(?:context|example)\b", re.IGNORECASE),
    re.compile(r"\bappears?\s+in\s+(?:the|a|an)\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+context\s+of\b", re.IGNORECASE),
)


def _looks_context_local(description: str, name: str) -> bool:
    """true when a description reads as context-local rather than identity.

    used by stage 0 to decide whether to override an incoming
    description with the registry's curated one. also consumed by
    ingest._is_generic_description (shared pattern pool).
    """
    if not description:
        return False
    for rx in _CONTEXT_LOCAL_PATTERNS:
        if rx.search(description):
            return True
    # descriptions that are literally just a restatement of the name
    # plus a filler noun are always context-local.
    if name and len(description) < 40:
        lowered = description.lower()
        if name.lower() in lowered and any(
            filler in lowered
            for filler in ("mentioned", "described", "referenced", "used")
        ):
            return True
    return False


# --- main entry point. ---

def resolve_item(
    item: dict,
    subdir: str,
    cache: dict | None = None,
    use_embeddings: bool = False,
    current_source_date: str = "",
    embed_cache: dict | None = None,
    calibration: list[dict] | None = None,
    registry: AliasRegistry | None = None,
) -> Resolution:
    """resolve one extracted item against the wiki. runs stages 0-5.

    args:
        item:                extraction dict with keys 'name', 'type',
                             'description'.
        subdir:              'entities' or 'concepts'.
        cache:               optional shared judge cache. callers pass one
                             dict across a batch to avoid duplicate llm calls.
        use_embeddings:      enables stage 5 (bge-m3 cosine on borderline
                             and llm-unsure cases) and age-gap tiebreaker.
        current_source_date: iso date of the source being ingested. used
                             for the historical-drift tiebreaker.
        embed_cache:         optional shared embed cache dict.
        calibration:         optional shared calibration samples list.
        registry:            optional AliasRegistry. defaults to the
                             module-level singleton. pass an isolated
                             registry in tests to keep state scoped.

    returns:
        a Resolution describing what the caller should do. the caller is
        responsible for renaming item['name'] to resolution.resolved_name
        before writing pages — this is how name changes back-propagate to
        the source page's citation list without a second rewrite pass.
    """
    name = (item.get("name") or "").strip()
    if not name:
        return Resolution(
            action="create",
            resolved_name="Untitled",
            original_name=name,
            reason="empty name",
            stage=1,
        )

    # --- stage 0: canonical alias anchor. ---
    # before running any similarity math, check the gazetteer for a
    # canonical form. if the incoming mention normalizes to a known
    # entity with a compatible type, route directly to that page.
    # this prevents the recurring 'ChatGPT' vs 'ChatGPT (model)' fork
    # epidemic that happens when different sources tag the same
    # proper noun with different types.
    #
    # when registry is None we skip stage 0 entirely. the ingest
    # pipeline passes the registry explicitly via _resolve_items, so
    # this is mostly a test-isolation knob — unit tests that want to
    # exercise stages 1-5 on a clean slate can call resolve_item()
    # without a registry and be guaranteed stage 0 stays out of their
    # way. callers that want the default gazetteer should pass
    # default_registry() explicitly.
    if registry is not None and len(registry) > 0:
        new_type_for_anchor = (item.get("type") or "").strip().lower()
        anchor = _stage_0_alias_anchor(
            name=name,
            type_hint=new_type_for_anchor,
            subdir=subdir,
            item=item,
            registry=registry,
        )
        if anchor is not None:
            return anchor

    safe = safe_filename(name)
    existing_path = find_existing_page(subdir, name)

    # --- stage 1: exact path check. ---
    if not existing_path.exists():
        return Resolution(
            action="create",
            resolved_name=safe,
            original_name=name,
            stage=1,
        )

    existing_meta = read_page_meta(existing_path)
    new_type = (item.get("type") or "").strip().lower()
    existing_type = (existing_meta.get("type") or "").strip().lower()

    # description jaccard is needed for both stage 2 (override) and stage 3.
    # compute once up front so we can use it as a type-mismatch tiebreaker.
    new_desc = item.get("description", "") or ""
    existing_desc = existing_meta.get("description", "") or ""
    sim = description_similarity(new_desc, existing_desc)

    # --- stage 2: type constraint. ---
    # ingest.py writes a type tag for entities. concepts get a topical tag
    # (method/framework/pattern/...) so this still discriminates within
    # the concepts subdirectory. ignore the constraint only when one side
    # has no type information at all.
    #
    # description-agreement override: near-identical descriptions under
    # different llm-assigned types are almost always classification noise,
    # not real polysemy. the type-mismatch fork would manufacture a fake
    # duplicate (see re-ingest reports where "Aedes aegypti" forks to
    # "Aedes aegypti (model)" because the llm re-classified the same
    # biological species across runs). only fork on type mismatch when
    # the descriptions also disagree — real polysems ("Python" snake vs
    # "Python" language) always have disjoint descriptions.
    if new_type and existing_type and new_type != existing_type:
        if sim < SIM_MERGE_THRESHOLD:
            return Resolution(
                action="fork",
                resolved_name=_fork_name(safe, new_type),
                original_name=name,
                existing_path=existing_path,
                similarity=sim,
                reason=f"type mismatch ({new_type} vs {existing_type})",
                stage=2,
            )
        # otherwise fall through to stage 3; the high jaccard will merge.

    # --- stage 3: description similarity. ---

    if sim >= SIM_MERGE_THRESHOLD:
        return Resolution(
            action="merge",
            resolved_name=existing_path.stem,
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason=f"jaccard={sim:.2f} >= {SIM_MERGE_THRESHOLD}",
            stage=3,
        )

    if sim < SIM_FORK_THRESHOLD:
        return Resolution(
            action="fork",
            resolved_name=_fork_name(safe, new_type or "alt"),
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason=f"jaccard={sim:.2f} < {SIM_FORK_THRESHOLD}",
            stage=3,
        )

    # --- stage 3b: age-gap tiebreaker (hamilton et al. acl 2016). ---
    # borderline jaccard + >=10 year gap between sources -> fork without
    # asking the llm. 10 years is the plateau from figure 3 of the
    # paper; earlier cutoffs leave too many coincidental merges, later
    # cutoffs miss real drift. only active when use_embeddings is on,
    # because the age-gap tiebreaker composes with stage 5 and we want
    # the default pipeline to stay dependency-free.
    if use_embeddings and current_source_date:
        existing_dates = existing_meta.get("source_dates") or []
        gap = _age_gap_years([current_source_date], list(existing_dates))
        if gap >= AGE_GAP_YEARS:
            return Resolution(
                action="fork",
                resolved_name=_fork_name(safe, new_type or "alt"),
                original_name=name,
                existing_path=existing_path,
                similarity=sim,
                reason=f"age-gap {gap}y >= {AGE_GAP_YEARS}y (drift)",
                stage=3,
                details={"age_gap_years": gap},
            )

    # --- stage 4: llm pairwise judge (borderline only). ---
    # empty descriptions can't go through the judge meaningfully. the
    # safest default when both are blank is to merge — case-insensitive
    # name match plus matching type is already high precision.
    if not new_desc.strip() or not existing_desc.strip():
        return Resolution(
            action="merge",
            resolved_name=existing_path.stem,
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason="borderline with blank description -> name+type match",
            stage=4,
        )

    cache_is_external = cache is not None
    if cache is None:
        cache = load_judge_cache()

    key = _judge_cache_key(item, existing_meta)
    if key in cache:
        verdict = cache[key]
    else:
        verdict = _llm_judge_same(item, existing_meta)
        cache[key] = verdict
        if not cache_is_external:
            save_judge_cache(cache)

    # stage 5 is the slow path: only compute the bge-m3 cosine when we
    # either need it as a tiebreaker (judge unsure) or want to record a
    # labeled sample for future calibration (judge confident).
    cosine_score: float | None = None
    if use_embeddings and embed_cache is not None:
        vec_new = _cached_embed(new_desc, embed_cache)
        vec_old = _cached_embed(existing_desc, embed_cache)
        if vec_new is not None and vec_old is not None:
            cosine_score = cosine_similarity(vec_new, vec_old)

    # confident llm verdict -> record calibration sample (if we have a
    # cosine) and return. the judge is the ground-truth signal; stage 5
    # learns its threshold from stage 4's confident decisions.
    if verdict is True:
        if cosine_score is not None and calibration is not None:
            _append_calibration_sample(calibration, cosine_score, True)
        return Resolution(
            action="merge",
            resolved_name=existing_path.stem,
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason="llm judge: same",
            stage=4,
            details={"cosine": cosine_score} if cosine_score is not None else {},
        )

    if verdict is False:
        if cosine_score is not None and calibration is not None:
            _append_calibration_sample(calibration, cosine_score, False)
        return Resolution(
            action="fork",
            resolved_name=_fork_name(safe, new_type or "alt"),
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason="llm judge: different",
            stage=4,
            details={"cosine": cosine_score} if cosine_score is not None else {},
        )

    # --- stage 5: embedding re-rank (llm unsure). ---
    # the llm returned None ("unsure" or an error). fall back to the
    # bge-m3 cosine using an f1-tuned threshold. if embeddings are
    # unavailable we default to fork (forks are recoverable).
    if use_embeddings and cosine_score is not None:
        threshold = (
            _f1_optimal_threshold(calibration)
            if calibration is not None
            else DEFAULT_EMBED_THRESHOLD
        )
        if cosine_score >= threshold:
            return Resolution(
                action="merge",
                resolved_name=existing_path.stem,
                original_name=name,
                existing_path=existing_path,
                similarity=sim,
                reason=f"stage 5: cosine={cosine_score:.3f} >= {threshold:.3f}",
                stage=5,
                details={"cosine": cosine_score, "threshold": threshold},
            )
        return Resolution(
            action="fork",
            resolved_name=_fork_name(safe, new_type or "alt"),
            original_name=name,
            existing_path=existing_path,
            similarity=sim,
            reason=f"stage 5: cosine={cosine_score:.3f} < {threshold:.3f}",
            stage=5,
            details={"cosine": cosine_score, "threshold": threshold},
        )

    # unsure and embeddings disabled / unavailable: default to fork so
    # we never silently collapse polysems. forks are recoverable with a
    # lint pass; silent merges are not.
    return Resolution(
        action="fork",
        resolved_name=_fork_name(safe, new_type or "alt"),
        original_name=name,
        existing_path=existing_path,
        similarity=sim,
        reason="borderline, defaulting to fork",
        stage=4,
    )


# --- stage 5 public api (used by ingest for batch workflows). ---

def load_embed_cache() -> dict:
    """public alias for ingest to pass a shared embed cache."""
    return _load_embed_cache()


def save_embed_cache(cache: dict) -> None:
    """public alias for ingest to persist the embed cache."""
    _save_embed_cache(cache)


def load_calibration_cache() -> list[dict]:
    """public alias for ingest to pass a shared calibration list."""
    return _load_calibration_cache()


def save_calibration_cache(samples: list[dict]) -> None:
    """public alias for ingest to persist calibration samples."""
    _save_calibration_cache(samples)


# --- disambiguation callout injection. ---

_CALLOUT_MARKER = "> **Disambiguation:**"


def apply_disambiguation_callout(path: Path, sibling_name: str) -> bool:
    """insert a 'see also' callout pointing at a forked sibling page.

    idempotent: if the callout already references the sibling, this is a
    no-op. inserts the block after the title line. returns True if the
    page was modified.
    """
    if not path.exists() or not sibling_name:
        return False

    try:
        text = path.read_text()
    except OSError:
        return False

    sibling_link = f"[[{sibling_name}]]"
    if sibling_link in text:
        return False

    callout = (
        f"{_CALLOUT_MARKER} see also {sibling_link} — "
        f"distinct entry that shares this name.\n\n"
    )

    # drop in after the h1 title. if we can't find one, fall back to the
    # top of the file (after the frontmatter) so the callout is still visible.
    fm_match = FRONTMATTER_RE.match(text)
    body_start = fm_match.end() if fm_match else 0

    body = text[body_start:]
    title_match = re.search(r"^#[^\n]*\n", body, re.MULTILINE)
    if title_match:
        insert_at = body_start + title_match.end()
        new_text = text[:insert_at] + "\n" + callout + text[insert_at:]
    else:
        new_text = text[:body_start] + callout + body

    try:
        path.write_text(new_text)
    except OSError:
        return False
    return True
