"""Travel + Leisure Co. board, metadata, and staged-asset contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _rows(path: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / path).open(newline="") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_travel_leisure_co_keeps_global_and_rci_mexico_sources() -> None:
    rows = {
        row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "travel-leisure-co")
    }

    assert set(rows) == {
        "travel-leisure-co-careers",
        "travel-leisure-co-rci-mexico",
    }
    global_board = rows["travel-leisure-co-careers"]
    assert (global_board["monitor_type"], global_board["scraper_type"]) == (
        "workday",
        "workday",
    )
    assert json.loads(global_board["monitor_config"]) == {
        "company": "wynd",
        "wd_instance": "wd5",
        "site": "external",
    }

    mexico = rows["travel-leisure-co-rci-mexico"]
    assert (mexico["monitor_type"], mexico["scraper_type"]) == ("dom", "dom")
    monitor_config = json.loads(mexico["monitor_config"])
    assert monitor_config["title_matched_url_scan"] == {
        "listing_title_selector": "#vacantes .vertical-links a[aria-label]",
        "detail_title_selector": "h4",
        "url_template": "https://www.rci.com/pre-rci/mx/es/landing/careers/vacante-{index}",
        "start": 1,
        "max_scan": 20,
    }


def test_travel_leisure_co_metadata_descriptions_and_assets() -> None:
    company = _rows("companies.csv", "slug", "travel-leisure-co")[0]
    assert company["name"] == "Travel + Leisure Co."
    assert company["industry"] == "17"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "2003"
    assert company["logo_type"] == "wordmark"

    descriptions = _rows("company_descriptions.csv", "slug", "travel-leisure-co")[0]
    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))

    asset_root = DATA_DIR / "images" / "travel-leisure-co"
    assert (asset_root / "logo.png").stat().st_size > 10_000
    assert (asset_root / "icon.svg").stat().st_size > 10_000
