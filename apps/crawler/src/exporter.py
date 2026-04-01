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


async def run_exporter(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
) -> None:
    """Main exporter loop.

    Queries local Postgres for changed rows, COPYs to Supabase.
    Runs every ``settings.export_interval`` seconds until *shutdown_event*
    is set.
    """
    interval = settings.export_interval
    posting_cursor = await _get_cursor(local_pool, "job_posting")

    while not shutdown_event.is_set():
        t0 = time.monotonic()
        try:
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

    log.info("reconciliation.completed", discrepancies=discrepancies)
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
