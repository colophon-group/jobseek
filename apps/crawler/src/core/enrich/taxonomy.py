"""Lazy-cached taxonomy resolution for enrichment results.

Resolves occupation, seniority, and technology from enrichment results
to FK IDs, and collects unmatched taxonomy strings as misses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import structlog

from src.core.occupation_resolve import load_occupation_ids, match_occupation
from src.core.seniority_resolve import load_seniority_ids

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger()

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


@dataclass
class TaxonomyMiss:
    taxonomy: str  # 'occupation' or 'technology'
    raw_value: str  # normalized (lowercase, trimmed)
    sample_value: str  # original-cased example


@dataclass
class TaxonomyResult:
    occupation_id: int | None = None
    seniority_id: int | None = None
    technology_ids: list[int] = field(default_factory=list)
    misses: list[TaxonomyMiss] = field(default_factory=list)


# Module-level caches, loaded once on first call.
_occupation_ids: dict[str, int] | None = None
_seniority_ids: dict[str, int] | None = None
_technology_ids: dict[str, int] | None = None
_tech_name_to_slug: dict[str, str] | None = None
_warned_empty = False


def _load_tech_name_map() -> dict[str, str]:
    """Build lowercase name -> slug map from technologies.csv."""
    path = DATA_DIR / "technologies.csv"
    if not path.exists():
        return {}
    df = pl.read_csv(path, infer_schema_length=0)
    mapping: dict[str, str] = {}
    for row in df.iter_rows(named=True):
        slug = row["slug"]
        name = row.get("name", "")
        if name:
            mapping[name.strip().lower()] = slug
    return mapping


async def _load_technology_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Load slug -> id mapping from the technology table."""
    rows = await pool.fetch("SELECT id, slug FROM technology")
    return {row["slug"]: row["id"] for row in rows}


async def _ensure_caches(pool: asyncpg.Pool) -> None:
    """Load taxonomy ID maps on first call."""
    global _occupation_ids, _seniority_ids, _technology_ids, _tech_name_to_slug, _warned_empty

    if _occupation_ids is None:
        _occupation_ids = await load_occupation_ids(pool)
    if _seniority_ids is None:
        _seniority_ids = await load_seniority_ids(pool)
    if _technology_ids is None:
        _technology_ids = await _load_technology_ids(pool)
    if _tech_name_to_slug is None:
        _tech_name_to_slug = _load_tech_name_map()

    if not _warned_empty and (not _occupation_ids or not _seniority_ids):
        _warned_empty = True
        log.warning(
            "enrich.taxonomy_tables_empty",
            occupations=len(_occupation_ids or {}),
            seniorities=len(_seniority_ids or {}),
        )


async def resolve_taxonomy(
    pool: asyncpg.Pool,
    parsed: dict,
) -> TaxonomyResult:
    """Resolve occupation, seniority, and technologies from enrichment result.

    Returns a TaxonomyResult with FK IDs and any unmatched misses.
    Never raises — taxonomy failure must not block enrichment persistence.
    """
    try:
        await _ensure_caches(pool)
        assert _occupation_ids is not None
        assert _seniority_ids is not None
        assert _technology_ids is not None
        assert _tech_name_to_slug is not None

        result = TaxonomyResult()

        # Occupation: fuzzy match via alias table, then look up ID
        raw_occupation = parsed.get("occupation")
        if raw_occupation:
            slug = match_occupation(raw_occupation)
            if slug:
                occ_id = _occupation_ids.get(slug)
                if occ_id is not None:
                    result.occupation_id = occ_id
                else:
                    log.warning("enrich.taxonomy.slug_not_in_db", taxonomy="occupation", slug=slug)
            else:
                normalized = raw_occupation.strip().lower()
                if normalized:
                    result.misses.append(
                        TaxonomyMiss(
                            taxonomy="occupation",
                            raw_value=normalized,
                            sample_value=raw_occupation.strip(),
                        )
                    )

        # Seniority: direct slug lookup (parsed value is already a valid slug)
        raw_seniority = parsed.get("seniority")
        if raw_seniority:
            sen_id = _seniority_ids.get(raw_seniority)
            if sen_id is not None:
                result.seniority_id = sen_id
            else:
                result.misses.append(
                    TaxonomyMiss(
                        taxonomy="seniority",
                        raw_value=raw_seniority.strip().lower(),
                        sample_value=raw_seniority.strip(),
                    )
                )

        # Technologies: name -> slug -> ID lookup
        raw_technologies = parsed.get("technologies") or []
        for tech_name in raw_technologies:
            if not tech_name or not isinstance(tech_name, str):
                continue
            normalized = tech_name.strip().lower()
            slug = _tech_name_to_slug.get(normalized)
            if slug:
                tech_id = _technology_ids.get(slug)
                if tech_id is not None:
                    result.technology_ids.append(tech_id)
                else:
                    log.warning("enrich.taxonomy.slug_not_in_db", taxonomy="technology", slug=slug)
            else:
                if normalized:
                    result.misses.append(
                        TaxonomyMiss(
                            taxonomy="technology",
                            raw_value=normalized,
                            sample_value=tech_name.strip(),
                        )
                    )

        return result

    except Exception:
        log.warning("enrich.taxonomy_resolve_error", exc_info=True)
        return TaxonomyResult()
