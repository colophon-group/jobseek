"""Go SuccessFactors routing keeps rich output, filters, and failure semantics."""

from __future__ import annotations

import asyncio
import hashlib
import os

import httpx
import pytest
import structlog

from src.processing.board import _monitor_runtime_for_board
from src.runtime.successfactors_rss_go import (
    GoSuccessFactorsRSSMonitorRuntime,
    _eligible,
    percentage_selected,
)

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://jobs.example.com/careers"
FEED_URL = "https://jobs.example.com/googlefeed.xml"
CONFIG = {"preset": "successfactors", "feed_url": FEED_URL, "scraper_type": "skip"}


def fake_binary(tmp_path, records: list[dict], *, exit_code: int = 0) -> str:
    path = tmp_path / "successfactors-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"assert sys.argv[1:] == ['--feed-url', {FEED_URL!r}]\n"
        f"for record in {records!r}: print(json.dumps(record))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def summary(*, jobs: int, status: int = 200, error: str | None = None) -> dict:
    return {
        "type": "summary",
        "items": jobs,
        "jobs": jobs,
        "truncated": False,
        "requests": 1,
        "responses": 1,
        "bytes": 100,
        "status": status,
        "final_url": FEED_URL,
        "error": error,
    }


@pytest.mark.asyncio
async def test_go_successfactors_streams_rich_jobs_through_shared_filters(tmp_path):
    config = {
        **CONFIG,
        "url_allowlist": r"^https://jobs\.example\.com/job/[0-9]+$",
        "url_filter": {"exclude": "/job/3$"},
        "url_transform": {"find": "/job/", "replace": "/canonical/"},
    }
    runtime = GoSuccessFactorsRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            [
                {
                    "type": "job",
                    "job": {
                        "url": "https://jobs.example.com/job/1",
                        "title": "Engineer",
                        "description": "<p>Build</p>",
                        "locations": ["Zurich"],
                        "metadata": {"id": "123"},
                    },
                },
                {"type": "job", "job": {"url": "https://elsewhere.example.com/job/2"}},
                {"type": "job", "job": {"url": "https://jobs.example.com/job/3"}},
                summary(jobs=3),
            ],
        ),
        board_id=BOARD_ID,
    )
    async with httpx.AsyncClient() as client:
        with structlog.testing.capture_logs() as logs:
            result = [item async for item in runtime.stream(BOARD_URL, "rss", config, client)]
    assert len(result) == 1
    assert result[0].urls == {"https://jobs.example.com/canonical/1"}
    event = next(r for r in logs if r["event"] == "go_successfactors_rss.monitor_postprocessed")
    assert event["urls"] == 1
    assert (
        event["url_sha256"] == hashlib.sha256(b"https://jobs.example.com/canonical/1").hexdigest()
    )
    assert result[0].security_filtered_count == 1
    assert (
        result[0].jobs_by_url["https://jobs.example.com/canonical/1"].description == "<p>Build</p>"
    )


@pytest.mark.asyncio
async def test_go_successfactors_partial_output_still_fails(tmp_path):
    runtime = GoSuccessFactorsRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            [
                {"type": "job", "job": {"url": "https://jobs.example.com/job/1"}},
                {**summary(jobs=1), "error": "truncated XML"},
            ],
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(RuntimeError, match="truncated XML"):
        async for _ in runtime.stream(BOARD_URL, "rss", CONFIG, None):
            pass


@pytest.mark.parametrize("history", [None, [], [0], [501, 600, 700]])
@pytest.mark.parametrize("variant", [None, "feed"])
def test_default_go_without_pilot_history_quota(monkeypatch, history, variant):
    monkeypatch.delenv("SUCCESSFACTORS_RSS_GO_PERCENT", raising=False)
    config = {**CONFIG, "recent_discovered_counts": history, "variant": variant}
    assert percentage_selected(BOARD_ID, BOARD_URL, config)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID, None, monitor_type="rss", board_url=BOARD_URL, monitor_config=config
        ).implementation
        == "go-successfactors-rss"
    )


