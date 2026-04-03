from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime

import asyncpg
import structlog

from src.config import settings
from src.metrics import (
    exporter_export_lag,
    exporter_flush_duration,
    exporter_last_flush_ts,
    exporter_rows_exported,
    local_db_pool_idle,
    local_db_pool_size,
    r2_pending_gauge,
    redis_connected,
    redis_queue_depth,
    supa_db_pool_idle,
    supa_db_pool_size,
)
from src.redis_queue import get_queue_depths

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Cursor persistence (exporter_state table)
# ---------------------------------------------------------------------------

_EPOCH = datetime.min.replace(tzinfo=UTC)
_ZERO_UUID = uuid.UUID(int=0)

# Cursor is a (timestamp, id) pair for keyset pagination.
# Stored as "ts_iso|uuid" in exporter_state.
Cursor = tuple[datetime, uuid.UUID]


async def _get_cursor(pool: asyncpg.Pool, table: str) -> Cursor:
    """Load the last export cursor from exporter_state."""
    row = await pool.fetchrow(
        "SELECT value FROM exporter_state WHERE key = $1",
        f"last_export_ts:{table}",
    )
    if row:
        val = row["value"]
        if "|" in val:
            ts_str, id_str = val.split("|", 1)
            return datetime.fromisoformat(ts_str), uuid.UUID(id_str)
        # Backward compat: old cursor stored just a timestamp
        return datetime.fromisoformat(val), _ZERO_UUID
    return _EPOCH, _ZERO_UUID


async def _save_cursor(pool: asyncpg.Pool, table: str, cursor: Cursor) -> None:
    """Persist the export cursor to exporter_state."""
    ts, last_id = cursor
    await pool.execute(
        "INSERT INTO exporter_state (key, value, updated_at) "
        "VALUES ($1, $2, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
        f"last_export_ts:{table}",
        f"{ts.isoformat()}|{last_id}",
    )


# ---------------------------------------------------------------------------
# Taxonomy name maps for Typesense denormalization
# ---------------------------------------------------------------------------

# Refresh interval for taxonomy maps (seconds)
_TAXONOMY_REFRESH_INTERVAL = 600  # 10 minutes


