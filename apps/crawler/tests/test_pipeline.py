"""Tests for pipeline and drain pure functions (no async loops)."""

from __future__ import annotations

import json

from src.redis_queue import ScrapeWork
from src.workers.pipeline import _BoardRecord, _resolve_scraper, _scrape_item_from_redis

# ---------------------------------------------------------------------------
# _BoardRecord
# ---------------------------------------------------------------------------


class TestBoardRecord:
    def test_basic_fields(self):
        config = {
            "company_id": "comp-1",
            "board_url": "https://example.com/jobs",
            "crawler_type": "greenhouse",
            "check_interval_minutes": "30",
            "metadata": json.dumps({"scraper_type": "jsonld"}),
        }
        rec = _BoardRecord("board-1", config)
        assert rec["id"] == "board-1"
        assert rec["company_id"] == "comp-1"
        assert rec["board_url"] == "https://example.com/jobs"
        assert rec["crawler_type"] == "greenhouse"
        assert rec["check_interval_minutes"] == 30
        assert rec["metadata"]["scraper_type"] == "jsonld"

    def test_missing_metadata(self):
        rec = _BoardRecord("board-2", {"board_url": "https://x.com"})
        assert rec["metadata"] == {}

    def test_invalid_metadata_json(self):
        rec = _BoardRecord("board-3", {"metadata": "not-json{"})
        assert rec["metadata"] == {}

    def test_get_with_default(self):
        rec = _BoardRecord("board-4", {})
        assert rec.get("nonexistent", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# _scrape_item_from_redis
# ---------------------------------------------------------------------------


class TestScrapeItemFromRedis:
    def test_basic_conversion(self):
        work = ScrapeWork(
            posting_id="post-1",
            source_url="https://example.com/job/123",
            board_id="board-1",
            description_r2_hash=12345,
            scraper_needs_browser=False,
            scrape_interval_hours=24,
        )
        item, step = _scrape_item_from_redis(work)
        assert item.job_posting_id == "post-1"
        assert item.url == "https://example.com/job/123"
        assert item.board_id == "board-1"
        assert item.description_r2_hash == 12345
        assert step == 0

    def test_with_scrape_step(self):
        work = ScrapeWork(
            posting_id="post-2",
            source_url="https://example.com/job/456",
            board_id="board-2",
            description_r2_hash=None,
            scraper_needs_browser=True,
            scrape_interval_hours=12,
            scrape_step=2,
        )
        item, step = _scrape_item_from_redis(work)
        assert item.job_posting_id == "post-2"
        assert step == 2

    def test_null_hash(self):
        work = ScrapeWork(
            posting_id="post-3",
            source_url="https://example.com/job/789",
            board_id="board-3",
            description_r2_hash=None,
            scraper_needs_browser=False,
            scrape_interval_hours=24,
        )
        item, _ = _scrape_item_from_redis(work)
        assert item.description_r2_hash is None


# ---------------------------------------------------------------------------
# _resolve_scraper
# ---------------------------------------------------------------------------


class TestResolveScraper:
    def test_explicit_scraper_wins(self):
        scraper_type, scraper_config = _resolve_scraper(
            {"scraper_type": "dom"}, crawler_type="sitemap", scraper_config={"render": True}
        )
        assert scraper_type == "dom"
        assert scraper_config == {"render": True}

    def test_auto_scraper_resolution(self):
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="workday", scraper_config=None
        )
        assert scraper_type == "workday"
        assert scraper_config is None

    def test_personio_without_scraper_type_defaults_to_dom(self):
        """Regression guard for issue #2186.

        personio has no auto_scraper_type mapping, so when metadata has no
        explicit scraper_type we must NOT fall back to using ``crawler_type``
        as the scraper name (which crashes).  ``dom`` is the safe default —
        the validator rejects this configuration at CI anyway.
        """
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="personio", scraper_config=None
        )
        assert scraper_type == "dom"
        assert scraper_config is None

    def test_rich_monitor_does_not_resolve_to_skip(self):
        """Rich monitors auto-resolve to ``skip``, but _is_skip_no_scrape is
        the real guard — this helper must never return ``skip`` itself, or
        the ``skip`` scraper would be invoked and raise."""
        scraper_type, _ = _resolve_scraper({}, crawler_type="greenhouse", scraper_config=None)
        assert scraper_type != "skip"
        assert scraper_type == "dom"

    def test_no_crawler_type_defaults_to_dom(self):
        scraper_type, scraper_config = _resolve_scraper({}, crawler_type=None, scraper_config=None)
        assert scraper_type == "dom"
        assert scraper_config is None

    def test_auto_config_applied_when_no_explicit_scraper_config(self):
        """breezy's auto config should flow through when no override is set."""
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="breezy", scraper_config=None
        )
        assert scraper_type == "json-ld"
        assert isinstance(scraper_config, dict)
        assert "fallback" in scraper_config

    def test_explicit_scraper_config_overrides_auto(self):
        override = {"steps": [{"tag": "h1", "field": "title"}]}
        scraper_type, scraper_config = _resolve_scraper(
            {}, crawler_type="breezy", scraper_config=override
        )
        assert scraper_type == "json-ld"
        assert scraper_config is override
