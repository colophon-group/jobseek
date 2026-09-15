"""Stable provider contract for XOCEAN's public LinkedIn inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_xocean_uses_provider_native_linkedin_adapter() -> None:
    with _BOARDS.open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["board_slug"] == "xocean-linkedin"]

    assert len(rows) == 1
    board = rows[0]
    assert board["company_slug"] == "xocean"
    assert board["board_url"].startswith("https://www.linkedin.com/jobs/")
    assert (board["monitor_type"], board["scraper_type"]) == (
        "linkedin",
        "linkedin",
    )
    assert json.loads(board["monitor_config"]) == {"company_id": "18506409"}
    assert json.loads(board["scraper_config"]) == {
        "enrich": ["description", "employment_type", "job_location_type"]
    }