class TaxonomyMaps:
    """In-memory lookup tables for denormalizing Typesense documents."""

    def __init__(self) -> None:
        # location_id -> {locale -> name}
        self.location_names: dict[int, dict[str, str]] = {}
        # location_id -> geo type (city/region/country/macro)
        self.location_types: dict[int, str] = {}
        # company_id (UUID) -> {name, slug, icon}
        self.company_info: dict[uuid.UUID, dict[str, str | None]] = {}
        # occupation_id -> name (English display name)
        self.occupation_names: dict[int, str] = {}
        # seniority_id -> name (English display name)
        self.seniority_names: dict[int, str] = {}
        # technology_id -> name
        self.technology_names: dict[int, str] = {}
        # location_id -> list of all ancestor IDs (self + parents + macro regions)
        self.location_ancestors: dict[int, list[int]] = {}
        # occupation_id -> list of all ancestor IDs (self + parents)
        self.occupation_ancestors: dict[int, list[int]] = {}
        self._last_refresh: float = 0.0

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > _TAXONOMY_REFRESH_INTERVAL

    async def refresh(
        self,
        local_pool: asyncpg.Pool,
        supa_pool: asyncpg.Pool,
    ) -> None:
        """Reload all taxonomy maps from the database."""
        await asyncio.gather(
            self._load_location_names(local_pool),
            self._load_location_geo_types(local_pool),
            self._load_company_info(supa_pool),
            self._load_occupation_names(local_pool),
            self._load_seniority_names(local_pool),
            self._load_technology_names(local_pool),
            self._load_location_ancestors(local_pool),
            self._load_occupation_ancestors(local_pool),
        )
        self._last_refresh = time.monotonic()
        log.info(
            "taxonomy_maps.refreshed",
            locations=len(self.location_names),
            companies=len(self.company_info),
            occupations=len(self.occupation_names),
            seniorities=len(self.seniority_names),
            technologies=len(self.technology_names),
            location_ancestors=len(self.location_ancestors),
            occupation_ancestors=len(self.occupation_ancestors),
        )

    async def _load_location_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT location_id, locale, name FROM location_name")
        names: dict[int, dict[str, str]] = {}
        for r in rows:
            loc_id = r["location_id"]
            if loc_id not in names:
                names[loc_id] = {}
            names[loc_id][r["locale"]] = r["name"]
        self.location_names = names

    async def _load_location_geo_types(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT id, type FROM location")
        self.location_types = {r["id"]: r["type"] for r in rows}

    async def _load_company_info(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT id, name, slug, icon FROM company")
        self.company_info = {
            r["id"]: {"name": r["name"], "slug": r["slug"], "icon": r.get("icon")} for r in rows
        }

    async def _load_occupation_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch(
            "SELECT occupation_id, name FROM occupation_name "
            "WHERE locale = 'en' AND is_display = true"
        )
        self.occupation_names = {r["occupation_id"]: r["name"] for r in rows}

    async def _load_seniority_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch(
            "SELECT seniority_id, name FROM seniority_name "
            "WHERE locale = 'en' AND is_display = true"
        )
        self.seniority_names = {r["seniority_id"]: r["name"] for r in rows}

    async def _load_technology_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT id, name FROM technology")
        self.technology_names = {r["id"]: r["name"] for r in rows}

    async def _load_location_ancestors(self, pool: asyncpg.Pool) -> None:
        """Build location_id -> [ancestor IDs] map (self + parents + macro regions)."""
        from collections import defaultdict

        # Load parent_id chains
        location_parents: dict[int, int | None] = {}
        rows = await pool.fetch("SELECT id, parent_id FROM location")
        for r in rows:
            location_parents[r["id"]] = r["parent_id"]

        # Load macro memberships (country_id -> [macro_id, ...])
        macro_members: dict[int, list[int]] = defaultdict(list)
        rows = await pool.fetch("SELECT country_id, macro_id FROM location_macro_member")
        for r in rows:
            macro_members[r["country_id"]].append(r["macro_id"])

        # Build ancestor sets
        ancestors: dict[int, list[int]] = {}
        for loc_id in location_parents:
            ancestor_set: set[int] = set()
            current: int | None = loc_id
            while current is not None and current not in ancestor_set:
                ancestor_set.add(current)
                # If this is a country, add its macro regions
                if current in macro_members:
                    ancestor_set.update(macro_members[current])
                current = location_parents.get(current)
            ancestors[loc_id] = list(ancestor_set)

        self.location_ancestors = ancestors

    async def _load_occupation_ancestors(self, pool: asyncpg.Pool) -> None:
        """Build occupation_id -> [ancestor IDs] map (self + parents)."""
        occupation_parents: dict[int, int | None] = {}
        rows = await pool.fetch("SELECT id, parent_id FROM occupation")
        for r in rows:
            occupation_parents[r["id"]] = r["parent_id"]

        ancestors: dict[int, list[int]] = {}
        for occ_id in occupation_parents:
            ancestor_set: set[int] = set()
            current: int | None = occ_id
            while current is not None and current not in ancestor_set:
                ancestor_set.add(current)
                current = occupation_parents.get(current)
            ancestors[occ_id] = list(ancestor_set)

        self.occupation_ancestors = ancestors


# Module-level singleton
_taxonomy_maps: TaxonomyMaps | None = None


async def _get_taxonomy_maps(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
) -> TaxonomyMaps:
    """Get or create taxonomy maps, refreshing if stale."""
    global _taxonomy_maps
    if _taxonomy_maps is None:
        _taxonomy_maps = TaxonomyMaps()
        await _taxonomy_maps.refresh(local_pool, supa_pool)
    elif _taxonomy_maps.stale:
        await _taxonomy_maps.refresh(local_pool, supa_pool)
    return _taxonomy_maps


# ---------------------------------------------------------------------------
# Typesense document builder
# ---------------------------------------------------------------------------


def _build_typesense_docs(
    rows: list,
    maps: TaxonomyMaps,
) -> list[dict]:
    """Build Typesense documents from asyncpg rows using taxonomy maps."""
    docs = []
    for row in rows:
        # titles[0] -> title
        titles = row["titles"]
        title = titles[0] if titles else ""

        # company denormalization
        company_id = row["company_id"]
        company = maps.company_info.get(company_id, {})
        company_name = company.get("name", "")
        company_slug = company.get("slug", "")
        company_icon = company.get("icon")

        # location denormalization
        raw_location_ids = row["location_ids"] or []
        location_names = []
        location_geo_types = []
        for loc_id in raw_location_ids:
            loc_name_map = maps.location_names.get(loc_id, {})
            # Use English name, fall back to any available locale
            name = loc_name_map.get("en", "")
            if not name and loc_name_map:
                name = next(iter(loc_name_map.values()))
            location_names.append(name)
            location_geo_types.append(maps.location_types.get(loc_id, ""))

        # Expand location_ids to include all ancestors (parents + macro regions).
        # Leaf IDs come first (aligned with location_names/location_geo_types),
        # then additional ancestor-only IDs.
        ancestor_only: set[int] = set()
        for lid in raw_location_ids:
            ancestor_only.update(maps.location_ancestors.get(lid, [lid]))
        ancestor_only -= set(raw_location_ids)
        expanded_location_ids = list(raw_location_ids) + list(ancestor_only)

        # occupation denormalization
        occ_id = row["occupation_id"]
        occ_name = maps.occupation_names.get(occ_id) if occ_id else None

        # Expand occupation_id to include all ancestors (parents)
        occupation_ids: list[int] = []
        if occ_id is not None:
            occupation_ids = maps.occupation_ancestors.get(occ_id, [occ_id])

        # seniority denormalization
        sen_id = row["seniority_id"]
        sen_name = maps.seniority_names.get(sen_id) if sen_id else None

        # technology denormalization
        tech_ids = row["technology_ids"] or []
        tech_names = [maps.technology_names.get(tid, "") for tid in tech_ids]

        # experience_min: sentinel -1 for NULL
        exp_min = row["experience_min"]
        if exp_min is None:
            exp_min = -1

        # locales: sentinel ["_none"] for empty
        locales = row["locales"] or []
        if not locales:
            locales = ["_none"]

        # timestamps -> Unix int
        first_seen = row["first_seen_at"]
        first_seen_ts = int(first_seen.timestamp()) if first_seen else 0

        last_seen = row["last_seen_at"]
        last_seen_ts = int(last_seen.timestamp()) if last_seen else None

        doc: dict = {
            "id": str(row["id"]),
            "company_id": str(company_id),
            "company_name": company_name,
            "company_slug": company_slug,
            "title": title,
            "is_active": row["is_active"],
            "location_ids": expanded_location_ids,
            "location_names": location_names,
            "location_types": list(row["location_types"] or []),
            "location_geo_types": location_geo_types,
            "technology_ids": list(tech_ids),
            "technology_names": tech_names,
            "employment_type": row["employment_type"] or "",
            "experience_min": exp_min,
            "locales": list(locales),
            "first_seen_at": first_seen_ts,
        }

        # Optional fields
        if company_icon:
            doc["company_icon"] = company_icon
        if occ_id is not None:
            doc["occupation_id"] = occ_id
        if occupation_ids:
            doc["occupation_ids"] = occupation_ids
        if occ_name is not None:
            doc["occupation_name"] = occ_name
        if sen_id is not None:
            doc["seniority_id"] = sen_id
        if sen_name is not None:
            doc["seniority_name"] = sen_name
        if row["salary_eur"] is not None:
            doc["salary_eur"] = row["salary_eur"]
        if row["source_url"]:
            doc["source_url"] = row["source_url"]
        if last_seen_ts is not None:
            doc["last_seen_at"] = last_seen_ts

        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Typesense upsert
# ---------------------------------------------------------------------------


async def _upsert_to_typesense(
    docs: list[dict],
) -> None:
    """Batch upsert documents to Typesense job_posting collection.

    The typesense client is synchronous, so we run it in an executor.
    """
    from src.typesense_client import get_typesense_client

    client = get_typesense_client()
    if client is None or not docs:
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: client.collections["job_posting"].documents.import_(docs, {"action": "upsert"}),
    )


# ---------------------------------------------------------------------------
# Export: changed job postings
# ---------------------------------------------------------------------------

# Columns selected from local Postgres and inserted into Supabase.
_POSTING_COLUMNS = (
    "id, company_id, board_id, source_url, is_active, "
    "titles, locales, location_ids, location_types, employment_type, "
    "salary_min, salary_max, salary_currency, salary_period, salary_eur, "
    "experience_min, experience_max, occupation_id, seniority_id, "
    "technology_ids, description_r2_hash, "
    "first_seen_at"
)

_POSTING_UPSERT_SET = (
    "is_active = EXCLUDED.is_active, "
    "titles = EXCLUDED.titles, "
    "locales = EXCLUDED.locales, "
    "location_ids = EXCLUDED.location_ids, "
    "location_types = EXCLUDED.location_types, "
    "employment_type = EXCLUDED.employment_type, "
    "salary_min = EXCLUDED.salary_min, "
    "salary_max = EXCLUDED.salary_max, "
    "salary_currency = EXCLUDED.salary_currency, "
    "salary_period = EXCLUDED.salary_period, "
    "salary_eur = EXCLUDED.salary_eur, "
    "experience_min = EXCLUDED.experience_min, "
    "experience_max = EXCLUDED.experience_max, "
    "occupation_id = EXCLUDED.occupation_id, "
    "seniority_id = EXCLUDED.seniority_id, "
    "technology_ids = EXCLUDED.technology_ids, "
    "description_r2_hash = EXCLUDED.description_r2_hash"
)


def _cursor_gt(row_ts: datetime, row_id: uuid.UUID, cursor: Cursor) -> bool:
    """Return True if (row_ts, row_id) > cursor."""
    c_ts, c_id = cursor
    return (row_ts, row_id) > (c_ts, c_id)


def _min_cursor(a: Cursor, b: Cursor) -> Cursor:
    """Return the smaller of two cursors."""
    return a if a <= b else b


async def _export_changed_postings(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    cursor: Cursor,
) -> tuple[int, Cursor]:
    """Export job_posting rows changed since cursor to Supabase.

    Uses keyset pagination on (updated_at, id) to avoid skipping rows
    when many share the same updated_at timestamp (e.g. bulk mark-gone).
    Returns (count_exported, new_cursor).
    """
    last_ts, last_id = cursor
    rows = await local_pool.fetch(
        f"SELECT {_POSTING_COLUMNS}, updated_at "
        "FROM job_posting WHERE (updated_at, id) > ($1, $2) "
        "ORDER BY updated_at, id LIMIT $3",
        last_ts,
        last_id,
        settings.export_batch_limit,
    )
    if not rows:
        return 0, cursor

    # Strip updated_at from records before COPY to Supabase
    col_names = _POSTING_COLUMNS.split(", ")
    async with supa_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE _export_postings ("
            "  id UUID, company_id UUID, board_id UUID, source_url TEXT,"
            "  is_active BOOLEAN, titles TEXT[], locales TEXT[],"
            "  location_ids INT[], location_types TEXT[],"
            "  employment_type TEXT,"
            "  salary_min INT, salary_max INT, salary_currency TEXT,"
            "  salary_period TEXT, salary_eur INT,"
            "  experience_min INT, experience_max INT,"
            "  occupation_id INT, seniority_id INT,"
            "  technology_ids INT[], description_r2_hash BIGINT,"
            "  first_seen_at TIMESTAMPTZ"
            ") ON COMMIT DROP"
        )

        await conn.copy_records_to_table(
            "_export_postings",
            records=[tuple(r[c] for c in col_names) for r in rows],
            columns=col_names,
        )

        # Delete from temp table any rows whose source_url would collide
        # with an existing row under a different ID (cross-board duplicates).
        await conn.execute(
            "DELETE FROM _export_postings t "
            "USING job_posting jp "
            "WHERE jp.source_url = t.source_url AND jp.id != t.id"
        )

        await conn.execute(
            f"INSERT INTO job_posting ({_POSTING_COLUMNS}) "
            "SELECT * FROM _export_postings "
            f"ON CONFLICT (id) DO UPDATE SET {_POSTING_UPSERT_SET}"
        )

    last_row = rows[-1]
    new_cursor = (last_row["updated_at"], last_row["id"])
    return len(rows), new_cursor


async def _export_postings_dual(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    supa_cursor: Cursor,
    ts_cursor: Cursor,
    maps: TaxonomyMaps,
) -> tuple[int, Cursor, Cursor]:
    """Fetch changed postings and upsert to both Supabase and Typesense concurrently.

    Uses two-cursor design: SELECT from MIN(supa_cursor, ts_cursor), then
    post-filter rows for each target. Returns (total_fetched, new_supa_cursor, new_ts_cursor).
    """
    fetch_cursor = _min_cursor(supa_cursor, ts_cursor)
    fetch_ts, fetch_id = fetch_cursor

    # Widen SELECT to include last_seen_at (needed by Typesense, not sent to Supabase)
    rows = await local_pool.fetch(
        f"SELECT {_POSTING_COLUMNS}, last_seen_at, updated_at "
        "FROM job_posting WHERE (updated_at, id) > ($1, $2) "
        "ORDER BY updated_at, id LIMIT $3",
        fetch_ts,
        fetch_id,
        settings.export_batch_limit,
    )
    if not rows:
        return 0, supa_cursor, ts_cursor

    # Post-fetch filtering: each target only gets rows past its own cursor
    supa_rows = [r for r in rows if _cursor_gt(r["updated_at"], r["id"], supa_cursor)]
    ts_rows = [r for r in rows if _cursor_gt(r["updated_at"], r["id"], ts_cursor)]

    # Build tasks
    tasks = []

    # Supabase upsert
    if supa_rows:
        tasks.append(_upsert_to_supabase(supa_pool, supa_rows))
    else:
        tasks.append(_noop())

    # Typesense upsert
    if ts_rows:
        docs = _build_typesense_docs(ts_rows, maps)
        tasks.append(_upsert_to_typesense(docs))
    else:
        tasks.append(_noop())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Advance cursors independently on success
    new_supa_cursor = supa_cursor
    if supa_rows:
        if isinstance(results[0], BaseException):
            log.error("exporter.supabase_upsert_error", error=str(results[0]))
        else:
            last = supa_rows[-1]
            new_supa_cursor = (last["updated_at"], last["id"])

    new_ts_cursor = ts_cursor
    if ts_rows:
        if isinstance(results[1], BaseException):
            log.error("exporter.typesense_upsert_error", error=str(results[1]))
        else:
            last = ts_rows[-1]
            new_ts_cursor = (last["updated_at"], last["id"])

    return len(rows), new_supa_cursor, new_ts_cursor


async def _noop() -> None:
    """No-op coroutine for gather slots."""


async def _upsert_to_supabase(
    supa_pool: asyncpg.Pool,
    rows: list,
) -> None:
    """Upsert rows to Supabase (extracted from _export_changed_postings)."""
    col_names = _POSTING_COLUMNS.split(", ")
    async with supa_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE _export_postings ("
            "  id UUID, company_id UUID, board_id UUID, source_url TEXT,"
            "  is_active BOOLEAN, titles TEXT[], locales TEXT[],"
            "  location_ids INT[], location_types TEXT[],"
            "  employment_type TEXT,"
            "  salary_min INT, salary_max INT, salary_currency TEXT,"
            "  salary_period TEXT, salary_eur INT,"
            "  experience_min INT, experience_max INT,"
            "  occupation_id INT, seniority_id INT,"
            "  technology_ids INT[], description_r2_hash BIGINT,"
            "  first_seen_at TIMESTAMPTZ"
            ") ON COMMIT DROP"
        )

        await conn.copy_records_to_table(
            "_export_postings",
            records=[tuple(r[c] for c in col_names) for r in rows],
            columns=col_names,
        )

        await conn.execute(
            "DELETE FROM _export_postings t "
            "USING job_posting jp "
            "WHERE jp.source_url = t.source_url AND jp.id != t.id"
        )

        await conn.execute(
            f"INSERT INTO job_posting ({_POSTING_COLUMNS}) "
            "SELECT * FROM _export_postings "
            f"ON CONFLICT (id) DO UPDATE SET {_POSTING_UPSERT_SET}"
        )


# ---------------------------------------------------------------------------
# Export: changed board status
# ---------------------------------------------------------------------------


async def _export_changed_boards(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    cursor: Cursor,
) -> tuple[int, Cursor]:
    """Export job_board status rows changed since cursor to Supabase.

    Row-by-row UPDATE is intentional -- board status changes are rare.
    Returns (count_exported, new_cursor).
    """
    last_ts, last_id = cursor
    rows = await local_pool.fetch(
        "SELECT id, board_status, last_error, is_enabled, updated_at "
        "FROM job_board WHERE (updated_at, id) > ($1, $2) "
        "ORDER BY updated_at, id LIMIT $3",
        last_ts,
        last_id,
        settings.export_batch_limit,
    )
    if not rows:
        return 0, cursor

    async with supa_pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                "UPDATE job_board SET board_status = $2, last_error = $3, "
                "is_enabled = $4, updated_at = $5 WHERE id = $1",
                row["id"],
                row["board_status"],
                row["last_error"],
                row["is_enabled"],
                row["updated_at"],
            )

    last_row = rows[-1]
    new_cursor = (last_row["updated_at"], last_row["id"])
    return len(rows), new_cursor


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def _update_metrics(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    posting_cursor: Cursor,
) -> None:
    """Update Prometheus gauges with queue depths, export lag, and R2 pending."""
    try:
        depths = await get_queue_depths()
        for key, count in depths.items():
            redis_queue_depth.labels(queue=key).set(count)
        redis_connected.set(1)
    except Exception:
        redis_connected.set(0)
        log.warning("exporter.metrics_redis_error", exc_info=True)

    try:
        last_ts, last_id = posting_cursor
        lag = await local_pool.fetchval(
            "SELECT count(*) FROM job_posting WHERE (updated_at, id) > ($1, $2)",
            last_ts,
            last_id,
        )
        exporter_export_lag.labels(table="job_posting").set(lag or 0)
    except Exception:
        log.warning("exporter.metrics_lag_error", exc_info=True)

    try:
        pending = await local_pool.fetchval(
            "SELECT count(*) FROM descriptions WHERE r2_uploaded = false"
        )
        r2_pending_gauge.set(pending or 0)
    except Exception:
        log.warning("exporter.metrics_r2_pending_error", exc_info=True)

    # Pool stats
    local_db_pool_size.set(local_pool.get_size())
    local_db_pool_idle.set(local_pool.get_idle_size())
    supa_db_pool_size.set(supa_pool.get_size())
    supa_db_pool_idle.set(supa_pool.get_idle_size())


