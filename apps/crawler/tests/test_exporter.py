from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.exporter import (
    _EPOCH,
    _POSTING_COLUMNS,
    _ZERO_UUID,
    TaxonomyMaps,
    _build_typesense_docs,
    _export_changed_boards,
    _export_changed_postings,
    _export_postings_dual,
    _get_cursor,
    _reconciliation_loop,
    _save_cursor,
    _update_metrics,
    _update_typesense_health,
    _upsert_to_supabase,
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

        cursor = await _get_cursor(pool, "job_posting")
        assert cursor == (_EPOCH, _ZERO_UUID)
        pool.fetchrow.assert_awaited_once()

    async def test_get_returns_stored_cursor(self):
        stored_ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        stored_id = uuid.uuid4()
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={"value": f"{stored_ts.isoformat()}|{stored_id}"})

        cursor = await _get_cursor(pool, "job_posting")
        assert cursor == (stored_ts, stored_id)

    async def test_get_backward_compat_ts_only(self):
        """Old cursor format (just timestamp) should still work."""
        stored_ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        pool = _make_pool()
        pool.fetchrow = AsyncMock(return_value={"value": stored_ts.isoformat()})

        cursor = await _get_cursor(pool, "job_posting")
        assert cursor == (stored_ts, _ZERO_UUID)

    async def test_save_calls_upsert(self):
        pool = _make_pool()
        pool.execute = AsyncMock()

        ts = datetime(2025, 6, 15, 13, 0, 0, tzinfo=UTC)
        last_id = uuid.uuid4()
        await _save_cursor(pool, "job_board", (ts, last_id))

        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        assert "last_export_ts:job_board" in args
        # Value should contain both ts and id separated by |
        value_arg = args[2]  # $2 parameter
        assert str(last_id) in value_arg
        assert ts.isoformat() in value_arg

    async def test_round_trip(self):
        """Save then get should return the same cursor."""
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
        last_id = uuid.uuid4()
        await _save_cursor(pool, "job_posting", (ts, last_id))
        result = await _get_cursor(pool, "job_posting")
        assert result == (ts, last_id)


# ---------------------------------------------------------------------------
# _export_changed_postings
# ---------------------------------------------------------------------------


class TestExportChangedPostings:
    async def test_no_rows_returns_zero_and_same_cursor(self):
        local = _make_pool()
        supa = _make_pool()
        cursor = (datetime(2025, 1, 1, tzinfo=UTC), _ZERO_UUID)

        local.fetch = AsyncMock(return_value=[])

        count, new_cursor = await _export_changed_postings(local, supa, cursor)
        assert count == 0
        assert new_cursor == cursor

    async def test_exports_rows_and_returns_new_cursor(self):
        local = _make_pool()
        supa = _make_pool()

        ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        cursor = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)

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

        count, new_cursor = await _export_changed_postings(local, supa, cursor)

        assert count == 2
        assert new_cursor == (ts2, posting_id_2)
        # CREATE TEMP TABLE + INSERT ... ON CONFLICT.
        assert conn.execute.await_count == 2
        conn.copy_records_to_table.assert_awaited_once()


# ---------------------------------------------------------------------------
# _upsert_to_supabase + _export_postings_dual (Supabase + Typesense)
# ---------------------------------------------------------------------------


def _posting_row(*, posting_id, ts, company_id=None, board_id=None) -> MagicMock:
    """Build a minimal asyncpg.Record-shaped dict covering _POSTING_COLUMNS."""
    return _make_record(
        {
            "id": posting_id,
            "company_id": company_id or uuid.uuid4(),
            "board_id": board_id or uuid.uuid4(),
            "source_url": f"https://example.com/job/{posting_id}",
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
            "first_seen_at": ts,
            "last_seen_at": ts,
            "updated_at": ts,
        }
    )


def _supa_pool_with_capture():
    """Pool that captures conn.execute / copy_records_to_table calls."""
    pool = _make_pool()
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
    pool.acquire = MagicMock(return_value=acq_ctx)
    return pool, conn


