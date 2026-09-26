"""Natural Recruitee capture observes an existing API response."""

from __future__ import annotations

import pytest

from src.core.monitors import recruitee_capture
from src.core.monitors.recruitee import discover


@pytest.mark.asyncio
async def test_capture_observes_existing_response(monkeypatch, tmp_path):
    import httpx

    monkeypatch.setattr(recruitee_capture, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("RECRUITEE_CAPTURE_TENANTS", "acme")
    requests = []
    body = b'{"offers":[]}'

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await discover(
            {"board_url": "https://acme.recruitee.com", "metadata": {"slug": "acme"}},
            client,
        )
    assert result == []
    assert requests == ["https://acme.recruitee.com/api/offers"]
    assert (tmp_path / "jobseek-recruitee-acme.json").read_bytes() == body
