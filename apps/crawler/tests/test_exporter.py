from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.exporter import (
    _EPOCH,
    _ZERO_UUID,
    TaxonomyMaps,
    _build_typesense_docs,
    _export_changed_boards,
    _export_changed_postings,
    _get_cursor,
    _reconciliation_loop,
    _save_cursor,
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
        # Should have called CREATE TEMP TABLE, DELETE dedup, INSERT
        assert conn.execute.await_count == 3  # CREATE + DELETE dedup + INSERT
        conn.copy_records_to_table.assert_awaited_once()


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
) -> MagicMock:
    """Simulate an asyncpg.Record for a job_posting row."""
    company_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    posting_id = uuid.uuid4()
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    data = {
        "id": posting_id,
        "company_id": company_id,
        "titles": ["Test Job"],
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