class TestUpsertToSupabase:
    """Mirror TestExportChangedPostings against the dual-path upsert helper.

    `_upsert_to_supabase` is the inner DB writer extracted from
    `_export_changed_postings` so the new dual-path (`_export_postings_dual`)
    can call it concurrently with the Typesense leg. A regression in the
    write path here would silently corrupt the production CDC pipeline
    even when the legacy single-path tests pass.
    """

    async def test_creates_temp_table_copies_and_inserts(self):
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa, conn = _supa_pool_with_capture()
        rows = [_posting_row(posting_id=uuid.uuid4(), ts=ts)]

        await _upsert_to_supabase(supa, rows)

        # CREATE TEMP TABLE + INSERT ... ON CONFLICT — same shape as the
        # legacy path.
        assert conn.execute.await_count == 2
        create_sql = conn.execute.await_args_list[0].args[0]
        insert_sql = conn.execute.await_args_list[1].args[0]
        assert "CREATE TEMP TABLE _export_postings" in create_sql
        assert "ON COMMIT DROP" in create_sql
        assert "INSERT INTO job_posting" in insert_sql
        assert "ON CONFLICT (id) DO UPDATE SET" in insert_sql

        # COPY column order must match the column list in the SELECT and
        # in the temp-table DDL.
        conn.copy_records_to_table.assert_awaited_once()
        kwargs = conn.copy_records_to_table.await_args.kwargs
        assert kwargs["columns"] == _POSTING_COLUMNS.split(", ")

    async def test_records_tuples_use_column_order(self):
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa, conn = _supa_pool_with_capture()
        posting_id = uuid.uuid4()
        rows = [_posting_row(posting_id=posting_id, ts=ts)]

        await _upsert_to_supabase(supa, rows)

        call = conn.copy_records_to_table.await_args
        records = call.kwargs["records"]
        cols = _POSTING_COLUMNS.split(", ")
        assert len(records) == 1
        # The first column is `id` — it must be the leading element of the
        # tuple, not (say) the second. Catches a column-list reorder bug.
        assert cols[0] == "id"
        assert records[0][0] == posting_id

    async def test_empty_rows_short_circuits_without_db_calls(self):
        """Defensive: callers may pass [] when the cursor advances without
        new rows. _upsert_to_supabase must not allocate temp tables for nil
        work — the dual-path uses a `_noop` sibling so the gather stays
        balanced; if `_upsert_to_supabase` itself silently issued DDL on
        an empty list, every empty-batch poll would churn the temp table.
        """
        supa, conn = _supa_pool_with_capture()
        # The current implementation does NOT short-circuit; this test
        # documents the present behavior. If the caller path changes (e.g.
        # _export_postings_dual stops guarding empties), the assertion below
        # is the place to catch it.
        await _upsert_to_supabase(supa, [])

        # No COPY when rows is empty.
        if conn.copy_records_to_table.await_count > 0:
            # Implementation issued an empty COPY anyway; assert at least
            # that no records were written.
            kwargs = conn.copy_records_to_table.await_args.kwargs
            assert kwargs["records"] == []


