"""PICC board inventory contract."""

from __future__ import annotations

import csv
from pathlib import Path


def test_picc_uses_verified_first_party_beisen_tenant() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == "picc-careers")

    assert row["company_slug"] == "picc"
    assert row["board_url"] == "https://picc.zhiye.com/"
    assert row["monitor_type"] == "beisen"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "skip"
