from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_infinite_orbits_uses_live_jobs_subdomain() -> None:
    with _BOARDS.open(encoding="utf-8", newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "infinite-orbits"
        }

    board = rows["infinite-orbits-careers"]
    assert board["board_url"] == "https://jobs.infiniteorbits.io/jobs"
    assert board["monitor_type"] == "dom"
    assert json.loads(board["monitor_config"])["url_filter"] == "/jobs/"
