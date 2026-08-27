"""Board contract for GE Vernova's two-site Workday inventory."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_ge_vernova_uses_one_ordered_multi_site_board() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "ge-vernova"]

    assert len(rows) == 1
    row = rows[0]
    assert row["board_slug"] == "ge-vernova-global-external"
    assert row["monitor_type"] == "workday"
    assert row["scraper_type"] == "workday"

    config = json.loads(row["monitor_config"])
    assert config == {
        "company": "gevernova",
        "wd_instance": "wd5",
        "site": "Vernova_ExternalSite",
        "sites": [
            "Vernova_ExternalSite",
            "Only_Confidential_Executive_Recruiting",
        ],
    }


def test_ge_vernova_metadata_has_provenance_and_finalized_assets() -> None:
    with (DATA_DIR / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "ge-vernova")

    metadata = json.loads(company["extras"])
    assert metadata["wikidataId"] == "Q118957699"
    assert metadata["tickerSymbol"] == "GEV"
    assert metadata["sameAs"] == [
        "https://www.linkedin.com/company/gevernova",
        "https://twitter.com/gevernova",
    ]
    assert company["logo_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/ge-vernova/"
        "logo-72353845345fab1ba3030abc7bb96ec66314481b083824cda33825009c1baefa.png"
    )
    assert company["icon_url"] == (
        "https://jobseek-assets.colophon-group.org/companies/ge-vernova/"
        "icon-33211cd3376b315b255129aa6b18a436c928bec7ffeae1b31a049a7e37361ca1.webp"
    )
    assert not (DATA_DIR / "images/ge-vernova").exists()
