from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _row(filename: str, key: str, value: str) -> dict[str, str]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def test_quanta_services_uses_the_verified_aggregate_icims_board() -> None:
    row = _row("boards.csv", "company_slug", "quanta-services")

    assert row["board_slug"] == "quanta-services-all-careers"
    assert row["board_url"] == "https://allcareers-quanta.icims.com/"
    assert row["monitor_type"] == "icims"
    assert json.loads(row["monitor_config"]) == {
        "host": "allcareers-quanta.icims.com",
        "job_hosts": ["careers-quanta.icims.com", "careers2-quanta.icims.com"],
    }
    assert row["scraper_type"] == "json-ld"


def test_quanta_services_metadata_is_complete() -> None:
    company = _row("companies.csv", "slug", "quanta-services")
    descriptions = _row("company_descriptions.csv", "slug", "quanta-services")

    assert company["name"] == "Quanta Services"
    assert company["website"] == "https://www.quantaservices.com/"
    assert company["industry"] == "11"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1997"
    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))
