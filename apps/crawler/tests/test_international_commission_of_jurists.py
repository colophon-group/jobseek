"""Regression coverage for the International Commission of Jurists board."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors.dom import dom_discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOARD_SLUG = "international-commission-of-jurists-careers"
FETCH_PATCH = "src.shared.http_retry.fetch_text_page_with_retry"


def _board() -> dict:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == BOARD_SLUG)
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }


@pytest.mark.parametrize(
    "marker",
    [
        "There are currently no published vacancies.",
        "There are currently no openings",
    ],
)
async def test_current_and_historical_empty_markers_are_authoritative(marker: str) -> None:
    html = (
        '<main><div class="et_pb_text_3"><div class="et_pb_text_inner">'
        f"<p> {marker} </p></div></div></main>"
    )

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(_board(), AsyncMock())

    assert result == set()


async def test_unrecognised_empty_wording_fails_closed() -> None:
    html = (
        '<main><div class="et_pb_text_3"><div class="et_pb_text_inner">'
        "<p>Vacancies will return soon.</p></div></div></main>"
    )

    with (
        patch(FETCH_PATCH, AsyncMock(return_value=html)),
        pytest.raises(ValueError, match="configured explicit empty state"),
    ):
        await dom_discover(_board(), AsyncMock())


@pytest.mark.parametrize(
    "href",
    [
        "https://www.icj.org/about/jobs-internships/legal-adviser/",
        "https://www.icj.org/wp-content/uploads/2026/09/new-role.pdf",
        "https://www.icj.org/wp-content/uploads/2026/09/new-role.pdf?download=1",
    ],
)
async def test_empty_marker_cannot_mask_any_link_in_the_vacancy_block(href: str) -> None:
    html = f"""
    <main><div class="et_pb_text_3">
      <div class="et_pb_text_inner">
        <p>There are currently no published vacancies.</p>
        <a href="{href}">Role</a>
      </div>
    </div></main>
    """

    with (
        patch(FETCH_PATCH, AsyncMock(return_value=html)),
        pytest.raises(ValueError, match="forbidden links present"),
    ):
        await dom_discover(_board(), AsyncMock())


def test_board_uses_selector_specific_empty_contracts() -> None:
    metadata = _board()["metadata"]

    assert "empty_selector" not in metadata
    assert "empty_text" not in metadata
    assert metadata["empty_states"] == [
        {
            "selector": "div.et_pb_text_3 .et_pb_text_inner p",
            "exact_text": "There are currently no published vacancies.",
            "forbidden_link_selector": "div.et_pb_text_3 a[href]",
        },
        {
            "selector": "div.et_pb_text_3 .et_pb_text_inner p",
            "exact_text": "There are currently no openings",
            "forbidden_link_selector": "div.et_pb_text_3 a[href]",
        },
    ]
