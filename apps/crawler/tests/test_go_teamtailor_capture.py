"""A normal Python Teamtailor cycle saves only a complete page-zero response."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest

from src.core.monitors import teamtailor_capture
from src.core.monitors.rss import discover_stream

FEED_URL = "https://careers.example.com/jobs.rss"
BOARD = {
    "board_url": "https://careers.example.com/jobs",
    "metadata": {"preset": "teamtailor", "feed_url": FEED_URL},
}
BODY = b'<rss version="2.0"><channel><item><link>https://careers.example.com/jobs/1</link></item></channel></rss>'


@pytest.mark.asyncio
async def test_captures_natural_page_without_extra_request(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMTAILOR_RSS_CAPTURE_HOST", "careers.example.com")
    monkeypatch.setattr(teamtailor_capture, "_CAPTURE_DIR", tmp_path)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=BODY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = [batch async for batch in discover_stream(BOARD, client)]
    assert len(result) == 1
    assert result[0][0].url == "https://careers.example.com/jobs/1"
    assert requests == [FEED_URL + "?offset=0&per_page=100"]
    captures = list(tmp_path.glob("jobseek-teamtailor-*-page0.rss"))
    assert len(captures) == 1
    assert captures[0].read_bytes() == BODY
    assert captures[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_incomplete_feed_does_not_leave_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMTAILOR_RSS_CAPTURE_HOST", "careers.example.com")
    monkeypatch.setattr(teamtailor_capture, "_CAPTURE_DIR", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=BODY[:-6]))
    ) as client:
        with pytest.raises(ET.ParseError):
            async for _ in discover_stream(BOARD, client):
                pass
    assert not list(tmp_path.iterdir())
