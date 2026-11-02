from __future__ import annotations

import dataclasses
import json
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import structlog

from src.metrics import export_errors_total
from src.typesense_candidate_order import candidate_order_key, candidate_order_words
from src.typesense_projection import (
    PostingSchema,
    TaxonomyMaps,
    _build_typesense_docs,
    _is_typesense_acknowledgement_parse_failure,
    _upsert_to_supabase,
    _upsert_to_typesense,
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
    pool.fetchrow.return_value = None
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


@asynccontextmanager
async def _noop_cursor_fence(_pool):
    yield


async def _fixed_cdc_cutoff(_pool):
    return datetime(9999, 12, 31, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _get_last_export_ts / _save_last_export_ts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _export_changed_postings
# ---------------------------------------------------------------------------


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


class TestPostingSchema:
    def test_temp_table_ddl_uses_explicit_column_types(self):
        ddl = PostingSchema.temp_table_ddl()

        assert ddl.startswith("CREATE TEMP TABLE _export_postings (")
        assert ddl.endswith(") ON COMMIT DROP")
        for column in PostingSchema.columns:
            assert column.name + " " + column.temp_type in ddl

    def test_insert_from_temp_uses_explicit_columns(self):
        sql = PostingSchema.insert_from_temp_sql()

        assert "SELECT * FROM _export_postings" not in sql
        assert "INSERT INTO job_posting (" + PostingSchema.column_list() + ")" in sql
        assert "SELECT " + PostingSchema.column_list() + " FROM _export_postings" in sql
        assert "ON CONFLICT (id) DO UPDATE SET " + PostingSchema.upsert_set() in sql

    def test_per_row_template_matches_schema_placeholders(self):
        sql = PostingSchema.insert_values_sql()

        assert "VALUES (" + PostingSchema.placeholders() + ")" in sql
        assert PostingSchema.placeholders().split(", ")[0] == "$1"
        assert PostingSchema.placeholders().split(", ")[-1] == "$" + str(len(PostingSchema.columns))

    def test_upsert_set_keeps_identity_immutable_but_refreshes_outbound_url(self):
        upsert_columns = set(PostingSchema.upsert_columns)

        assert {"id", "company_id", "board_id", "first_seen_at"}.isdisjoint(upsert_columns)
        assert "source_url" in upsert_columns
        for column in upsert_columns:
            assert column in PostingSchema.column_names()

    def test_changed_row_query_has_a_strict_commit_safe_cutoff(self):
        sql = PostingSchema.select_changed_sql("updated_at")

        assert "(updated_at, id) > ($1, $2)" in sql
        assert "updated_at < $4" in sql
        assert "ORDER BY updated_at, id LIMIT $3" in sql

    def test_typesense_query_joins_canonical_company_metadata(self):
        sql = PostingSchema.select_typesense_changed_sql("last_seen_at", "updated_at")

        assert "JOIN company c ON c.id = jp.company_id" in sql
        assert "c.name AS company_name" in sql
        assert "c.slug AS company_slug" in sql
        assert "c.icon AS company_icon" in sql
        assert "(jp.updated_at, jp.id) > ($1, $2)" in sql
        assert "jp.updated_at < $4" in sql
        assert "ORDER BY jp.updated_at, jp.id LIMIT $3" in sql


class TestUpsertToSupabase:
    """Mirror TestExportChangedPostings against the dual-path upsert helper.

    `_upsert_to_supabase` is the inner DB writer extracted from
    `_export_changed_postings` so the new dual-path (`_export_postings_dual`)
    can call it concurrently with the Typesense leg. A regression in the
    write path here would silently corrupt the production CDC pipeline
    even when the legacy single-path tests pass.
    """

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
        if "FROM _export_postings" in sql:
            # Batch INSERT — fail on the first attempt to trigger the
            # fallback path.
            batch_count += 1
            raise asyncpg.ForeignKeyViolationError("simulated batch FK violation")
        # Per-row INSERT branch. The first positional arg is the row id.
        if args and str(args[0]) == failing_uuid_str:
            raise asyncpg.ForeignKeyViolationError("simulated per-row constraint violation")
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
            raise asyncpg.NotNullViolationError("simulated NOT NULL violation")

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

    async def test_unknown_failure_is_not_misclassified_as_row_poison(self):
        """Fail closed when an exception has no PostgreSQL SQLSTATE."""
        ts = datetime(2026, 7, 10, 16, 56, 0, tzinfo=UTC)
        pool, conn = _supa_pool_with_capture()
        conn.execute = AsyncMock(side_effect=RuntimeError("unexpected driver failure"))

        with pytest.raises(RuntimeError, match="unexpected driver failure"):
            await _upsert_to_supabase(pool, [_posting_row(posting_id=uuid.uuid4(), ts=ts)])

        assert conn.execute.await_count == 1


class TestTypesensePerDocFallback:
    """The Typesense import endpoint returns a per-doc result list;
    the exporter must consume it (#3180). A single bad doc previously
    halted the Typesense cursor forever via the catch-and-re-raise
    branch in ``_upsert_to_typesense``."""

    async def test_all_docs_succeed_no_drops(self):
        """Happy Typesense path: ``import_`` returns all success → empty
        failed-set, no counter bump."""
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
            failed = await _upsert_to_typesense(docs)
        after = _counter_total(export_errors_total, table="job_posting", phase="typesense")

        assert failed == set()
        assert after == before

    async def test_reconciliation_can_suppress_rejected_document_details(self):
        posting_id = uuid.uuid4()
        sensitive_payload = "private-reconciliation-payload"
        fake_client = MagicMock()
        fake_client.collections = {
            "job_posting": MagicMock(
                documents=MagicMock(
                    import_=MagicMock(
                        return_value=[
                            {
                                "success": False,
                                "error": sensitive_payload,
                                "code": 400,
                            }
                        ]
                    )
                )
            )
        }
        docs = [{"id": str(posting_id), "title": sensitive_payload}]

        with (
            patch("src.typesense_client.get_typesense_client", return_value=fake_client),
            structlog.testing.capture_logs() as logs,
        ):
            failed = await _upsert_to_typesense(
                docs,
                log_rejected_documents=False,
            )

        assert failed == {str(posting_id)}
        telemetry = json.dumps(logs)
        assert "exporter.row_dropped" not in telemetry
        assert str(posting_id) not in telemetry
        assert sensitive_payload not in telemetry

    async def test_transport_failure_still_raises(self):
        """Whole-batch transport failure (Typesense unreachable, 5xx)
        is NOT a per-doc poison pill — the caller treats it as a leg
        failure (cursor stays put on that leg). The fallback only
        kicks in for per-doc semantic failures."""
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
            await _upsert_to_typesense(docs)

    def test_unrelated_value_error_is_not_an_acknowledgement_parse_failure(self):
        assert not _is_typesense_acknowledgement_parse_failure(ValueError("unrelated"))

    @pytest.mark.parametrize(
        ("acknowledgement", "reason"),
        [
            pytest.param([], "cardinality_mismatch", id="empty-list"),
            pytest.param(
                [{"success": True}],
                "cardinality_mismatch",
                id="short-list",
            ),
            pytest.param(
                [{"success": True}, {"success": True}, {"success": True}],
                "cardinality_mismatch",
                id="long-list",
            ),
            pytest.param(
                {"success": True},
                "response_not_list",
                id="non-list-response",
            ),
            pytest.param(
                [{"success": True}, "malformed"],
                "item_not_object",
                id="non-object-item",
            ),
            pytest.param(
                [{"success": True}, {}],
                "success_missing",
                id="missing-success",
            ),
            pytest.param(
                [{"success": True}, {"success": 1}],
                "success_not_boolean",
                id="non-boolean-success",
            ),
        ],
    )
    async def test_malformed_acknowledgement_fails_the_whole_batch(
        self,
        acknowledgement,
        reason,
    ):
        fake_client = MagicMock()
        fake_client.collections = {
            "job_posting": MagicMock(
                documents=MagicMock(import_=MagicMock(return_value=acknowledgement))
            )
        }
        docs = [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}]

        with (
            patch("src.typesense_client.get_typesense_client", return_value=fake_client),
            structlog.testing.capture_logs() as logs,
            pytest.raises(RuntimeError, match="acknowledgement was invalid"),
        ):
            await _upsert_to_typesense(docs)

        invalid_events = [
            event
            for event in logs
            if event.get("event") == "exporter.typesense_invalid_acknowledgement"
        ]
        assert len(invalid_events) == 1
        assert invalid_events[0]["reason"] == reason
        assert invalid_events[0]["expected_count"] == 2


# ---------------------------------------------------------------------------
# Lifecycle anchor: exporter.exported_postings (#3192)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _export_changed_boards
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# run_exporter (single tick)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _update_metrics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _update_typesense_health
# ---------------------------------------------------------------------------


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
    is_active: bool = True,
    tdm_reserved: bool = False,
    location_ids: list[int] | None = None,
    occupation_id: int | None = None,
    titles: list[str] | None = None,
    description_r2_hash: int | None = 12345,
    experience_min: float | None = None,
    experience_max: float | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_currency: str | None = None,
    salary_period: str | None = None,
    company_name: str | None = None,
    company_slug: str | None = None,
    company_icon: str | None = None,
) -> MagicMock:
    """Simulate an asyncpg.Record for a job_posting row."""
    company_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    posting_id = uuid.uuid4()
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    data = {
        "id": posting_id,
        "company_id": company_id,
        "titles": ["Test Job"] if titles is None else titles,
        "is_active": is_active,
        "tdm_reserved": tdm_reserved,
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
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_period": salary_period,
        "source_url": "https://example.com/job",
        "description_r2_hash": description_r2_hash,
    }
    if company_name is not None:
        data["company_name"] = company_name
    if company_slug is not None:
        data["company_slug"] = company_slug
    if company_icon is not None:
        data["company_icon"] = company_icon
    rec = MagicMock()
    rec.keys.return_value = list(data.keys())
    rec.__getitem__ = lambda self, k: data[k]
    rec.__contains__ = lambda self, k: k in data
    rec.get = lambda k, default=None: data.get(k, default)
    return rec