# ---------------------------------------------------------------------------
# Main export loop
# ---------------------------------------------------------------------------


def _typesense_enabled() -> bool:
    """Check if Typesense integration is enabled."""
    return bool(settings.typesense_admin_key)


async def run_exporter(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Main exporter loop.

    Queries local Postgres for changed rows, COPYs to Supabase,
    and upserts to Typesense when enabled.
    Runs every ``settings.export_interval`` seconds until *shutdown_event*
    is set.
    """
    interval = settings.export_interval
    posting_cursor = await _get_cursor(local_pool, "job_posting")

    ts_enabled = _typesense_enabled()
    ts_cursor: Cursor = (_EPOCH, _ZERO_UUID)
    maps: TaxonomyMaps | None = None

    if ts_enabled:
        ts_cursor = await _get_cursor(local_pool, "typesense:job_posting")
        maps = await _get_taxonomy_maps(local_pool, supa_pool)
        log.info("exporter.typesense_enabled")

    while not shutdown_event.is_set():
        t0 = time.monotonic()
        try:
            if ts_enabled and maps is not None:
                # Refresh taxonomy maps if stale
                if maps.stale:
                    await maps.refresh(local_pool, supa_pool)

                # Two-cursor dual export
                exported, posting_cursor, ts_cursor = await _export_postings_dual(
                    local_pool, supa_pool, posting_cursor, ts_cursor, maps
                )
                await _save_cursor(local_pool, "job_posting", posting_cursor)
                await _save_cursor(local_pool, "typesense:job_posting", ts_cursor)
            else:
                # Supabase-only export (original path)
                exported, posting_cursor = await _export_changed_postings(
                    local_pool, supa_pool, posting_cursor
                )
                await _save_cursor(local_pool, "job_posting", posting_cursor)

            await _update_metrics(local_pool, supa_pool, posting_cursor)

            duration = time.monotonic() - t0
            exporter_flush_duration.observe(duration)
            exporter_last_flush_ts.set(time.time())
            if exported:
                exporter_rows_exported.labels(table="job_posting").inc(exported)

            log.info(
                "exporter.tick",
                exported=exported,
                duration_s=round(duration, 2),
            )
        except Exception:
            log.exception("exporter.tick_error")

        # Sleep for *interval* seconds, but wake early on shutdown.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)


# ---------------------------------------------------------------------------
# Backfill: full Typesense re-index
# ---------------------------------------------------------------------------


async def backfill_typesense(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
) -> None:
    """Iterate ALL job_posting rows and upsert to Typesense.

    Used for initial population or full re-sync. Logs progress every 10K rows.
    """
    maps = await _get_taxonomy_maps(local_pool, supa_pool)

    cursor: Cursor = (_EPOCH, _ZERO_UUID)
    total = 0
    batch_size = settings.export_batch_limit

    while True:
        last_ts, last_id = cursor
        rows = await local_pool.fetch(
            f"SELECT {_POSTING_COLUMNS}, last_seen_at, updated_at "
            "FROM job_posting WHERE (updated_at, id) > ($1, $2) "
            "ORDER BY updated_at, id LIMIT $3",
            last_ts,
            last_id,
            batch_size,
        )
        if not rows:
            break

        # Refresh maps periodically during long backfills
        if maps.stale:
            await maps.refresh(local_pool, supa_pool)

        docs = _build_typesense_docs(rows, maps)
        try:
            await _upsert_to_typesense(docs)
        except Exception:
            log.exception("backfill.typesense_upsert_error", batch_start=str(cursor))
            # Continue with next batch rather than aborting entirely
            pass

        last_row = rows[-1]
        cursor = (last_row["updated_at"], last_row["id"])
        total += len(rows)

        if total % 10_000 < batch_size:
            log.info("backfill.progress", total=total)

    # Save the final cursor so the CDC exporter picks up from here
    await _save_cursor(local_pool, "typesense:job_posting", cursor)
    log.info("backfill.completed", total=total)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


async def run_reconciliation(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
) -> int:
    """Compare local Postgres vs Supabase per board, touch discrepancies.

    This does NOT export directly -- it sets ``updated_at = now()`` on local
    Postgres rows that are missing or stale on Supabase, letting the CDC
    exporter pick them up on the next cycle.

    Returns the number of discrepancies found.
    """
    discrepancies = 0

    local_boards = await local_pool.fetch("SELECT DISTINCT board_id FROM job_posting")

    for board_row in local_boards:
        board_id = board_row["board_id"]

        local_rows = await local_pool.fetch(
            "SELECT id, source_url, is_active, description_r2_hash "
            "FROM job_posting WHERE board_id = $1",
            board_id,
        )

        remote_rows = await supa_pool.fetch(
            "SELECT id, source_url, is_active, description_r2_hash "
            "FROM job_posting WHERE board_id = $1",
            board_id,
        )

        remote_map = {r["id"]: r for r in remote_rows}
        for local in local_rows:
            remote = remote_map.get(local["id"])
            if remote is None:
                # Missing from Supabase -- touch updated_at to trigger CDC
                await local_pool.execute(
                    "UPDATE job_posting SET updated_at = now() WHERE id = $1",
                    local["id"],
                )
                discrepancies += 1
            elif (
                remote["is_active"] != local["is_active"]
                or remote["description_r2_hash"] != local["description_r2_hash"]
            ):
                # State mismatch -- touch updated_at to trigger CDC
                await local_pool.execute(
                    "UPDATE job_posting SET updated_at = now() WHERE id = $1",
                    local["id"],
                )
                discrepancies += 1

    # Typesense reconciliation (if enabled)
    if _typesense_enabled():
        ts_discrepancies = await _reconcile_typesense(local_pool)
        discrepancies += ts_discrepancies

    log.info("reconciliation.completed", discrepancies=discrepancies)
    return discrepancies


async def _reconcile_typesense(local_pool: asyncpg.Pool) -> int:
    """Compare Postgres vs Typesense, touch discrepant rows.

    1. Compare total doc counts
    2. Sample 100 random IDs from Postgres, check in Typesense
    Returns number of discrepancies found.
    """
    from src.typesense_client import get_typesense_client

    client = get_typesense_client()
    if client is None:
        return 0

    discrepancies = 0
    loop = asyncio.get_running_loop()

    # 1. Compare document counts
    try:
        pg_count = await local_pool.fetchval("SELECT count(*) FROM job_posting")
        collection_info = await loop.run_in_executor(
            None,
            lambda: client.collections["job_posting"].retrieve(),
        )
        ts_count = collection_info.get("num_documents", 0)

        log.info(
            "reconciliation.typesense.counts",
            postgres=pg_count,
            typesense=ts_count,
        )
    except Exception:
        log.exception("reconciliation.typesense.count_error")
        return 0

    # 2. Sample 100 random IDs from Postgres, verify in Typesense
    try:
        sample_rows = await local_pool.fetch(
            "SELECT id, is_active FROM job_posting ORDER BY random() LIMIT 100"
        )

        for row in sample_rows:
            posting_id = str(row["id"])
            try:
                ts_doc = await loop.run_in_executor(
                    None,
                    lambda pid=posting_id: (
                        client.collections["job_posting"].documents[pid].retrieve()
                    ),
                )
                # Check is_active match
                if ts_doc.get("is_active") != row["is_active"]:
                    await local_pool.execute(
                        "UPDATE job_posting SET updated_at = now() WHERE id = $1",
                        row["id"],
                    )
                    discrepancies += 1
            except Exception:
                # Document not found in Typesense -- touch to trigger CDC
                await local_pool.execute(
                    "UPDATE job_posting SET updated_at = now() WHERE id = $1",
                    row["id"],
                )
                discrepancies += 1
    except Exception:
        log.exception("reconciliation.typesense.sample_error")

    log.info("reconciliation.typesense.completed", discrepancies=discrepancies)
    return discrepancies


# ---------------------------------------------------------------------------
# Reconciliation loop
# ---------------------------------------------------------------------------


async def _reconciliation_loop(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Periodically run reconciliation in the background."""
    interval = settings.reconciliation_interval
    while not shutdown_event.is_set():
        # Sleep first -- reconciliation is not urgent on startup.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
        if shutdown_event.is_set():
            break
        try:
            discrepancies = await run_reconciliation(local_pool, supa_pool)
            log.info("reconciliation.tick", discrepancies=discrepancies)
        except Exception:
            log.exception("reconciliation.error")


# ---------------------------------------------------------------------------
# Combined runner
# ---------------------------------------------------------------------------


async def run_exporter_with_reconciliation(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Run the exporter and reconciliation loops concurrently."""
    await asyncio.gather(
        run_exporter(local_pool, supa_pool, shutdown_event),
        _reconciliation_loop(local_pool, supa_pool, shutdown_event),
    )
