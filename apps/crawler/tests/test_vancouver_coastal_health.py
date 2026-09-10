"""Reviewed metadata and board contracts for Vancouver Coastal Health."""

from __future__ import annotations

import csv
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors.dom import dom_discover
from src.shared.constants import DATA_DIR

_COMPANY = "vancouver-coastal-health"
_VCHRI_URL = "https://www.vchri.ca/careers"


def _rows(filename: str, slug_field: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[slug_field] == _COMPANY]


def _boards() -> dict[str, dict[str, str]]:
    return {row["board_slug"]: row for row in _rows("boards.csv", "company_slug")}


def test_metadata_descriptions_and_assets_are_complete() -> None:
    company = _rows("companies.csv", "slug")
    descriptions = _rows("company_descriptions.csv", "slug")

    assert len(company) == 1
    assert company[0]["name"] == "Vancouver Coastal Health"
    assert company[0]["website"] == "https://www.vch.ca/"
    assert company[0]["industry"] == "3"
    assert company[0]["employee_count_range"] == "8"
    assert company[0]["founded_year"] == "2001"
    assert company[0]["logo_type"] == "wordmark+icon"
    assert "/companies/vancouver-coastal-health/logo" in company[0]["logo_url"]
    assert "/companies/vancouver-coastal-health/icon" in company[0]["icon_url"]
    assert len(descriptions) == 1
    assert all(descriptions[0][locale] for locale in ("en", "de", "fr", "it"))


def test_general_and_research_institute_boards_are_complete() -> None:
    boards = _boards()

    assert set(boards) == {
        "vancouver-coastal-health-careers",
        "vancouver-coastal-health-research-institute",
    }
    general = boards["vancouver-coastal-health-careers"]
    assert (general["monitor_type"], general["scraper_type"]) == ("icims", "json-ld")

    research = boards["vancouver-coastal-health-research-institute"]
    config = json.loads(research["monitor_config"])
    assert (research["monitor_type"], research["scraper_type"]) == ("dom", "skip")
    assert config["rich_rows"]["description_next_selector"] == "p"
    assert config["rich_rows"]["default_locations"] == ["British Columbia, CA"]


async def test_research_institute_extracts_adjacent_current_opportunities() -> None:
    config = json.loads(_boards()["vancouver-coastal-health-research-institute"]["monitor_config"])
    html = """
    <section class="field--name-body">
      <div class="container--field">
        <h3>Current opportunity</h3>
        <p><a href="https://www.med.ubc.ca/careers/director-one/">Director One</a></p>
        <p>Lead the first provincial research programme.</p>
        <p><a href="https://www.med.ubc.ca/careers/director-two/">Director Two</a></p>
        <p>Lead the second provincial research programme.</p>
      </div>
    </section>
    """

    with patch(
        "src.shared.http_retry.fetch_with_retry",
        AsyncMock(return_value=html),
    ):
        jobs = await dom_discover(
            {"board_url": _VCHRI_URL, "metadata": config},
            AsyncMock(),
        )

    assert [(job.title, job.locations) for job in jobs] == [
        ("Director One", ["British Columbia, CA"]),
        ("Director Two", ["British Columbia, CA"]),
    ]
    assert [job.description for job in jobs] == [
        "<p>Lead the first provincial research programme.</p>",
        "<p>Lead the second provincial research programme.</p>",
    ]


async def test_research_institute_zero_fails_closed_without_explicit_empty_evidence() -> None:
    config = json.loads(_boards()["vancouver-coastal-health-research-institute"]["monitor_config"])
    html = """
    <section class="field--name-body">
      <div class="container--field"><h3>Current opportunity</h3></div>
    </section>
    """

    with (
        patch(
            "src.shared.http_retry.fetch_with_retry",
            AsyncMock(return_value=html),
        ),
        pytest.raises(ValueError, match="matched no listing rows"),
    ):
        await dom_discover(
            {"board_url": _VCHRI_URL, "metadata": config},
            AsyncMock(),
        )
