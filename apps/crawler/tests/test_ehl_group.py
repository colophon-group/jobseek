"""Regression coverage for EHL Group's cross-provider board configuration."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors.dom import dom_discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
INFONIQA_SLUG = "ehl-group-passugg-infoniqa"
INFONIQA_BOARD_URL = (
    "https://ehlcampus.infoniqa.io/hcm/jobexchange/showJobOfferList.do?init=true&j=jobexchange"
)
_EMPTY_FETCH_PATCH = "src.shared.http_retry.fetch_text_page_with_retry"

INFONIQA_EMPTY_HTML = """
<html>
  <body class="jobOfferList">
    <nav>
      <a class="menu menuQuicksearch"
         href="/hcm/jobexchange/showJobOfferList.do?j=jobexchange">Offene Jobs</a>
    </nav>
    <h1 class="caption">Stellenangebote der EHL Hotelfachschule Passugg</h1>
    <form id="jobOfferSearch">
      <section id="jobOfferListResult" class="jobOfferListResult"></section>
    </form>
  </body>
</html>
"""


def _ehl_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row["company_slug"] == "ehl-group"]


def _board(slug: str) -> dict:
    row = next(row for row in _ehl_rows() if row["board_slug"] == slug)
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"] or "{}"),
    }


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


async def test_infoniqa_accepts_provider_bound_structural_empty_state() -> None:
    with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=INFONIQA_EMPTY_HTML)):
        result = await dom_discover(_board(INFONIQA_SLUG), AsyncMock())

    assert result == set()


async def test_infoniqa_persistent_heading_alone_does_not_prove_empty() -> None:
    shell = """
    <body class="jobOfferList">
      <h1 class="caption">Stellenangebote der EHL Hotelfachschule Passugg</h1>
      <p>Temporarily unavailable</p>
    </body>
    """
    with (
        patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=shell)),
        pytest.raises(ValueError, match="did not match the configured explicit empty state"),
    ):
        await dom_discover(_board(INFONIQA_SLUG), AsyncMock())


async def test_infoniqa_rejects_wrong_origin_provider_identity() -> None:
    wrong_origin = INFONIQA_EMPTY_HTML.replace(
        'href="/hcm/jobexchange/showJobOfferList.do?j=jobexchange"',
        'href="https://attacker.example/hcm/jobexchange/showJobOfferList.do?j=jobexchange"',
    )
    with (
        patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=wrong_origin)),
        pytest.raises(ValueError, match="did not match the configured explicit empty state"),
    ):
        await dom_discover(_board(INFONIQA_SLUG), AsyncMock())


async def test_infoniqa_linked_vacancy_is_not_misclassified_as_empty() -> None:
    active = INFONIQA_EMPTY_HTML.replace(
        '<section id="jobOfferListResult" class="jobOfferListResult"></section>',
        """<section id="jobOfferListResult" class="jobOfferListResult">
          <a href="/hcm/jobexchange/showJobOfferDetail.do?jobOfferId=abc">Chef</a>
        </section>""",
    )
    with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=active)):
        result = await dom_discover(_board(INFONIQA_SLUG), AsyncMock())

    assert result == {
        "https://ehlcampus.infoniqa.io/hcm/jobexchange/showJobOfferDetail.do?jobOfferId=abc"
    }
