"""Natural Pinpoint capture observes an existing API response."""

from __future__ import annotations

import pytest

from src.core.monitors import pinpoint_capture
from src.core.monitors.pinpoint import discover


@pytest.mark.asyncio
async def test_capture_observes_existing_response(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr(pinpoint_capture, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("PINPOINT_CAPTURE_TENANTS", "acme")
    requests = []
    body = b'{"data":[]}'

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover(
            {"board_url": "https://acme.pinpointhq.com", "metadata": {"slug": "acme"}},
            client,
        )
    assert result == []
    assert requests == ["https://acme.pinpointhq.com/postings.json"]
    assert (tmp_path / "jobseek-pinpoint-acme.json").read_bytes() == body
