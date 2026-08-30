"""Board contracts for PVH's verified global and regional sources."""

from __future__ import annotations

import csv
import json

from src.shared.constants import DATA_DIR


def _boards() -> dict[str, dict[str, object]]:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "pvh"]
    return {
        row["board_slug"]: {
            "url": row["board_url"],
            "monitor": row["monitor_type"],
            "monitor_config": json.loads(row["monitor_config"] or "{}"),
            "scraper": row["scraper_type"],
        }
        for row in rows
    }


def test_pvh_uses_three_distinct_employer_listing_sources() -> None:
    boards = _boards()

    assert set(boards) == {
        "pvh-calvin-klein-brasil",
        "pvh-careers",
        "pvh-china",
    }
    assert boards["pvh-careers"] == {
        "url": "https://pvh.wd1.myworkdayjobs.com/pvh_careers",
        "monitor": "workday",
        "monitor_config": {
            "company": "pvh",
            "wd_instance": "wd1",
            "site": "PVH_Careers",
        },
        "scraper": "workday",
    }


def test_calvin_klein_brazil_fails_closed_on_stale_detail_links() -> None:
    board = _boards()["pvh-calvin-klein-brasil"]

    assert board["url"].endswith("/Vacancies")
    assert board["monitor"] == "dom"
    assert board["scraper"] == "json-ld"
    assert board["monitor_config"] == {
        "render": True,
        "resource_policy": "none",
        "link_selector": "a[href*='/Detail']",
        "require_jsonld_jobposting": True,
    }


def test_china_uses_rich_51job_api_monitor() -> None:
    board = _boards()["pvh-china"]

    assert board == {
        "url": "https://pvh.51job.com/C01job_list.html",
        "monitor": "job51",
        "monitor_config": {"ctmid": 258121},
        "scraper": "skip",
    }
