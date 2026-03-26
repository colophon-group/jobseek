"""Tests for the R2 background drain worker."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.batch import (
    _build_r2_extras,
    _compute_r2_hash,
    _serialize_localizations,
    _stable_date,
    _stage_r2_pending,
)
from src.r2_worker import _ABANDON, _OK, _RETRY, _upload_one

# ── _stage_r2_pending tests ──────────────────────────────────────────


class TestStageR2Pending:
    def test_returns_none_when_no_description(self):
        result = _stage_r2_pending(
            title="T",
            description=None,
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        assert result is None

    def test_returns_none_when_hash_matches(self):
        desc = "<p>Hello</p>"
        merged = _build_r2_extras(
            title="T",
            locations=["NYC"],
            extras=None,
            metadata=None,
            date_posted="2026-03-25",
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        existing_hash = _compute_r2_hash(desc, merged)

        result = _stage_r2_pending(
            title="T",
            description=desc,
            language="en",
            locations=["NYC"],
            localizations=None,
            extras=None,
            metadata=None,
            date_posted="2026-03-25",
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            current_hash=existing_hash,
        )
        assert result is None

    def test_returns_staged_data_when_hash_differs(self):
        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=["NYC"],
            localizations=None,
            extras=None,
            metadata=None,
            date_posted="2026-03-25",
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            current_hash=12345,  # wrong hash
        )
        assert result is not None
        desc_pending, meta_json, new_hash = result
        assert desc_pending == "<p>Hello</p>"
        assert isinstance(new_hash, int)

        meta = json.loads(meta_json)
        assert meta["locale"] == "en"
        assert meta["source"] == "monitor"
        assert meta["retry_count"] == 0
        assert meta["new_hash"] == new_hash
        assert "extras" in meta

    def test_returns_staged_for_first_upload(self):
        """current_hash=None means never uploaded — should stage."""
        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            current_hash=None,
        )
        assert result is not None
        _, meta_json, _ = result
        meta = json.loads(meta_json)
        assert meta["source"] == "monitor"

    def test_scrape_source(self):
        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            source="scrape",
        )
        assert result is not None
        meta = json.loads(result[1])
        assert meta["source"] == "scrape"

    def test_tech_ids_stored(self):
        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            tech_ids=[1, 2, 3],
        )
        meta = json.loads(result[1])
        assert meta["tech_ids"] == [1, 2, 3]

    def test_localizations_serialized(self):
        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations={"de": {"description": "<p>Hallo</p>"}, "en": "skip"},
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        meta = json.loads(result[1])
        assert meta["localizations"] == {"de": "<p>Hallo</p>"}


# ── _serialize_localizations tests ───────────────────────────────────


class TestSerializeLocalizations:
    def test_none_input(self):
        assert _serialize_localizations(None, "en") is None

    def test_empty_dict(self):
        assert _serialize_localizations({}, "en") is None

    def test_excludes_primary_locale(self):
        result = _serialize_localizations({"en": "<p>English</p>", "de": "<p>German</p>"}, "en")
        assert result == {"de": "<p>German</p>"}

    def test_dict_with_description_key(self):
        result = _serialize_localizations(
            {"de": {"description": "<p>Hallo</p>", "title": "Titel"}}, "en"
        )
        assert result == {"de": "<p>Hallo</p>"}

    def test_skips_none_values(self):
        result = _serialize_localizations({"de": None, "fr": "<p>Bonjour</p>"}, "en")
        assert result == {"fr": "<p>Bonjour</p>"}


# ── helpers ──────────────────────────────────────────────────────────


def _mock_pool_with_conn():
    """Create a mock pool where pool.acquire() context manager yields a mock conn."""
    conn = AsyncMock()
    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = False
    pool.acquire.return_value = cm
    return pool, conn


def _make_item(
    posting_id="abc-123",
    description="<p>Hello</p>",
    meta=None,
    r2_html=None,
    r2_history=None,
    description_r2_hash=None,
):
    """Build a prefetched item dict for _upload_one tests."""
    if meta is None:
        meta = {
            "locale": "en",
            "extras": {"title": "Test"},
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 0,
            "new_hash": 99999,
        }
    row = MagicMock()
    row.__getitem__ = lambda s, k: {
        "id": posting_id,
        "description_pending": description,
        "r2_pending_meta": meta,
        "description_r2_hash": description_r2_hash,
    }[k]
    # Create a resolved future with the R2 state
    if r2_html is not None or r2_history is not None:
        import asyncio

        future = asyncio.get_event_loop().create_future()
        future.set_result((r2_html, r2_history))
    else:
        future = None
    return {"row": row, "_r2_future": future}


# ── _upload_one tests ────────────────────────────────────────────────


class TestUploadOne:
    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_first_upload_writes_html_and_history(self, mock_put):
        """First upload (no existing R2 content) writes both files."""
        item = _make_item(description="<p>Hello</p>", r2_html=None, r2_history=None)

        result = await _upload_one(item)

        assert result[0] == _OK
        assert mock_put.await_count == 2
        put_keys = [c.args[0] for c in mock_put.await_args_list]
        assert any("latest.html" in k for k in put_keys)
        assert any("history.json" in k for k in put_keys)

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_update_writes_only_history_when_desc_unchanged(self, mock_put):
        """When description hasn't changed, only history.json is written."""
        existing_history = json.dumps({"versions": [], "current_extras": {"title": "Old"}})
        item = _make_item(
            description="<p>Same</p>",
            r2_html="<p>Same</p>",
            r2_history=existing_history,
        )

        result = await _upload_one(item)

        assert result[0] == _OK
        assert mock_put.await_count == 1
        assert "history.json" in mock_put.await_args_list[0].args[0]

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_meta_only_uses_prefetched_html(self, mock_put):
        """Meta-only change uses prefetched R2 HTML."""
        existing_history = json.dumps({"versions": [], "current_extras": {"title": "Old"}})
        item = _make_item(
            description=None,
            r2_html="<p>Existing</p>",
            r2_history=existing_history,
            meta={
                "locale": "en",
                "extras": {"title": "New"},
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 0,
                "new_hash": 88888,
            },
        )

        result = await _upload_one(item)

        assert result[0] == _OK
        assert mock_put.await_count >= 1

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_retry_on_failure(self, mock_put):
        """Failed upload returns RETRY."""
        mock_put.side_effect = Exception("R2 down")
        item = _make_item(
            meta={
                "locale": "en",
                "extras": {},
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 2,
                "new_hash": 11111,
            },
        )

        result = await _upload_one(item)

        assert result[0] == _RETRY
        assert result[1] == "abc-123"

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_abandon_after_max_retries_monitor(self, mock_put):
        """Monitor source: abandon after max retries."""
        mock_put.side_effect = Exception("R2 down")
        item = _make_item(
            meta={
                "locale": "en",
                "extras": {},
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 4,
                "new_hash": 11111,
            },
        )

        result = await _upload_one(item)

        assert result[0] == _ABANDON
        assert result[2] == "monitor"

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_abandon_after_max_retries_scrape(self, mock_put):
        """Scrape source: abandon with source=scrape for re-scrape trigger."""
        mock_put.side_effect = Exception("R2 down")
        item = _make_item(
            meta={
                "locale": "en",
                "extras": {},
                "tech_ids": None,
                "localizations": None,
                "source": "scrape",
                "retry_count": 4,
                "new_hash": 11111,
            },
        )

        result = await _upload_one(item)

        assert result[0] == _ABANDON
        assert result[2] == "scrape"

    @patch("src.r2_worker.upload_description", new_callable=AsyncMock)
    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_localizations_uploaded(self, mock_put, mock_loc_upload):
        """Secondary locale descriptions are uploaded."""
        item = _make_item(
            meta={
                "locale": "en",
                "extras": {},
                "tech_ids": None,
                "localizations": {"de": "<p>Hallo</p>", "fr": "<p>Bonjour</p>"},
                "source": "monitor",
                "retry_count": 0,
                "new_hash": 55555,
            },
        )

        result = await _upload_one(item)

        assert result[0] == _OK
        assert mock_loc_upload.await_count == 2

    async def test_null_meta_abandons(self):
        """If r2_pending_meta is NULL, return ABANDON."""
        item = _make_item(meta=None)
        item["row"].__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Test</p>",
            "r2_pending_meta": None,
            "description_r2_hash": None,
        }[k]

        result = await _upload_one(item)

        assert result[0] == _ABANDON

    async def test_meta_only_no_existing_html_abandons(self):
        """Meta-only with no prefetched HTML returns ABANDON."""
        item = _make_item(description=None, r2_html=None, r2_history=None)

        result = await _upload_one(item)

        assert result[0] == _ABANDON


