"""Registry contracts for the State of Florida company configuration."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_state_of_florida_keeps_complementary_official_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "state-of-florida"]

    assert [row["board_slug"] for row in rows] == [
        "state-of-florida-careers",
        "state-of-florida-courts",
        "state-of-florida-legislature",
    ]
    careers, courts, legislature = rows

    assert (careers["monitor_type"], careers["scraper_type"]) == ("rss", "skip")
    assert json.loads(careers["monitor_config"]) == {"preset": "successfactors"}

    courts_config = json.loads(courts["monitor_config"])
    assert (courts["monitor_type"], courts["scraper_type"]) == ("nextdata", "skip")
    assert courts_config["source"] == "browser"
    assert courts_config["path"] == "items"
    assert "resource_policy" not in courts_config
    assert courts_config["fields"]["title"] == "position_title"
    assert courts_config["fields"]["locations"] == "job_location"

    assert (legislature["monitor_type"], legislature["scraper_type"]) == ("rss", "skip")
    assert json.loads(legislature["monitor_config"]) == {
        "preset": "governmentjobs",
        "agency": "fleg",
    }


def test_state_of_florida_references_uploaded_company_images() -> None:
    with (DATA_DIR / "companies.csv").open(newline="", encoding="utf-8") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "state-of-florida")

    asset_root = "https://jobseek-assets.colophon-group.org/companies/state-of-florida"
    assert re.fullmatch(rf"{asset_root}/logo-[0-9a-f]{{64}}\.png", company["logo_url"])
    assert re.fullmatch(rf"{asset_root}/icon-[0-9a-f]{{64}}\.webp", company["icon_url"])
