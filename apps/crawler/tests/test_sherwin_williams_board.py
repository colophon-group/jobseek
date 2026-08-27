"""Sherwin-Williams Oracle HCM board contract."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_sherwin_williams_uses_the_verified_global_oracle_tenant() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "sherwin-williams"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "sherwin-williams-careers"
    assert row["board_url"] == (
        "https://ejhp.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2"
    )
    assert row["monitor_type"] == "oracle_hcm"
    assert row["monitor_config"] == ""
    assert row["scraper_type"] == "oracle_hcm"
    assert json.loads(row["scraper_config"]) == {"enrich": ["description"]}
