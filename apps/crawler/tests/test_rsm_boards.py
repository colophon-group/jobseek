from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _boards() -> dict[str, dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(newline="") as file:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(file)
            if row["company_slug"] == "rsm"
        }


def test_rsm_regional_boards_replace_duplicate_us_source():
    boards = _boards()

    assert len(boards) == 27
    assert "rsm-us" not in boards
    assert {
        "rsm-careers",
        "rsm-australia",
        "rsm-france",
        "rsm-netherlands",
        "rsm-spain",
        "rsm-uk",
    } <= boards.keys()


def test_rsm_dom_boards_always_have_a_scraper():
    boards = _boards()

    missing = [
        slug
        for slug, row in boards.items()
        if row["monitor_type"] == "dom" and not row["scraper_type"]
    ]

    assert missing == []


def test_rsm_netherlands_keeps_complete_pagination_and_jsonld_gate():
    row = _boards()["rsm-netherlands"]
    monitor = json.loads(row["monitor_config"])

    assert monitor["actions"] == [
        {
            "action": "paginate_collect",
            "next_selector": ".pagination li:last-child:not(.disabled):not(.active) a",
            "max_pages": 20,
            "wait_ms": 2500,
        }
    ]
    assert monitor["require_jsonld_jobposting"] is True
    assert row["scraper_type"] == "json-ld"


def test_rsm_spain_excludes_the_general_talent_pool():
    monitor = json.loads(_boards()["rsm-spain"]["monitor_config"])

    assert monitor["url_filter"]["exclude"] == "/jobs/candidatura-espontanea"
