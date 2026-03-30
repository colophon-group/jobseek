from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.exporter import (
    _EPOCH,
    _export_changed_boards,
    _export_changed_postings,
    _get_last_export_ts,
    _reconciliation_loop,
    _save_last_export_ts,
    _update_metrics,
    run_exporter,
    run_exporter_with_reconciliation,
    run_reconciliation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> AsyncMock:
    """Return an AsyncMock that behaves like an asyncpg.Pool.

    asyncpg's pool.acquire() is *not* a coroutine -- it returns an object that
    directly implements __aenter__/__aexit__.  We use MagicMock for acquire so
    that ``async with pool.acquire() as conn`` works without awaiting first.
    """
    pool = AsyncMock()
    conn = AsyncMock()
    # acquire() -> async context manager (not a coroutine)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    # conn.transaction() -> async context manager
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    return pool


def _make_record(data: dict) -> MagicMock:
    """Simulate an asyncpg.Record (supports both key access and .keys())."""
    rec = MagicMock()
    rec.keys.return_value = list(data.keys())
    rec.__getitem__ = lambda self, k: data[k]
    rec.__contains__ = lambda self, k: k in data
    return rec


# ---------------------------------------------------------------------------
# _get_last_export_ts / _save_last_export_ts
# ---------------------------------------------------------------------------


class TestCursorPersistence:
    async def test_get_returns_epoch_when_no_row(self):
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value=None)

        ts = await _get_last_export_ts(pool, "job_posting")
        assert ts == _EPOCH
        pool.fetchrow.assert_awaited_once()

    async def test_get_returns_stored_timestamp(self):
        stored = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={"value": stored.isoformat()})

        ts = await _get_last_export_ts(pool, "job_posting")
        assert ts == stored

    async def test_save_calls_upsert(self):
        pool = _make_pool()
        pool.execute = AsyncMock()

        ts = datetime(2025, 6, 15, 13, 0, 0, tzinfo=UTC)
        await _save_last_export_ts(pool, "job_board", ts)

        pool.execute.assert_awaited_once()
        args = pool.execute.call_args
        assert "last_export_ts:job_board" in args[0]
        assert ts.isoformat() in args[0]

    async def test_round_trip(self):
        """Save then get should return the same timestamp."""
        stored = {}
        pool = _make_pool()

        async def fake_execute(query, *args):
            if "INSERT INTO exporter_state" in query:
                stored[args[0]] = args[1]

        async def fake_fetchrow(query, key):
            val = stored.get(key)
            if val:
                return {"value": val}
            return None

        pool.execute = AsyncMock(side_effect=fake_execute)
        pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)

        ts = datetime(2025, 7, 1, 10, 30, 0, tzinfo=UTC)
        await _save_last_export_ts(pool, "job_posting", ts)
        result = await _get_last_export_ts(pool, "job_posting")
        assert result == ts


# ---------------------------------------------------------------------------
# _export_changed_postings
# ---------------------------------------------------------------------------


class TestExportChangedPostings:
    async def test_no_rows_returns_zero_and_same_ts(self):
        local = _make_pool()
        supa = _make_pool()
        last_ts = datetime(2025, 1, 1, tzinfo=UTC)

        local.fetch = AsyncMock(return_value=[])

        count, new_ts = await _export_changed_postings(local, supa, last_ts)
        assert count == 0
        assert new_ts == last_ts

    async def test_exports_rows_and_returns_new_ts(self):
        local = _make_pool()
        supa = _make_pool()

        ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        last_ts = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)

        posting_id_1 = uuid.uuid4()
        posting_id_2 = uuid.uuid4()
        company_id = uuid.uuid4()
        board_id = uuid.uuid4()

        base_data = {
            "company_id": company_id,
            "board_id": board_id,
            "source_url": "https://example.com/job/1",
            "is_active": True,
            "titles": ["Engineer"],
            "locales": ["en"],
            "location_ids": [1],
            "location_types": ["office"],
            "employment_type": "full_time",
            "salary_min": 50000,
            "salary_max": 80000,
            "salary_currency": "EUR",
            "salary_period": "year",
            "salary_eur": 65000,
            "experience_min": 2,
            "experience_max": 5,
            "occupation_id": 1,
            "seniority_id": 2,
            "technology_ids": [10, 20],
            "description_r2_hash": 123456789,
            "missing_count": 0,
            "to_be_enriched": False,
            "enrichment": None,
            "enrich_version": None,
            "last_enriched_at": None,
            "first_seen_at": ts1,
            "last_seen_at": ts1,
        }

        row1 = _make_record({"id": posting_id_1, **base_data, "updated_at": ts1})
        row2 = _make_record({"id": posting_id_2, **base_data, "updated_at": ts2})

        local.fetch = AsyncMock(return_value=[row1, row2])

        # Set up supa pool acquire chain (MagicMock, not AsyncMock,
        # because asyncpg acquire() is not a coroutine)
        conn = AsyncMock()
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx_ctx)
        conn.execute = AsyncMock()
        conn.copy_records_to_table = AsyncMock()

        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        supa.acquire = MagicMock(return_value=acq_ctx)

        count, new_ts = await _export_changed_postings(local, supa, last_ts)

        assert count == 2
        assert new_ts == ts2
        # Should have called CREATE TEMP TABLE, copy_records_to_table, INSERT
        assert conn.execute.await_count == 2  # CREATE + INSERT
        conn.copy_records_to_table.assert_awaited_once()


