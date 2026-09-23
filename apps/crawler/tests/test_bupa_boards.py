from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.api_sniffer import discover

BOARDS_CSV = Path(__file__).resolve().parents[1] / "data" / "boards.csv"


def _saudi_config() -> dict:
    with BOARDS_CSV.open(encoding="utf-8", newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "bupa-saudi-arabia"
        )
    return json.loads(row["monitor_config"])


@pytest.mark.asyncio
async def test_saudi_value_template_pagination_fails_closed_on_duplicate_and_total_drift():
    config = _saudi_config()
    assert config["pagination_convergence"] == {
        "max_passes": 6,
        "required_no_growth_passes": 2,
        "identity_by": ["url"],
        "stable_fields": ["title", "url"],
    }
    config["api_url"] = "https://example.com/jobs"
    requested_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["query"]
        requested_pages.append(page)
        if page == "page=1":
            payload = {
                "totalJobs": 3,
                "jobs": [
                    {"title": "A", "url": "https://example.com/a"},
                    {"title": "B", "url": "https://example.com/b"},
                ],
            }
        else:
            assert page == "page=2"
            payload = {
                "totalJobs": 2,
                "jobs": [{"title": "B", "url": "https://example.com/b"}],
            }
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": "https://example.com/careers", "metadata": config},
            client,
        )

    assert requested_pages == ["page=1", "page=2"]
    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert result.urls == {"https://example.com/a", "https://example.com/b"}
