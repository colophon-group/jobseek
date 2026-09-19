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
    assert uaeacc["monitor_config"] == {
        "rich_rows": {
            "row_selector": (
                '#bodyContainer > div:first-of-type > ul li:has(a[href^="/plugins/show_image.php"])'
            ),
            "link_selector": 'a[href^="/plugins/show_image.php"]',
            "default_locations": ["Forrest City, Arkansas, United States"],
            "title_replacements": {"Accou nts": "Accounts"},
            "duplicate_url_policy": "prefer_longer_title",
        },
        "url_filter": r"^https://www\.uaeacc\.edu/plugins/show_image\.php\?id=\d+$",
    }
    assert uaeacc["scraper_type"] == "pdf"
    assert uaeacc["scraper_config"] == {
        "enrich": ["description"],
        "ocr": True,
        "ocr_languages": "eng",
        "ocr_scale": 2,
        "title_source": "text",
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
                "idList": "5762d916b5e6abe35afc24f3",
            },
            {
                "name": "Unrelated Local Employer Role",
                "desc": "This card belongs to the public local-jobs list.",
                "locationName": "",
                "shortUrl": "https://trello.com/c/external/unrelated-role",
                "idList": "external-local-jobs-list",
            },
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
    assert "https://trello.com/c/external/unrelated-role" not in result.jobs_by_url


@pytest.mark.asyncio
async def test_uaeacc_monitor_deduplicates_shared_job_documents() -> None:
    board = _boards()[f"{COMPANY}-careers-uaeacc"]
    # The live page uses spans both within a word and at legitimate word boundaries.
    html = """
    <div id="bodyContainer">
      <div>
        <ul>
          <li><a href="/plugins/show_image.php?id=5312">PT RN Clinical Instructor</a></li>
          <li><a href="/plugins/show_image.php?id=5312">
            Part-Time Clinical Instructor Registered Nursing
          </a></li>
          <li><a href="/plugins/show_image.php?id=5556">
            Director of <span>Workday</span> Implementation
          </a></li>
          <li><a
            href="/plugins/show_image.php?id=5358"
          >Student Accou<span>nts Coordinator</span></a></li>
          <li><a href="/documents/employment-application.pdf">Application</a></li>
        </ul>
      </div>
    </div>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            str(board["board_url"]),
            str(board["monitor_type"]),
            dict(board["monitor_config"]),
            client,
        )

    assert result.urls == {
        "https://www.uaeacc.edu/plugins/show_image.php?id=5312",
        "https://www.uaeacc.edu/plugins/show_image.php?id=5358",
        "https://www.uaeacc.edu/plugins/show_image.php?id=5556",
    }
    duplicate = result.jobs_by_url["https://www.uaeacc.edu/plugins/show_image.php?id=5312"]
    assert duplicate.title == "Part-Time Clinical Instructor Registered Nursing"
    assert duplicate.locations == ["Forrest City, Arkansas, United States"]
    split_title = result.jobs_by_url["https://www.uaeacc.edu/plugins/show_image.php?id=5358"]
    assert split_title.title == "Student Accounts Coordinator"
    spaced_title = result.jobs_by_url["https://www.uaeacc.edu/plugins/show_image.php?id=5556"]
    assert spaced_title.title == "Director of Workday Implementation"
