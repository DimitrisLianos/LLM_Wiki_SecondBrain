#!/usr/bin/env python3
"""llm wiki — canonical alias registry (gazetteer).

cross-document entity linking requires a fixed canonical catalog to
anchor mentions against. without one, every new ingest re-litigates
whether "ChatGPT" is the same ChatGPT, and thin per-source descriptions
("mentioned in the context of X") fail jaccard similarity -> forks.

this module maintains a two-tier registry:

    seed tier
        curated, committed to git at scripts/data/seed_aliases.json.
        ~150 high-value entries derived from wikidata canonical labels
        for ai labs, models, frameworks, and tech companies. the seed is
        the authoritative baseline that ships with the repo.

    runtime tier
        self-promoted, written to db/alias_registry.json (gitignored).
        after each ingest, entities with >=3 sources + a stable type +
        a non-generic description graduate into this tier automatically.

stage 0 in resolver.py consults both tiers before stage 1. if an incoming
mention normalizes to a known canonical form AND its type is in the
entry's compatible_types set, the resolver short-circuits to the
canonical page. the existing 5-stage pipeline runs only when stage 0
produces no hit — so non-gazetteer entities behave exactly as before.

academic grounding
    - wu et al. (emnlp 2020) — blink: dense entity linking with a fixed
      catalog (wikipedia) as the anchor space.
    - ayoola et al. (naacl 2022) — refined: entity linking with refined
      typed entity embeddings against a wikidata catalog.
    - ferragina & scaiella (cikm 2010) — tagme (already cited in resolver).
    - de cao et al. (tacl 2022) — mgenre: multilingual seq2seq entity
      linking over wikipedia titles.
    - shen, wang, han (ieee tkde 2015) — entity linking with a knowledge
      base: issues, techniques, solutions (section 3: candidate generation).
    - bunescu & pasca (eacl 2006) — using encyclopedic knowledge for
      named entity disambiguation (the foundational gazetteer paper).

industry precedent
    - spacy entityruler: token-based gazetteer with canonical entity ids.
    - stanford corenlp regexner: regex gazetteer mapping mentions to types.
    - wikidata surface forms: multilingual alias dictionary (the model
      this registry scales down from).
    - apple/google knowledge graph: curated brand alias tables for high-
      traffic proper nouns.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from llm_client import BASE_DIR, FRONTMATTER_RE, WIKI_DIR

# --- paths. ---

SEED_REGISTRY_PATH = BASE_DIR / "scripts" / "data" / "seed_aliases.json"
RUNTIME_REGISTRY_PATH = BASE_DIR / "db" / "alias_registry.json"

# --- promotion gates. ---

# a wiki page must cite at least this many distinct sources before its
# entry graduates into the runtime tier. three is the minimum that
# implies "this mention has been seen independently multiple times, not
# just re-ingested from one file". lower would promote one-off mentions;
# higher would never promote anything in a small personal wiki.
MIN_SOURCES_FOR_PROMOTION = 3

# promoted descriptions must be at least this many characters after
# stripping the canonical name. below this, the description is almost
# always a context-local snippet ("used in the X pipeline") and has no
# identity value.
MIN_DESCRIPTION_LENGTH = 30

# cap on description length stored in the registry. longer strings come
# from merge artifacts ("### From [[source]]"-style per-source sections)
# and should be canonicalized before promotion.
MAX_DESCRIPTION_LENGTH = 400

# max aliases kept per entry. seed entries can have more explicitly;
# promoted entries shouldn't accumulate unbounded surface forms.
MAX_ALIASES_PER_ENTRY = 12


# --- normalization. ---
#
# the normalization function is the load-bearing piece: every lookup
# passes through it, so it must be idempotent and stable across runs.
# we strip parenthetical qualifiers aggressively because the resolver's
# fork_name helper manufactures them ("ChatGPT (model)"), and round-trip
# stability means a forked name normalizes to the same key as the
# original - which is exactly what we need to detect and merge forks.

_QUALIFIER_RE = re.compile(r"\s*\([^)]*\)\s*")
_NON_WORD_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_alias_key(name: str) -> str:
    """aggressive lowercase normalization for registry lookup.

    examples::

        'ChatGPT'               -> 'chatgpt'
        'ChatGPT (model)'       -> 'chatgpt'
        'Chat-GPT'              -> 'chat-gpt'
        'OpenAI, Inc.'          -> 'openai inc'
        'GPT-4'                 -> 'gpt-4'
        'Claude 3.5 Sonnet'     -> 'claude 35 sonnet'

    the function is intentionally lossy: punctuation, parenthetical
    qualifiers, and multi-whitespace all collapse. this is the desired
    behavior for gazetteer lookup (we want 'ChatGPT' and 'ChatGPT (model)'
    to hit the same entry) but it means callers must keep the original
    surface form separately if they need it for display.
    """
    if not name:
        return ""
    s = name.strip().lower()
    s = _QUALIFIER_RE.sub(" ", s)
    s = _NON_WORD_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


# --- entry dataclass. ---


@dataclass(frozen=True)
class AliasEntry:
    """one canonical entry in the registry.

    immutable by design - mutating a loaded entry in place would
    desynchronize the reverse index. use ``dataclasses.replace`` to
    derive modified copies.
    """

    canonical_name: str
    canonical_type: str
    compatible_types: frozenset[str]
    description: str
    aliases: tuple[str, ...]
    subdir: str  # 'entities' or 'concepts'
    source: str  # 'seed' or 'promoted'

    def is_type_compatible(self, incoming_type: str) -> bool:
        """true if ``incoming_type`` is compatible with this entry.

        a missing, empty, or whitespace-only incoming type is always
        compatible - extraction sometimes omits the type field, and
        we don't want that to block a stage 0 anchor when the name
        itself is unambiguous.
        """
        if not incoming_type:
            return True
        normalized = incoming_type.strip().lower()
        if not normalized:
            return True
        return normalized in self.compatible_types

    def to_json(self) -> dict:
        """serialize to a plain dict for json storage."""
        return {
            "canonical_name": self.canonical_name,
            "canonical_type": self.canonical_type,
            "compatible_types": sorted(self.compatible_types),
            "description": self.description,
            "aliases": list(self.aliases),
            "subdir": self.subdir,
        }

    @classmethod
    def from_json(cls, data: dict, source: str) -> AliasEntry:
        """deserialize from a plain dict. unknown fields are ignored."""
        return cls(
            canonical_name=str(data.get("canonical_name", "")).strip(),
            canonical_type=str(data.get("canonical_type", "")).strip().lower(),
            compatible_types=frozenset(
                t.lower() for t in data.get("compatible_types", [])
                if isinstance(t, str) and t.strip()
            ),
            description=str(data.get("description", "")).strip(),
            aliases=tuple(
                a for a in data.get("aliases", [])
                if isinstance(a, str) and a.strip()
            ),
            subdir=str(data.get("subdir", "entities")).strip().lower(),
            source=source,
        )


# --- registry. ---


@dataclass
class AliasRegistry:
    """two-tier canonical alias registry.

    the seed tier is read-only at runtime - we never write to it from
    code. the runtime tier is the only thing that ``save()`` writes to.

    lookup is O(1) on the normalized key via ``_primary_index``, with a
    fallback O(1) check against ``_alias_index`` for non-canonical
    surface forms. the reverse indexes are rebuilt whenever entries are
    mutated via ``add`` or ``load``.
    """

    # primary index: normalized canonical_name -> entry.
    # runtime-tier entries override seed-tier entries with the same key.
    _primary_index: dict[str, AliasEntry] = field(default_factory=dict)

    # alias index: normalized alias -> primary key. an alias can only
    # point at one canonical form; later registrations overwrite earlier.
    _alias_index: dict[str, str] = field(default_factory=dict)

    # --- construction. ---

    @classmethod
    def load(cls) -> AliasRegistry:
        """load seed + runtime registries. runtime overrides seed.

        missing files are treated as empty - the registry degrades
        gracefully on a fresh clone (no seed file = empty registry, stage
        0 becomes a no-op, existing 5-stage pipeline handles everything).
        """
        registry = cls()
        if SEED_REGISTRY_PATH.exists():
            registry._load_from(SEED_REGISTRY_PATH, source="seed")
        if RUNTIME_REGISTRY_PATH.exists():
            registry._load_from(RUNTIME_REGISTRY_PATH, source="promoted")
        return registry

    def _load_from(self, path: Path, source: str) -> None:
        """load entries from a registry file. tolerates missing/corrupt
        files - we never crash ingest because the registry is unreadable.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            # surface the error but keep the registry usable.
            print(f"  warning: alias registry {path.name} unreadable: "
                  f"{type(e).__name__}: {e}")
            return

        entries = data.get("entries", {})
        if isinstance(entries, dict):
            iterable = entries.values()
        elif isinstance(entries, list):
            iterable = entries
        else:
            print(f"  warning: alias registry {path.name} has invalid "
                  f"'entries' field (expected dict or list, got "
                  f"{type(entries).__name__})")
            return

        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            entry = AliasEntry.from_json(raw, source=source)
            if not entry.canonical_name:
                continue
            self.add(entry, overwrite=True)

    # --- mutation. ---

    def add(self, entry: AliasEntry, overwrite: bool = False) -> None:
        """insert or update an entry. maintains both indexes.

        ``overwrite=False`` is the safe default: a later registration
        with the same key is dropped unless the caller explicitly asks
        for it. runtime-tier loading uses ``overwrite=True`` because
        the runtime tier is authoritative over the seed.
        """
        key = normalize_alias_key(entry.canonical_name)
        if not key:
            return
        if key in self._primary_index and not overwrite:
            return
        self._primary_index[key] = entry
        # register canonical name as its own alias so lookups by exact
        # canonical form hit the alias index too.
        self._alias_index[key] = key
        for alias in entry.aliases:
            alias_key = normalize_alias_key(alias)
            if alias_key and alias_key not in self._primary_index:
                # never let an alias shadow a canonical key from another
                # entry - that would silently re-route mentions to the
                # wrong entity.
                self._alias_index[alias_key] = key

    # --- lookup. ---

    def lookup(self, name: str, type_hint: str = "") -> AliasEntry | None:
        """return the canonical entry for a mention, or None.

        the lookup order is:

            1. normalize(name) directly matches a primary key.
            2. normalize(name) matches an alias in the alias index.
            3. no match -> None.

        ``type_hint`` is used as a soft filter: a match whose
        ``compatible_types`` set doesn't include the hint returns None
        so the resolver can fall through to the normal 5-stage pipeline
        and apply stage 2 type-constraint forking. this is how we avoid
        collapsing real polysems (e.g. 'Python' the language vs 'Python'
        the snake) via the registry.
        """
        key = normalize_alias_key(name)
        if not key:
            return None
        # try primary first, then alias.
        primary_key = self._alias_index.get(key)
        if primary_key is None:
            return None
        entry = self._primary_index.get(primary_key)
        if entry is None:
            return None
        if not entry.is_type_compatible(type_hint):
            return None
        return entry

    def __contains__(self, name: str) -> bool:
        return self.lookup(name) is not None

    def __len__(self) -> int:
        return len(self._primary_index)

    def all_entries(self) -> list[AliasEntry]:
        """snapshot of every entry. order is insertion order."""
        return list(self._primary_index.values())

    # --- self-promotion. ---

    def promote_from_wiki(
        self,
        wiki_dir: Path = WIKI_DIR,
        min_sources: int = MIN_SOURCES_FOR_PROMOTION,
    ) -> int:
        """scan the live wiki and auto-promote stable entities.

        a page graduates into the runtime tier when it meets all of:
          - page is in entities/ or concepts/ subdir.
          - page cites >= ``min_sources`` distinct sources.
          - page has a non-generic description (>= MIN_DESCRIPTION_LENGTH
            chars after stripping the name).
          - the normalized canonical key isn't already in the seed tier
            with a different canonical form - we never override seed
            entries with runtime ones.

        returns the number of entries added. the caller is responsible
        for calling ``save_runtime()`` afterwards.
        """
        added = 0
        for subdir in ("entities", "concepts"):
            d = wiki_dir / subdir
            if not d.exists():
                continue
            for page_path in d.glob("*.md"):
                entry = _entry_from_page(page_path, subdir, min_sources)
                if entry is None:
                    continue
                key = normalize_alias_key(entry.canonical_name)
                existing = self._primary_index.get(key)
                if existing is not None and existing.source == "seed":
                    # seed wins. never let runtime data override curated.
                    continue
                self.add(entry, overwrite=True)
                added += 1
        return added

    def save_runtime(self, path: Path = RUNTIME_REGISTRY_PATH) -> None:
        """persist the promoted tier to disk.

        only promoted entries are written - seed entries are read from
        scripts/data/seed_aliases.json on every load, so writing them
        back would duplicate curated data into a gitignored file.
        """
        promoted = [
            entry.to_json()
            for entry in self._primary_index.values()
            if entry.source == "promoted"
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": promoted,
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


# --- page scanning helpers (for promote_from_wiki). ---


_SOURCES_FIELD_RE = re.compile(
    r"^sources:\s*\[([^\]]*)\]\s*$",
    re.MULTILINE,
)
_TAGS_FIELD_RE = re.compile(
    r"^tags:\s*\[([^\]]*)\]\s*$",
    re.MULTILINE,
)


def _entry_from_page(
    page_path: Path,
    subdir: str,
    min_sources: int,
) -> AliasEntry | None:
    """derive an AliasEntry from a wiki page, or None if ineligible.

    the parsing is intentionally regex-based rather than yaml-based
    because the rest of the codebase has no yaml dependency and the
    frontmatter format is stable and narrow.
    """
    try:
        text = page_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    frontmatter = match.group(1)

    sources_match = _SOURCES_FIELD_RE.search(frontmatter)
    if not sources_match:
        return None
    sources = [s.strip() for s in sources_match.group(1).split(",") if s.strip()]
    if len(sources) < min_sources:
        return None

    tags_match = _TAGS_FIELD_RE.search(frontmatter)
    tags = []
    if tags_match:
        tags = [t.strip().strip("'\"") for t in tags_match.group(1).split(",")]
    canonical_type = tags[0].lower() if tags else ""
    if not canonical_type:
        return None

    # body description: first paragraph after the h1 that isn't a
    # callout. the per-source "### From [[...]]" sections are skipped
    # because their text is context-local to one source.
    body = text[match.end():]
    description = _first_body_paragraph(body)
    if len(description) < MIN_DESCRIPTION_LENGTH:
        return None
    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH].rsplit(" ", 1)[0] + "..."

    canonical_name = page_path.stem
    # strip a trailing '(qualifier)' from the canonical name so a
    # promoted fork doesn't leak its fork-name into the registry.
    clean_name = _QUALIFIER_RE.sub(" ", canonical_name).strip()
    if not clean_name:
        clean_name = canonical_name

    return AliasEntry(
        canonical_name=clean_name,
        canonical_type=canonical_type,
        compatible_types=frozenset({canonical_type}),
        description=description,
        aliases=(),
        subdir=subdir,
        source="promoted",
    )


