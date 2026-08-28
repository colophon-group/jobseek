"""T-Mobile US board inventory contract."""

from __future__ import annotations

import csv
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_t_mobile_uses_the_verified_workday_board() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "t-mobile"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "t-mobile-careers"
    assert row["board_url"] == "https://tmobile.wd1.myworkdayjobs.com/external"
    assert row["monitor_type"] == "workday"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "workday"
    assert row["scraper_config"] == ""