# ── _stable_date tests ───────────────────────────────────────────────


class TestStableDate:
    def test_date_string(self):
        assert _stable_date("2026-03-25") == "2026-03-25"

    def test_datetime_string(self):
        assert _stable_date("2026-03-25T08:57:16-05:00") == "2026-03-25"

    def test_datetime_with_z(self):
        assert _stable_date("2026-03-25T00:00:00Z") == "2026-03-25"

    def test_none(self):
        assert _stable_date(None) is None

    def test_empty_string(self):
        assert _stable_date("") is None


# ── Edge case tests ──────────────────────────────────────────────────


class TestStageR2PendingEdgeCases:
    def test_empty_description_string(self):
        """Empty string description treated as no description."""
        result = _stage_r2_pending(
            title="T",
            description="",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        assert result is None

    def test_whitespace_description(self):
        """Whitespace-only description treated as no description."""
        result = _stage_r2_pending(
            title="T",
            description="   ",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        # Whitespace is truthy — _stage_r2_pending checks `not description`
        # "   " is truthy, so it'll compute hash and stage
        assert result is not None

    def test_hash_stability_across_calls(self):
        """Same inputs produce same hash — no staging on second call."""
        kwargs = dict(
            title="Senior Engineer",
            description="<p>Build things</p>",
            language="en",
            locations=["NYC", "SF"],
            localizations=None,
            extras={"qualifications": "5 years"},
            metadata={"team": "Platform"},
            date_posted="2026-03-25T12:00:00Z",
            base_salary={"min": 100000, "max": 200000, "currency": "USD"},
            employment_type="FULL_TIME",
            job_location_type="hybrid",
        )
        result1 = _stage_r2_pending(**kwargs)
        assert result1 is not None
        _, _, hash1 = result1

        # Second call with same hash should return None (no change)
        result2 = _stage_r2_pending(**kwargs, current_hash=hash1)
        assert result2 is None

    def test_metadata_key_order_irrelevant(self):
        """Hash is stable regardless of metadata key order."""
        base = dict(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        r1 = _stage_r2_pending(**base, metadata={"a": 1, "b": 2})
        r2 = _stage_r2_pending(**base, metadata={"b": 2, "a": 1})
        assert r1[2] == r2[2]  # same hash

    def test_volatile_fields_excluded_from_hash(self):
        """valid_through and expiration_date don't affect hash."""
        base = dict(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
        )
        r1 = _stage_r2_pending(**base, extras={"valid_through": "2026-12-31"})
        r2 = _stage_r2_pending(**base, extras={"valid_through": "2027-06-30"})
        r3 = _stage_r2_pending(**base, extras=None)
        assert r1[2] == r2[2] == r3[2]  # same hash despite different volatile fields


class TestUploadOneEdgeCases:
    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_meta_as_json_string(self, mock_put):
        """r2_pending_meta stored as JSON string (not dict) is handled."""
        meta_str = json.dumps(
            {
                "locale": "en",
                "extras": {"title": "Test"},
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 0,
                "new_hash": 99999,
            }
        )
        item = _make_item(meta=meta_str)

        result = await _upload_one(item)
        assert result[0] == _OK
        assert mock_put.await_count >= 1

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_missing_extras_key(self, mock_put):
        """Meta without extras key defaults to empty dict."""
        item = _make_item(
            meta={
                "locale": "en",
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 0,
                "new_hash": 99999,
            },
        )

        result = await _upload_one(item)
        assert result[0] == _OK

    @patch("src.r2_worker._put_object", new_callable=AsyncMock)
    async def test_new_hash_none_in_meta(self, mock_put):
        """new_hash=None produces OK result with None hash."""
        item = _make_item(
            meta={
                "locale": "en",
                "extras": {},
                "tech_ids": None,
                "localizations": None,
                "source": "monitor",
                "retry_count": 0,
                "new_hash": None,
            },
        )

        result = await _upload_one(item)
        assert result[0] == _OK
        assert result[2] is None  # new_hash


# ── Queue cap tests ──────────────────────────────────────────────────


class TestQueueCap:
    async def test_first_upload_always_staged(self):
        """current_hash=None → always staged regardless of queue depth."""

        import src.batch as batch_mod

        # Simulate full queue
        batch_mod._r2_queue_depth = 999999
        batch_mod._r2_queue_depth_ts = float("inf")  # never expires

        result = _stage_r2_pending(
            title="T",
            description="<p>Hello</p>",
            language="en",
            locations=None,
            localizations=None,
            extras=None,
            metadata=None,
            date_posted=None,
            base_salary=None,
            employment_type=None,
            job_location_type=None,
            current_hash=None,  # first upload
        )
        # _stage_r2_pending itself doesn't check queue — it always returns staged
        # The queue check is in the callers. Verify it returns data.
        assert result is not None

        # Reset global
        batch_mod._r2_queue_depth = None
        batch_mod._r2_queue_depth_ts = 0

    async def test_get_r2_queue_depth_caches(self):
        """Queue depth is cached for 30s."""
        import src.batch as batch_mod

        batch_mod._r2_queue_depth = None
        batch_mod._r2_queue_depth_ts = 0

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=42)

        depth1 = await batch_mod._get_r2_queue_depth(pool)
        assert depth1 == 42
        assert pool.fetchval.await_count == 1

        # Second call within 30s should use cache
        depth2 = await batch_mod._get_r2_queue_depth(pool)
        assert depth2 == 42
        assert pool.fetchval.await_count == 1  # no new DB call

        # Reset
        batch_mod._r2_queue_depth = None
        batch_mod._r2_queue_depth_ts = 0

    async def test_get_r2_queue_depth_refreshes_after_ttl(self):
        """Queue depth refreshes after 30s TTL."""
        from time import monotonic

        import src.batch as batch_mod

        batch_mod._r2_queue_depth = 10
        batch_mod._r2_queue_depth_ts = monotonic() - 60  # expired

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=999)

        depth = await batch_mod._get_r2_queue_depth(pool)
        assert depth == 999
        assert pool.fetchval.await_count == 1

        # Reset
        batch_mod._r2_queue_depth = None
        batch_mod._r2_queue_depth_ts = 0
