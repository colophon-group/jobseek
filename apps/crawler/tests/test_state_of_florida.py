"""Registry contracts for the State of Florida company configuration."""

from __future__ import annotations

import csv
import json
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
    assert courts_config["resource_policy"] == "none"
    assert courts_config["fields"]["title"] == "position_title"
    assert courts_config["fields"]["locations"] == "job_location"

    assert (legislature["monitor_type"], legislature["scraper_type"]) == ("rss", "skip")
    assert json.loads(legislature["monitor_config"]) == {
        "preset": "governmentjobs",
        "agency": "fleg",
    }


def test_state_of_florida_stages_both_company_images() -> None:
    image_dir = DATA_DIR / "images" / "state-of-florida"
    assert (image_dir / "logo.png").stat().st_size > 0
    assert (image_dir / "icon.png").stat().st_size > 0