@pytest.mark.parametrize("percent", ["0", "-1", "101", "garbage", "01"])
def test_percentage_reversal(monkeypatch, percent):
    monkeypatch.setenv("SUCCESSFACTORS_RSS_GO_PERCENT", percent)
    monkeypatch.delenv("SUCCESSFACTORS_RSS_GO_BOARD_IDS", raising=False)
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID, None, monitor_type="rss", board_url=BOARD_URL, monitor_config=CONFIG
        ).implementation
        == "python"
    )
    monkeypatch.setenv("SUCCESSFACTORS_RSS_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-successfactors-rss"


@pytest.mark.parametrize(
    "feed",
    [
        "https://other.test/googlefeed.xml",
        "https://jobs.example.com/services/rss/category/?catid=2842101",
    ],
)
def test_explicit_configured_feed_request_preserved(feed):
    assert _eligible(BOARD_URL, "rss", {**CONFIG, "feed_url": feed}, None) == feed


@pytest.mark.parametrize(
    "extra",
    [
        {"variant": "legacy_xml"},
        {"variant": "rmk"},
        {"variant": "legacy"},
        {"detail_fields": {"company": "customfield1"}},
        {"fetch_company": True},
        {"resolve_job_invite_identity": True},
        {"proxy": "browser"},
        {"render": True},
        {"feed_url": "http://jobs.example.com/googlefeed.xml"},
        {"feed_url": "https://user@jobs.example.com/googlefeed.xml"},
        {"feed_url": FEED_URL + "?locale=en"},
        {"feed_url": "https://jobs.example.com/services/rss/category/?catid=1&offset=10"},
    ],
)
def test_unmigrated_profiles_and_transport_rejected(extra):
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**CONFIG, **extra})


def test_downstream_writer_configuration_retained():
    config = {
        **CONFIG,
        "identity_migration": "postfinance-swiss-post-stable-id-v1",
        "url_filter": {"exclude": "/legacy/"},
    }
    assert _eligible(BOARD_URL, "rss", config, None) == FEED_URL


@pytest.mark.asyncio
async def test_empty_feed_success(tmp_path):
    runtime = GoSuccessFactorsRSSMonitorRuntime(
        fake_binary(tmp_path, [summary(jobs=0)]), board_id=BOARD_ID
    )
    with structlog.testing.capture_logs() as logs:
        assert [x async for x in runtime.stream(BOARD_URL, "rss", CONFIG, None)] == []
    event = next(r for r in logs if r["event"] == "go_successfactors_rss.monitor_postprocessed")
    assert event["urls"] == 0
    assert event["url_sha256"] == hashlib.sha256(b"").hexdigest()


@pytest.mark.asyncio
async def test_cancel_reaps_child_that_ignores_termination(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"
    path = tmp_path / "stuck-child"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    path.chmod(0o755)
    monkeypatch.setattr("src.runtime.successfactors_rss_go._CHILD_STOP_SECONDS", 0.05)
    runtime = GoSuccessFactorsRSSMonitorRuntime(str(path), board_id=BOARD_ID)

    async def consume():
        return [item async for item in runtime.stream(BOARD_URL, "rss", CONFIG, None)]

    task = asyncio.create_task(consume())
    async with asyncio.timeout(5):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


@pytest.mark.asyncio
async def test_large_stream_accounting_is_not_rejected_at_pilot_byte_cap(tmp_path):
    runtime = GoSuccessFactorsRSSMonitorRuntime(
        fake_binary(
            tmp_path,
            [
                {"type": "job", "job": {"url": "https://jobs.example.com/job/1"}},
                {**summary(jobs=1), "bytes": 300 << 20},
            ],
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "rss", CONFIG, None)]
    assert result[0].urls == {"https://jobs.example.com/job/1"}
