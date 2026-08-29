"""Board contracts for UNIQLO's verified regional recruiting sources."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _boards() -> dict[str, dict[str, object]]:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "uniqlo"]
    return {
        row["board_slug"]: {
            "url": row["board_url"],
            "monitor": row["monitor_type"],
            "config": json.loads(row["monitor_config"] or "{}"),
            "scraper": row["scraper_type"],
        }
        for row in rows
    }


def test_uniqlo_uses_verified_distinct_regional_boards() -> None:
    boards = _boards()

    assert set(boards) == {
        "uniqlo-careers-cn",
        "uniqlo-careers-workday",
        "uniqlo-careers-workday-asia",
        "uniqlo-germany-heyjobs",
        "uniqlo-japan-store",
        "uniqlo-jobs-canada",
    }
    assert boards["uniqlo-careers-cn"]["monitor"] == "beisen"
    assert boards["uniqlo-careers-cn"]["scraper"] == "skip"
    assert boards["uniqlo-japan-store"]["config"] == {
        "sitemap_url": "https://uniqlo-staff.jp/jobfind-pc/sitemap.xml",
        "url_filter": "/jobfind-pc/job/",
    }
    assert boards["uniqlo-jobs-canada"]["config"] == {
        "tenant": "uniqloca",
        "listing_url": "https://jobs.jobvite.com/uniqloca/jobs",
    }


def test_workday_sites_are_explicit_non_overlapping_uniqlo_partitions() -> None:
    boards = _boards()
    primary = boards["uniqlo-careers-workday"]["config"]
    secondary = boards["uniqlo-careers-workday-asia"]["config"]
    primary_sites = set(primary["sites"])
    secondary_sites = set(secondary["sites"])

    assert len(primary_sites) == 17
    assert len(secondary_sites) == 8
    assert primary_sites.isdisjoint(secondary_sites)
    assert all("Uniqlo" in site for site in primary_sites | secondary_sites)
    assert "careers_cd_Uniqlo" not in primary_sites | secondary_sites
    assert primary["site"] == "graduates_eu_Uniqlo"
    assert secondary["site"] == "headquarters_sg_Uniqlo"


def test_heyjobs_keyword_results_are_filtered_to_exact_uniqlo_employer() -> None:
    board = _boards()["uniqlo-germany-heyjobs"]
    config = board["config"]

    assert board["monitor"] == "nextdata"
    assert board["scraper"] == "skip"
    assert config["path"] == "props.pageProps.initialState.search.jobs[].job"
    assert config["include_item_values"] == {
        "company_display_name": ["UNIQLO EUROPE LTD - German branch"]
    }
    assert config["pagination"] == {
        "path": "props.pageProps.initialState.search",
        "page_count": "totalPages",
        "page_param": "page",
    }
    assert config["source_identity"] == {
        "provider": "heyjobs",
        "tenant": "uniqlo-europe",
        "field": "requisition_id",
    }


def test_uniqlo_image_staging_is_complete() -> None:
    assert (DATA_DIR / "images/uniqlo/logo.png").stat().st_size > 0
    assert (DATA_DIR / "images/uniqlo/icon.png").stat().st_size > 0
