"""Board contracts for GE Vernova and its integrated operating companies."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_ge_vernova_uses_verified_distinct_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "ge-vernova"]

    assert [row["board_slug"] for row in rows] == [
        "ge-vernova-fieldcore-global",
        "ge-vernova-global-external",
        "ge-vernova-menk-usa",
        "ge-vernova-prolec-field-service",
    ]
    by_slug = {row["board_slug"]: row for row in rows}

    fieldcore = by_slug["ge-vernova-fieldcore-global"]
    assert fieldcore["board_url"] == "https://jobs.jobvite.com/fieldcore-review/jobs"
    assert fieldcore["monitor_type"] == "jobvite"
    assert fieldcore["scraper_type"] == "json-ld"
    assert json.loads(fieldcore["monitor_config"]) == {
        "tenant": "fieldcore-review",
        "listing_url": "https://jobs.jobvite.com/fieldcore-review/jobs",
    }

    workday = by_slug["ge-vernova-global-external"]
    assert workday["monitor_type"] == "workday"
    assert workday["scraper_type"] == "workday"
    assert json.loads(workday["monitor_config"]) == {
        "company": "gevernova",
        "wd_instance": "wd5",
        "site": "Vernova_ExternalSite",
        "sites": [
            "Vernova_ExternalSite",
            "Only_Confidential_Executive_Recruiting",
        ],
    }

    menk = by_slug["ge-vernova-menk-usa"]
    assert menk["monitor_type"] == "api_sniffer"
    assert menk["scraper_type"] == "json-ld"
    menk_config = json.loads(menk["monitor_config"])
    assert menk_config["api_url"] == "https://menkusa.isolvedhire.com/core/jobs/10734"
    assert menk_config["json_path"] == "data.jobs"
    assert menk_config["total_path"] == "data.jobCount"
    assert menk_config["url_field"] == "jobUrl"
    assert json.loads(menk["scraper_config"])["enrich"] == [
        "description",
        "date_posted",
        "base_salary",
    ]

    prolec = by_slug["ge-vernova-prolec-field-service"]
    assert prolec["monitor_type"] == "adp"
    assert prolec["scraper_type"] == "adp"
    assert json.loads(prolec["monitor_config"]) == {
        "cid": "4f2488ef-4bca-483d-b781-fe39c53a74c2",
        "cc_id": "19000101_000001",
        "locale": "en_US",
    }
    assert json.loads(prolec["scraper_config"]) == {"enrich": ["description"]}


def test_ge_vernova_metadata_has_provenance_and_assets() -> None:
    with (DATA_DIR / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "ge-vernova")

    metadata = json.loads(company["extras"])
    assert metadata["wikidataId"] == "Q118957699"
    assert metadata["tickerSymbol"] == "GEV"
    assert metadata["sameAs"] == [
        "https://www.linkedin.com/company/gevernova",
        "https://twitter.com/gevernova",
    ]
    asset_prefix = "https://jobseek-assets.colophon-group.org/companies/ge-vernova/"
    assert company["logo_url"].startswith(asset_prefix)
    assert company["icon_url"].startswith(asset_prefix)

    staged_images = DATA_DIR / "images/ge-vernova"
    if staged_images.exists():
        assert {path.name for path in staged_images.iterdir()} == {"logo.svg", "icon.ico"}
