"""Board contracts for Poly Developments' verified employer sources."""

from __future__ import annotations

import csv
import json

from src.shared.constants import DATA_DIR


def _boards() -> dict[str, dict[str, object]]:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [
            row for row in csv.DictReader(handle) if row["company_slug"] == "poly-developments"
        ]
    return {
        row["board_slug"]: {
            "url": row["board_url"],
            "monitor": row["monitor_type"],
            "monitor_config": json.loads(row["monitor_config"] or "{}"),
            "scraper": row["scraper_type"],
        }
        for row in rows
    }


def test_poly_developments_uses_three_distinct_employer_sources() -> None:
    boards = _boards()

    assert boards == {
        "poly-developments-careers": {
            "url": "https://polycareer.zhiye.com/",
            "monitor": "beisen",
            "monitor_config": {},
            "scraper": "skip",
        },
        "poly-developments-commerce-travel": {
            "url": "https://campus.51job.com/polycommercetourism/job.html",
            "monitor": "job51",
            "monitor_config": {"ctmid": 1787809},
            "scraper": "skip",
        },
        "poly-developments-hospitality": {
            "url": "https://job.veryeast.cn/1129501/jobs",
            "monitor": "dom",
            "monitor_config": {
                "link_selector": 'a[href^="/1129501/"]',
                "url_filter": r"^https?://(?:www\.)?job\.veryeast\.cn/1129501/\d+/?$",
            },
            "scraper": "veryeast",
        },
    }
