from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.scrapers.dom import parse_html
from src.shared.csv_io import read_csv

BOARDS_CSV = Path(__file__).resolve().parents[1] / "data" / "boards.csv"


def _faculty_scraper_config() -> dict:
    _, rows = read_csv(BOARDS_CSV)
    row = next(item for item in rows if item["board_slug"] == "wustl-faculty")
    return json.loads(row["scraper_config"])


def _detail_html(title: str) -> str:
    return f"""
    <article>
      <h3>Title</h3><p>{title}</p>
      <h3>Position Description</h3>
      <p>Lead patient care, teaching, and research for WashU Medicine.</p>
      <h3>Basic Qualifications</h3><p>Board certification is required.</p>
      <h3>Posting Date</h3><p>4/1/2026</p>
      <h3>End Date</h3><p>No end date</p>
      <a href="/myapp/1011200">Apply</a>
    </article>
    """


@pytest.mark.parametrize(
    ("title", "locations", "location_type"),
    [
        (
            "Diagnostic Radiologist - Remote",
            ["United States"],
            "remote",
        ),
        (
            "Subspecialty Radiologist - Hybrid of Remote and Onsite Coverage",
            ["Missouri, United States"],
            "hybrid",
        ),
        (
            "Clinical Faculty Opportunity in Springfield, Missouri",
            ["Springfield, Missouri, United States"],
            None,
        ),
        (
            "Diagnostic Radiologist - Onsite - Phelps and Sullivan",
            ["Rolla, Missouri, United States", "Sullivan, Missouri, United States"],
            None,
        ),
        (
            "Assistant Professor of Medicine",
            ["Missouri, United States"],
            None,
        ),
    ],
)
def test_wustl_faculty_config_extracts_complete_jobs(
    title: str,
    locations: list[str],
    location_type: str | None,
) -> None:
    result = parse_html(_detail_html(title), _faculty_scraper_config())

    assert result.title == title
    assert result.description is not None
    assert "Lead patient care" in result.description
    assert "Board certification" in result.description
    assert result.locations == locations
    assert result.job_location_type == location_type
    assert result.date_posted == "2026-04-01"


def test_wustl_has_three_distinct_official_boards() -> None:
    _, rows = read_csv(BOARDS_CSV)
    rows = [row for row in rows if row["company_slug"] == "wustl"]

    assert {row["board_slug"] for row in rows} == {
        "wustl-careers",
        "wustl-faculty",
        "wustl-interfolio",
    }
    assert next(row for row in rows if row["board_slug"] == "wustl-interfolio")[
        "scraper_type"
    ] == "skip"