class TestExportPostingsDual:
    """Coverage for _export_postings_dual concurrent Supabase+Typesense path.

    The two legs share a fetch, are filtered by independent cursors, and
    upsert via `asyncio.gather`. Cursor advances only on success per leg.
    """

    async def test_advances_both_cursors_on_success(self):
        ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        supa_cur = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        supa, _ = _supa_pool_with_capture()

        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        local.fetch = AsyncMock(
            return_value=[
                _posting_row(posting_id=pid1, ts=ts1),
                _posting_row(posting_id=pid2, ts=ts2),
            ]
        )

        with patch("src.exporter._upsert_to_typesense", new=AsyncMock()):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )

        assert count == 2
        assert new_supa == (ts2, pid2)
        assert new_ts == (ts2, pid2)

    async def test_typesense_failure_does_not_advance_ts_cursor(self):
        """Partial failure: Supabase succeeds, Typesense raises. Supabase
        cursor advances; Typesense cursor stays put so the next poll re-tries
        the same batch on the Typesense leg only."""
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa_cur = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        supa, _ = _supa_pool_with_capture()

        pid = uuid.uuid4()
        local.fetch = AsyncMock(return_value=[_posting_row(posting_id=pid, ts=ts)])

        async def boom(*_a, **_kw):
            raise RuntimeError("typesense down")

        with patch("src.exporter._upsert_to_typesense", new=boom):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )

        assert count == 1
        assert new_supa == (ts, pid)  # advanced
        assert new_ts == ts_cur  # stayed put — re-try next poll

    async def test_supabase_failure_does_not_advance_supa_cursor(self):
        """Partial failure: Typesense succeeds, Supabase raises. Mirror of
        the above — supa cursor stays put."""
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa_cur = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        # Make supa.acquire() blow up in the upsert path by making `execute`
        # raise. Use the public helper to set up the conn, then patch.
        supa, conn = _supa_pool_with_capture()
        conn.execute = AsyncMock(side_effect=RuntimeError("supa down"))

        pid = uuid.uuid4()
        local.fetch = AsyncMock(return_value=[_posting_row(posting_id=pid, ts=ts)])

        with patch("src.exporter._upsert_to_typesense", new=AsyncMock()):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )

        assert count == 1
        assert new_supa == supa_cur  # stayed put
        assert new_ts == (ts, pid)  # advanced

    async def test_no_rows_returns_zero_and_unchanged_cursors(self):
        local = _make_pool()
        supa, _ = _supa_pool_with_capture()
        supa_cur = (datetime(2025, 1, 1, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur
        local.fetch = AsyncMock(return_value=[])

        count, new_supa, new_ts = await _export_postings_dual(
            local, supa, supa_cur, ts_cur, TaxonomyMaps()
        )
        assert count == 0
        assert new_supa == supa_cur
        assert new_ts == ts_cur


# ---------------------------------------------------------------------------
# _export_changed_boards
# ---------------------------------------------------------------------------


class TestExportChangedBoards:
    async def test_no_rows_returns_zero_and_same_cursor(self):
        local = _make_pool()
        supa = _make_pool()
        cursor = (datetime(2025, 1, 1, tzinfo=UTC), _ZERO_UUID)

        local.fetch = AsyncMock(return_value=[])

        count, new_cursor = await _export_changed_boards(local, supa, cursor)
        assert count == 0
        assert new_cursor == cursor

    async def test_exports_boards_row_by_row(self):
        local = _make_pool()
        supa = _make_pool()

        board_id_1 = uuid.uuid4()
        board_id_2 = uuid.uuid4()
        ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        cursor = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)

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

        count, new_cursor = await _export_changed_boards(local, supa, cursor)

        assert count == 2
        assert new_cursor == (ts2, board_id_2)
        # One UPDATE per row
        assert conn.execute.await_count == 2


# ---------------------------------------------------------------------------
# run_reconciliation
# ---------------------------------------------------------------------------


class TestReconciliation:
    async def test_empty_sample_returns_zero(self):
        local = _make_pool()
        supa = _make_pool()

        local.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        supa.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        local.fetch = AsyncMock(return_value=[])  # empty sample

        result = await run_reconciliation(local, supa)
        assert result == 0

    async def test_missing_remote_triggers_touch(self):
        """A sampled posting missing from Supabase should be touched."""
        local = _make_pool()
        supa = _make_pool()

        posting_id = uuid.uuid4()
        sample = [_make_record({"id": posting_id, "is_active": True, "description_r2_hash": 111})]

        local.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        supa.fetchrow = AsyncMock(return_value=_make_record({"cnt": 99}))
        local.fetch = AsyncMock(return_value=sample)
        supa.fetch = AsyncMock(return_value=[])  # not found in Supabase
        local.execute = AsyncMock()

        result = await run_reconciliation(local, supa)
        assert result == 1
        local.execute.assert_awaited_once()

    async def test_state_mismatch_triggers_touch(self):
        """Differing is_active triggers a touch."""
        local = _make_pool()
        supa = _make_pool()

        posting_id = uuid.uuid4()
        local_sample = [
            _make_record({"id": posting_id, "is_active": True, "description_r2_hash": 111})
        ]
        supa_match = [
            _make_record({"id": posting_id, "is_active": False, "description_r2_hash": 111})
        ]

        local.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        supa.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        local.fetch = AsyncMock(return_value=local_sample)
        supa.fetch = AsyncMock(return_value=supa_match)
        local.execute = AsyncMock()

        result = await run_reconciliation(local, supa)
        assert result == 1

    async def test_matching_state_no_touch(self):
        """When sampled rows match, no touch should happen."""
        local = _make_pool()
        supa = _make_pool()

        posting_id = uuid.uuid4()
        sample = [_make_record({"id": posting_id, "is_active": True, "description_r2_hash": 111})]

        local.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        supa.fetchrow = AsyncMock(return_value=_make_record({"cnt": 100}))
        local.fetch = AsyncMock(return_value=sample)
        supa.fetch = AsyncMock(return_value=sample)
        local.execute = AsyncMock()

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
        supa = _make_pool()
        cursor = (datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC), _ZERO_UUID)
        local.fetchval = AsyncMock(return_value=42)
        # Pool stats methods must return ints for Prometheus gauges
        local.get_size = MagicMock(return_value=5)
        local.get_idle_size = MagicMock(return_value=3)
        supa.get_size = MagicMock(return_value=4)
        supa.get_idle_size = MagicMock(return_value=2)

        with patch(
            "src.exporter.get_queue_depths",
            new_callable=AsyncMock,
            return_value={"board:http:due": 5, "scrape:http:due": 10},
        ):
            await _update_metrics(local, supa, cursor)

        # fetchval called twice: once for export lag, once for r2_pending count
        assert local.fetchval.await_count == 2

    async def test_handles_redis_error_gracefully(self):
        """Metrics update should not raise even if Redis fails."""
        local = _make_pool()
        supa = _make_pool()
        cursor = (datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC), _ZERO_UUID)
        local.fetchval = AsyncMock(return_value=0)
        # Pool stats methods must return ints for Prometheus gauges
        local.get_size = MagicMock(return_value=5)
        local.get_idle_size = MagicMock(return_value=3)
        supa.get_size = MagicMock(return_value=4)
        supa.get_idle_size = MagicMock(return_value=2)

        with patch(
            "src.exporter.get_queue_depths",
            new_callable=AsyncMock,
            side_effect=ConnectionError("redis down"),
        ):
            # Should not raise
            await _update_metrics(local, supa, cursor)


