"""Lazy-loaded singleton lookups for taxonomy resolution."""

from __future__ import annotations

import asyncpg
import structlog

from src.core.location_resolve import LocationResolver
from src.core.occupation_resolve import load_occupation_ids
from src.core.seniority_resolve import load_seniority_ids
from src.core.technology_resolve import load_technology_ids
from src.queries.monitor import _UPSERT_LOCATION_MISSES

log = structlog.get_logger()

# Lazy-loaded singletons (populated once per batch run)
_location_resolver: LocationResolver | None = None
_technology_id_map: dict[str, int] | None = None
_occupation_id_map: dict[str, int] | None = None
_seniority_id_map: dict[str, int] | None = None
_currency_rates: dict[str, float] | None = None


async def _get_location_resolver(pool: asyncpg.Pool) -> LocationResolver:
    """Get or create the location resolver singleton."""
    global _location_resolver
    if _location_resolver is None:
        _location_resolver = LocationResolver()
        await _location_resolver.load(pool)
        log.info("batch.location_resolver.loaded", entries=_location_resolver.entry_count)
    return _location_resolver


async def _flush_location_misses(
    resolver: LocationResolver,
    pool: asyncpg.Pool,
) -> None:
    """Drain location misses from the resolver and upsert to taxonomy_miss."""
    raw_misses = resolver.drain_location_misses()
    if not raw_misses:
        return
    seen: set[str] = set()
    deduped_raw: list[str] = []
    deduped_sample: list[str] = []
    for raw, sample in raw_misses:
        if raw not in seen:
            seen.add(raw)
            deduped_raw.append(raw)
            deduped_sample.append(sample)
    await pool.execute(_UPSERT_LOCATION_MISSES, deduped_raw, deduped_sample)


async def _get_technology_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the technology slug -> id mapping."""
    global _technology_id_map
    if _technology_id_map is None:
        _technology_id_map = await load_technology_ids(pool)
        log.info("batch.technology_ids.loaded", count=len(_technology_id_map))
    return _technology_id_map


async def _get_occupation_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the occupation slug -> id mapping."""
    global _occupation_id_map
    if _occupation_id_map is None:
        _occupation_id_map = await load_occupation_ids(pool)
        log.info("batch.occupation_ids.loaded", count=len(_occupation_id_map))
    return _occupation_id_map


async def _get_seniority_ids(pool: asyncpg.Pool) -> dict[str, int]:
    """Get or load the seniority slug -> id mapping."""
    global _seniority_id_map
    if _seniority_id_map is None:
        _seniority_id_map = await load_seniority_ids(pool)
        log.info("batch.seniority_ids.loaded", count=len(_seniority_id_map))
    return _seniority_id_map


async def _get_currency_rates(pool: asyncpg.Pool) -> dict[str, float]:
    """Get or load the currency -> to_eur rate mapping."""
    global _currency_rates
    if _currency_rates is None:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT currency, to_eur FROM currency_rate")
        _currency_rates = {r["currency"]: float(r["to_eur"]) for r in rows}
        log.info("batch.currency_rates.loaded", count=len(_currency_rates))
    return _currency_rates


async def _resolve_locations(
    resolver: LocationResolver,
    locations: list[str] | None,
    job_location_type: str | None,
    posting_language: str | None = None,
) -> tuple[list[int] | None, list[str] | None]:
    """Resolve locations to parallel arrays of (location_ids, location_types).

    Uses the in-memory core-locale cache first.  On cache misses (non-core
    locale names), batch-queries the DB and retries.
    """
    results = resolver.resolve(locations, job_location_type, posting_language)

    # DB fallback for non-core locale names (rare path).
    # Clear location_misses before retry — only misses from the final attempt matter.
    if await resolver.backfill_misses():
        resolver.drain_location_misses()
        results = resolver.resolve(locations, job_location_type, posting_language)

    if not results:
        return None, None

    # Build parallel arrays — only entries with location_ids
    loc_ids = []
    loc_types = []
    for r in results:
        if r.location_id is not None:
            loc_ids.append(r.location_id)
            loc_types.append(r.location_type)

    return loc_ids or None, loc_types or None
