"""Tests for the r2_drain orphan reaper (#3168).

The 3-state ``r2_uploaded`` claim scheme (``false → NULL → true``) has
no natural reaper. If a consumer dies between the producer's claim
(``false → NULL``) and either the success update (``NULL → true``) or
the failure revert (``NULL → false``), the row stays ``NULL`` forever
— the producer query filters on ``r2_uploaded = false`` so the row is
never re-claimed. Consumer-side crashes that bypass the ``except``
block (OOM kill, SIGKILL, segfault, host reboot) are the cause.

The fix has two parts:

1. ``_reap_orphaned_claims`` resets ``r2_uploaded = NULL`` rows back to
   ``false``. At startup (``stale_after_seconds=None``) the reaper is
   unfiltered — any NULL row predates this process and is therefore a
   crash leftover. The periodic sweep uses ``stale_after_seconds`` so
   it does not race with in-flight consumer claims.

2. The producer stamps ``updated_at = now()`` when claiming, so the
   periodic sweep can tell a recently claimed (in-flight) row from a
   genuinely orphaned one by timestamp.

These tests pin both pieces.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.r2_drain import (
    _REAP_STALE_AFTER_SECONDS,
    _reap_orphaned_claims,
    _reaper_loop,
    r2_drain_loop,
)


def _pool_with_conn(*, fetchval_return=0, execute_records: list | None = None):
    """Build an asyncpg.Pool-shaped AsyncMock.

    ``execute_records`` is appended-to on every ``conn.execute`` call so
    a test can assert what was run inside the acquired connection.
    """
    pool = AsyncMock()
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    if execute_records is not None:

        async def fake_execute(query, *args):
            execute_records.append((query, args))

        conn.execute = AsyncMock(side_effect=fake_execute)
    else:
        conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])

    acq_ctx = MagicMock()
    acq_ctx.__aenter__ = AsyncMock(return_value=conn)
    acq_ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_ctx)
    return pool, conn


class TestReapOrphanedClaims:
    """Behavioural contract for the reaper helper."""

    async def test_startup_reap_uses_no_age_filter(self):
        """Without ``stale_after_seconds`` the WHERE clause must be the
        bare ``r2_uploaded IS NULL`` — at startup any NULL row predates
        the new process so the age filter would be redundant and would
        wrongly leave genuinely orphaned rows untouched if their
        timestamps happen to be fresh (e.g. a producer that crashed
        right after stamping updated_at)."""
        captured: dict = {}

        async def fake_fetchval(query, *args):
            captured["query"] = query
            captured["args"] = args
            return 0

        pool, conn = _pool_with_conn()
        conn.fetchval = AsyncMock(side_effect=fake_fetchval)
        log = MagicMock()

        await _reap_orphaned_claims(pool, log)

        sql_compact = " ".join(captured["query"].split())
        # Filter present
        assert "r2_uploaded IS NULL" in sql_compact
        # No age filter
        assert "updated_at <" not in sql_compact
        assert captured["args"] == ()

    async def test_periodic_reap_uses_age_filter(self):
        """When given a stale-age, the reaper must include the
        ``updated_at < now() - $1::interval`` clause and pass an
        asyncpg-compatible ``timedelta`` bind parameter. Without this, the
        periodic sweep would race with an in-flight consumer claim
        and revert it back to ``false`` while the consumer is still
        running, producing a duplicate buffer entry the next time a
        producer fires. Passing a string here regresses #3629 because
        asyncpg encodes interval parameters before PostgreSQL sees the
        ``::interval`` cast."""
        captured: dict = {}

        async def fake_fetchval(query, *args):
            captured["query"] = query
            captured["args"] = args
            return 0

        pool, conn = _pool_with_conn()
        conn.fetchval = AsyncMock(side_effect=fake_fetchval)
        log = MagicMock()

        await _reap_orphaned_claims(pool, log, stale_after_seconds=600)

        sql_compact = " ".join(captured["query"].split())
        assert "r2_uploaded IS NULL" in sql_compact
        assert "updated_at <" in sql_compact
        assert "interval" in sql_compact
        assert captured["args"] == (timedelta(seconds=600),)

    async def test_returns_reaped_count(self):
        pool, _ = _pool_with_conn(fetchval_return=42)
        log = MagicMock()
        n = await _reap_orphaned_claims(pool, log)
        assert n == 42

    async def test_db_error_is_swallowed_and_returns_zero(self):
        """A reaper crash must not bring down the drain process — if
        the local DB hiccups, we log a warning and try again next
        sweep. Returning zero lets the caller carry on."""
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)
        log = MagicMock()

        n = await _reap_orphaned_claims(pool, log)
        assert n == 0
        log.warning.assert_called_once()

    async def test_reap_resets_to_false_not_true(self):
        """Crashed claims must return to the pending state (``false``),
        not be marked uploaded (``true``). Marking them ``true`` would
        leave the description missing from R2 forever."""
        captured: dict = {}

        async def fake_fetchval(query, *args):
            captured["query"] = query
            captured["args"] = args
            return 0

        pool, conn = _pool_with_conn()
        conn.fetchval = AsyncMock(side_effect=fake_fetchval)
        log = MagicMock()

        await _reap_orphaned_claims(pool, log)

        sql_compact = " ".join(captured["query"].split())
        # Smoking-gun: the SET clause must move NULL → false, not → true.
        assert "SET r2_uploaded = false" in sql_compact, captured["query"]
        # And it must not zero out the hash or html columns — those
        # are needed for the next claim/upload cycle.
        assert "html" not in sql_compact
        assert "hash" not in sql_compact


class TestReaperLoop:
    async def test_runs_and_shuts_down(self):
        """The periodic loop must respect the shutdown event and call
        the reaper at least once on a short interval."""
        pool, conn = _pool_with_conn(fetchval_return=0)
        log = MagicMock()
        shutdown = asyncio.Event()

        async def set_shutdown():
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(
            _reaper_loop(pool, shutdown, log, interval=0.05),
            set_shutdown(),
        )

        # At least one sweep ran (fetchval invoked).
        assert conn.fetchval.await_count >= 1

    async def test_loop_uses_stale_age_filter(self):
        """The periodic sweep MUST pass the stale-age threshold — the
        startup reap is the only invocation allowed to bypass it.
        Without the filter, a heavy-load drain with slow R2 latency
        would have its in-flight claims reverted by the very loop
        that's meant to protect them."""
        captured_args: list = []

        async def fake_fetchval(_query, *args):
            captured_args.append(args)
            return 0

        pool, conn = _pool_with_conn()
        conn.fetchval = AsyncMock(side_effect=fake_fetchval)
        log = MagicMock()
        shutdown = asyncio.Event()

        async def set_shutdown():
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(
            _reaper_loop(pool, shutdown, log, interval=0.05),
            set_shutdown(),
        )

        assert captured_args, "reaper loop never ran"
        for args in captured_args:
            assert args == (timedelta(seconds=_REAP_STALE_AFTER_SECONDS),), (
                f"periodic sweep must use the stale-age filter; got args={args!r}"
            )


