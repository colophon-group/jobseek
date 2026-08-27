"""Humana board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

from src.core.monitor import monitor_one

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def _row(slug: str) -> dict[str, str]:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row["board_slug"] == slug)


def test_humana_contractors_uses_complete_search_inventory() -> None:
    row = _row("humana-contractors")
    config = json.loads(row["monitor_config"])

    assert config["api_url"].endswith("/api/community/jobs/search")
    assert "/featured" not in config["api_url"]
    assert config["total_path"] == "meta.totalCount"
    assert config["params"] == {"limit": "100"}
    assert config["pagination"] == {
        "param_name": "page",
        "style": "page",
        "start_value": 1,
        "increment": 1,
        "location": "query",
    }
    assert config["request_headers"] == {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
        "x-spa-type": "community",
        "x-tenant": "humana",
    }
    assert "browser" not in config


async def test_humana_contractors_config_extracts_every_advertised_job() -> None:
    row = _row("humana-contractors")
    config = json.loads(row["monitor_config"])
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        jobs = [
            {
                "id": "197884d2-fd4d-4da3-b2b1-59ea38195070",
                "title": {"name": "Occupational Therapist"},
                "description": "<p>Role one</p>",
                "type": "contract",
                "isRemote": False,
                "location": {"city": "Tucson"},
            },
            {
                "id": "8a87b8fd-0bf1-4076-b89f-796a522ba3b0",
                "title": {"name": "Market Research Professional"},
                "description": "<p>Role two</p>",
                "type": "contract",
                "isRemote": True,
                "location": {"city": "Remote"},
            },
        ]
        return httpx.Response(
            200,
            json={"data": jobs, "meta": {"pageCount": 1, "totalCount": len(jobs)}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await monitor_one(
            row["board_url"],
            row["monitor_type"],
            config,
            client,
        )

    assert len(result.urls) == 2
    assert result.truncated is False
    assert result.jobs_by_url is not None
    assert {job.title for job in result.jobs_by_url.values()} == {
        "Occupational Therapist",
        "Market Research Professional",
    }
    assert seen_request is not None
    assert seen_request.url.path.endswith("/api/community/jobs/search")
    assert seen_request.url.params["limit"] == "100"
    assert seen_request.headers["x-spa-type"] == "community"
    assert seen_request.headers["x-tenant"] == "humana"


def test_humana_primary_board_is_the_authoritative_phenom_source() -> None:
    row = _row("humana-careers")

    assert row["board_url"] == "https://careers.humana.com/"
    assert row["monitor_type"] == "phenom"
    assert row["scraper_type"] == "json-ld"
