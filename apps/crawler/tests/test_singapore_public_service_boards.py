"""Singapore Public Service metadata, board, and asset contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _rows(path: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / path).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_singapore_public_service_keeps_all_four_official_sources() -> None:
    rows = _rows("boards.csv", "company_slug", "singapore-public-service")
    assert [row["board_slug"] for row in rows] == [
        "singapore-public-service-careers",
        "singapore-public-service-govtech",
        "singapore-public-service-portal",
        "singapore-public-service-psd",
    ]

    by_slug = {row["board_slug"]: row for row in rows}
    assert (
        by_slug["singapore-public-service-careers"]["monitor_type"],
        by_slug["singapore-public-service-careers"]["scraper_type"],
    ) == (
        "workday",
        "workday",
    )
    assert (
        by_slug["singapore-public-service-govtech"]["monitor_type"],
        by_slug["singapore-public-service-govtech"]["scraper_type"],
    ) == (
        "greenhouse",
        "skip",
    )
    assert (
        by_slug["singapore-public-service-psd"]["monitor_type"],
        by_slug["singapore-public-service-psd"]["scraper_type"],
    ) == (
        "workable",
        "workable",
    )

    portal = by_slug["singapore-public-service-portal"]
    assert portal["board_url"] == "https://jobs.careers.gov.sg/"
    assert (portal["monitor_type"], portal["scraper_type"]) == ("api_sniffer", "skip")
    config = json.loads(portal["monitor_config"])
    assert config["api_url"].startswith(
        "https://raw.githubusercontent.com/opengovsg/careersgovsg-jobs-data/"
    )
    assert config["item_filter"]["include"] == {"platform": ["hrp"]}
    assert config["item_filter"]["dedupe_by"] == ["platform", "jobId", "postingNo"]
    assert config["fields"]["locations"] == "=Singapore"
    assert config["fields"]["date_posted"] == {
        "path": "startDate",
        "timestamp_unit": "milliseconds",
    }


def test_singapore_public_service_metadata_and_staged_assets() -> None:
    company = _rows("companies.csv", "slug", "singapore-public-service")[0]
    assert company["name"] == "Singapore Public Service"
    assert company["website"] == "https://www.careers.gov.sg/"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "13"
    assert company["employee_count_range"] == "8"

    description = _rows("company_descriptions.csv", "slug", "singapore-public-service")[0]
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    assert company["logo_url"].endswith("/singapore-public-service/logo.svg")
    assert company["icon_url"].endswith("/singapore-public-service/icon.png")
    assert (DATA_DIR / "images" / "singapore-public-service-logo.svg").is_file()
    assert (DATA_DIR / "images" / "singapore-public-service-icon.png").is_file()
