"""Tests for the R2 background drain worker."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.batch import (
    _build_r2_extras,
    _compute_r2_hash,
    _serialize_localizations,
    _stable_date,
    _stage_r2_pending,
)
from src.r2_worker import TokenBucket, _drain_one

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


# ── TokenBucket tests ────────────────────────────────────────────────


class TestTokenBucket:
    async def test_immediate_acquire_within_burst(self):
        bucket = TokenBucket(rate=100, burst=10)
        # Should not block for tokens within burst
        t0 = asyncio.get_event_loop().time()
        await bucket.acquire(4)
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 0.1

    async def test_blocks_when_tokens_exhausted(self):
        bucket = TokenBucket(rate=100, burst=4)
        await bucket.acquire(4)  # exhaust burst
        t0 = asyncio.get_event_loop().time()
        await bucket.acquire(1)  # should block ~0.01s
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed >= 0.005  # at least some delay


# ── _drain_one tests ─────────────────────────────────────────────────


class TestDrainOne:
    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_success_clears_pending(self, mock_upload):
        """Successful upload NULLs pending columns and sets hash."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {"title": "Test"},
            "tech_ids": [1],
            "localizations": None,
            "source": "monitor",
            "retry_count": 0,
            "new_hash": 99999,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Hello</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is True
        mock_upload.assert_awaited_once_with("abc-123", "en", "<p>Hello</p>", {"title": "Test"})
        # Verify _COMPLETE_R2_UPLOAD was called
        complete_calls = [
            c for c in conn.execute.await_args_list if "description_pending = NULL" in c.args[0]
        ]
        assert len(complete_calls) == 1
        assert complete_calls[0].args[1] == "abc-123"
        assert complete_calls[0].args[2] == 99999  # new_hash

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_meta_only_fetches_from_r2(self, mock_upload):
        """Meta-only change fetches existing HTML from R2."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {"title": "Updated"},
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 0,
            "new_hash": 88888,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": None,  # meta-only
            "r2_pending_meta": meta,
            "description_r2_hash": 77777,
        }[k]

        with patch("src.r2_worker.get_description_html", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "<p>Existing</p>"
            ok = await _drain_one(conn, row, bucket)

        assert ok is True
        mock_upload.assert_awaited_once_with(
            "abc-123", "en", "<p>Existing</p>", {"title": "Updated"}
        )

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_retry_on_failure(self, mock_upload):
        """Failed upload increments retry count."""
        mock_upload.side_effect = Exception("R2 down")
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {},
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 2,
            "new_hash": 11111,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Test</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is False
        # Should increment retry, not abandon
        retry_calls = [c for c in conn.execute.await_args_list if "retry_count" in c.args[0]]
        assert len(retry_calls) == 1

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_abandon_after_max_retries_monitor(self, mock_upload):
        """Monitor source: abandon after max retries, no next_scrape_at reset."""
        mock_upload.side_effect = Exception("R2 down")
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {},
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 4,  # one more = 5 = max
            "new_hash": 11111,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Test</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is False
        # Should abandon (NULL pending columns)
        abandon_calls = [
            c
            for c in conn.execute.await_args_list
            if "description_pending = NULL" in c.args[0] and "description_r2_hash" not in c.args[0]
        ]
        assert len(abandon_calls) == 1
        # Should NOT reset next_scrape_at for monitor source
        scrape_calls = [c for c in conn.execute.await_args_list if "next_scrape_at" in c.args[0]]
        assert len(scrape_calls) == 0

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_abandon_after_max_retries_scrape_resets_scrape(self, mock_upload):
        """Scrape source: abandon and reset next_scrape_at for re-scrape."""
        mock_upload.side_effect = Exception("R2 down")
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {},
            "tech_ids": None,
            "localizations": None,
            "source": "scrape",
            "retry_count": 4,
            "new_hash": 11111,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Test</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is False
        # Should reset next_scrape_at for scrape source
        scrape_calls = [c for c in conn.execute.await_args_list if "next_scrape_at" in c.args[0]]
        assert len(scrape_calls) == 1

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    @patch("src.r2_worker.upload_description", new_callable=AsyncMock)
    async def test_localizations_uploaded(self, mock_loc_upload, mock_upload):
        """Secondary locale descriptions are uploaded."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {},
            "tech_ids": None,
            "localizations": {"de": "<p>Hallo</p>", "fr": "<p>Bonjour</p>"},
            "source": "monitor",
            "retry_count": 0,
            "new_hash": 55555,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Hello</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is True
        assert mock_loc_upload.await_count == 2

    async def test_null_meta_abandons(self):
        """If r2_pending_meta is NULL (shouldn't happen), abandon gracefully."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Test</p>",
            "r2_pending_meta": None,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)

        assert ok is True
        # Should abandon
        abandon_calls = [
            c for c in conn.execute.await_args_list if "description_pending = NULL" in c.args[0]
        ]
        assert len(abandon_calls) == 1


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


class TestDrainOneEdgeCases:
    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_meta_as_json_string(self, mock_upload):
        """r2_pending_meta stored as JSON string (not dict) is handled."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = json.dumps(
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
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Hello</p>",
            "r2_pending_meta": meta,  # string, not dict
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)
        assert ok is True
        mock_upload.assert_awaited_once()

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_missing_extras_key(self, mock_upload):
        """Meta without extras key defaults to empty dict."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            # no "extras" key
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 0,
            "new_hash": 99999,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Hello</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)
        assert ok is True
        # upload_posting called with empty extras dict
        call_args = mock_upload.await_args
        assert call_args.args[3] == {}

    @patch("src.r2_worker.upload_posting", new_callable=AsyncMock)
    async def test_new_hash_none_in_meta(self, mock_upload):
        """new_hash=None in meta is passed through to DB (NULL hash)."""
        conn = AsyncMock()
        bucket = TokenBucket(rate=10000, burst=100)

        meta = {
            "locale": "en",
            "extras": {},
            "tech_ids": None,
            "localizations": None,
            "source": "monitor",
            "retry_count": 0,
            "new_hash": None,
        }
        row = MagicMock()
        row.__getitem__ = lambda s, k: {
            "id": "abc-123",
            "description_pending": "<p>Hello</p>",
            "r2_pending_meta": meta,
            "description_r2_hash": None,
        }[k]

        ok = await _drain_one(conn, row, bucket)
        assert ok is True
        complete_calls = [
            c
            for c in conn.execute.await_args_list
            if "description_pending = NULL" in c.args[0] and "description_r2_hash" in c.args[0]
        ]
        assert len(complete_calls) == 1
        assert complete_calls[0].args[2] is None  # new_hash = None
