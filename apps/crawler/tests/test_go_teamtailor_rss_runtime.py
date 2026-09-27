"""Exclusive Go Teamtailor RSS routing preserves rich output and HTTP failures."""

from __future__ import annotations

import asyncio
import hashlib
import os

import httpx
import pytest
import structlog

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
    with structlog.testing.capture_logs() as logs:
        result = [item async for item in runtime.stream(BOARD_URL, "rss", config, None)]
    event = next(row for row in logs if row["event"] == "go_teamtailor_rss.monitor_postprocessed")
    assert event["urls"] == 2
    assert (
        event["url_sha256"]
        == hashlib.sha256(f"{BOARD_URL}/123\n{BOARD_URL}/456".encode()).hexdigest()
    )
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


@pytest.mark.parametrize("history", [None, [], [4], [0, 0, 0], [501, 600, 700]])
def test_default_go_without_history_quota(monkeypatch, history):
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_PERCENT", raising=False)
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_BOARD_IDS", raising=False)
    config = {**CONFIG, "recent_discovered_counts": history}
    assert percentage_selected(BOARD_ID, BOARD_URL, config)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID, None, monitor_type="rss", board_url=BOARD_URL, monitor_config=config
        ).implementation
        == "go-teamtailor-rss"
    )


@pytest.mark.parametrize("percent", ["0", "-1", "101", "garbage", "01"])
def test_percentage_reversal_and_invalid_values(monkeypatch, percent):
    monkeypatch.setenv("TEAMTAILOR_RSS_GO_PERCENT", percent)
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_BOARD_IDS", raising=False)
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID, None, monitor_type="rss", board_url=BOARD_URL, monitor_config=CONFIG
        ).implementation
        == "python"
    )
    monkeypatch.setenv("TEAMTAILOR_RSS_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-teamtailor-rss"


@pytest.mark.parametrize(
    "extra",
    [
        {"render": True},
        {"proxy": "browser"},
        {"verify_ssl": False},
        {"feed_url": "https://other.test/jobs.rss"},
        {"feed_url": FEED_URL + "?offset=10"},
        {"preset": "successfactors"},
    ],
)
def test_transport_guard(monkeypatch, extra):
    monkeypatch.delenv("TEAMTAILOR_RSS_GO_PERCENT", raising=False)
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**CONFIG, **extra})


@pytest.mark.asyncio
async def test_empty_feed_is_success(tmp_path):
    runtime = GoTeamtailorRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 50,
                "status": 200,
                "final_url": FEED_URL,
            },
        ),
        board_id=BOARD_ID,
    )
    with structlog.testing.capture_logs() as logs:
        assert [item async for item in runtime.stream(BOARD_URL, "rss", CONFIG, None)] == []
    event = next(row for row in logs if row["event"] == "go_teamtailor_rss.monitor_postprocessed")
    assert event["urls"] == 0
    assert event["url_sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_child_is_bounded_and_reaped_even_when_it_ignores_termination(
    tmp_path, monkeypatch, cancel
):
    pid_file = tmp_path / "pid"
    path = tmp_path / "stuck-child"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        + ("" if cancel else "os.write(1, b'x' * 2048)\n")
        + "time.sleep(30)\n"
    )
    path.chmod(0o755)
    monkeypatch.setattr("src.runtime.teamtailor_rss_go._MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr("src.runtime.teamtailor_rss_go._CHILD_STOP_SECONDS", 0.05)
    runtime = GoTeamtailorRSSMonitorRuntime(str(path), board_id=BOARD_ID)

    async def consume():
        return [item async for item in runtime.stream(BOARD_URL, "rss", CONFIG, None)]

    task = asyncio.create_task(consume())
    async with asyncio.timeout(5):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.01)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError, match="exceeded"):
                await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
