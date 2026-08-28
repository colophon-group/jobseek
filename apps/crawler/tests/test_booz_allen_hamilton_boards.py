"""Stable inventory contracts for Booz Allen Hamilton job boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"


def test_booz_allen_hamilton_board_inventory_is_exact():
    with _BOARDS_PATH.open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "booz-allen-hamilton"
        }

    assert set(rows) == {
        "booz-allen-hamilton-careers",
        "booz-allen-hamilton-defy-security",
        "booz-allen-hamilton-everwatch",
    }
    assert rows["booz-allen-hamilton-careers"] == {
        "company_slug": "booz-allen-hamilton",
        "board_slug": "booz-allen-hamilton-careers",
        "board_url": "https://bah.wd1.myworkdayjobs.com/bah_jobs",
        "monitor_type": "workday",
        "monitor_config": "",
        "scraper_type": "workday",
        "scraper_config": "",
    }
    assert rows["booz-allen-hamilton-defy-security"] == {
        "company_slug": "booz-allen-hamilton",
        "board_slug": "booz-allen-hamilton-defy-security",
        "board_url": "https://jobs.ashbyhq.com/defy-security",
        "monitor_type": "ashby",
        "monitor_config": json.dumps({"token": "defy-security"}),
        "scraper_type": "skip",
        "scraper_config": "",
    }
    assert rows["booz-allen-hamilton-everwatch"] == {
        "company_slug": "booz-allen-hamilton",
        "board_slug": "booz-allen-hamilton-everwatch",
        "board_url": "https://everwatch-everwatchsolutions.icims.com/jobs/intro",
        "monitor_type": "icims",
        "monitor_config": json.dumps({"host": "everwatch-everwatchsolutions.icims.com"}),
        "scraper_type": "json-ld",
        "scraper_config": "",
    }
