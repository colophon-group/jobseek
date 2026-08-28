"""BrightSpring Health Services board inventory contract."""

from __future__ import annotations

import csv
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_brightspring_health_services_uses_the_verified_icims_board() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "brightspring-health-services"
        ]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "brightspring-health-services-careers"
    assert row["board_url"] == "https://careers-brightspring.icims.com/"
    assert row["monitor_type"] == "icims"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "json-ld"
    assert row["scraper_config"] == ""
