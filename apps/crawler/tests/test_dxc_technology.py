"""Configuration contracts for DXC Technology and its distinct regional boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _rows(path: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / path).open(newline="") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_dxc_technology_board_inventory() -> None:
    rows = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "dxc-technology")}

    assert set(rows) == {
        "dxc-technology-careers",
        "dxc-technology-careers-advanced-solutions",
        "dxc-technology-careers-fds",
        "dxc-technology-careers-ma",
        "dxc-technology-careers-ma-linkedin",
        "dxc-technology-careers-vn-linkedin",
    }
    careers = rows["dxc-technology-careers"]
    assert (careers["monitor_type"], careers["scraper_type"]) == (
        "workday",
        "workday",
    )

    fds = rows["dxc-technology-careers-fds"]
    assert (fds["monitor_type"], fds["scraper_type"]) == ("linkedin", "linkedin")
    assert json.loads(fds["monitor_config"]) == {
        "company_ids": [
            "18160437",
            "20533386",
            "18114432",
            "11205564",
            "11214645",
            "11183353",
        ],
        "canonical_numeric_job_urls": True,
    }


def test_dxc_technology_metadata_and_descriptions() -> None:
    company = _rows("companies.csv", "slug", "dxc-technology")[0]
    assert company["name"] == "DXC Technology"
    assert company["website"] == "https://dxc.com"
    assert company["logo_type"] == "wordmark"
    assert company["industry"] == "1"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "2017"

    description = _rows("company_descriptions.csv", "slug", "dxc-technology")[0]
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
