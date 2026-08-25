"""Regression coverage for EHL Group's cross-provider board configuration."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INFONIQA_SLUG = "ehl-group-passugg-infoniqa"


def _ehl_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row["company_slug"] == "ehl-group"]


def test_ehl_sources_are_distinct_and_preserve_verified_regional_boards() -> None:
    rows = {row["board_slug"]: row for row in _ehl_rows()}

    assert set(rows) == {
        "ehl-group-lausanne-jobup",
        "ehl-group-passugg-apprenticeships",
        INFONIQA_SLUG,
        "ehl-group-singapore-jobstreet",
    }
    yousty = rows["ehl-group-passugg-apprenticeships"]
    assert yousty["monitor_type"] == "dom"
    assert "organization_ids%5B%5D=1213953-ehl-hotelfachschule-passugg-ssth" in yousty["board_url"]
    assert "-ehl-hotelfachschule-passugg-ssth" in json.loads(yousty["monitor_config"])["url_filter"]
    singapore = rows["ehl-group-singapore-jobstreet"]
    assert singapore["monitor_type"] == "jobstreet"
    assert json.loads(singapore["monitor_config"]) == {
        "host": "sg.jobstreet.com",
        "company_id": "172077835588736",
        "organisation_id": "699133",
    }
    infoniqa = rows[INFONIQA_SLUG]
    assert infoniqa["monitor_type"] == "infoniqa"
    assert json.loads(infoniqa["monitor_config"]) == {
        "employer_name": "EHL Hotelfachschule Passugg"
    }