_CALLOUT_RE = re.compile(r"^\s*>\s*\*\*Disambiguation\*\*", re.MULTILINE)
_PER_SOURCE_HEADER_RE = re.compile(r"^###\s+From\s+\[\[", re.MULTILINE)
_H1_RE = re.compile(r"^#\s+[^\n]*\n", re.MULTILINE)
_MENTIONED_IN_RE = re.compile(r"^##\s+Mentioned\s+In", re.MULTILINE | re.IGNORECASE)


def _first_body_paragraph(body: str) -> str:
    """extract the first real description paragraph from a page body.

    skips the h1, disambiguation callouts, per-source headers, and the
    "Mentioned In" tail. returns the first paragraph of actual prose or
    an empty string if nothing eligible is found.
    """
    if not body:
        return ""

    # drop the h1 line.
    body = _H1_RE.sub("", body, count=1)

    # cut off everything from the first per-source header or "Mentioned In".
    earliest = len(body)
    for rx in (_PER_SOURCE_HEADER_RE, _MENTIONED_IN_RE):
        m = rx.search(body)
        if m is not None and m.start() < earliest:
            earliest = m.start()
    body = body[:earliest]

    # strip callout lines.
    body = _CALLOUT_RE.sub("", body)

    # first non-empty paragraph.
    for para in body.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        # skip leftover blockquote fragments.
        if p.startswith(">"):
            continue
        # collapse internal whitespace.
        return _WHITESPACE_RE.sub(" ", p)
    return ""


# --- module-level default registry (lazy-loaded singleton). ---


_default_registry: AliasRegistry | None = None


def default_registry() -> AliasRegistry:
    """return the process-wide default registry, loading on first call.

    callers that want an isolated registry (tests, cleanup tools) should
    construct their own ``AliasRegistry`` and not touch this function.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = AliasRegistry.load()
    return _default_registry


def reset_default_registry() -> None:
    """drop the cached default so the next call re-reads the files.

    used by tests and by the cleanup tool after it writes a new runtime
    registry that the same process wants to observe.
    """
    global _default_registry
    _default_registry = None
