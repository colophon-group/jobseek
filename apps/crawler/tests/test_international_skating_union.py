"""Regression coverage for the International Skating Union career sources."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors.dom import dom_discover
from src.core.scrapers.pdf import parse_bytes

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EMPTY_FETCH_PATCH = "src.shared.http_retry.fetch_text_page_with_retry"
COMPANY_SLUG = "international-skating-union"
CAREERS_BOARD_SLUG = "international-skating-union-careers"
LUCCA_BOARD_SLUG = "international-skating-union-lucca"
LUCCA_JOB_URL = (
    "https://jobs.world.luccasoftware.com/isu-org-careers/"
    "event-administration-manager-fe62f90b-0100-43a7-b254-6c1623d3ff66"
)
PDF_ROOT = (
    "https://isu-d8g8b4b7ece7aphs.a03.azurefd.net/isudamcontainer/"
    "CMS/Corporate-Site/Governance/Transparency/Jobs"
)
STAFF_DOCUMENTS = {
    "Senior-Event-Manager-2026-1773417513-1364.pdf": (
        "Senior Event Manager",
        "Lausanne",
    ),
    "Event-Manager-2026-1773417546-2282.pdf": (
        "Event Manager",
        "Lausanne",
    ),
    "ISU-Development-Program-Coordinator-YC-10-04-2026-1776949493-0667.pdf": (
        "Development Program Coordinator",
        "Lausanne, Switzerland",
    ),
    "ISU-Job-Ad-Junior-Legal-Counsel-v0-2-TVL-approved-JCP-1776949512-5538.pdf": (
        "Junior Legal Counsel",
        "Lausanne, Switzerland",
    ),
}
COMMITTEE_URL = (
    "https://isu-d8g8b4b7ece7aphs.a03.azurefd.net/isudamcontainer/"
    "CMS/jobtenders/pdf/Sports-Medicine-Athlete-Health-Committee-1778062683-6153.pdf"
)


def _csv_row(filename: str, key: str, value: str) -> dict[str, str]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def _board(board_slug: str) -> tuple[dict, dict]:
    row = _csv_row("boards.csv", "board_slug", board_slug)
    return (
        {
            "board_url": row["board_url"],
            "metadata": json.loads(row["monitor_config"]),
        },
        json.loads(row["scraper_config"]),
    )


def _staff_pdf_text(title: str, location: str) -> str:
    # Preserves the heading order used by all four first-party PDFs linked in
    # the 2026-06-10 archived careers page.
    return f"""
Chemin de Brillancourt 4, 1006 Lausanne 1
{title}
Location: {location}
Reports to: ISU Management
Contract Type: Permanent
About Us
Founded in 1892, the International Skating Union (ISU) is the oldest
international winter sports Federation.
The Role
"""


def _fake_reader(stream) -> SimpleNamespace:
    text = stream.read().removeprefix(b"%PDF ").decode()
    return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])


async def test_lucca_live_zero_layout_is_authoritative() -> None:
    board, _ = _board(LUCCA_BOARD_SLUG)
    # Live Lucca Switzerland zero-board layout observed 2026-08-24. ISU uses
    # the same English provider template and marker.
    html = """
    <html><body class="jobBoard"><div class="jobBoard-offers" id="jobBoardOffers">
      <p class="jobBoard-offers-empty">There are no job vacancies at the moment.</p>
    </div></body></html>
    """

    with patch(EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(board, AsyncMock())

    assert result == []


async def test_current_migrated_careers_layout_is_an_explicit_pdf_zero() -> None:
    board, _ = _board(CAREERS_BOARD_SLUG)
    # Current first-party layout: the Job Vacancies block remains present but
    # points only to Lucca. The separate Lucca board owns those identities.
    html = f"""
    <section class="jobfold"><div class="container"><div>
      <div class="blockbox">
        <h2 class="fluid-text-5xlmain">Job Vacancies</h2>
        <div class="grid">
          <a href="{LUCCA_JOB_URL}">Event Administration Manager</a>
        </div>
      </div>
    </div></div></section>
    """

    with patch(EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(board, AsyncMock())

    assert result == set()


async def test_archived_no_positions_layout_is_authoritative() -> None:
    board, _ = _board(CAREERS_BOARD_SLUG)
    # Exact empty heading structure from the 2026-01-01 and 2026-02-10
    # first-party snapshots.
    html = """
    <section class="jobfold"><div class="container"><div>
      <h2 class="fluid-text-lg3">No positions available at the moment</h2>
    </div></div></section>
    """

    with patch(EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
        result = await dom_discover(board, AsyncMock())

    assert result == set()


async def test_archived_pdf_only_layout_keeps_staff_and_excludes_committee(
    monkeypatch,
) -> None:
    board, _ = _board(CAREERS_BOARD_SLUG)
    # The 2026-06-10 snapshot had four staff PDFs in the first block and a
    # Sports Medicine & Athlete Health Committee appointment in the second.
    # Committee membership is governance service, not employment, so this
    # source deliberately classifies and excludes it.
    staff_links = "".join(
        f'<a href="{PDF_ROOT}/{filename}">{title}</a>'
        for filename, (title, _location) in STAFF_DOCUMENTS.items()
    )
    html = f"""
    <section class="jobfold"><div class="container"><div>
      <div class="blockbox">
        <h2>Job Vacancies</h2>
        <div class="grid">{staff_links}</div>
      </div>
      <div class="blockbox">
        <h2>Committee Vacancies</h2>
        <a href="{COMMITTEE_URL}">
          Sports Medicine Athlete Health Committee Committee Vacancy
        </a>
      </div>
    </div></div></section>
    """
    text_by_url = {
        f"{PDF_ROOT}/{filename}": _staff_pdf_text(title, location)
        for filename, (title, location) in STAFF_DOCUMENTS.items()
    }
    text_by_url[COMMITTEE_URL] = """
    Sports Medicine & Athlete Health Committee
    The Committee shall be the competent advisory body for all medical matters.
    Expectations of the Committee
    """
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader)

    def handler(request: httpx.Request) -> httpx.Response:
        text = text_by_url[str(request.url)]
        return httpx.Response(200, content=f"%PDF {text}".encode(), request=request)

    with patch(EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(board, client)

    expected_staff_urls = {f"{PDF_ROOT}/{filename}" for filename in STAFF_DOCUMENTS}
    assert isinstance(result, set)
    assert result == expected_staff_urls
    assert COMMITTEE_URL not in result


@pytest.mark.parametrize(
    ("title", "location"),
    list(STAFF_DOCUMENTS.values()),
)
async def test_archived_staff_pdf_layout_extracts_required_fields(
    monkeypatch,
    title: str,
    location: str,
) -> None:
    _, scraper_config = _board(CAREERS_BOARD_SLUG)
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader)

    result = await parse_bytes(
        f"%PDF {_staff_pdf_text(title, location)}".encode(),
        f"{PDF_ROOT}/fixture.pdf",
        scraper_config,
    )

    assert result.title == title
    assert result.locations == [location]


def test_requested_isu_acronym_is_structured_metadata() -> None:
    row = _csv_row("companies.csv", "slug", COMPANY_SLUG)
    extras = json.loads(row["extras"])

    assert extras["alternateName"] == "ISU"
