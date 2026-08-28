"""Board and asset contracts for Beth Israel Lahey Health."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_bilh_uses_three_complementary_provider_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "beth-israel-lahey-health"
        ]

    assert [row["board_slug"] for row in rows] == [
        "beth-israel-lahey-health-careers",
        "beth-israel-lahey-health-exeter",
        "beth-israel-lahey-health-joslin",
    ]

    central, exeter, joslin = rows
    assert (central["monitor_type"], central["scraper_type"]) == (
        "workday",
        "json-ld",
    )
    assert json.loads(central["monitor_config"]) == {
        "company": "bilh",
        "wd_instance": "wd1",
        "site": "External",
    }

    assert exeter["monitor_type"] == "rss"
    assert exeter["scraper_type"] == "json-ld"
    assert json.loads(exeter["monitor_config"]) == {
        "preset": "wp_job_manager",
        "feed_url": "https://exetercareers.com/?feed=job_feed",
        "proxy": True,
    }
    assert json.loads(exeter["scraper_config"])["proxy"] is True

    assert joslin["monitor_type"] == "icims"
    assert joslin["scraper_type"] == "json-ld"
    assert json.loads(joslin["monitor_config"]) == {"host": "jobs-joslin.icims.com"}


def test_bilh_keeps_finalized_content_addressed_assets() -> None:
    with (DATA_DIR / "companies.csv").open(newline="") as handle:
        company = next(
            row for row in csv.DictReader(handle) if row["slug"] == "beth-israel-lahey-health"
        )

    assert company["logo_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/beth-israel-lahey-health/"
        "logo-bf7d0cd4f584971e5fbbc4018ad193e2cff49893eaaf03d587b099e0b14d3496.svg"
    )
    assert company["icon_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/beth-israel-lahey-health/"
        "icon-c4ec4bfb625b0b81fa9cae3193ae6600de5d314e20f5d5454368fb0694752122.webp"
    )
    assert not (DATA_DIR / "images/beth-israel-lahey-health").exists()
