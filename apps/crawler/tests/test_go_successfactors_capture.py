"""A natural Python SuccessFactors feed response is captured once without extra traffic."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import pytest

from src.core.monitors import successfactors_capture
from src.core.monitors.rss import discover_stream

FEED_URL = "https://jobs.example.com/googlefeed.xml"
BOARD = {
    "board_url": "https://jobs.example.com/careers",
    "metadata": {
        "preset": "successfactors",
        "feed_url": FEED_URL,
    },
}
BODY = b"<rss><channel><item><link>https://jobs.example.com/job/1</link></item></channel></rss>"


@pytest.mark.asyncio
async def test_capture_natural_successfactors_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("SUCCESSFACTORS_RSS_CAPTURE_HOST", "jobs.example.com")
    monkeypatch.setattr(successfactors_capture, "_CAPTURE_DIR", tmp_path)
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=BODY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = [batch async for batch in discover_stream(BOARD, client)]
    assert result[0][0].url == "https://jobs.example.com/job/1"
    assert requests == [FEED_URL]
    captures = list(tmp_path.glob("jobseek-successfactors-*-feed.rss"))
    assert len(captures) == 1
    assert captures[0].read_bytes() == BODY
    assert captures[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_incomplete_successfactors_feed_is_not_captured(tmp_path, monkeypatch):
    monkeypatch.setenv("SUCCESSFACTORS_RSS_CAPTURE_HOST", "jobs.example.com")
    monkeypatch.setattr(successfactors_capture, "_CAPTURE_DIR", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=BODY[:-6]))
    ) as client:
        with pytest.raises(ET.ParseError):
            async for _ in discover_stream(BOARD, client):
                pass
    assert not list(tmp_path.iterdir())