class TestStartupReaperWiring:
    """The startup reap MUST run before producers begin claiming.
    Otherwise an unfiltered reap would compete with active producers,
    reverting their fresh claims and producing duplicate buffer
    entries.
    """

    async def test_drain_loop_reaps_at_startup_before_producers(self):
        """Pin the order: ``_reap_orphaned_claims`` is awaited before
        the TaskGroup that spawns producers/consumers.

        We stub out the workers (TaskGroup body) so the loop exits
        immediately, and we set the shutdown event up-front to make
        the periodic reaper exit on first tick.
        """
        from unittest.mock import patch

        pool, conn = _pool_with_conn(fetchval_return=7)
        shutdown = asyncio.Event()
        shutdown.set()  # Make all workers exit immediately.

        events: list[str] = []

        original_reap = _reap_orphaned_claims

        async def tracking_reap(local_pool, drain_log, *, stale_after_seconds=None):
            events.append("startup_reap" if stale_after_seconds is None else "periodic_reap")
            return await original_reap(
                local_pool, drain_log, stale_after_seconds=stale_after_seconds
            )

        async def fake_producer(*_a, **_kw):
            events.append("producer_start")

        async def fake_consumer(*_a, **_kw):
            events.append("consumer_start")

        with (
            patch("src.workers.r2_drain._reap_orphaned_claims", side_effect=tracking_reap),
            patch("src.workers.r2_drain._producer", side_effect=fake_producer),
            patch("src.workers.r2_drain._consumer", side_effect=fake_consumer),
        ):
            await r2_drain_loop(pool, shutdown)

        # First event must be the startup reap. The producers can run
        # in any order after that, but must NOT precede the reap.
        assert events, "drain loop ran no tracked events"
        assert events[0] == "startup_reap", (
            f"startup reap must run before producers; got events={events}"
        )
        # All subsequent reaps (from the loop, if any) are periodic.
        for ev in events[1:]:
            if ev.endswith("reap"):
                assert ev == "periodic_reap", (
                    f"only the first reap should be unfiltered; got {events}"
                )