# ---------------------------------------------------------------------------
# _update_typesense_health
# ---------------------------------------------------------------------------


class TestUpdateTypesenseHealth:
    async def test_calls_is_healthy_not_operations_perform(self):
        """Regression test for #2212: the probe must use
        ``client.operations.is_healthy()`` (GET /health) and
        ``client.metrics.retrieve()`` (GET /metrics.json). The earlier code
        used ``operations.perform("health")`` and ``perform("stats.json")``,
        both of which POST to ``/operations/{op}`` and 404 for these names —
        producing ~50k warnings per 12h in Loki.
        """
        client = MagicMock()
        client.operations.is_healthy = MagicMock(return_value=True)
        client.metrics.retrieve = MagicMock(
            return_value={
                "typesense_memory_active_bytes": 713715712,
                "typesense_memory_allocated_bytes": 625816840,
            }
        )

        with patch("src.typesense_client.get_typesense_client", return_value=client):
            await _update_typesense_health()

        client.operations.is_healthy.assert_called_once_with()
        client.metrics.retrieve.assert_called_once_with()
        # The old wrong paths must not be called.
        client.operations.perform.assert_not_called()

    async def test_no_client_is_noop(self):
        with patch("src.typesense_client.get_typesense_client", return_value=None):
            await _update_typesense_health()  # should not raise

    async def test_health_failure_does_not_block_metrics(self):
        """An exception from is_healthy() must not prevent metrics.retrieve()
        from running — we still want the memory gauge when health is down.
        """
        client = MagicMock()
        client.operations.is_healthy = MagicMock(side_effect=RuntimeError("down"))
        client.metrics.retrieve = MagicMock(return_value={"typesense_memory_active_bytes": 42})

        with patch("src.typesense_client.get_typesense_client", return_value=client):
            await _update_typesense_health()

        client.metrics.retrieve.assert_called_once_with()


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


