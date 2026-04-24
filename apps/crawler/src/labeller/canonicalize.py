"""Rule-based canonicalization of free-text labels against crawler taxonomies.

Only **free-text canonicalizable** label fields go through this pass; verbatim
content, display strings, and closed enums are out of scope. Output is a
sidecar JSON at ``canonical/{{date}}/<id>.json`` — the gold is never mutated.

v0.1.0 uses case-folded exact match + alias lookup + RapidFuzz fuzzy match at
a conservative threshold (85). Unmapped values are recorded with a field
path so they can be triaged and added to the taxonomy over time.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from rapidfuzz import process as _rapidfuzz_process
except ImportError:  # rapidfuzz is a hard dep in pyproject; stub for import-time
    _rapidfuzz_process = None

CANONICALIZER_VERSION = "v0.1.0"

_FUZZY_THRESHOLD = 85

# Fields that go through canonicalization, keyed by taxonomy name
_FIELD_MAP: dict[str, str] = {
    "labels.globals.occupation": "occupations",
    "labels.globals.seniority": "seniority",
    "labels.globals.technologies_aggregate[]": "technologies",
    "labels.sections[role].extracted.tools_used[]": "technologies",
    "labels.sections[company].extracted.industry_tags[]": "industries",
    "labels.sections[requirements].extracted.required_skills[].skill": "technologies",
    "labels.sections[preferred].extracted.preferred_skills[].skill": "technologies",
}


@dataclass(frozen=True)
class _Entry:
    id: str
    aliases: frozenset[str]  # case-folded alias set including the canonical name


@dataclass(frozen=True)
class _Taxonomy:
    name: str
    entries: tuple[_Entry, ...]

    @property
    def all_aliases(self) -> tuple[str, ...]:
        return tuple(a for e in self.entries for a in e.aliases)


def _csv_path(name: str) -> Path:
    # Resolve relative to the crawler package root, not CWD. The package
    # layout is apps/crawler/src/labeller/canonicalize.py, so ../../../data
    # points at apps/crawler/data regardless of where the process runs.
    root = Path(__file__).resolve().parent.parent.parent / "data"
    mapping = {
        "technologies": root / "technologies.csv",
        "occupations": root / "occupations.csv",
        "seniority": root / "seniority.csv",
        "industries": root / "industries.csv",
    }
    return mapping[name]


@lru_cache(maxsize=8)
def _load_taxonomy(name: str) -> _Taxonomy:
    path = _csv_path(name)
    if not path.exists():
        return _Taxonomy(name=name, entries=())
    entries: list[_Entry] = []
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            aliases = _aliases_from_row(name, row)
            if not aliases:
                continue
            entry_id = row.get("slug") or row.get("id") or next(iter(aliases))
            entries.append(_Entry(id=str(entry_id), aliases=frozenset(aliases)))
    return _Taxonomy(name=name, entries=tuple(entries))


def _aliases_from_row(taxonomy: str, row: dict[str, str]) -> set[str]:
    """Extract the case-folded alias set for a taxonomy row."""
    out: set[str] = set()

    def _add(value: str | None) -> None:
        if value:
            out.add(value.strip().casefold())

    if taxonomy == "technologies":
        _add(row.get("slug"))
        _add(row.get("name"))
        for p in (row.get("patterns") or "").split("|"):
            _add(p)
    elif taxonomy == "occupations" or taxonomy == "seniority":
        _add(row.get("slug"))
        for col in ("en", "de", "fr", "it"):
            _add(row.get(col))
        for a in (row.get("aliases") or "").split("|"):
            _add(a)
    elif taxonomy == "industries":
        _add(row.get("name"))
        for k in (row.get("keywords") or "").split(","):
            _add(k)
    return out


def _lookup(taxonomy: _Taxonomy, value: str) -> str | None:
    """Return the ID for an exact / alias match, else a fuzzy match, else None."""
    key = value.strip().casefold()
    if not key:
        return None
    for entry in taxonomy.entries:
        if key in entry.aliases:
            return entry.id
    if _rapidfuzz_process is None:
        return None
    all_aliases = taxonomy.all_aliases
    if not all_aliases:
        return None
    match = _rapidfuzz_process.extractOne(key, all_aliases, score_cutoff=_FUZZY_THRESHOLD)
    if not match:
        return None
    alias = match[0]
    for entry in taxonomy.entries:
        if alias in entry.aliases:
            return entry.id
    return None


def canonicalize_posting(posting: dict) -> dict:
    """Produce the sidecar canonical payload for a labelled posting."""
    mappings: dict[str, list[str]] = {
        "occupation_id": [],
        "technology_ids": [],
        "industry_ids": [],
        "seniority_ids": [],
    }
    unmapped: list[dict[str, str]] = []
    coverage: dict[str, dict[str, int]] = {
        "technologies": {"mapped": 0, "unmapped": 0},
        "occupations": {"mapped": 0, "unmapped": 0},
        "industries": {"mapped": 0, "unmapped": 0},
        "seniority": {"mapped": 0, "unmapped": 0},
    }

    def _canonicalize(path: str, value: str, taxonomy_name: str, bucket: str) -> None:
        tax = _load_taxonomy(taxonomy_name)
        matched = _lookup(tax, value)
        if matched is None:
            unmapped.append({"field": path, "value": value, "reason": "no-match"})
            coverage[taxonomy_name]["unmapped"] += 1
        else:
            mappings[bucket].append(matched)
            coverage[taxonomy_name]["mapped"] += 1

    globals_ = (posting.get("labels") or {}).get("globals") or {}
    if globals_.get("occupation"):
        _canonicalize(
            "labels.globals.occupation", globals_["occupation"], "occupations", "occupation_id"
        )
    if globals_.get("seniority"):
        _canonicalize(
            "labels.globals.seniority", globals_["seniority"], "seniority", "seniority_ids"
        )
    for i, tech in enumerate(globals_.get("technologies_aggregate") or []):
        _canonicalize(
            f"labels.globals.technologies_aggregate[{i}]", tech, "technologies", "technology_ids"
        )

    for si, sec in enumerate(posting.get("labels", {}).get("sections", [])):
        extracted = sec.get("extracted") or {}
        kind = sec["kind"]
        if kind == "company":
            for ti, tag in enumerate(extracted.get("industry_tags") or []):
                _canonicalize(
                    f"labels.sections[{si}].extracted.industry_tags[{ti}]",
                    tag,
                    "industries",
                    "industry_ids",
                )
        elif kind == "role":
            for ti, tool in enumerate(extracted.get("tools_used") or []):
                _canonicalize(
                    f"labels.sections[{si}].extracted.tools_used[{ti}]",
                    tool,
                    "technologies",
                    "technology_ids",
                )
        elif kind == "requirements":
            for ti, skill_obj in enumerate(extracted.get("required_skills") or []):
                _canonicalize(
                    f"labels.sections[{si}].extracted.required_skills[{ti}].skill",
                    skill_obj["skill"],
                    "technologies",
                    "technology_ids",
                )
        elif kind == "preferred":
            for ti, skill_obj in enumerate(extracted.get("preferred_skills") or []):
                _canonicalize(
                    f"labels.sections[{si}].extracted.preferred_skills[{ti}].skill",
                    skill_obj["skill"],
                    "technologies",
                    "technology_ids",
                )

    # Dedupe mapped ids while preserving insertion order
    for bucket, ids in list(mappings.items()):
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        mappings[bucket] = seen

    return {
        "posting_id": posting["id"],
        "canonicalizer_version": CANONICALIZER_VERSION,
        "mappings": mappings,
        "unmapped": unmapped,
        "coverage": coverage,
    }


def supported_fields() -> dict[str, str]:
    """Return the canonicalized field → taxonomy mapping for docs/diagnostics."""
    return dict(_FIELD_MAP)
