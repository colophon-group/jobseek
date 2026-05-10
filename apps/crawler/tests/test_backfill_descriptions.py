"""Tests for backfill_descriptions (#2996).

The helper resets ``next_scrape_at = now()`` AND enqueues Redis scrape
tasks for rich-monitor postings whose description is missing because the
board's enrich config flipped AFTER the rows were first inserted.
Without this backfill, scraper-config fixes shipped in PRs #2947,
#2953, #2954, #2961, #2962, #2964, #2967, #2968, #2970, #2971, #2972
only affect future inserts — existing rows stay stuck behind
``_INSERT_RICH_JOB``'s NULL ``next_scrape_at`` and never re-enter the
scrape pipeline.

Two-pass design (mirrors ``backfill_locations``):

* **Pass 1 (PROMOTE)** — ``next_scrape_at IS NULL`` rows; UPDATE +
  enqueue.
* **Pass 2 (FETCH)** — ``next_scrape_at IS NOT NULL`` rows; read-only
  enqueue. Catches rows stranded by an ad-hoc operator UPDATE that set
  ``next_scrape_at`` in DB without touching Redis (production workers
  claim from Redis only, so a Postgres-only update is invisible to
  them).

The complementary self-heal in ``_DIFF_BATCH`` (also #2996) prevents the
bug for FUTURE config flips.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backfill import (
    _COUNT_DESCRIPTIONS_CANDIDATES,
    _DEFAULT_STUCK_DESCRIPTION_SLUGS,
    _FETCH_STUCK_DESCRIPTIONS_BATCH,
    _PROMOTE_DESCRIPTIONS_BATCH,
    backfill_descriptions,
)


def _row(id_, source_url="https://example.com/job/1", board_id="board-1", r2_hash=None):
    """Mock asyncpg.Record-like dict for the promote query result."""
    data = {
        "id": id_,
        "source_url": source_url,
        "board_id": board_id,
        "description_r2_hash": r2_hash,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


# ── SQL constants ────────────────────────────────────────────────────


class TestSqlConstants:
    """Pin the SQL shape so refactors don't silently widen the WHERE clause."""

    def test_promote_filters_stuck_rows(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # The whole point of the helper: only stuck rows.
        assert "is_active = true" in sql_compact
        assert "description_r2_hash IS NULL" in sql_compact
        assert "next_scrape_at IS NULL" in sql_compact

    def test_promote_supports_slug_filter(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # Optional ANY filter — empty/NULL falls through to "all stuck".
        assert "$1::text[] IS NULL OR c.slug = ANY($1::text[])" in sql_compact

    def test_promote_returns_enqueue_columns(self):
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        # Caller needs id, source_url, board_id, description_r2_hash to
        # construct the Redis enqueue payload.
        assert "RETURNING jp.id::text, jp.source_url, jp.board_id::text" in sql_compact
        assert "jp.description_r2_hash" in sql_compact

    def test_promote_orders_by_id_for_progress(self):
        # ORDER BY id keeps each batch deterministic so the WHERE clause
        # narrows monotonically as rows get next_scrape_at set.
        sql_compact = " ".join(_PROMOTE_DESCRIPTIONS_BATCH.split())
        assert "ORDER BY jp.id" in sql_compact

    def test_fetch_filters_already_promoted_stuck_rows(self):
        """Pass 2 query targets rows whose next_scrape_at is non-NULL but
        description is still missing — the bucket left stranded by an
        ad-hoc operator UPDATE that set next_scrape_at in DB without
        enqueueing into Redis."""
        sql_compact = " ".join(_FETCH_STUCK_DESCRIPTIONS_BATCH.split())
        assert "is_active = true" in sql_compact
        assert "description_r2_hash IS NULL" in sql_compact
        assert "next_scrape_at IS NOT NULL" in sql_compact
        # Same slug filter shape as Pass 1.
        assert "$1::text[] IS NULL OR c.slug = ANY($1::text[])" in sql_compact

    def test_fetch_uses_offset_pagination(self):
        """Pass 2 walks via OFFSET because the criteria stay true across
        iterations (read-only fetch). A no-OFFSET LIMIT loop would
        re-fetch the same first-N rows forever."""
        sql_compact = " ".join(_FETCH_STUCK_DESCRIPTIONS_BATCH.split())
        assert "ORDER BY jp.id" in sql_compact
        assert "LIMIT $2" in sql_compact
        assert "OFFSET $3" in sql_compact

    def test_fetch_returns_enqueue_columns(self):
        sql_compact = " ".join(_FETCH_STUCK_DESCRIPTIONS_BATCH.split())
        # Caller needs id, source_url, board_id, description_r2_hash to
        # construct the Redis enqueue payload — same shape as Pass 1.
        for col in (
            "jp.id::text",
            "jp.source_url",
            "jp.board_id::text",
            "jp.description_r2_hash",
        ):
            assert col in sql_compact

    def test_count_query_unions_both_buckets(self):
        """Dry-run's count must reflect Pass 1 + Pass 2 actual work,
        not just the legacy ``next_scrape_at IS NULL`` bucket — otherwise
        an operator running --dry-run AFTER an ad-hoc UPDATE would see
        zero candidates and conclude the helper is unneeded."""
        count_compact = " ".join(_COUNT_DESCRIPTIONS_CANDIDATES.split())
        # Combined predicate — drops the IS NULL clause.
        assert "is_active = true" in count_compact
        assert "description_r2_hash IS NULL" in count_compact
        assert "$1::text[] IS NULL OR c.slug = ANY($1::text[])" in count_compact
        # The IS NULL predicate must NOT appear — that would re-introduce
        # the original bug (operator ad-hoc UPDATE makes helper a no-op).
        assert "next_scrape_at IS NULL" not in count_compact


# ── Default slug list ────────────────────────────────────────────────


class TestDefaultSlugList:
    """Default 20 slugs match #2996's audit — DiDi (#2997) and NEURA
    (#2998) are excluded. A regression in this list silently changes the
    operator-side semantic (the CLI defaults to ``--slug``-less
    invocation)."""

    def test_default_slug_count(self):
        assert len(_DEFAULT_STUCK_DESCRIPTION_SLUGS) == 20

    def test_default_excludes_didi_and_neura(self):
        # Per #2996: didi-global has a disabled board, NEURA's scrape-
        # side is already healthy — both must be excluded from default.
        assert "didi-global" not in _DEFAULT_STUCK_DESCRIPTION_SLUGS
        assert "neura-robotics" not in _DEFAULT_STUCK_DESCRIPTION_SLUGS

    def test_default_includes_all_in_scope_slugs(self):
        expected = {
            "alibaba",
            "ayuda-en-accion",
            "bajaj-finserv",
            "barcelona-activa",
            "continental",
            "decathlon",
            "goldman-sachs",
            "haier-group",
            "hitachi-energy",
            "infineon",
            "itau-unibanco",
            "larsen-toubro",
            "loreal",
            "meta",
            "netflix",
            "nokia",
            "terveystalo",
            "tesla",
            "texas-instruments",
            "zte",
        }
        assert set(_DEFAULT_STUCK_DESCRIPTION_SLUGS) == expected


# ── dry_run path ─────────────────────────────────────────────────────


class TestDryRun:
    @patch("src.backfill.get_redis")
    async def test_dry_run_returns_count_without_writes(self, mock_get_redis, mock_pool):
        """dry_run=True must call fetchval (COUNT) and not pool.fetch (UPDATE)."""
        mock_pool.fetchval = AsyncMock(return_value=42)
        n = await backfill_descriptions(mock_pool, dry_run=True)
        assert n == 42
        # The UPDATE must not run in dry_run.
        mock_pool.fetch.assert_not_called()
        # And we must not have touched Redis (no enqueues).
        mock_get_redis.assert_not_called()

    @patch("src.backfill.get_redis")
    async def test_dry_run_passes_default_slugs(self, mock_get_redis, mock_pool):
        """When ``company_slugs`` is None, the COUNT query gets the default 20."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await backfill_descriptions(mock_pool, dry_run=True)
        mock_pool.fetchval.assert_called_once()
        sql, slugs = mock_pool.fetchval.call_args.args
        assert sql == _COUNT_DESCRIPTIONS_CANDIDATES
        assert slugs == list(_DEFAULT_STUCK_DESCRIPTION_SLUGS)

    @patch("src.backfill.get_redis")
    async def test_dry_run_with_explicit_slugs(self, mock_get_redis, mock_pool):
        mock_pool.fetchval = AsyncMock(return_value=7)
        n = await backfill_descriptions(mock_pool, company_slugs=["tesla", "nokia"], dry_run=True)
        assert n == 7
        sql, slugs = mock_pool.fetchval.call_args.args
        assert slugs == ["tesla", "nokia"]

    @patch("src.backfill.get_redis")
    async def test_dry_run_with_empty_list_means_all_stuck(self, mock_get_redis, mock_pool):
        """An empty list is the documented escape hatch for 'all rows'.

        ``slugs=None`` in the SQL bypasses the slug filter — useful for
        operator one-shots that want to clean every stuck row regardless
        of company. This is intentionally separate from the default
        20-company semantic that ``company_slugs=None`` selects.
        """
        mock_pool.fetchval = AsyncMock(return_value=99)
        await backfill_descriptions(mock_pool, company_slugs=[], dry_run=True)
        sql, slugs = mock_pool.fetchval.call_args.args
        assert slugs is None  # SQL falls through to "all rows"

    async def test_only_missing_false_raises(self, mock_pool):
        """Future-proof guard: only_missing=False is reserved for later."""
        with pytest.raises(NotImplementedError):
            await backfill_descriptions(mock_pool, only_missing=False)


# ── enqueue path ─────────────────────────────────────────────────────


class TestEnqueuePath:
    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_enqueues_each_promoted_row(self, mock_enqueue, mock_get_redis, mock_pool):
        """A non-dry_run call must enqueue once per row from BOTH passes.

        Pass 1 returns 2 rows then []; Pass 2 returns [] immediately.
        Total enqueues = 2.
        """
        rows = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        # [Pass1 batch1, Pass1 done, Pass2 done]
        mock_pool.fetch = AsyncMock(side_effect=[rows, [], []])

        # Redis hgetall for board:b-1 — empty hash means needs_browser=False.
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis

        # enqueue_scrape returns truthy → counts toward the total.
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 2
        assert mock_enqueue.await_count == 2

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_dedup_does_not_inflate_count(self, mock_enqueue, mock_get_redis, mock_pool):
        """enqueue_scrape returning False (dedup'd) must not be counted."""
        rows = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        # Pass 1 returns rows, Pass 1 done, Pass 2 done.
        mock_pool.fetch = AsyncMock(side_effect=[rows, [], []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        # First enqueue succeeds, second is dedup'd.
        mock_enqueue.side_effect = [True, False]

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 1

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_browser_board_routes_to_browser_queue(
        self, mock_enqueue, mock_get_redis, mock_pool
    ):
        """Board hash with scraper_needs_browser=1 → enqueue with browser=True."""
        rows = [_row("id-1", "https://example.com/job/1", "b-browser")]
        # Pass 1 returns 1 row, Pass 1 done, Pass 2 done.
        mock_pool.fetch = AsyncMock(side_effect=[rows, [], []])
        redis = AsyncMock()
        # Redis returns "1" for needs_browser.
        redis.hgetall = AsyncMock(return_value={"scraper_needs_browser": "1"})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert mock_enqueue.await_count == 1
        # browser kwarg should be True
        kwargs = mock_enqueue.await_args.kwargs
        assert kwargs.get("browser") is True
        # first_time False → tier-2 priority (lowest).
        assert kwargs.get("first_time") is False

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_pass1_loops_until_fetch_returns_empty(
        self, mock_enqueue, mock_get_redis, mock_pool
    ):
        """Pass 1 must keep going until pool.fetch returns [], then Pass 2."""
        # Pass 1: 2, 1, 0 rows. Pass 2: 0 rows.
        batch1 = [
            _row("id-1", "https://example.com/job/1", "b-1"),
            _row("id-2", "https://example.com/job/2", "b-1"),
        ]
        batch2 = [_row("id-3", "https://example.com/job/3", "b-1")]
        mock_pool.fetch = AsyncMock(side_effect=[batch1, batch2, [], []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 3
        # 3 Pass-1 calls (2 yielding rows + 1 empty terminator) + 1 Pass-2 empty.
        assert mock_pool.fetch.await_count == 4

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_pass2_enqueues_already_promoted_stuck_rows(
        self, mock_enqueue, mock_get_redis, mock_pool
    ):
        """Pass 2 must enqueue rows whose next_scrape_at IS NOT NULL but
        description is missing. This is the regression that made the
        helper a no-op after the operator ad-hoc UPDATE in #2996 set
        ``next_scrape_at = now()`` without touching Redis: workers claim
        from Redis only, so a Postgres-only update is invisible to them.
        """
        # Pass 1: empty (everything already has next_scrape_at non-NULL).
        # Pass 2: returns the stranded rows.
        pass2_rows = [
            _row("id-tesla-1", "https://www.tesla.com/careers/job/1", "b-tesla"),
            _row("id-bajaj-1", "https://bflcareers.peoplestrong.com/job/1", "b-bajaj"),
        ]
        mock_pool.fetch = AsyncMock(side_effect=[[], pass2_rows, []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        n = await backfill_descriptions(
            mock_pool, company_slugs=["tesla", "bajaj-finserv"], dry_run=False
        )
        assert n == 2
        assert mock_enqueue.await_count == 2

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_pass2_uses_offset_pagination(self, mock_enqueue, mock_get_redis, mock_pool):
        """Pass 2 must increment OFFSET by the batch size on each call —
        otherwise it loops forever on the same first-N rows since the
        criteria stay true across iterations."""
        batch1 = [_row("id-1", "https://example.com/1", "b-1")]
        batch2 = [_row("id-2", "https://example.com/2", "b-1")]
        # Pass 1: empty. Pass 2: batch1, batch2, [].
        mock_pool.fetch = AsyncMock(side_effect=[[], batch1, batch2, []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 2

        # Inspect the OFFSETs passed to pool.fetch for Pass 2 calls.
        # Call signature: (sql, slugs, batch_size, offset). The first
        # call is Pass 1's promote (no offset arg). Pass 2 calls are
        # the next 3 with offsets 0, 1, 2.
        pass2_calls = mock_pool.fetch.await_args_list[1:]
        offsets = [call.args[3] for call in pass2_calls]
        assert offsets == [0, 1, 2]

    @patch("src.backfill.get_redis")
    @patch("src.backfill.enqueue_scrape", new_callable=AsyncMock)
    async def test_combined_pass1_and_pass2(self, mock_enqueue, mock_get_redis, mock_pool):
        """Mixed bucket: some rows are NULL-next (Pass 1), others
        already-promoted-stuck (Pass 2). Total enqueues = sum of both."""
        pass1_rows = [_row("id-null", "https://example.com/1", "b-1")]
        pass2_rows = [
            _row("id-stuck-a", "https://example.com/2", "b-1"),
            _row("id-stuck-b", "https://example.com/3", "b-1"),
        ]
        # Pass 1: rows then []. Pass 2: rows then [].
        mock_pool.fetch = AsyncMock(side_effect=[pass1_rows, [], pass2_rows, []])
        redis = AsyncMock()
        redis.hgetall = AsyncMock(return_value={})
        mock_get_redis.return_value = redis
        mock_enqueue.return_value = True

        n = await backfill_descriptions(mock_pool, company_slugs=["tesla"], dry_run=False)
        assert n == 3
        assert mock_enqueue.await_count == 3
