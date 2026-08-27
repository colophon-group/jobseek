"""Board contract for Sentara Health's coverage-proven Workday inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_sentara_uses_complementary_facets_with_independent_coverage() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "sentara-health"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "sentara-health-careers"
    assert row["monitor_type"] == "workday"
    assert row["scraper_type"] == "workday"
    assert json.loads(row["monitor_config"]) == {
        "facet_union": {
            "facets": ["primaryLocation", "jobFamilyGroup"],
            "coverage_facet": "workerSubType",
        }
    }


def test_sentara_keeps_finalized_content_addressed_assets() -> None:
    with (DATA_DIR / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "sentara-health")

    assert company["logo_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/sentara-health/"
        "logo-bff4b0a5e86cfba916d6ccb0195167c6513aad8bd6dde769efa04ff0f77a29e9.png"
    )
    assert company["icon_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/sentara-health/"
        "icon-fdc56a92f2c7eba64a9a67e6a8b64cd63788442f7b2bbb717d421e20313d4ed6.webp"
    )
    assert not (DATA_DIR / "images/sentara-health").exists()
