"""BNY company and board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_bny_company_metadata_is_complete() -> None:
    with (DATA_DIR / "companies.csv").open(newline="", encoding="utf-8") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "bny")

    assert company["name"] == "BNY"
    assert company["website"] == "https://www.bny.com"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "2"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1784"

    with (DATA_DIR / "company_descriptions.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        descriptions = next(row for row in csv.DictReader(handle) if row["slug"] == "bny")

    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))


def test_bny_uses_verified_complementary_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "bny"]

    assert [row["board_slug"] for row in rows] == [
        "bny-careers",
        "bny-contractors-us",
        "bny-insight-investment",
    ]
    by_slug = {row["board_slug"]: row for row in rows}

    primary = by_slug["bny-careers"]
    assert primary["board_url"] == (
        "https://eofe.fa.us2.oraclecloud.com/"
        "hcmUI/CandidateExperience/en/sites/CX_1"
    )
    assert primary["monitor_type"] == primary["scraper_type"] == "oracle_hcm"
    assert json.loads(primary["monitor_config"]) == {"total_count_tolerance": 3}
    assert json.loads(primary["scraper_config"]) == {"enrich": ["description"]}

    contractors = by_slug["bny-contractors-us"]
    assert contractors["board_url"] == "https://us.bny.talentnet.community/"
    assert contractors["monitor_type"] == "api_sniffer"
    assert contractors["scraper_type"] == "skip"
    contractor_config = json.loads(contractors["monitor_config"])
    assert contractor_config["api_url"].endswith("/api/community/jobs/search")
    assert contractor_config["total_path"] == "meta.totalCount"
    assert contractor_config["request_headers"]["x-tenant"] == "bnymellon"
    assert contractor_config["fields"] == {
        "title": "title.name",
        "description": "description",
        "employment_type": "type",
        "job_location_type": "workplaceType",
        "locations": "location.city",
        "skills": "skills[].name",
        "metadata.start_date": "startDate",
        "metadata.country": "location.country",
    }

    insight = by_slug["bny-insight-investment"]
    assert insight["board_url"] == "https://apply.workable.com/insight-investment/"
    assert insight["monitor_type"] == insight["scraper_type"] == "workable"
    assert json.loads(insight["monitor_config"]) == {"token": "insight-investment"}
