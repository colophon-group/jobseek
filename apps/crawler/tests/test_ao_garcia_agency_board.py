"""AO Garcia Agency board inventory contract."""

from __future__ import annotations

import csv
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_ao_garcia_agency_uses_the_verified_lever_board() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "ao-garcia-agency"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "ao-garcia-agency-careers"
    assert row["board_url"] == "https://jobs.lever.co/aogarciaagency"
    assert row["monitor_type"] == "lever"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "skip"
    assert row["scraper_config"] == ""
