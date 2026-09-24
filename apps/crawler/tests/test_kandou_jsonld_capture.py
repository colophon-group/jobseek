"""A selected natural Kandou scrape records its actual rendered attempts."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.scrapers import jsonld_capture
from src.core.scrapers.jsonld import scrape

URL = "https://kandou.bamboohr.com/careers/360"
EMPTY = "<html><title>Open positions</title></html>"
JOB = '<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>'


@pytest.mark.asyncio
async def test_selected_natural_scrape_captures_both_renders_without_extra_request(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KANDOU_JSONLD_CAPTURE_URL", URL)
    monkeypatch.setattr(jsonld_capture, "_TRACE_DIR", tmp_path)
    with (
        patch(
            "src.core.scrapers.jsonld._render_with_origin_block_recovery",
            new_callable=AsyncMock,
            side_effect=[EMPTY, JOB],
        ) as render,
        patch("src.core.scrapers.jsonld.asyncio.sleep", new_callable=AsyncMock),
    ):
        async with httpx.AsyncClient() as client:
            result = await scrape(URL, {"render": True}, client, pw="existing-browser")

    assert result.title == "Engineer"
    assert render.await_count == 2
    records = [
        json.loads(line)
        for line in (tmp_path / "jobseek-kandou-jsonld-360.jsonl").read_text().splitlines()
    ]
    assert records[0] == {"schema": "jobseek.kandou-rendered-dom-capture/v1", "url": URL}
    assert [record["title_found"] for record in records[1:-1]] == [False, True]
    assert [base64.b64decode(record["html_b64"]).decode() for record in records[1:-1]] == [
        EMPTY,
        JOB,
    ]
    assert records[-1] == {"complete": True, "attempts": 2}
    assert (tmp_path / "jobseek-kandou-jsonld-360.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_capture_stays_off_for_other_origin_and_oversize_does_not_change_scrape(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KANDOU_JSONLD_CAPTURE_URL", URL)
    monkeypatch.setattr(jsonld_capture, "_TRACE_DIR", tmp_path)
    with patch(
        "src.core.scrapers.jsonld._render_with_origin_block_recovery",
        new_callable=AsyncMock,
        return_value=JOB,
    ) as render:
        async with httpx.AsyncClient() as client:
            result = await scrape("https://other.example/careers/360", {"render": True}, client)
    assert result.title == "Engineer"
    render.assert_awaited_once()
    assert list(tmp_path.iterdir()) == []

    too_large = "x" * 1_000_001
    with (
        patch(
            "src.core.scrapers.jsonld._render_with_origin_block_recovery",
            new_callable=AsyncMock,
            side_effect=[too_large, JOB],
        ) as render,
        patch("src.core.scrapers.jsonld.asyncio.sleep", new_callable=AsyncMock),
    ):
        async with httpx.AsyncClient() as client:
            result = await scrape(URL, {"render": True}, client)
    assert result.title == "Engineer"
    assert render.await_count == 2
    assert list(tmp_path.iterdir()) == []
