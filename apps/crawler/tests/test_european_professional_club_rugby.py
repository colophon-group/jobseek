"""Regression coverage for the EPCR careers board."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.monitors.dom import dom_discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOARD_SLUG = "european-professional-club-rugby-careers"
COMPANY_SLUG = "european-professional-club-rugby"

EMPTY_HTML = """
<main>
  <h1>Careers</h1>
  <p>European Professional Club Rugby organises international club competitions.</p>
  <p>Established in 2014 with headquarters in Lausanne, Switzerland.</p>
  <p>There are currently no vacancies available.</p>
</main>
"""


def _csv_row(filename: str, key: str, value: str) -> dict[str, str]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def _board() -> dict:
    row = _csv_row("boards.csv", "board_slug", BOARD_SLUG)
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }


async def test_rendered_board_accepts_authoritative_empty_paragraph() -> None:
    page = MagicMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=page)
    context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("src.core.monitors.dom.open_page", return_value=context),
        patch("src.core.monitors.dom._extract_links_rendered", AsyncMock(return_value=set())),
        patch("src.core.monitors.dom.safe_content", AsyncMock(return_value=EMPTY_HTML)),
    ):
        result = await dom_discover(_board(), AsyncMock(), pw=MagicMock())

    assert result == set()


async def test_empty_marker_with_a_linked_vacancy_fails_closed() -> None:
    page = MagicMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=page)
    context.__aexit__ = AsyncMock(return_value=None)
    html = EMPTY_HTML.replace(
        "</main>",
        '<a href="https://media.example/epcr-role.pdf">Job description</a></main>',
    )

    with (
        patch("src.core.monitors.dom.open_page", return_value=context),
        patch(
            "src.core.monitors.dom._extract_links_rendered",
            AsyncMock(return_value={"https://media.example/epcr-role.pdf"}),
        ),
        patch("src.core.monitors.dom.safe_content", AsyncMock(return_value=html)),
        pytest.raises(ValueError, match="forbidden links present"),
    ):
        await dom_discover(_board(), AsyncMock(), pw=MagicMock())


def test_company_metadata_preserves_epcr_identity() -> None:
    company = _csv_row("companies.csv", "slug", COMPANY_SLUG)
    extras = json.loads(company["extras"])

    assert company["name"] == "European Professional Club Rugby"
    assert company["industry"] == "19"
    assert company["employee_count_range"] == "2"
    assert company["founded_year"] == "2014"
    assert extras["alternateName"] == "EPCR"

    descriptions = _csv_row("company_descriptions.csv", "slug", COMPANY_SLUG)
    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))