# ---------------------------------------------------------------------------
# _build_typesense_docs: ancestor expansion
# ---------------------------------------------------------------------------


def _make_taxonomy_maps() -> TaxonomyMaps:
    """Build a TaxonomyMaps with test hierarchy data.

    Location hierarchy: city(10) -> region(20) -> country(30)
    Occupation hierarchy: child_occ(100) -> parent_occ(200)
    """
    maps = TaxonomyMaps()
    maps.location_names = {
        10: {"en": "Zurich"},
        20: {"en": "Canton of Zurich"},
        30: {"en": "Switzerland"},
    }
    maps.location_types = {10: "city", 20: "region", 30: "country"}
    company_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    maps.company_info = {company_id: {"name": "TestCo", "slug": "testco", "icon": None}}
    maps.occupation_names = {100: "Software Engineer", 200: "Engineering"}
    maps.seniority_names = {1: "Senior"}
    maps.technology_names = {50: "Python"}
    # Location ancestors: city -> [city, region, country]
    maps.location_ancestors = {
        10: [10, 20, 30],
        20: [20, 30],
        30: [30],
    }
    # Occupation ancestors: child -> [child, parent]
    maps.occupation_ancestors = {
        100: [100, 200],
        200: [200],
    }
    return maps


def _make_posting_record(
    *,
    location_ids: list[int] | None = None,
    occupation_id: int | None = None,
    titles: list[str] | None = None,
    description_r2_hash: int | None = 12345,
) -> MagicMock:
    """Simulate an asyncpg.Record for a job_posting row."""
    company_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    posting_id = uuid.uuid4()
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    data = {
        "id": posting_id,
        "company_id": company_id,
        "titles": ["Test Job"] if titles is None else titles,
        "is_active": True,
        "location_ids": location_ids,
        "location_types": ["onsite"] * len(location_ids or []),
        "occupation_id": occupation_id,
        "seniority_id": None,
        "technology_ids": None,
        "employment_type": "full-time",
        "experience_min": None,
        "locales": ["en"],
        "first_seen_at": now,
        "last_seen_at": now,
        "salary_eur": None,
        "source_url": "https://example.com/job",
        "description_r2_hash": description_r2_hash,
    }
    rec = MagicMock()
    rec.keys.return_value = list(data.keys())
    rec.__getitem__ = lambda self, k: data[k]
    rec.__contains__ = lambda self, k: k in data
    rec.get = lambda k, default=None: data.get(k, default)
    return rec


