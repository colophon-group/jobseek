"""Stable provider contracts for Bass Pro Shops and its conservation family."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_bass_pro_shops_board_inventory_is_exact() -> None:
    with _BOARDS.open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "bass-pro-shops"
        }

    assert set(rows) == {
        "bass-pro-shops-careers",
        "bass-pro-shops-dogwood-canyon",
        "bass-pro-shops-top-of-the-rock",
        "bass-pro-shops-wonders-of-wildlife",
    }

    primary = rows["bass-pro-shops-careers"]
    assert primary["board_url"] == "https://basspro.wd1.myworkdayjobs.com/careers"
    assert (primary["monitor_type"], primary["scraper_type"]) == (
        "workday",
        "workday",
    )

    for slug in {
        "bass-pro-shops-dogwood-canyon",
        "bass-pro-shops-top-of-the-rock",
        "bass-pro-shops-wonders-of-wildlife",
    }:
        row = rows[slug]
        assert row["monitor_type"] == "dom"
        monitor = json.loads(row["monitor_config"])
        assert monitor["render"] is True
        assert monitor["wait"] == "networkidle"
        assert "applicantpro.com/jobs/" in row["board_url"]

    assert rows["bass-pro-shops-dogwood-canyon"]["scraper_type"] == "json-ld"
    assert rows["bass-pro-shops-top-of-the-rock"]["scraper_type"] == "api_sniffer"
    assert rows["bass-pro-shops-wonders-of-wildlife"]["scraper_type"] == ("api_sniffer")
