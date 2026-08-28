"""Board contracts for Saskatchewan Health Authority."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _boards() -> dict[str, dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "saskatchewan-health-authority"
        }


def test_sha_oracle_board_is_scoped_to_the_verified_organization() -> None:
    boards = _boards()

    assert set(boards) == {
        "saskatchewan-health-authority-careers",
        "saskatchewan-health-authority-physicians",
    }
    row = boards["saskatchewan-health-authority-careers"]
    assert row["monitor_type"] == row["scraper_type"] == "oracle_hcm"
    assert json.loads(row["monitor_config"]) == {
        "organization_id": "300000003438826",
        "total_count_tolerance": 1,
    }
    assert "selectedOrganizationsFacet=300000003438826" in row["board_url"]
    assert json.loads(row["scraper_config"]) == {"enrich": ["description"]}


def test_sha_physician_board_uses_the_employer_filtered_public_api() -> None:
    row = _boards()["saskatchewan-health-authority-physicians"]
    monitor = json.loads(row["monitor_config"])
    scraper = json.loads(row["scraper_config"])

    assert row["monitor_type"] == "api_sniffer"
    assert monitor["api_url"].endswith("/dotnet/saskdocs/jobpostings/search")
    assert monitor["params"] == {"hr": "1cc44167-d731-e711-80fb-5065f38b1181"}
    assert monitor["url_template"].endswith("job-posting?jobid={jobPostingId}")
    assert monitor["fields"]["title"] == "positionTitle"
    assert monitor["fields"]["locations"] == "community"

    assert row["scraper_type"] == "dom"
    assert {step.get("field") for step in scraper["steps"]} >= {
        "title",
        "description",
        "location",
    }


def test_sha_keeps_finalized_content_addressed_assets() -> None:
    with (DATA_DIR / "companies.csv").open(newline="", encoding="utf-8") as handle:
        company = next(
            row for row in csv.DictReader(handle) if row["slug"] == "saskatchewan-health-authority"
        )

    assert company["logo_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/"
        "saskatchewan-health-authority/"
        "logo-d2cd06f84f042aea5455fd118a509659c6657de72b1ed86362e9edc306b38f4c.svg"
    )
    assert company["icon_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/"
        "saskatchewan-health-authority/"
        "icon-167a7b7b80bb2dd7c40c892ae0ee7e45ee5bcb21d630511220ff13e7b6343364.webp"
    )
    assert not (DATA_DIR / "images/saskatchewan-health-authority").exists()