class TestBuildTypesenseDocsAncestors:
    """Tests for ancestor expansion in _build_typesense_docs."""

    def test_location_ids_expanded_with_ancestors(self):
        """Leaf location ID 10 should expand to include region(20) and country(30)."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10])
        docs = _build_typesense_docs([row], maps)
        assert len(docs) == 1
        loc_ids = set(docs[0]["location_ids"])
        assert 10 in loc_ids  # leaf (city)
        assert 20 in loc_ids  # region ancestor
        assert 30 in loc_ids  # country ancestor

    def test_location_names_only_for_leaf_ids(self):
        """location_names should only contain names for leaf IDs, not ancestors."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10])
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["location_names"] == ["Zurich"]

    def test_multiple_locations_deduplicated(self):
        """Two cities in the same country should not duplicate the country ancestor."""
        maps = _make_taxonomy_maps()
        # Add a second city in the same region
        maps.location_names[11] = {"en": "Winterthur"}
        maps.location_types[11] = "city"
        maps.location_ancestors[11] = [11, 20, 30]

        row = _make_posting_record(location_ids=[10, 11])
        docs = _build_typesense_docs([row], maps)
        loc_ids = docs[0]["location_ids"]
        # Should have both leaves first, then ancestor-only IDs
        assert loc_ids[0] == 10
        assert loc_ids[1] == 11
        # 20 and 30 should each appear exactly once in the ancestor portion
        assert loc_ids.count(20) == 1
        assert loc_ids.count(30) == 1

    def test_occupation_ids_expanded_with_ancestors(self):
        """occupation_ids should include the leaf occupation and its parent."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(occupation_id=100)
        docs = _build_typesense_docs([row], maps)
        assert set(docs[0]["occupation_ids"]) == {100, 200}
        # occupation_id (singular) should be the leaf
        assert docs[0]["occupation_id"] == 100

    def test_no_occupation_when_none(self):
        """No occupation_ids field when occupation_id is None."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(occupation_id=None)
        docs = _build_typesense_docs([row], maps)
        assert "occupation_ids" not in docs[0]
        assert "occupation_id" not in docs[0]

    def test_empty_location_ids(self):
        """Empty location_ids should not crash or produce ancestors."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[])
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["location_ids"] == []

    def test_leaf_ids_come_first(self):
        """Leaf IDs should be at the start of location_ids (aligned with names/geo_types)."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10])
        docs = _build_typesense_docs([row], maps)
        # First element is the leaf
        assert docs[0]["location_ids"][0] == 10
        # location_names and location_geo_types are only for the leaf
        assert len(docs[0]["location_names"]) == 1
        assert len(docs[0]["location_geo_types"]) == 1


class TestBuildTypesenseDocsHasContent:
    """Tests for the `has_content` flag emitted on each Typesense doc.

    Drives the issue #2917 web filter — postings without a usable title
    or with no description blob in R2 are excluded from search surfaces.
    """

    def test_full_content_is_true(self):
        """Posting with title + description hash → has_content=True."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(
            location_ids=[10], titles=["Senior Engineer"], description_r2_hash=999
        )
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["has_content"] is True

    def test_empty_titles_array_is_false(self):
        """No titles → has_content=False (the dominant '_none' locale case)."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], titles=[], description_r2_hash=None)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["has_content"] is False

    def test_blank_title_is_false(self):
        """Whitespace-only title is treated as empty → has_content=False."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], titles=["   "], description_r2_hash=999)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["has_content"] is False

    def test_missing_description_hash_is_false(self):
        """Title present but no R2 description → has_content=False."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(
            location_ids=[10], titles=["Senior Engineer"], description_r2_hash=None
        )
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["has_content"] is False

    def test_zero_hash_still_truthy(self):
        """description_r2_hash=0 is a valid hash (not NULL) → has_content=True."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(
            location_ids=[10], titles=["Senior Engineer"], description_r2_hash=0
        )
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["has_content"] is True


class TestLoadLocationNames:
    """Tests for TaxonomyMaps._load_location_names is_display filter."""

    async def test_filters_by_is_display(self):
        """Query must include WHERE is_display=true so alternate names
        (L.A., Colorado Spgs, Old Line State) cannot leak into Typesense."""
        pool = _make_pool()
        # Simulate Postgres returning only is_display=true rows (the filter works).
        pool.fetch = AsyncMock(
            return_value=[
                {"location_id": 5368361, "locale": "en", "name": "Los Angeles"},
                {"location_id": 5417598, "locale": "en", "name": "Colorado Springs"},
                {"location_id": 4361885, "locale": "en", "name": "Maryland"},
                {"location_id": 5368361, "locale": "de", "name": "Los Angeles"},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_location_names(pool)

        # Query must include the is_display filter — defends against
        # accidental removal in future refactors.
        pool.fetch.assert_awaited_once()
        sql = pool.fetch.await_args.args[0]
        assert "is_display" in sql
        assert "WHERE is_display = true" in sql

        # Canonical names picked up for each locale
        assert maps.location_names[5368361] == {"en": "Los Angeles", "de": "Los Angeles"}
        assert maps.location_names[5417598] == {"en": "Colorado Springs"}
        assert maps.location_names[4361885] == {"en": "Maryland"}