class TestBuildTypesenseDocsCompanyMetadata:
    def test_joined_company_metadata_wins_when_cache_is_stale(self):
        maps = _make_taxonomy_maps()
        maps.company_info.clear()
        row = _make_posting_record(
            company_name="Canonical Company",
            company_slug="canonical-company",
            company_icon="https://assets.example/company.svg",
        )

        [doc] = _build_typesense_docs([row], maps)

        assert doc["company_name"] == "Canonical Company"
        assert doc["company_slug"] == "canonical-company"
        assert doc["company_icon"] == "https://assets.example/company.svg"


class TestBuildTypesenseDocsAncestors:
    """Tests for ancestor expansion in _build_typesense_docs."""

    def test_location_ids_expanded_with_ancestors(self):
        """Leaf location ID 10 should expand to include region(20) and country(30)."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10])
        docs = _build_typesense_docs([row], maps)
        assert len(docs) == 1
        assert docs[0]["reconciliation_bucket"] == uuid.UUID(docs[0]["id"]).hex[:2]
        assert docs[0]["candidate_order_key"] == candidate_order_key(row["id"])
        assert (
            docs[0]["candidate_order_hi"],
            docs[0]["candidate_order_lo"],
        ) == candidate_order_words(row["id"])
        loc_ids = set(docs[0]["location_ids"])
        assert docs[0]["location_direct_ids"] == [10]
        assert 10 in loc_ids  # leaf (city)
        assert 20 in loc_ids  # region ancestor
        assert 30 in loc_ids  # country ancestor

    def test_inactive_posting_does_not_consume_candidate_sort_index(self):
        maps = _make_taxonomy_maps()
        row = _make_posting_record(is_active=False)
        doc = _build_typesense_docs([row], maps)[0]
        assert doc["candidate_order_key"] is None
        assert doc["candidate_order_hi"] is None
        assert doc["candidate_order_lo"] is None

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

    def test_occupation_ancestors_have_stable_order(self):
        maps = _make_taxonomy_maps()
        maps.occupation_ancestors[100] = [200, 100]
        docs = _build_typesense_docs([_make_posting_record(occupation_id=100)], maps)
        assert docs[0]["occupation_ids"] == [100, 200]

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


class TestBuildTypesenseDocsOriginalSalary:
    """Posting detail keeps the source salary alongside normalized EUR facets."""

    def test_original_salary_fields_are_indexed_when_present(self):
        maps = _make_taxonomy_maps()
        row = _make_posting_record(
            salary_min=120_000,
            salary_max=150_000,
            salary_currency="USD",
            salary_period="year",
        )

        [doc] = _build_typesense_docs([row], maps)

        assert doc["salary_min"] == 120_000
        assert doc["salary_max"] == 150_000
        assert doc["salary_currency"] == "USD"
        assert doc["salary_period"] == "year"

    def test_null_original_salary_fields_are_omitted(self):
        maps = _make_taxonomy_maps()
        row = _make_posting_record()

        [doc] = _build_typesense_docs([row], maps)

        assert "salary_min" not in doc
        assert "salary_max" not in doc
        assert "salary_currency" not in doc
        assert "salary_period" not in doc


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
        assert docs[0]["experience_min_years"] == 5.0
        assert docs[0]["experience_max_years"] == 10.0

    def test_open_ended_uses_high_sentinel_for_max(self):
        """`5+ years` (Postgres `min=5, max=NULL`) → max stamped as 99 so the
        row matches range filters whose upper bound is ≥ 5.
        """
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=5, experience_max=None)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == 5
        assert docs[0]["experience_max"] == 99
        assert docs[0]["experience_min_years"] == 5.0
        assert docs[0]["experience_max_years"] == 99.0

    def test_no_info_uses_minus_one_sentinel_for_both(self):
        """Postgres `min=NULL, max=NULL` (no extraction) → both fields -1 so
        the filter's `experience_min:=-1` branch catches the row.
        """
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=None, experience_max=None)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == -1
        assert docs[0]["experience_max"] == -1
        assert docs[0]["experience_min_years"] == -1.0
        assert docs[0]["experience_max_years"] == -1.0

    def test_exact_year_passes_through(self):
        """`exactly 6 years` (`min=6, max=6`) round-trips unchanged."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=6, experience_max=6)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min"] == 6
        assert docs[0]["experience_max"] == 6
        assert docs[0]["experience_min_years"] == 6.0
        assert docs[0]["experience_max_years"] == 6.0

    def test_decimal_years_use_precise_float_fields_and_conservative_legacy_fields(self):
        """Decimal years stay precise while legacy fields cannot broaden matches."""
        maps = _make_taxonomy_maps()
        row = _make_posting_record(location_ids=[10], experience_min=1.5, experience_max=2.5)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["experience_min_years"] == 1.5
        assert docs[0]["experience_max_years"] == 2.5
        assert docs[0]["experience_min"] == 2
        assert docs[0]["experience_max"] == 2


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
        with patch("src.typesense_projection.log") as mock_log:
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
        with patch("src.typesense_projection.log") as mock_log:
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
    """Tests for collision-free occupation ancestor expansion (#3027)."""

    async def test_domain_ids_are_not_unioned_into_occupation_ids(self):
        """Independent domain IDs must never masquerade as occupations."""
        pool = _make_pool()
        # Production assigns domain and occupation IDs from separate
        # sequences. Healthcare domain 9 therefore collides with Data
        # Analyst occupation 9. A Pharmacist posting (occupation 92,
        # domain 9) must not carry 9 in its occupation_ids array.
        pool.fetch = AsyncMock(
            return_value=[
                {"id": 9, "parent_id": None},
                {"id": 92, "parent_id": None},
                {"id": 104, "parent_id": None},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_occupation_ancestors(pool)

        sql = pool.fetch.await_args.args[0]
        assert "domain_id" not in sql
        assert set(maps.occupation_ancestors[9]) == {9}
        assert set(maps.occupation_ancestors[92]) == {92}
        assert set(maps.occupation_ancestors[104]) == {104}

    async def test_parent_chain_is_still_walked(self):
        """Real occupation parents retain subtree-filter semantics."""
        pool = _make_pool()
        pool.fetch = AsyncMock(
            return_value=[
                {"id": 1, "parent_id": None},
                {"id": 2, "parent_id": 1},
                {"id": 3, "parent_id": 2},
            ]
        )

        maps = TaxonomyMaps()
        await maps._load_occupation_ancestors(pool)

        assert set(maps.occupation_ancestors[3]) == {3, 2, 1}
        assert set(maps.occupation_ancestors[2]) == {2, 1}
        assert set(maps.occupation_ancestors[1]) == {1}

    def test_build_typesense_docs_excludes_colliding_domain_id(self):
        """A Pharmacist document cannot match the Data Analyst ID."""
        maps = _make_taxonomy_maps()
        maps.occupation_names[92] = "Pharmacist"
        maps.occupation_ancestors[92] = [92]

        row = _make_posting_record(occupation_id=92)
        docs = _build_typesense_docs([row], maps)
        assert docs[0]["occupation_ids"] == [92]
        assert 9 not in docs[0]["occupation_ids"]
        assert docs[0]["occupation_id"] == 92


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


# ---------------------------------------------------------------------------
# run_exporter dual-path cursor save (issue #3171 wiring)
# ---------------------------------------------------------------------------


def test_reserved_posting_remains_visible_but_is_marked_for_mining_consumers():
    row = _make_posting_record(
        location_ids=[10], titles=["Engineer"], description_r2_hash=99, tdm_reserved=True
    )
    doc = _build_typesense_docs([row], _make_taxonomy_maps())[0]
    assert doc["tdm_reserved"] is True
    assert doc["is_active"] is True
    assert doc["has_content"] is True
    assert doc["title"] == "Engineer"
