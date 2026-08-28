"""Stable provider contract for Brookdale Senior Living."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def test_brookdale_senior_living_board_inventory_is_exact() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "brookdale-senior-living"
        ]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "brookdale-senior-living-careers"
    assert row["board_url"] == (
        "https://ibmwjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
    )
    assert (row["monitor_type"], row["scraper_type"]) == (
        "oracle_hcm",
        "oracle_hcm",
    )
    assert json.loads(row["scraper_config"]) == {"enrich": ["description"]}
