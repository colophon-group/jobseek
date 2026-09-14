"""ZEISS Group board, metadata, and staged-asset contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _rows(path: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / path).open(newline="") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_zeiss_group_keeps_global_and_dorc_sources() -> None:
    rows = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "zeiss-group")}

    assert set(rows) == {"zeiss-group-careers", "zeiss-group-dorc"}
    global_board = rows["zeiss-group-careers"]
    assert (global_board["monitor_type"], global_board["scraper_type"]) == (
        "workday",
        "workday",
    )
    assert json.loads(global_board["monitor_config"]) == {
        "company": "zeissgroup",
        "wd_instance": "wd3",
        "site": "external",
    }

    dorc = rows["zeiss-group-dorc"]
    assert (dorc["monitor_type"], dorc["scraper_type"]) == ("dom", "json-ld")
    assert json.loads(dorc["monitor_config"]) == {
        "url_filter": (
            r"(?i)^https://www\.workingatdorc\.com/vacancies/"
            r"[^/?#]*\d[^/?#]*/?(?:[?#].*)?$"
        )
    }


def test_zeiss_group_metadata_descriptions_and_assets() -> None:
    company = _rows("companies.csv", "slug", "zeiss-group")[0]
    assert company["name"] == "ZEISS Group"
    assert company["industry"] == "4"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1846"
    assert company["logo_type"] == "wordmark+icon"
    assert json.loads(company["extras"])["legalName"] == "Carl Zeiss AG"

    descriptions = _rows("company_descriptions.csv", "slug", "zeiss-group")[0]
    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))

    asset_root = "https://jobseek-assets.colophon-group.org/companies/zeiss-group/"
    assert company["logo_url"].startswith(f"{asset_root}logo-")
    assert company["logo_url"].endswith(".jpg")
    assert company["icon_url"].startswith(f"{asset_root}icon-")
    assert company["icon_url"].endswith(".webp")
