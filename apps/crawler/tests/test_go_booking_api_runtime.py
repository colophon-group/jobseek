"""The selected Booking DOM inventory can use one exclusive Go API route."""

from __future__ import annotations

import httpx
import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.booking_api_go import (
    API_URL,
    BOARD_ID,
    BOARD_URL,
    GoBookingAPIMonitorRuntime,
)

CONFIG = {
    "render": True,
    "url_filter": {"include": r"jobs\.booking\.com/booking/jobs/\d+"},
    "scraper_type": "json-ld",
}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "booking-api-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert len(sys.argv) == 1\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_go_booking_emits_current_dom_urls(tmp_path):
    urls = [
        "https://jobs.booking.com/booking/jobs/30336?lang=en-us",
        "https://jobs.booking.com/booking/jobs/13402?lang=en-us",
    ]
    runtime = GoBookingAPIMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": urls,
                "advertised": 92,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": API_URL,
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "dom", CONFIG, None, pw=object())]
    assert len(result) == 1
    assert result[0].urls == set(urls)


@pytest.mark.asyncio
async def test_go_booking_preserves_http_failure(tmp_path):
    runtime = GoBookingAPIMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": [],
                "advertised": 0,
                "requests": 1,
                "responses": 1,
                "bytes": 4,
                "status": 429,
                "final_url": API_URL,
                "error": "publisher rate limited",
            },
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in runtime.stream(BOARD_URL, "dom", CONFIG, None):
            pass


def test_dark_default_and_exact_config(monkeypatch):
    monkeypatch.delenv("BOOKING_GO_BOARD_ID", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("BOOKING_GO_BOARD_ID", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-booking-api"
    assert _monitor_runtime_for_board("other", None).implementation == "python"


@pytest.mark.asyncio
async def test_go_booking_rejects_changed_configuration_before_request(tmp_path):
    runtime = GoBookingAPIMonitorRuntime("/nonexistent-binary", board_id=BOARD_ID)
    with pytest.raises(ValueError, match="unchanged"):
        async for _ in runtime.stream(
            BOARD_URL,
            "dom",
            {**CONFIG, "url_filter": {"include": "all-jobs"}},
            None,
        ):
            pass
