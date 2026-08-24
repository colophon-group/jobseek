"""Regression coverage for the International Golf Federation careers source."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors.dom import dom_discover
from src.core.scrapers.dom import parse_html

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FETCH_PATCH = "src.shared.http_retry.fetch_with_retry"
BOARD_SLUG = "international-golf-federation-careers"
COMPANY_SLUG = "international-golf-federation"


def _csv_row(filename: str, key: str, value: str) -> dict[str, str]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def _board() -> tuple[dict, dict]:
    row = _csv_row("boards.csv", "board_slug", BOARD_SLUG)
    return (
        {
            "board_url": row["board_url"],
            "metadata": json.loads(row["monitor_config"]),
        },
        json.loads(row["scraper_config"]),
    )


async def test_authoritative_empty_state_is_accepted() -> None:
    board, _ = _board()
    html = """
    <main>
      <div class="layout">
        <p>There are currently no job vacancies at the International Golf Federation (IGF).</p>
        <p>Please check back regularly for future positions that may be of interest to you.</p>
      </div>
    </main>
    """

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(board, AsyncMock())

    assert result == set()


async def test_linked_document_or_application_is_discovered() -> None:
    board, _ = _board()
    html = """
    <main>
      <div class="layout">
        <h2>Open positions</h2>
        <a href="/documents/jobs/operations-manager.pdf">Operations Manager</a>
        <a href="https://apply.example.org/igf/technology-lead">Technology Lead</a>
      </div>
    </main>
    """

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(board, AsyncMock())

    assert result == {
        "https://www.igfgolf.org/documents/jobs/operations-manager.pdf",
        "https://apply.example.org/igf/technology-lead",
    }


async def test_unsupported_nonempty_layout_fails_closed() -> None:
    board, _ = _board()
    html = """
    <main>
      <div class="layout">
        <h2>Programme Coordinator</h2>
        <p>Apply by email to careers@example.org.</p>
      </div>
    </main>
    """

    with (
        patch(FETCH_PATCH, AsyncMock(return_value=html)),
        pytest.raises(ValueError, match="did not match the configured explicit empty state"),
    ):
        await dom_discover(board, AsyncMock())


@pytest.mark.parametrize(
    "href",
    [
        "#apply",
        "https://www.igfgolf.org/site-settings/igf-careers",
        "https://www.igfgolf.org/about/igf",
        "https://www.igfgolf.org/about/international-golf-federation",
        "https://career-advice.example.org/about",
    ],
)
async def test_self_or_unrelated_links_fail_closed(href: str) -> None:
    board, _ = _board()
    html = f"""
    <main>
      <div class="layout">
        <h2>Programme Coordinator</h2>
        <a href="{href}">Learn more</a>
        <p id="apply">Apply by email to careers@example.org.</p>
      </div>
    </main>
    """

    with (
        patch(FETCH_PATCH, AsyncMock(return_value=html)),
        pytest.raises(ValueError, match="did not match the configured explicit empty state"),
    ):
        await dom_discover(board, AsyncMock())


def test_html_and_document_scrapers_are_configured() -> None:
    _, scraper_config = _board()
    content = parse_html(
        """
        <html><body><main>
          <h1>Technology Lead</h1>
          <p>Lead the federation's technology programme.</p>
        </main><footer>Footer</footer></body></html>
        """,
        scraper_config,
    )

    assert content.title == "Technology Lead"
    assert "technology programme" in (content.description or "")
    assert scraper_config["document_fallback"]["pdf"]["title_source"] == "text"
    assert scraper_config["document_fallback"]["docx"]["title_source"] == "text"


def test_requested_igf_acronym_is_structured_metadata() -> None:
    row = _csv_row("companies.csv", "slug", COMPANY_SLUG)
    extras = json.loads(row["extras"])

    assert extras["alternateName"] == "IGF"
    assert all(value.startswith(("http://", "https://")) for value in extras["sameAs"])
