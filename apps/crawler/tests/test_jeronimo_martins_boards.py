"""Jerónimo Martins board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_jeronimo_martins_uses_verified_global_and_slovak_boards() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "jeronimo-martins"]

    assert [row["board_slug"] for row in rows] == [
        "jeronimo-martins-careers",
        "jeronimo-martins-careers-sk",
    ]
    global_board, slovak_board = rows

    assert global_board["board_url"] == "https://careers.jeronimomartins.com/"
    assert global_board["monitor_type"] == "rss"
    assert json.loads(global_board["monitor_config"]) == {"preset": "successfactors"}
    assert global_board["scraper_type"] == "skip"

    assert slovak_board["board_url"] == "https://pracujvbiedronke.sk/"
    assert slovak_board["monitor_type"] == "dom"
    assert json.loads(slovak_board["monitor_config"]) == {
        "link_selector": 'a[href*="biedronka.traffit.com/public/an/"]'
    }
    assert slovak_board["scraper_type"] == "dom"
    scraper_config = json.loads(slovak_board["scraper_config"])
    assert [step["field"] for step in scraper_config["steps"]] == [
        "title",
        "description",
        "location",
        "date_posted",
    ]
