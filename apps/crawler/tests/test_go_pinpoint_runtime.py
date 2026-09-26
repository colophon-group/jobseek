"""Selected Pinpoint inventory is produced exclusively by the Go runtime."""

from __future__ import annotations

import httpx
import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.pinpoint_go import GoPinpointMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://acme.pinpointhq.com"
CONFIG = {"slug": "acme", "scraper_type": "skip"}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "pinpoint-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--tenant', 'acme']\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_go_pinpoint_emits_rich_job(tmp_path):
    url = f"{BOARD_URL}/o/engineer"
    runtime = GoPinpointMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [{"url": url, "title": "Engineer", "description": "<p>Build</p>"}],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": f"{BOARD_URL}/postings.json",
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "pinpoint", CONFIG, None)]
    assert len(result) == 1
    assert result[0].urls == {url}
    assert result[0].jobs_by_url[url].description == "<p>Build</p>"


@pytest.mark.asyncio
async def test_go_pinpoint_preserves_http_404_failure(tmp_path):
    runtime = GoPinpointMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 4,
                "status": 404,
                "final_url": f"{BOARD_URL}/postings.json",
                "error": "Pinpoint tenant returned HTTP 404",
            },
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in runtime.stream(BOARD_URL, "pinpoint", CONFIG, None):
            pass


def test_dark_default_and_strict_percent(monkeypatch):
    monkeypatch.delenv("PINPOINT_GO_PERCENT", raising=False)
    monkeypatch.delenv("PINPOINT_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("PINPOINT_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-pinpoint"
    monkeypatch.delenv("PINPOINT_GO_BOARD_IDS")
    config = {**CONFIG, "recent_discovered_counts": [4, 4, 4]}
    monkeypatch.setenv("PINPOINT_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, BOARD_URL, config)
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**config, "api_base": "https://other.test"}
    )
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**config, "scraper_type": "json-ld"})
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**config, "recent_discovered_counts": [4, 4]}
    )
