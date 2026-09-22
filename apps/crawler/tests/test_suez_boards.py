"""Metadata, board, and extraction contracts for SUEZ."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.scrapers.dom import parse_html

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _rows(filename: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_suez_metadata_descriptions_and_staged_assets_are_complete() -> None:
    company = _rows("companies.csv", "slug", "suez")[0]
    description = _rows("company_descriptions.csv", "slug", "suez")[0]

    assert company["name"] == "SUEZ"
    assert company["website"] == "https://www.suez.com"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "8"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1858"
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    assert (DATA_DIR / "images/suez/logo.png").is_file()
    assert (DATA_DIR / "images/suez/icon.png").is_file()


def test_suez_boards_cover_global_and_regional_career_surfaces() -> None:
    boards = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "suez")}

    assert set(boards) == {
        "safege-poland",
        "suez-asia",
        "suez-careers",
        "suez-china-infrastructure",
        "suez-china-operations",
        "suez-macao-water",
        "suez-uk",
    }
    assert (boards["suez-careers"]["monitor_type"], boards["suez-careers"]["scraper_type"]) == (
        "cornerstone",
        "skip",
    )
    assert boards["suez-asia"]["monitor_type"] == "sitemap"

    uk_config = json.loads(boards["suez-uk"]["monitor_config"])
    assert uk_config["render"] is True
    assert "/vacancies/" in uk_config["url_filter"]

    for slug in ("suez-china-infrastructure", "suez-china-operations"):
        monitor_config = json.loads(boards[slug]["monitor_config"])
        scraper_config = json.loads(boards[slug]["scraper_config"])
        assert monitor_config["proxy"] is True
        assert scraper_config["proxy"] is True
        assert "safe.liepin.com" not in monitor_config["url_filter"]


def test_suez_uk_detail_config_extracts_complete_role() -> None:
    board = _rows("boards.csv", "board_slug", "suez-uk")[0]
    config = json.loads(board["scraper_config"])
    html = """
    <main>
      <h2 class="vacancy_title">EC&amp;I Maintenance Technician</h2>
      <dl>
        <dt class="field_contract_type">Contract type</dt>
        <dd class="value_contract_type">Permanent</dd>
        <dt class="field_working_pattern">Working Pattern</dt>
        <dd class="value_working_pattern">Full time</dd>
        <dt class="field_location_based">Location based</dt>
        <dd class="value_location_based">Bristol</dd>
        <dt class="field_summary_of_vacancy">Summary of vacancy</dt>
        <dd class="value_summary_of_vacancy">
          <p>Maintain control and instrumentation systems at an energy recovery centre.</p>
        </dd>
        <dt class="field_about_the_role">About the role</dt>
        <dd class="value_about_the_role">
          <p>Deliver reactive and planned maintenance safely.</p>
        </dd>
        <dt class="field_job_description">Job Description</dt>
        <dd class="value_job_description"><a href="/role.pdf">Download PDF</a></dd>
        <dt class="field_closing_date">Closing Date</dt>
        <dd class="value_closing_date">03/10/2026</dd>
      </dl>
    </main>
    """

    result = parse_html(html, config)

    assert result.title == "EC&I Maintenance Technician"
    assert result.locations == ["Bristol"]
    assert result.employment_type == "Permanent"
    assert result.metadata == {"working_pattern": "Full time"}
    assert result.extras == {"valid_through": "2026-10-03"}
    assert result.description is not None
    assert "reactive and planned maintenance" in result.description
    assert "Download PDF" not in result.description


def test_suez_liepin_detail_config_uses_verified_document_metadata() -> None:
    board = _rows("boards.csv", "board_slug", "suez-china-infrastructure")[0]
    config = json.loads(board["scraper_config"])
    html = (
        "<html><head>"
        "<title>【上海 工程咨询实习生招聘】-苏伊士招聘信息-猎聘</title>"
        '<meta name="description" content="为水务基础设施项目提供工程咨询支持。">'
        "</head><body><script>"
        'var $CONFIG = {"jobId": 77394783, "jobKind": "6"};'
        "</script></body></html>"
    )

    result = parse_html(html, config)

    assert result.title == "工程咨询实习生"
    assert result.locations == ["上海"]
    assert result.description == "为水务基础设施项目提供工程咨询支持。"
