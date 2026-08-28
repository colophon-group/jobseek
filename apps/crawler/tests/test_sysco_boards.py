"""Board contracts for Sysco's global and European inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_sysco_uses_three_complementary_provider_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "sysco"]

    assert [row["board_slug"] for row in rows] == [
        "sysco-careers",
        "sysco-gb",
        "sysco-menigo-sweden",
    ]

    primary, great_britain, menigo = rows
    assert (primary["monitor_type"], primary["scraper_type"]) == (
        "workday",
        "workday",
    )
    assert json.loads(great_britain["monitor_config"]) == {"token": "SyscoGB"}
    assert (great_britain["monitor_type"], great_britain["scraper_type"]) == (
        "smartrecruiters",
        "smartrecruiters",
    )

    monitor = json.loads(menigo["monitor_config"])
    scraper = json.loads(menigo["scraper_config"])
    assert menigo["monitor_type"] == menigo["scraper_type"] == "dom"
    assert monitor["render"] is True
    assert monitor["url_filter"] == r"^https://menigo\.weselect\.com/p/"
    assert scraper["steps"][-1] == {
        "tag": "title",
        "field": "location",
        "regex": " - (.+)$",
        "from": 0,
    }
