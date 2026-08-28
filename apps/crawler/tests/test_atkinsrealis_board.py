"""Board contract for AtkinsRéalis' Workday inventory."""

from __future__ import annotations

import csv
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_atkinsrealis_uses_one_authoritative_workday_board() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "atkinsrealis"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "atkinsrealis-careers"
    assert row["board_url"] == "https://slihrms.wd3.myworkdayjobs.com/careers"
    assert row["monitor_type"] == row["scraper_type"] == "workday"
    assert row["monitor_config"] == row["scraper_config"] == ""