class TestProducerClaimUpdatesTimestamp:
    """The producer's claim UPDATE must stamp ``updated_at = now()`` so
    the periodic reaper's age filter can distinguish recently claimed
    in-flight rows from genuinely orphaned ones."""

    async def test_claim_query_sets_updated_at(self):
        from src.workers.r2_drain import _producer

        pool = AsyncMock()
        conn = AsyncMock()
        captured: dict = {}

        async def fake_fetch(query, *args):
            captured.setdefault("queries", []).append(query)
            return []  # exit the loop on next iteration via shutdown

        conn.fetch = AsyncMock(side_effect=fake_fetch)
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)

        buffer: asyncio.Queue = asyncio.Queue(maxsize=10)
        shutdown = asyncio.Event()
        log = MagicMock()
        # Make sure log.bind returns something with the same attrs.
        log.bind = MagicMock(return_value=log)

        async def set_shutdown():
            # Let producer hit the first fetch then exit.
            await asyncio.sleep(0.05)
            shutdown.set()

        await asyncio.gather(
            _producer(0, pool, buffer, shutdown, log),
            set_shutdown(),
        )

        assert captured.get("queries"), "producer never ran a claim query"
        claim_sql = " ".join(captured["queries"][0].split())
        # Smoking gun: the claim must stamp updated_at along with the
        # NULL flip, or the periodic reaper can't tell fresh claims
        # from crash leftovers.
        assert "updated_at = now()" in claim_sql, (
            f"producer claim must stamp updated_at = now(); got: {claim_sql}"
        )
        assert "r2_uploaded = NULL" in claim_sql


class TestEndToEndOrphanRecovery:
    """High-level scenario: a row stuck in ``r2_uploaded=NULL`` after a
    consumer crash must be picked up after a restart.

    The reaper runs unfiltered at startup, flips the row back to
    ``false``, and the producer's next ``WHERE r2_uploaded = false``
    claim sees the row again.
    """

    async def test_null_row_becomes_pending_after_startup_reap(self):
        """Simulate one orphaned NULL row in the table. After the
        startup reap, the bare claim WHERE clause should match it.

        We model the table as a single dict and assert the state
        transition.
        """
        descriptions: dict[str, object] = {"r2_uploaded": None}  # orphan

        pool = AsyncMock()
        conn = AsyncMock()

        async def fake_fetchval(query, *_args):
            # Simulate WHERE r2_uploaded IS NULL matching the orphan.
            if descriptions["r2_uploaded"] is None:
                descriptions["r2_uploaded"] = False
                return 1
            return 0

        conn.fetchval = AsyncMock(side_effect=fake_fetchval)
        acq_ctx = MagicMock()
        acq_ctx.__aenter__ = AsyncMock(return_value=conn)
        acq_ctx.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_ctx)
        log = MagicMock()

        # Before: row is NULL — invisible to producer.
        assert descriptions["r2_uploaded"] is None

        # Startup reap (no age filter).
        n = await _reap_orphaned_claims(pool, log)

        # After: row is false — producer can re-claim it.
        assert n == 1
        assert descriptions["r2_uploaded"] is False, (
            "reaper must move NULL → false so the producer's "
            "`WHERE r2_uploaded = false` query picks the row up"
        )

    async def test_idempotent_when_no_orphans(self):
        """If everything is healthy (no NULL rows), the reaper must do
        nothing — and explicitly must not flip any ``true`` row back
        to ``false``. That would re-upload every description on every
        restart."""
        pool, conn = _pool_with_conn(fetchval_return=0)
        log = MagicMock()
        n = await _reap_orphaned_claims(pool, log)
        assert n == 0


# ---------------------------------------------------------------------------
# Lifecycle anchor: r2_drain.uploaded (#3192)
# ---------------------------------------------------------------------------


class TestR2DrainUploadedLog:
    """The consumer must emit ``r2_drain.uploaded`` on success, mirroring
    the ``r2_drain.consumer_error`` event on the error path so an operator
    grepping Loki by posting_id can confirm whether the description ever
    reached R2 (closes #3192)."""

    async def test_consumer_emits_uploaded_event_with_posting_id_on_success(self):
        import structlog

        from src.workers.r2_drain import _consumer

        pool = AsyncMock()
        pool.execute = AsyncMock()
        buffer: asyncio.Queue = asyncio.Queue(maxsize=10)
        shutdown = asyncio.Event()
        stats = {"uploaded": 0, "errors": 0, "total_time": 0.0}

        posting_id = "11111111-2222-3333-4444-555555555555"
        await buffer.put(
            {
                "posting_id": posting_id,
                "locale": "en",
                "html": "<p>desc</p>",
                "hash": 123,
            }
        )

        async def set_shutdown_after_drain():
            # Wait until the consumer has popped the only buffered row,
            # then trigger shutdown so the test exits promptly.
            await asyncio.sleep(0.1)
            shutdown.set()

        drain_log = structlog.get_logger().bind(name="drain-test")
        with (
            patch(
                "src.workers.r2_drain.put_description",
                new_callable=AsyncMock,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            await asyncio.gather(
                _consumer(0, pool, buffer, shutdown, drain_log, stats),
                set_shutdown_after_drain(),
            )

        events = [
            e
            for e in logs
            if e.get("event") == "r2_drain.uploaded" and e.get("posting_id") == posting_id
        ]
        assert events, (
            "consumer must emit r2_drain.uploaded on success — mirrors "
            "r2_drain.consumer_error which already carries posting_id"
        )
        assert events[0]["locale"] == "en"
        assert stats["uploaded"] == 1
