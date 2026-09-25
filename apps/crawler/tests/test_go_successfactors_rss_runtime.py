"""Go SuccessFactors routing keeps rich output, filters, and failure semantics."""

from __future__ import annotations

import httpx
import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.successfactors_rss_go import (
    GoSuccessFactorsRSSMonitorRuntime,
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
                summary(jobs=2),
            ],
        ),
        board_id=BOARD_ID,
    )
    async with httpx.AsyncClient() as client:
        result = [item async for item in runtime.stream(BOARD_URL, "rss", config, client)]
    assert len(result) == 1
    assert result[0].urls == {"https://jobs.example.com/job/1"}
    assert result[0].security_filtered_count == 1
    assert result[0].jobs_by_url["https://jobs.example.com/job/1"].description == "<p>Build</p>"


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


def test_go_successfactors_default_dark_and_config_guard(monkeypatch):
    monkeypatch.delenv("SUCCESSFACTORS_RSS_GO_PERCENT", raising=False)
    monkeypatch.delenv("SUCCESSFACTORS_RSS_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("SUCCESSFACTORS_RSS_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-successfactors-rss"
    monkeypatch.delenv("SUCCESSFACTORS_RSS_GO_BOARD_IDS")
    selected = {**CONFIG, "recent_discovered_counts": [4, 4, 4]}
    monkeypatch.setenv("SUCCESSFACTORS_RSS_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, BOARD_URL, selected)
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**selected, "variant": "legacy_xml"})
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**selected, "detail_fields": ["title"]})
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**selected, "feed_url": "https://other.test/googlefeed.xml"}
    )
