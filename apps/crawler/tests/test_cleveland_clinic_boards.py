"""Stable provider contracts for Cleveland Clinic job boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_cleveland_clinic_board_inventory_is_exact() -> None:
    with _BOARDS.open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "cleveland-clinic"
        }

    assert set(rows) == {
        "cleveland-clinic-abu-dhabi",
        "cleveland-clinic-careers",
    }
    abu_dhabi = rows["cleveland-clinic-abu-dhabi"]
    assert abu_dhabi["board_url"] == (
        "https://www.linkedin.com/company/cleveland-clinic-abu-dhabi/jobs/"
    )
    assert json.loads(abu_dhabi["monitor_config"]) == {
        "company_id": "1201599",
        "company_slug": "cleveland-clinic-abu-dhabi",
    }
    assert abu_dhabi["scraper_type"] == "linkedin"
    assert json.loads(abu_dhabi["scraper_config"])["enrich"] == [
        "description",
        "employment_type",
        "job_location_type",
    ]

    primary = rows["cleveland-clinic-careers"]
    assert primary["board_url"] == ("https://ccf.wd1.myworkdayjobs.com/clevelandcliniccareers")
    assert (primary["monitor_type"], primary["scraper_type"]) == (
        "workday",
        "workday",
    )
