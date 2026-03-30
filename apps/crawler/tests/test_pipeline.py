"""Tests for pipeline and drain pure functions (no async loops)."""

from __future__ import annotations

import json

from src.redis_queue import ScrapeWork
from src.workers.pipeline import _BoardRecord, _scrape_item_from_redis

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
