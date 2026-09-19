"""Board contracts for TD SYNNEX's central and subsidiary recruiting sources."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _boards() -> dict[str, dict[str, object]]:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "td-synnex"]
    return {
        row["board_slug"]: {
            "url": row["board_url"],
            "monitor": row["monitor_type"],
            "config": json.loads(row["monitor_config"] or "{}"),
            "scraper": row["scraper_type"],
        }
        for row in rows
    }


def test_td_synnex_keeps_central_and_hyve_sources() -> None:
    boards = _boards()

    assert set(boards) == {"td-synnex-careers", "td-synnex-hyve"}
    assert boards["td-synnex-careers"] == {
        "url": "https://careers.tdsynnex.com/",
        "monitor": "phenom",
        "config": {},
        "scraper": "json-ld",
    }


def test_hyve_workday_source_is_scoped_to_its_exact_site() -> None:
    hyve = _boards()["td-synnex-hyve"]

    assert hyve["url"] == "https://synnex.wd5.myworkdayjobs.com/hyvecareers"
    assert hyve["monitor"] == hyve["scraper"] == "workday"
    assert hyve["config"] == {
        "company": "synnex",
        "wd_instance": "wd5",
        "site": "hyvecareers",
        "all_sites": False,
    }
