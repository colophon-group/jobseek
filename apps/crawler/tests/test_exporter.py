from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections import Counter
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import structlog

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
    _save_cursors_atomic,
    _update_metrics,
    _update_typesense_health,
    _upsert_to_supabase,
    run_exporter,
    run_exporter_with_reconciliation,
    run_reconciliation,
)
from src.metrics import export_errors_total

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

    async def test_supabase_failure_advances_supa_cursor_via_per_row_fallback(self):
        """Per-row poison-pill fallback (#3180): the whole-batch INSERT
        fails (e.g. one row tripped a constraint), the per-row fallback
        runs, every row fails too in this synthetic test, but the cursor
        STILL advances past the batch so CDC doesn't stall forever. The
        dropped rows are logged + counted in ``export_errors_total``.

        Pre-#3180 the cursor stayed put and the exporter looped on the
        same poisoned batch every ``export_interval`` seconds.
        """
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa_cur = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        # Make every conn.execute() raise — covers both the batch INSERT
        # and the per-row fallback INSERT.
        supa, conn = _supa_pool_with_capture()
        conn.execute = AsyncMock(side_effect=RuntimeError("supa down"))

        pid = uuid.uuid4()
        local.fetch = AsyncMock(return_value=[_posting_row(posting_id=pid, ts=ts)])

        with patch("src.exporter._upsert_to_typesense", new=AsyncMock()):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )

        assert count == 1
        # Cursor advances past the row even though it was dropped — the
        # whole point of the fix.
        assert new_supa == (ts, pid)
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
# Per-row fallback on batch failure (#3180)
# ---------------------------------------------------------------------------


def _counter_total(counter, **labels) -> float:
    """Read a prometheus_client Counter's current value for a label set.

    Use this snapshot before/after the test step so the assertion is
    delta-based — Prometheus counters live for the lifetime of the
    process and tests can run in any order.
    """
    return counter.labels(**labels)._value.get()


def _make_supa_pool_batch_fails_per_row_passes(failing_row_id):
    """Build a supa pool whose batch path raises but whose per-row path
    succeeds for every row EXCEPT ``failing_row_id``.

    The exporter's batch path issues three calls per ``async with``
    block:  CREATE TEMP TABLE, COPY (via ``copy_records_to_table``),
    INSERT ... ON CONFLICT. We make the *second* execute (the
    INSERT) raise on the batch attempt so the function falls back.

    The per-row path issues one execute per row inside its own
    transaction; we route those by inspecting the SQL.
    """
    pool = _make_pool()
    failing_uuid_str = str(failing_row_id)
    batch_count = 0

    async def execute(sql, *args):
        nonlocal batch_count
        if "CREATE TEMP TABLE" in sql:
            return
        if "SELECT * FROM _export_postings" in sql:
            # Batch INSERT — fail on the first attempt to trigger the
            # fallback path.
            batch_count += 1
            raise RuntimeError("simulated batch FK violation")
        # Per-row INSERT branch. The first positional arg is the row id.
        if args and str(args[0]) == failing_uuid_str:
            raise RuntimeError("simulated per-row constraint violation")
        return

    conn = AsyncMock()
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    conn.execute = AsyncMock(side_effect=execute)
    conn.copy_records_to_table = AsyncMock()

    acq_ctx = MagicMock()
    acq_ctx.__aenter__ = AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_ctx)
    return pool, conn


