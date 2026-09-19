"""Configuration contracts for the University of Arkansas System boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import monitor_one

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COMPANY = "university-of-arkansas-system"


def _rows(filename: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def _boards() -> dict[str, dict[str, object]]:
    return {
        row["board_slug"]: {
            **row,
            "monitor_config": json.loads(row["monitor_config"] or "{}"),
            "scraper_config": json.loads(row["scraper_config"] or "{}"),
        }
        for row in _rows("boards.csv", "company_slug", COMPANY)
    }


def test_metadata_assets_and_board_inventory_are_complete() -> None:
    company = _rows("companies.csv", "slug", COMPANY)[0]
    description = _rows("company_descriptions.csv", "slug", COMPANY)[0]
    boards = _boards()

    assert company["name"] == "University of Arkansas System"
    assert company["industry"] == "10"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1871"
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    assert (DATA_DIR / "images" / COMPANY / "logo.jpg").is_file()
    assert (DATA_DIR / "images" / COMPANY / "icon.jpg").is_file()
    assert set(boards) == {
        f"{COMPANY}-careers",
        f"{COMPANY}-careers-northark",
        f"{COMPANY}-careers-uaeacc",
        f"{COMPANY}-careers-wri",
    }


def test_provider_configs_preserve_verified_runtime_contracts() -> None:
    boards = _boards()

    central = boards[f"{COMPANY}-careers"]
    assert central["monitor_type"] == "workday"
    assert central["monitor_config"] == {
        "company": "uasys",
        "wd_instance": "wd5",
        "site": "uasys",
    }

    uaeacc = boards[f"{COMPANY}-careers-uaeacc"]
    assert uaeacc["monitor_type"] == "dom"
    assert uaeacc["scraper_type"] == "pdf"
    assert uaeacc["scraper_config"] == {
        "ocr": True,
        "ocr_languages": "eng",
        "ocr_scale": 2,
        "title_source": "text",
        "defaults": {"locations": ["Forrest City, Arkansas, United States"]},
    }

    wri = boards[f"{COMPANY}-careers-wri"]
    assert wri["monitor_type"] == "paylocity"
    assert wri["monitor_config"] == {"proxy": True}
    assert wri["scraper_config"]["proxy"] is True


@pytest.mark.asyncio
async def test_northark_api_returns_rich_jobs_with_statewide_location_default() -> None:
    board = _boards()[f"{COMPANY}-careers-northark"]
    payload = {
        "cards": [
            {
                "name": "Director of Student Services",
                "desc": "EMPLOYMENT TYPE: Full-time\n\nLead the student services team.",
                "locationName": "",
                "shortUrl": "https://trello.com/c/abc123/director-of-student-services",
            }
        ]
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )

    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            str(board["board_url"]),
            str(board["monitor_type"]),
            dict(board["monitor_config"]),
            client,
        )

    job = result.jobs_by_url["https://trello.com/c/abc123/director-of-student-services"]
    assert job.title == "Director of Student Services"
    assert job.description == "EMPLOYMENT TYPE: Full-time\n\nLead the student services team."
    assert job.locations == ["Arkansas, United States"]


@pytest.mark.asyncio
async def test_uaeacc_monitor_deduplicates_shared_job_documents() -> None:
    board = _boards()[f"{COMPANY}-careers-uaeacc"]
    html = """
    <div id="bodyContainer">
      <div>
        <ul>
          <li><a href="/plugins/show_image.php?id=5312">Nursing Faculty</a></li>
          <li><a href="/plugins/show_image.php?id=5312">Nursing Instructor</a></li>
          <li><a href="/plugins/show_image.php?id=5556">Workday Director</a></li>
          <li><a href="/documents/employment-application.pdf">Application</a></li>
        </ul>
      </div>
    </div>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, request=request)
    )

    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            str(board["board_url"]),
            str(board["monitor_type"]),
            dict(board["monitor_config"]),
            client,
        )

    assert result.urls == {
        "https://www.uaeacc.edu/plugins/show_image.php?id=5312",
        "https://www.uaeacc.edu/plugins/show_image.php?id=5556",
    }
