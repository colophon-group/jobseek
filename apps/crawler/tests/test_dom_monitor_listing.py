"""Tests for opt-in listing-card fields on the DOM monitor."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.dom import _extract_listing_jobs, dom_discover

BOARD_URL = "https://example.com/careers"
LISTING_CONFIG = {
    "item": ".job-card",
    "link": "h3 a",
    "fields": {
        "title": "h3",
        "locations": {"selector": ".location", "split": ";"},
    },
}
HTML = """
<ul>
  <li class="job-card">
    <h3><a href="/careers/job/one">Researcher</a></h3>
    <span class="location">Berlin Mitte; Campus Wedding</span>
  </li>
  <li class="job-card">
    <h3><a href="/careers/job/two">Engineer</a></h3>
    <span class="location">Berlin Mitte</span>
  </li>
</ul>
"""


def test_extract_listing_jobs_maps_relative_urls_and_split_locations() -> None:
    jobs = _extract_listing_jobs(HTML, BOARD_URL, LISTING_CONFIG)

    assert [job.url for job in jobs] == [
        "https://example.com/careers/job/one",
        "https://example.com/careers/job/two",
    ]
    assert jobs[0].title == "Researcher"
    assert jobs[0].locations == ["Berlin Mitte", "Campus Wedding"]
    assert jobs[1].locations == ["Berlin Mitte"]


def test_extract_listing_jobs_rejects_unknown_fields() -> None:
    config = {**LISTING_CONFIG, "fields": {"salary": ".salary"}}

    with pytest.raises(ValueError, match="Unsupported DOM listing fields: salary"):
        _extract_listing_jobs(HTML, BOARD_URL, config)


@pytest.mark.asyncio
async def test_dom_discover_returns_hybrid_partial_rich_result(monkeypatch) -> None:
    fetch = AsyncMock(return_value=HTML)
    monkeypatch.setattr("src.shared.http_retry.fetch_with_retry", fetch)
    board = {
        "board_url": BOARD_URL,
        "metadata": {
            "url_filter": "/careers/job/",
            "listing": LISTING_CONFIG,
        },
    }

    result = await dom_discover(board, client=object())

    assert isinstance(result, MonitorResult)
    assert result.hybrid is True
    assert result.urls == {
        "https://example.com/careers/job/one",
        "https://example.com/careers/job/two",
    }
    assert result.jobs_by_url is not None
    assert result.jobs_by_url["https://example.com/careers/job/one"].locations == [
        "Berlin Mitte",
        "Campus Wedding",
    ]


@pytest.mark.asyncio
async def test_dom_discover_listing_fails_when_a_job_link_is_unmapped(monkeypatch) -> None:
    html = HTML + '<a href="/careers/job/unmapped">Unmapped</a>'
    monkeypatch.setattr(
        "src.shared.http_retry.fetch_with_retry",
        AsyncMock(return_value=html),
    )
    board = {
        "board_url": BOARD_URL,
        "metadata": {
            "url_filter": "/careers/job/",
            "listing": LISTING_CONFIG,
        },
    }

    with pytest.raises(ValueError, match="must map every discovered job URL"):
        await dom_discover(board, client=object())


@pytest.mark.asyncio
async def test_dom_listing_rejects_pagination() -> None:
    board = {
        "board_url": BOARD_URL,
        "metadata": {
            "listing": LISTING_CONFIG,
            "pagination": {"param_name": "page"},
        },
    }

    with pytest.raises(ValueError, match="does not support pagination"):
        await dom_discover(board, client=object())