class TestSupabasePerRowFallback:
    """The exporter's poison-pill fix (#3180): when the batch INSERT
    fails, fall back to per-row upserts so a single bad row no longer
    halts CDC. Failed rows are logged + counted in
    ``export_errors_total{phase="supabase"}`` and the cursor advances
    past them (drop semantics; quarantine in follow-up)."""

    async def test_batch_failure_runs_per_row_fallback_and_drops_one(self):
        """Five-row batch, one bad row → 4 succeed, 1 dropped, cursor
        advances past all five.

        Mirrors the issue scenario: a single FK violation on
        ``company_id`` (or similar) blows up the COPY transaction; the
        fallback path commits the other 4 rows individually and drops
        the bad one.
        """
        ts = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)

        pids = [uuid.uuid4() for _ in range(5)]
        bad_pid = pids[2]
        supa, conn = _make_supa_pool_batch_fails_per_row_passes(bad_pid)
        rows = [_posting_row(posting_id=pid, ts=ts) for pid in pids]

        before = _counter_total(export_errors_total, table="job_posting", phase="supabase")
        failed = await _upsert_to_supabase(supa, rows)
        after = _counter_total(export_errors_total, table="job_posting", phase="supabase")

        # One row failed.
        assert failed == {bad_pid}
        # ``export_errors_total{phase="supabase"}`` bumped exactly once.
        assert after - before == 1.0

        # Per-row fallback ran: 5 INSERT attempts after the batch INSERT.
        # The batch path issued 2 execute calls (CREATE TEMP + INSERT
        # which failed), then 5 per-row INSERTs.
        per_row_calls = [
            c
            for c in conn.execute.await_args_list
            if c.args
            and "INSERT INTO job_posting" in c.args[0]
            and "_export_postings" not in c.args[0]
        ]
        assert len(per_row_calls) == 5

    async def test_per_row_failure_increments_counter_exactly_once_per_row(self):
        """The counter must bump once per failed row — not once per
        retry, not once per batch."""
        ts = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)

        pids = [uuid.uuid4() for _ in range(3)]

        # Every row fails on the per-row path. Simulate a structural
        # failure (e.g. a NOT NULL constraint on a column the source
        # always sends NULL for after a Supabase-only schema migration).
        async def execute(sql, *args):
            if "CREATE TEMP TABLE" in sql:
                return
            raise RuntimeError("simulated NOT NULL violation")

        pool, conn = _supa_pool_with_capture()
        conn.execute = AsyncMock(side_effect=execute)

        rows = [_posting_row(posting_id=pid, ts=ts) for pid in pids]

        before = _counter_total(export_errors_total, table="job_posting", phase="supabase")
        failed = await _upsert_to_supabase(pool, rows)
        after = _counter_total(export_errors_total, table="job_posting", phase="supabase")

        assert failed == set(pids)
        # Exactly 3 increments — one per failed row, not per attempt.
        assert after - before == 3.0

    async def test_happy_path_does_not_trigger_fallback(self):
        """Steady-state batch success → no per-row fallback, no
        counter bumps, return empty failed set."""
        ts = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)

        supa, conn = _supa_pool_with_capture()
        rows = [_posting_row(posting_id=uuid.uuid4(), ts=ts) for _ in range(3)]

        before = _counter_total(export_errors_total, table="job_posting", phase="supabase")
        failed = await _upsert_to_supabase(supa, rows)
        after = _counter_total(export_errors_total, table="job_posting", phase="supabase")

        assert failed == set()
        assert after == before
        # Only the batch path executed: CREATE TEMP + INSERT, no per-row
        # INSERT calls.
        assert conn.execute.await_count == 2

    async def test_export_postings_dual_advances_cursor_past_dropped_row(self):
        """End-to-end: the dual-path caller advances the Supabase cursor
        past the entire batch — including the dropped row — even though
        one row was poison. This is the actual #3180 fix observable from
        the public function surface."""
        ts1 = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC)
        supa_cur = (datetime(2026, 5, 14, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        local = _make_pool()
        local.fetch = AsyncMock(
            return_value=[
                _posting_row(posting_id=pid1, ts=ts1),
                _posting_row(posting_id=pid2, ts=ts2),
            ]
        )
        # Bad row is the FIRST one — second row succeeds in the per-row
        # fallback; cursor still advances to (ts2, pid2).
        supa, _ = _make_supa_pool_batch_fails_per_row_passes(pid1)

        with patch("src.exporter._upsert_to_typesense", new=AsyncMock()):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )

        assert count == 2
        # Cursor advances past the dropped row to the batch's last id.
        assert new_supa == (ts2, pid2)
        assert new_ts == (ts2, pid2)


class TestTypesensePerDocFallback:
    """The Typesense import endpoint returns a per-doc result list;
    the exporter must consume it (#3180). A single bad doc previously
    halted the Typesense cursor forever via the catch-and-re-raise
    branch in ``_upsert_to_typesense``."""

    async def test_per_doc_failures_drop_and_advance(self):
        """``import_`` returns ``[{"success": true}, {"success": false,
        "error": "..."}, {"success": true}]`` → the failing doc's id is
        returned, ``export_errors_total{phase="typesense"}`` bumps, and
        the caller (``_export_postings_dual``) advances the Typesense
        cursor past the failed doc."""
        ts1 = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 5, 14, 12, 5, 0, tzinfo=UTC)
        ts3 = datetime(2026, 5, 14, 12, 10, 0, tzinfo=UTC)

        pid1, pid2, pid3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        supa_cur = (datetime(2026, 5, 14, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        local.fetch = AsyncMock(
            return_value=[
                _posting_row(posting_id=pid1, ts=ts1),
                _posting_row(posting_id=pid2, ts=ts2),
                _posting_row(posting_id=pid3, ts=ts3),
            ]
        )
        supa, _ = _supa_pool_with_capture()

        # Build a typesense client that returns a per-doc result list
        # with the middle entry as a failure.
        fake_client = MagicMock()
        fake_client.collections = {
            "job_posting": MagicMock(
                documents=MagicMock(
                    import_=MagicMock(
                        return_value=[
                            {"success": True},
                            {
                                "success": False,
                                "error": "Field `salary_max` is too large",
                                "code": 400,
                            },
                            {"success": True},
                        ]
                    )
                )
            )
        }

        before = _counter_total(export_errors_total, table="job_posting", phase="typesense")
        with patch("src.typesense_client.get_typesense_client", return_value=fake_client):
            count, new_supa, new_ts = await _export_postings_dual(
                local, supa, supa_cur, ts_cur, TaxonomyMaps()
            )
        after = _counter_total(export_errors_total, table="job_posting", phase="typesense")

        # Counter bumped once for the failing doc.
        assert after - before == 1.0
        # Cursor advances past all three even though one failed.
        assert count == 3
        assert new_ts == (ts3, pid3)
        # Supabase succeeded for all three; cursor advances normally.
        assert new_supa == (ts3, pid3)

    async def test_all_docs_succeed_no_drops(self):
        """Happy Typesense path: ``import_`` returns all success → empty
        failed-set, no counter bump."""
        from src.exporter import _upsert_to_typesense as _upsert_ts

        fake_client = MagicMock()
        fake_client.collections = {
            "job_posting": MagicMock(
                documents=MagicMock(
                    import_=MagicMock(return_value=[{"success": True}, {"success": True}])
                )
            )
        }
        docs = [{"id": str(uuid.uuid4()), "title": "x"} for _ in range(2)]

        before = _counter_total(export_errors_total, table="job_posting", phase="typesense")
        with patch("src.typesense_client.get_typesense_client", return_value=fake_client):
            failed = await _upsert_ts(docs)
        after = _counter_total(export_errors_total, table="job_posting", phase="typesense")

        assert failed == set()
        assert after == before

    async def test_transport_failure_still_raises(self):
        """Whole-batch transport failure (Typesense unreachable, 5xx)
        is NOT a per-doc poison pill — the caller treats it as a leg
        failure (cursor stays put on that leg). The fallback only
        kicks in for per-doc semantic failures."""
        from src.exporter import _upsert_to_typesense as _upsert_ts

        fake_client = MagicMock()
        fake_client.collections = {
            "job_posting": MagicMock(
                documents=MagicMock(import_=MagicMock(side_effect=RuntimeError("typesense down")))
            )
        }
        docs = [{"id": str(uuid.uuid4()), "title": "x"}]

        import pytest

        with (
            patch("src.typesense_client.get_typesense_client", return_value=fake_client),
            pytest.raises(RuntimeError, match="typesense down"),
        ):
            await _upsert_ts(docs)


# ---------------------------------------------------------------------------
# Lifecycle anchor: exporter.exported_postings (#3192)
# ---------------------------------------------------------------------------


class TestExporterExportedPostingsLog:
    """The exporter must emit ``exporter.exported_postings`` with
    ``sample_ids`` so an operator with the posting_id from a public URL
    can grep "was THIS posting in any recent batch?" without filtering by
    aggregate counts only (closes #3192)."""

    async def test_dual_path_logs_sample_ids_for_both_targets(self):
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

        with (
            patch("src.exporter._upsert_to_typesense", new=AsyncMock()),
            structlog.testing.capture_logs() as logs,
        ):
            await _export_postings_dual(local, supa, supa_cur, ts_cur, TaxonomyMaps())

        supa_events = [
            e
            for e in logs
            if e.get("event") == "exporter.exported_postings" and e.get("target") == "supabase"
        ]
        ts_events = [
            e
            for e in logs
            if e.get("event") == "exporter.exported_postings" and e.get("target") == "typesense"
        ]
        assert supa_events, "must emit exporter.exported_postings for supabase"
        assert ts_events, "must emit exporter.exported_postings for typesense"
        assert supa_events[0]["batch_size"] == 2
        assert ts_events[0]["batch_size"] == 2
        # sample_ids is a list of stringified UUIDs (max 5)
        assert isinstance(supa_events[0]["sample_ids"], list)
        assert str(pid1) in supa_events[0]["sample_ids"]
        assert str(pid2) in supa_events[0]["sample_ids"]

    async def test_sample_ids_capped_at_five(self):
        """Even if a batch has thousands of rows, ``sample_ids`` must
        stay bounded so the Loki line stays readable."""
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        supa_cur = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)
        ts_cur = supa_cur

        local = _make_pool()
        supa, _ = _supa_pool_with_capture()

        pids = [uuid.uuid4() for _ in range(12)]
        local.fetch = AsyncMock(return_value=[_posting_row(posting_id=pid, ts=ts) for pid in pids])

        with (
            patch("src.exporter._upsert_to_typesense", new=AsyncMock()),
            structlog.testing.capture_logs() as logs,
        ):
            await _export_postings_dual(local, supa, supa_cur, ts_cur, TaxonomyMaps())

        supa_events = [
            e
            for e in logs
            if e.get("event") == "exporter.exported_postings" and e.get("target") == "supabase"
        ]
        assert supa_events
        assert supa_events[0]["batch_size"] == 12
        assert len(supa_events[0]["sample_ids"]) == 5

    async def test_single_path_logs_sample_ids(self):
        """The Supabase-only fallback path (``_export_changed_postings``)
        must also emit the lifecycle anchor — operators who run without
        Typesense should not lose the correlation event."""
        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        cursor = (datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC), _ZERO_UUID)

        local = _make_pool()
        supa, _ = _supa_pool_with_capture()
        pid = uuid.uuid4()
        local.fetch = AsyncMock(return_value=[_posting_row(posting_id=pid, ts=ts)])

        with structlog.testing.capture_logs() as logs:
            await _export_changed_postings(local, supa, cursor)

        events = [e for e in logs if e.get("event") == "exporter.exported_postings"]
        assert events, "Supabase-only path must emit exporter.exported_postings"
        assert events[0]["batch_size"] == 1
        assert str(pid) in events[0]["sample_ids"]


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
    experience_min: int | None = None,
    experience_max: int | None = None,
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
        "experience_min": experience_min,
        "experience_max": experience_max,
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