# ---------------------------------------------------------------------------
# _export_changed_boards
# ---------------------------------------------------------------------------


class TestExportChangedBoards:
    async def test_no_rows_returns_zero_and_same_ts(self):
        local = _make_pool()
        supa = _make_pool()
        last_ts = datetime(2025, 1, 1, tzinfo=UTC)

        local.fetch = AsyncMock(return_value=[])

        count, new_ts = await _export_changed_boards(local, supa, last_ts)
        assert count == 0
        assert new_ts == last_ts

    async def test_exports_boards_row_by_row(self):
        local = _make_pool()
        supa = _make_pool()

        board_id_1 = uuid.uuid4()
        board_id_2 = uuid.uuid4()
        ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        last_ts = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)

        row1 = _make_record(
            {
                "id": board_id_1,
                "board_status": "ok",
                "last_error": None,
                "is_enabled": True,
                "updated_at": ts1,
            }
        )
        row2 = _make_record(
            {
                "id": board_id_2,
                "board_status": "error",
                "last_error": "timeout",
                "is_enabled": True,
                "updated_at": ts2,
            }
        )

        local.fetch = AsyncMock(return_value=[row1, row2])

        conn = AsyncMock()
        conn.execute = AsyncMock()
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        supa.acquire = MagicMock(return_value=acq_ctx)

        count, new_ts = await _export_changed_boards(local, supa, last_ts)

        assert count == 2
        assert new_ts == ts2
        # One UPDATE per row
        assert conn.execute.await_count == 2


# ---------------------------------------------------------------------------
# run_reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    async def test_no_boards_returns_zero(self):
        local = _make_pool()
        supa = _make_pool()

        local.fetch = AsyncMock(return_value=[])

        result = await run_reconciliation(local, supa)
        assert result == 0

    async def test_missing_remote_triggers_touch(self):
        """A posting in local but not in Supabase should be touched."""
        local = _make_pool()
        supa = _make_pool()

        board_id = uuid.uuid4()
        posting_id = uuid.uuid4()

        # local has one board and one posting
        local_board_row = _make_record({"board_id": board_id})
        local_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 111,
            }
        )

        call_count = 0

        async def fake_local_fetch(query, *args):
            nonlocal call_count
            call_count += 1
            if "DISTINCT board_id" in query:
                return [local_board_row]
            return [local_posting]

        local.fetch = AsyncMock(side_effect=fake_local_fetch)
        local.execute = AsyncMock()

        # Supabase has no postings for this board
        supa.fetch = AsyncMock(return_value=[])

        result = await run_reconciliation(local, supa)
        assert result == 1
        # Should have touched updated_at
        local.execute.assert_awaited_once()
        assert "updated_at = now()" in local.execute.call_args[0][0]

    async def test_state_mismatch_triggers_touch(self):
        """Differing is_active between local and remote triggers a touch."""
        local = _make_pool()
        supa = _make_pool()

        board_id = uuid.uuid4()
        posting_id = uuid.uuid4()

        local_board_row = _make_record({"board_id": board_id})
        local_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 111,
            }
        )
        remote_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": False,  # mismatch
                "description_r2_hash": 111,
            }
        )

        async def fake_local_fetch(query, *args):
            if "DISTINCT board_id" in query:
                return [local_board_row]
            return [local_posting]

        local.fetch = AsyncMock(side_effect=fake_local_fetch)
        local.execute = AsyncMock()
        supa.fetch = AsyncMock(return_value=[remote_posting])

        result = await run_reconciliation(local, supa)
        assert result == 1

    async def test_hash_mismatch_triggers_touch(self):
        """Differing description_r2_hash triggers a touch."""
        local = _make_pool()
        supa = _make_pool()

        board_id = uuid.uuid4()
        posting_id = uuid.uuid4()

        local_board_row = _make_record({"board_id": board_id})
        local_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 111,
            }
        )
        remote_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 999,  # different hash
            }
        )

        async def fake_local_fetch(query, *args):
            if "DISTINCT board_id" in query:
                return [local_board_row]
            return [local_posting]

        local.fetch = AsyncMock(side_effect=fake_local_fetch)
        local.execute = AsyncMock()
        supa.fetch = AsyncMock(return_value=[remote_posting])

        result = await run_reconciliation(local, supa)
        assert result == 1

    async def test_matching_state_no_touch(self):
        """When local and remote match, no touch should happen."""
        local = _make_pool()
        supa = _make_pool()

        board_id = uuid.uuid4()
        posting_id = uuid.uuid4()

        local_board_row = _make_record({"board_id": board_id})
        local_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 111,
            }
        )
        remote_posting = _make_record(
            {
                "id": posting_id,
                "source_url": "https://example.com/j/1",
                "is_active": True,
                "description_r2_hash": 111,
            }
        )

        async def fake_local_fetch(query, *args):
            if "DISTINCT board_id" in query:
                return [local_board_row]
            return [local_posting]

        local.fetch = AsyncMock(side_effect=fake_local_fetch)
        local.execute = AsyncMock()
        supa.fetch = AsyncMock(return_value=[remote_posting])

        result = await run_reconciliation(local, supa)
        assert result == 0
        local.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_exporter (single tick)
