"""Board contracts for Bon Secours Mercy Health's global inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_bon_secours_mercy_health_uses_five_complementary_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "bon-secours-mercy-health"
        ]

    assert [row["board_slug"] for row in rows] == [
        "bon-secours-mercy-health-careers",
        "bon-secours-mercy-health-ireland-workday",
        "bon-secours-mercy-health-philippines-gbs",
        "bon-secours-mercy-health-physicians",
        "bon-secours-mercy-health-roper-st-francis",
    ]

    central, ireland, philippines, physicians, roper = rows
    assert (central["monitor_type"], central["scraper_type"]) == (
        "phenom",
        "json-ld",
    )

    assert ireland["monitor_type"] == ireland["scraper_type"] == "workday"
    assert json.loads(ireland["monitor_config"])["site"] == "bon_secours_careers"

    assert philippines["monitor_type"] == philippines["scraper_type"] == "workday"
    assert json.loads(philippines["monitor_config"])["site"] == (
        "BonSecoursMercyHealthGlobalBusinessServicesCareers"
    )

    assert physicians["monitor_type"] == "sitemap"
    assert physicians["scraper_type"] == "json-ld"
    assert json.loads(physicians["monitor_config"])["url_filter"] == ("/search/jobdetails/")

    assert roper["monitor_type"] == "sitemap"
    assert roper["scraper_type"] == "json-ld"
    assert json.loads(roper["monitor_config"])["url_filter"] == "/us/en/job/"
