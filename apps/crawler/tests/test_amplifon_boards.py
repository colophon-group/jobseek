"""Regression coverage for Amplifon's global and regional board inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

import src.core.monitors.inline as inline_monitor
from src.core.monitors.inline import discover
from src.shared.http_retry import fetch_text_page_with_retry

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"


def _boards() -> dict[str, dict]:
    with _BOARDS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "amplifon"]
    return {
        row["board_slug"]: {
            "board_url": row["board_url"],
            "monitor_type": row["monitor_type"],
            "metadata": json.loads(row["monitor_config"] or "{}"),
            "scraper_type": row["scraper_type"],
            "scraper_config": json.loads(row["scraper_config"] or "{}"),
        }
        for row in rows
    }


def test_amplifon_inventory_keeps_global_and_distinct_regional_sources():
    boards = _boards()

    assert set(boards) == {
        "amplifon-australia",
        "amplifon-careers",
        "amplifon-egypt",
    }
    assert boards["amplifon-careers"]["monitor_type"] == "oracle_hcm"
    assert boards["amplifon-careers"]["scraper_config"] == {"enrich": ["description"]}
    assert boards["amplifon-australia"]["metadata"]["fields"] == {
        "title": "positionTitle",
        "employment_type": "jobtype",
        "locations": "location",
        "metadata.team": "category",
    }
    egypt = boards["amplifon-egypt"]
    assert (egypt["monitor_type"], egypt["scraper_type"]) == ("inline", "skip")
    assert egypt["metadata"]["proxy"] is True
    assert egypt["metadata"]["require_zero_proof"] is True
    assert egypt["metadata"]["synthetic_identity_field"] == "location"


@pytest.mark.asyncio
async def test_egypt_inline_config_retries_transient_root_failure_and_extracts_all_roles(
    monkeypatch,
):
    board = _boards()["amplifon-egypt"]
    locations = [
        "Haram",
        "Mansoura",
        "Damietta",
        "Qalyobia",
        "Shobra",
        "Ismailia",
        "Heliopolis",
    ]
    rows = "".join(
        f"""
        <h3>Sales &amp; Fitting Specialist ( {location} )</h3>
        <p>Advise customers and fit appropriate hearing solutions in {location}.</p>
        <ul><li>Build customer relationships.</li><li>Maintain professional standards.</li></ul>
        """
        for location in locations
    )
    html = f"<html><body><h2>Available Vacancies</h2>{rows}</body></html>"
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, text=html, request=request)

    async def fetch_without_delay(client, url, **kwargs):
        return await fetch_text_page_with_retry(client, url, base_delay=0, **kwargs)

    monkeypatch.setattr(inline_monitor, "fetch_text_page_with_retry", fetch_without_delay)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(board, client)

    assert requests == 2
    assert [job.title for job in jobs] == ["Sales & Fitting Specialist"] * 7
    assert [job.locations for job in jobs] == [[location] for location in locations]
    assert all("hearing solutions" in (job.description or "") for job in jobs)
    assert len({job.url for job in jobs}) == 7
    assert all("_jid=" in job.url for job in jobs)
