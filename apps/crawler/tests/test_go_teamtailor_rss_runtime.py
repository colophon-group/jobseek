"""Exclusive Go Teamtailor RSS routing preserves rich output and HTTP failures."""

from __future__ import annotations

import httpx
import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.teamtailor_rss_go import GoTeamtailorRSSMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://careers.example.com/jobs"
FEED_URL = "https://careers.example.com/jobs.rss"
CONFIG = {"preset": "teamtailor", "feed_url": FEED_URL, "scraper_type": "skip"}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "teamtailor-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"assert sys.argv[1:] == ['--feed-url', {FEED_URL!r}]\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_go_teamtailor_emits_rich_job(tmp_path):
    url = f"{BOARD_URL}/123"
    runtime = GoTeamtailorRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [
                    {
                        "url": url,
                        "title": "Engineer",
                        "description": "<p>Build</p>",
                        "locations": ["Zurich"],
                        "job_location_type": "hybrid",
                        "metadata": {"id": "guid-123"},
                    }
                ],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": FEED_URL + "?offset=0&per_page=100",
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "rss", CONFIG, None)]
    assert len(result) == 1
    assert result[0].urls == {url}
    assert result[0].jobs_by_url[url].description == "<p>Build</p>"
    assert result[0].jobs_by_url[url].metadata == {"id": "guid-123"}


@pytest.mark.asyncio
async def test_go_teamtailor_applies_streamed_url_policy(tmp_path):
    """Go output must retain the Python monitor's provider-boundary identity policy."""
    first = f"{BOARD_URL}/123-engineer"
    second = f"{BOARD_URL}/456-designer"
    rejected = "https://elsewhere.example.com/jobs/789"
    config = {
        **CONFIG,
        "url_allowlist": r"^https://careers\.example\.com/jobs/[0-9]+(?:-[^/?#]+)?$",
        "url_transform": {
            "find": r"^https://careers\.example\.com/jobs/([0-9]+)(?:-[^/?#]+)?$",
            "replace": r"https://careers.example.com/jobs/\1",
        },
    }
    runtime = GoTeamtailorRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [
                    {"url": first, "title": "Engineer", "metadata": {"id": "123"}},
                    {"url": second, "title": "Designer", "metadata": {"id": "456"}},
                    {"url": rejected, "title": "Other"},
                ],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": FEED_URL + "?offset=0&per_page=100",
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "rss", config, None)]
    assert len(result) == 1
    assert result[0].urls == {
        f"{BOARD_URL}/123",
        f"{BOARD_URL}/456",
    }
    assert result[0].security_filtered_count == 1
    assert result[0].jobs_by_url[f"{BOARD_URL}/123"].title == "Engineer"


@pytest.mark.asyncio
async def test_go_teamtailor_preserves_http_failure(tmp_path):
    runtime = GoTeamtailorRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 4,
                "status": 404,
                "final_url": FEED_URL + "?offset=0&per_page=100",
                "error": "Teamtailor RSS returned HTTP 404",
            },
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in runtime.stream(BOARD_URL, "rss", CONFIG, None):
            pass


def test_default_dark_and_config_guard(monkeypatch):
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_PERCENT", raising=False)
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("TEAMTAILOR_RSS_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-teamtailor-rss"
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_BOARD_IDS")
    selected = {**CONFIG, "recent_discovered_counts": [4, 4, 4]}
    monkeypatch.setenv("TEAMTAILOR_RSS_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, BOARD_URL, selected)
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**selected, "render": True})
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**selected, "feed_url": "https://other.test/jobs.rss"}
    )
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**selected, "recent_discovered_counts": [4]}
    )
