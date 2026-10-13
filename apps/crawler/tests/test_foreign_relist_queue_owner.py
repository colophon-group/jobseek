"""Foreign relists must publish the canonical board's detail route."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from src.processing.board import _enqueue_foreign_relisted_scrapes

POSTING = "9e3f7c70-ac82-465f-bb18-741d5de8cd5c"
OWNER = "c6513f39-dfdb-4a7a-b7b9-6ac589a7bd7c"
URL = "https://jobs.example.invalid/job/123"


def route(metadata: dict, *, source: str = URL, content_hash: int | None = 123) -> dict:
    return {
        "id": POSTING,
        "board_id": OWNER,
        "source_url": source,
        "description_r2_hash": content_hash,
        "crawler_type": "sitemap",
        "metadata": metadata,
    }


async def enqueue(rows: list[dict], monkeypatch) -> AsyncMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    publish = AsyncMock()
    monkeypatch.setattr("src.processing.board._enqueue_scrape", publish)
    await _enqueue_foreign_relisted_scrapes(
        pool,
        [{"id": POSTING, "url": URL, "r2_hash": None}],
        structlog.get_logger(),
    )
    return publish


@pytest.mark.parametrize("browser", [False, True])
async def test_foreign_relist_cache_uses_current_owner_content_and_lane(monkeypatch, browser):
    metadata = {"scraper_type": "dom", "scraper_config": {"render": browser}}
    publish = await enqueue([route(metadata)], monkeypatch)
    publish.assert_awaited_once()
    domain, posting_id, score, cache = publish.await_args.args
    assert domain == "jobs.example.invalid" and posting_id == POSTING and score > 0
    assert cache["board_id"] == OWNER and cache["source_url"] == URL
    assert cache["description_r2_hash"] == "123" and cache["scrape_step"] == "0"
    assert publish.await_args.kwargs == {"browser": browser, "first_time": False}


async def test_foreign_relist_without_content_retains_first_time_priority(monkeypatch):
    publish = await enqueue([route({"scraper_type": "json-ld"}, content_hash=None)], monkeypatch)
    assert publish.await_args.args[2] == 0
    assert publish.await_args.args[3]["board_id"] == OWNER
    assert publish.await_args.kwargs == {"browser": False, "first_time": True}


@pytest.mark.parametrize(
    "rows",
    [
        [],  # Withdrawn, completed, disabled or reserved rows fail the SQL predicate.
        [route({"scraper_type": "skip"})],
        [route({"scraper_type": "dom"}, source=URL + "-changed")],
    ],
)
async def test_foreign_relist_does_not_publish_invalidated_or_skip_intent(monkeypatch, rows):
    publish = await enqueue(rows, monkeypatch)
    publish.assert_not_awaited()