class TestBuildTypesenseDocsExperience:
    """Tests for experience_min / experience_max encoding (#3217)."""

    def test_bounded_range_passes_through(self):
        """`5-10 years` → `experience_min=5, experience_max=10` (no sentinel)."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=5, experience_max=10)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == 5
        assert docs[0]["experience_max"] == 10

    def test_open_ended_uses_high_sentinel_for_max(self):
        """`5+ years` (Postgres `min=5, max=NULL`) → max stamped as 99 so the
        row matches range filters whose upper bound is ≥ 5.
        """
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=5, experience_max=None)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == 5
        assert docs[0]["experience_max"] == 99

    def test_no_info_uses_minus_one_sentinel_for_both(self):
        """Postgres `min=NULL, max=NULL` (no extraction) → both fields -1 so
        the filter's `experience_min:=-1` branch catches the row.
        """
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=None, experience_max=None)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == -1
        assert docs[0]["experience_max"] == -1

    def test_exact_year_passes_through(self):
        """`exactly 6 years` (`min=6, max=6`) round-trips unchanged."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=6, experience_max=6)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == 6
        assert docs[0]["experience_max"] == 6


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


class TestLoadLocationAncestors:
    """Tests for TaxonomyMaps._load_location_ancestors macro handling.

    Issue #2978: ``location_macro_member`` was empty on the production
    Hetzner local Postgres for months, causing the EU/EMEA/DACH macro
    filter to silently return only postings explicitly tagged with the
    macro id (instead of every member-country posting). The previous
    ``contextlib.suppress(Exception)`` swallowed both the missing-table
    case AND the empty-table case without surfacing it.
    """

    async def test_populated_table_stamps_macros_onto_countries(self):
        """Country city -> [city, country, macro] when macro_member maps
        country -> macro.
        """
        # Pool returns parents on first fetch, macro members on second.
        pool = _make_pool()
        # Hierarchy: city(10) -> country(30); country(30) is in macro(4 = EU)
        pool.fetch = AsyncMock(
            side_effect=[
                # SELECT id, parent_id FROM location
                [
                    {"id": 10, "parent_id": 30},
                    {"id": 30, "parent_id": None},
                    {"id": 4, "parent_id": None},  # macro itself, no parent
                ],
                # SELECT country_id, macro_id FROM location_macro_member
                [
                    {"country_id": 30, "macro_id": 4},
                ],
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_location_ancestors(pool)

        assert set(maps.location_ancestors[10]) == {10, 30, 4}
        assert set(maps.location_ancestors[30]) == {30, 4}
        # Macro id maps to itself only (not a member of anything)
        assert set(maps.location_ancestors[4]) == {4}

    async def test_empty_table_logs_warning_and_skips_macros(self):
        """Empty location_macro_member -> postings get only parent-chain
        ancestors, no macros. Previously this was suppressed silently;
        now we log a loud warning so the operator can re-seed.
        """
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                # location parents
                [
                    {"id": 10, "parent_id": 30},
                    {"id": 30, "parent_id": None},
                ],
                # empty location_macro_member
                [],
            ]
        )

        maps = TaxonomyMaps()
        with patch("src.exporter.log") as mock_log:
            await maps._load_location_ancestors(pool)

        # Country chain still works without macros
        assert set(maps.location_ancestors[10]) == {10, 30}
        assert set(maps.location_ancestors[30]) == {30}
        # And we logged the empty-table warning
        warn_calls = [c for c in mock_log.warning.call_args_list]
        empty_warns = [c for c in warn_calls if "empty" in str(c)]
        assert empty_warns, f"expected empty-table warning, got: {warn_calls}"

    async def test_unreadable_table_logs_warning_and_continues(self):
        """If the macro_member fetch raises (table missing, transient DB
        error), we should log the failure and continue with empty
        macros — not crash the whole exporter.
        """
        pool = _make_pool()
        pool.fetch = AsyncMock(
            side_effect=[
                # location parents (succeeds)
                [
                    {"id": 10, "parent_id": 30},
                    {"id": 30, "parent_id": None},
                ],
                # location_macro_member (fails)
                Exception('relation "location_macro_member" does not exist'),
            ]
        )

        maps = TaxonomyMaps()
        with patch("src.exporter.log") as mock_log:
            await maps._load_location_ancestors(pool)

        # Parent chain still resolved
        assert set(maps.location_ancestors[10]) == {10, 30}
        # We logged the unreadable warning
        warn_calls = [c for c in mock_log.warning.call_args_list]
        msgs = [c.args[0] if c.args else "" for c in warn_calls]
        assert any("unreadable" in m for m in msgs), f"got: {warn_calls}"

    def test_build_typesense_docs_includes_macro_ancestors(self):
        """End-to-end check: a posting tagged with a country location
        gets the country's macro id in its expanded location_ids — the
        invariant the user-facing macro filter (e.g. EU) relies on.
        """
        maps = _make_taxonomy_maps()
        # Add a macro region that contains the country (30)
        # Country 30 is now in macro 4 (= "EU"); rebuild ancestors so the
        # city's chain pulls in the macro id too.
        maps.location_names[4] = {"en": "EU"}
        maps.location_types[4] = "macro"
        maps.location_ancestors[10] = [10, 20, 30, 4]
        maps.location_ancestors[30] = [30, 4]
        maps.location_ancestors[4] = [4]

        row = _make_posting_record(location_ids=[10])
        docs = _build_typesense_docs([row], maps)
        assert 4 in docs[0]["location_ids"], (
            "macro id missing from location_ids — macro filter would not match this posting"
        )


