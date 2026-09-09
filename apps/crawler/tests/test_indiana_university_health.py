"""Regression coverage for Indiana University Health's two job inventories."""

from __future__ import annotations

import csv
import json

from src.shared.constants import DATA_DIR


def _rows(filename: str, slug_field: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [
            row for row in csv.DictReader(handle) if row[slug_field] == "indiana-university-health"
        ]


def test_company_metadata_and_uploaded_assets_are_complete() -> None:
    company = _rows("companies.csv", "slug")
    descriptions = _rows("company_descriptions.csv", "slug")

    assert len(company) == 1
    assert company[0]["name"] == "Indiana University Health"
    assert company[0]["industry"] == "3"
    assert company[0]["employee_count_range"] == "8"
    assert company[0]["founded_year"] == "1997"
    assert len(descriptions) == 1
    assert all(descriptions[0][locale] for locale in ("en", "de", "fr", "it"))
    assert company[0]["logo_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/indiana-university-health/logo-"
    )
    assert company[0]["logo_url"].endswith(".jpg")
    assert company[0]["icon_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/indiana-university-health/icon-"
    )
    assert company[0]["icon_url"].endswith(".webp")


def test_general_and_physician_inventories_are_distinct_and_complete() -> None:
    rows = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug")}

    assert set(rows) == {
        "indiana-university-health-careers",
        "indiana-university-health-physicians",
    }
    assert rows["indiana-university-health-careers"]["monitor_type"] == "oracle_hcm"
    assert rows["indiana-university-health-careers"]["scraper_type"] == "oracle_hcm"

    physicians = rows["indiana-university-health-physicians"]
    config = json.loads(physicians["monitor_config"])
    assert physicians["monitor_type"] == "dom"
    assert physicians["scraper_type"] == "skip"
    assert config["render"] is True
    assert config["resource_policy"] == "none"
    assert config["pagination"] == {
        "param_name": "pg",
        "max_pages": 1_000,
        "browser": True,
    }
    assert config["rich_rows"]["row_selector"] == "#accordion .panel.panel-default"
    assert config["rich_rows"]["description_selector"] == ".jobDropDownDesc"
