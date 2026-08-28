"""Learning Care Group board inventory contract."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_learning_care_group_uses_the_verified_oracle_board() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(handle) if row["company_slug"] == "learning-care-group"
        ]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "learning-care-group-careers"
    assert row["board_url"] == (
        "https://ejql.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"
    )
    assert row["monitor_type"] == "oracle_hcm"
    assert json.loads(row["monitor_config"]) == {
        "host": "ejql.fa.us6.oraclecloud.com",
        "site": "CX",
        "offset_overlap": 5,
        "total_count_tolerance": 1,
    }
    assert row["scraper_type"] == "oracle_hcm"
    assert json.loads(row["scraper_config"]) == {"enrich": ["description"]}
