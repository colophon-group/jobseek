"""STRABAG board inventory contract."""

from __future__ import annotations

import csv
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_strabag_uses_the_verified_cornerstone_board() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "strabag"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "strabag-careers"
    assert row["board_url"] == ("https://strabag.csod.com/ux/ats/careersite/2/home?c=strabag")
    assert row["monitor_type"] == "cornerstone"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "skip"
    assert row["scraper_config"] == ""