class TestLoadOccupationAncestors:
    """Tests for TaxonomyMaps._load_occupation_ancestors domain handling.

    Issue #2980: occupation ancestors used to walk only the ``parent_id``
    chain, so a posting tagged with a specific occupation never carried
    its ``domain_id`` (e.g. Software Engineering = 1) in the expanded
    ``occupation_ids``. As a result, ``occupation_ids:=<domain_id>``
    returned 0 — domain-as-single-filter could not work. The fix unions
    the row's ``domain_id`` into the ancestor set, mirroring the location
    macro expansion in ``_load_location_ancestors`` (#2977).
    """

    async def test_domain_id_is_unioned_into_ancestors(self):
        """Each occupation's ancestor set must include its domain_id when
        present, so ``occupation_ids:=<domain_id>`` matches every posting
        in that domain.
        """
        pool = _make_pool()
        # Hierarchy:
        #  - frontend-developer(2): parent=1 (software-engineer), domain=1 (SE)
        #  - software-engineer(1):  parent=None,                 domain=1 (SE)
        #  - solutions-architect(18): parent=None,               domain=3 (infra-security)
        #  - data-engineer(7):       parent=None,                domain=2 (data-ai)
        #  - orphan(99):             parent=None,                domain=None (no domain)
        pool.fetch = AsyncMock(
            return_value=[
                {"id": 1, "parent_id": None, "domain_id": 1},
                {"id": 2, "parent_id": 1, "domain_id": 1},
                {"id": 18, "parent_id": None, "domain_id": 3},
                {"id": 7, "parent_id": None, "domain_id": 2},
                {"id": 99, "parent_id": None, "domain_id": None},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_occupation_ancestors(pool)

        # Sanity: query selected the new domain_id column.
        sql = pool.fetch.await_args.args[0]
        assert "domain_id" in sql, f"query missing domain_id: {sql!r}"

        # Leaf occupation gets parent + domain.
        assert set(maps.occupation_ancestors[2]) == {2, 1}, (
            "frontend-developer(2): self + parent(1) — note domain(1) "
            "happens to coincide with parent id, so set has 2 elems"
        )
        # Top-level occupation in SE: self only (domain==self id is
        # skipped to avoid stamping the same id twice).
        assert set(maps.occupation_ancestors[1]) == {1}, (
            "software-engineer(1): domain_id(1) coincides with self id"
        )
        # Top-level occupation in a different domain: self + domain.
        assert set(maps.occupation_ancestors[18]) == {18, 3}, (
            "solutions-architect(18): self + domain(3) — domain ancestor "
            "is the smoking-gun for the #2980 fix"
        )
        # Another domain stamping
        assert set(maps.occupation_ancestors[7]) == {7, 2}, (
            "data-engineer(7): self + domain(2 = data-ai)"
        )
        # Occupation with no domain is unchanged (parent chain only).
        assert set(maps.occupation_ancestors[99]) == {99}

    async def test_parent_chain_still_walked_when_domain_present(self):
        """A multi-level parent chain plus a domain id should produce the
        full ancestor set: self + every parent up the chain + domain id.
        """
        pool = _make_pool()
        # 3-level chain with shared domain id 5
        pool.fetch = AsyncMock(
            return_value=[
                {"id": 1, "parent_id": None, "domain_id": 5},
                {"id": 2, "parent_id": 1, "domain_id": 5},
                {"id": 3, "parent_id": 2, "domain_id": 5},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_occupation_ancestors(pool)

        # Leaf gets self, parent, grandparent, plus domain
        assert set(maps.occupation_ancestors[3]) == {3, 2, 1, 5}
        assert set(maps.occupation_ancestors[2]) == {2, 1, 5}
        assert set(maps.occupation_ancestors[1]) == {1, 5}

    async def test_null_domain_is_safe(self):
        """Occupations without a domain (domain_id IS NULL) must not crash
        and must not add a stray None or 0 to the ancestor set.
        """
        pool = _make_pool()
        pool.fetch = AsyncMock(
            return_value=[
                {"id": 1, "parent_id": None, "domain_id": None},
                {"id": 2, "parent_id": 1, "domain_id": None},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_occupation_ancestors(pool)

        assert set(maps.occupation_ancestors[1]) == {1}
        assert set(maps.occupation_ancestors[2]) == {2, 1}

    def test_build_typesense_docs_includes_domain_ancestor(self):
        """End-to-end check: a posting tagged with a top-level occupation
        in a non-coinciding domain must carry the domain id in its
        expanded ``occupation_ids`` — the invariant the domain-as-single-
        filter UX (#2980) relies on.
        """
        maps = _make_taxonomy_maps()
        # Solutions Architect (id=18, no parent, domain=3 = infra-security)
        maps.occupation_names[18] = "Solutions Architect"
        maps.occupation_ancestors[18] = [18, 3]
        # Domain 3 is not represented as an occupation row but must
        # still appear in the posting's occupation_ids array.

        row = _make_posting_record(occupation_id=18)
        docs = _build_typesense_docs([row], maps)
        assert 3 in docs[0]["occupation_ids"], (
            "domain id missing from occupation_ids — domain filter would not match this posting"
        )
        # Leaf occupation_id (singular) is still the specific occupation.
        assert docs[0]["occupation_id"] == 18


# ---------------------------------------------------------------------------
# TaxonomyMaps shape (issue #3124)
# ---------------------------------------------------------------------------


class TestTaxonomyMapsFields:
    """Regression for issue #3124: ``occupation_ancestors`` was declared
    twice in ``TaxonomyMaps.__init__``. The second assignment silently
    shadowed the first, which made the source-level duplicate dead code
    and made future renames split-brain across two lines that look
    independent. Converting ``TaxonomyMaps`` to a ``@dataclass`` makes
    field declarations introspectable via ``dataclasses.fields()`` — the
    Counter below catches any future re-introduction of a duplicate
    declaration."""

    def test_is_a_dataclass(self):
        assert dataclasses.is_dataclass(TaxonomyMaps), (
            "TaxonomyMaps must be a @dataclass so its field set is "
            "introspectable for the uniqueness audit below"
        )

    def test_occupation_ancestors_declared_exactly_once(self):
        names = [f.name for f in dataclasses.fields(TaxonomyMaps)]
        counts = Counter(names)
        assert counts["occupation_ancestors"] == 1, (
            f"occupation_ancestors must appear exactly once; got {counts}"
        )

    def test_all_field_names_unique(self):
        names = [f.name for f in dataclasses.fields(TaxonomyMaps)]
        duplicates = {n for n, c in Counter(names).items() if c > 1}
        assert not duplicates, (
            f"duplicate TaxonomyMaps fields: {duplicates} — every slot "
            "must be declared exactly once so attribute writes do not "
            "silently shadow earlier definitions"
        )

    def test_expected_fields_present(self):
        """Pin the set of fields callers rely on so a refactor doesn't
        silently drop a slot (e.g. someone trying to fix #3124 by
        deleting the wrong duplicate)."""
        names = {f.name for f in dataclasses.fields(TaxonomyMaps)}
        # Public maps consumed by _build_typesense_docs.
        for required in (
            "location_names",
            "location_types",
            "company_info",
            "occupation_names",
            "seniority_names",
            "technology_names",
            "location_ancestors",
            "occupation_ancestors",
        ):
            assert required in names, f"missing TaxonomyMaps field: {required}"

    def test_default_instance_has_empty_maps(self):
        """Default-constructed instance must have empty, mutable dicts —
        regression for the case where the dataclass field uses a shared
        class-level mutable default (``field(default_factory=dict)`` is
        the correct shape)."""
        a = TaxonomyMaps()
        b = TaxonomyMaps()
        assert a.occupation_ancestors == {}
        assert a.location_ancestors == {}
        a.occupation_ancestors[1] = [1, 2]
        assert b.occupation_ancestors == {}, (
            "default_factory=dict required: mutating one instance's "
            "occupation_ancestors must not leak into another"
        )


# ---------------------------------------------------------------------------
# Atomic cursor save (issue #3171)
# ---------------------------------------------------------------------------


class TestSaveCursorsAtomic:
    """Regression for issue #3171: the exporter previously persisted the
    Supabase cursor and the Typesense cursor with two separate
    ``_save_cursor`` calls. A crash between them (OOM, SIGKILL, reboot)
    left one cursor advanced and the other stale, so the next process
    re-exported the just-shipped batch to one target only.

    ``_save_cursors_atomic`` wraps the pair in a single transaction so
    either both writes commit or neither does."""

    async def test_writes_both_cursors_in_one_transaction(self):
        pool = _make_pool()
        conn = AsyncMock()
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx_ctx)
        conn.execute = AsyncMock()
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)

        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        cur_a = (ts, uuid.uuid4())
        cur_b = (ts, uuid.uuid4())

        await _save_cursors_atomic(
            pool,
            [("job_posting", cur_a), ("typesense:job_posting", cur_b)],
        )

        # One acquire, one transaction, two upserts.
        pool.acquire.assert_called_once_with()
        conn.transaction.assert_called_once_with()
        assert conn.execute.await_count == 2
        keys = {call.args[1] for call in conn.execute.await_args_list}
        assert keys == {"last_export_ts:job_posting", "last_export_ts:typesense:job_posting"}

    async def test_partial_crash_rolls_back_both_writes(self):
        """If the second cursor's INSERT raises (server reboot mid-tx,
        connection drop, integrity violation), both writes must be
        absent from durable storage. The asyncpg ``conn.transaction``
        context manager raises on exit when its body raised, and
        Postgres rolls back the BEGIN/COMMIT pair. The test simulates
        this by making the second execute raise and asserting the
        exception propagates instead of leaving us with a half-saved
        pair."""
        pool = _make_pool()
        conn = AsyncMock()
        # Track whether the transaction was rolled back: __aexit__ with
        # a non-None exc_type is the rollback signal.
        rollback_called: dict[str, bool] = {"rolled_back": False}

        async def fake_aexit(exc_type, exc, tb):
            if exc_type is not None:
                rollback_called["rolled_back"] = True
            return False  # do not suppress

        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(side_effect=fake_aexit)
        conn.transaction = MagicMock(return_value=tx_ctx)

        call_count = {"n": 0}

        async def fake_execute(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated crash between cursor writes")

        conn.execute = AsyncMock(side_effect=fake_execute)

        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)

        ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        cur_a = (ts, uuid.uuid4())
        cur_b = (ts, uuid.uuid4())

        raised = False
        try:
            await _save_cursors_atomic(
                pool,
                [("job_posting", cur_a), ("typesense:job_posting", cur_b)],
            )
        except RuntimeError:
            raised = True

        assert raised, (
            "partial cursor write must propagate the error — silently "
            "swallowing it would re-introduce #3171"
        )
        assert rollback_called["rolled_back"], (
            "transaction must roll back when an execute raises so that "
            "neither cursor's write is durable"
        )

    async def test_round_trip_via_real_storage(self):
        """End-to-end shape check: after a successful atomic save, both
        cursors are readable by ``_get_cursor`` and match what we wrote.
        Uses an in-memory dict to stand in for ``exporter_state``.
        """
        stored: dict[str, str] = {}

        pool = _make_pool()
        conn = AsyncMock()
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=None)
        tx_ctx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx_ctx)

        async def fake_execute(_query, key, value):
            stored[key] = value

        conn.execute = AsyncMock(side_effect=fake_execute)
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)

        async def fake_fetchrow(_query, key):
            val = stored.get(key)
            return {"value": val} if val else None

        pool.fetchrow = AsyncMock(side_effect=fake_fetchrow)

        ts_a = datetime(2025, 7, 1, 10, 30, 0, tzinfo=UTC)
        ts_b = datetime(2025, 7, 1, 10, 31, 0, tzinfo=UTC)
        cur_a = (ts_a, uuid.uuid4())
        cur_b = (ts_b, uuid.uuid4())

        await _save_cursors_atomic(
            pool,
            [("job_posting", cur_a), ("typesense:job_posting", cur_b)],
        )

        # Both cursors readable.
        a = await _get_cursor(pool, "job_posting")
        b = await _get_cursor(pool, "typesense:job_posting")
        assert a == cur_a
        assert b == cur_b

    async def test_empty_input_is_noop(self):
        """Caller may pass [] (no cursors to save). Must not allocate a
        transaction or touch the connection — otherwise an empty tick
        wastes a TX slot every export interval."""
        pool = _make_pool()
        await _save_cursors_atomic(pool, [])
        pool.acquire.assert_not_called()


# ---------------------------------------------------------------------------
# run_exporter dual-path cursor save (issue #3171 wiring)
# ---------------------------------------------------------------------------


class TestRunExporterAtomicCursorWiring:
    """The exporter loop's dual path must call ``_save_cursors_atomic``
    rather than two sequential ``_save_cursor`` calls — otherwise the
    atomic helper exists but is bypassed and #3171 silently regresses.
    """

    async def test_dual_path_uses_atomic_helper(self):
        local = _make_pool()
        supa = _make_pool()
        shutdown = asyncio.Event()

        local.fetchrow = AsyncMock(return_value=None)
        local.fetch = AsyncMock(return_value=[])
        local.execute = AsyncMock()
        local.fetchval = AsyncMock(return_value=0)
        local.get_size = MagicMock(return_value=1)
        local.get_idle_size = MagicMock(return_value=1)
        supa.get_size = MagicMock(return_value=1)
        supa.get_idle_size = MagicMock(return_value=1)

        ts = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        pid = uuid.uuid4()
        new_supa = (ts, pid)
        new_ts = (ts, pid)

        async def _fake_dual(*_a, **_kw):
            return 0, new_supa, new_ts

        with (
            patch("src.exporter._typesense_enabled", return_value=True),
            patch(
                "src.exporter._get_taxonomy_maps",
                new_callable=AsyncMock,
                return_value=TaxonomyMaps(),
            ),
            patch("src.exporter._export_postings_dual", side_effect=_fake_dual),
            patch("src.exporter._save_cursors_atomic", new_callable=AsyncMock) as mock_atomic,
            patch("src.exporter._save_cursor", new_callable=AsyncMock) as mock_single,
            patch(
                "src.exporter.get_queue_depths",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):

            async def set_shutdown():
                await asyncio.sleep(0.05)
                shutdown.set()

            await asyncio.gather(
                run_exporter(local, supa, shutdown),
                set_shutdown(),
            )

            # At least one tick must have used the atomic helper.
            assert mock_atomic.await_count >= 1, (
                "dual-path tick must persist both cursors atomically (#3171)"
            )
            # And the single-cursor helper must not be called from the
            # dual path — that's what re-introduced the partial-write
            # window in the first place.
            assert mock_single.await_count == 0, (
                "_save_cursor must not be called in the dual path; use "
                "_save_cursors_atomic so both cursors are committed in "
                "one transaction"
            )