# ---------------------------------------------------------------------------


class TestRunExporter:
    async def test_single_tick_then_shutdown(self):
        """Exporter should run one tick and then stop when shutdown is set."""
        local = _make_pool()
        supa = _make_pool()
        shutdown = asyncio.Event()

        # _get_last_export_ts returns epoch
        local.fetchrow = AsyncMock(return_value=None)
        # _export_changed_postings and _export_changed_boards return no rows
        local.fetch = AsyncMock(return_value=[])
        local.execute = AsyncMock()
        local.fetchval = AsyncMock(return_value=0)

        with patch(
            "src.exporter.get_queue_depths",
            new_callable=AsyncMock,
            return_value={},
        ):
            # Set shutdown after a brief delay so the loop runs once
            async def set_shutdown():
                await asyncio.sleep(0.05)
                shutdown.set()

            await asyncio.gather(
                run_exporter(local, supa, shutdown),
                set_shutdown(),
            )


# ---------------------------------------------------------------------------
# _update_metrics
# ---------------------------------------------------------------------------


class TestUpdateMetrics:
    async def test_updates_gauges(self):
        local = _make_pool()
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        local.fetchval = AsyncMock(return_value=42)

        with patch(
            "src.exporter.get_queue_depths",
            new_callable=AsyncMock,
            return_value={"board:http:due": 5, "scrape:http:due": 10},
        ):
            await _update_metrics(local, ts)

        local.fetchval.assert_awaited_once()

    async def test_handles_redis_error_gracefully(self):
        """Metrics update should not raise even if Redis fails."""
        local = _make_pool()
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        local.fetchval = AsyncMock(return_value=0)

        with patch(
            "src.exporter.get_queue_depths",
            new_callable=AsyncMock,
            side_effect=ConnectionError("redis down"),
        ):
            # Should not raise
            await _update_metrics(local, ts)


# ---------------------------------------------------------------------------
# _reconciliation_loop
# ---------------------------------------------------------------------------


class TestReconciliationLoop:
    async def test_loop_runs_and_shuts_down(self):
        local = _make_pool()
        supa = _make_pool()
        shutdown = asyncio.Event()

        with (
            patch("src.exporter.settings") as mock_settings,
            patch(
                "src.exporter.run_reconciliation",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_recon,
        ):
            # Use a tiny interval so the test is fast
            mock_settings.reconciliation_interval = 0.05

            async def set_shutdown():
                await asyncio.sleep(0.15)
                shutdown.set()

            await asyncio.gather(
                _reconciliation_loop(local, supa, shutdown),
                set_shutdown(),
            )

            # Should have run reconciliation at least once
            assert mock_recon.await_count >= 1


# ---------------------------------------------------------------------------
# run_exporter_with_reconciliation
# ---------------------------------------------------------------------------


class TestCombinedRunner:
    async def test_gathers_both_tasks(self):
        """Combined runner should start both exporter and reconciliation."""
        local = _make_pool()
        supa = _make_pool()
        shutdown = asyncio.Event()

        with (
            patch("src.exporter.run_exporter", new_callable=AsyncMock) as mock_exp,
            patch("src.exporter._reconciliation_loop", new_callable=AsyncMock) as mock_recon,
        ):
            await run_exporter_with_reconciliation(local, supa, shutdown)

            mock_exp.assert_awaited_once_with(local, supa, shutdown)
            mock_recon.assert_awaited_once_with(local, supa, shutdown)
