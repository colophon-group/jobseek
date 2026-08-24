"""Regression coverage for the International Gymnastics Federation source."""

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
FETCH_PATCH = "src.shared.http_retry.fetch_text_page_with_retry"
BOARD_SLUG = "international-gymnastics-federation-jobs"
COMPANY_SLUG = "international-gymnastics-federation"

FIG_HEADER = """
Fédération Internationale de Gymnastique
Avenue de la Gare 12A I Case postale 630
1001 Lausanne I Switzerland
T +41 (0)21 321 55 10 I www.worldgymnastics.sport
"""


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


def _fake_reader(stream) -> SimpleNamespace:
    text = stream.read().removeprefix(b"%PDF ").decode()
    return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])


async def test_authoritative_trailing_empty_state_is_accepted() -> None:
    board, _ = _board()
    html = (
        (" " * 836_000)
        + """
    <div class="container">
      <h4>There are currently no job opportunities available, please check back later</h4>
      <h5>0 entries</h5>
    </div>
    """
    )
    fetch = AsyncMock(return_value=html)

    with patch(FETCH_PATCH, fetch):
        result = await dom_discover(board, AsyncMock())

    assert result == set()
    assert fetch.await_args.kwargs["max_bytes"] == 2 * 1024 * 1024


async def test_historical_mixed_directory_keeps_only_fig_documents(monkeypatch) -> None:
    board, _ = _board()
    base_url = "https://www.gymnastics.sport/publicdir/opportunities/files"
    owned_ids = {216, 217, 218, 219}
    member_ids = {212, 220}
    cards = "".join(
        f"""
        <div class="highlight">
          <div class="highlight__btn"><a class="btn btn-secondary"
            href="/publicdir/opportunities/files/{document_id}.pdf">FILE</a></div>
        </div>
        """
        for document_id in sorted(owned_ids | member_ids)
    )
    html = f'<div class="container">{cards}</div>'
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader)

    def handler(request: httpx.Request) -> httpx.Response:
        document_id = int(request.url.path.rsplit("/", 1)[-1].removesuffix(".pdf"))
        text_by_id = {
            212: "TURN-GYM-UNION SALZBURG – STELLENAUSSCHREIBUNG",
            216: FIG_HEADER + "FIG owned vacancy 216",
            217: FIG_HEADER + "FIG owned vacancy 217",
            218: FIG_HEADER + "FIG owned vacancy 218",
            219: FIG_HEADER + "FIG owned vacancy 219",
            220: "Luxembourg Gymnastics Federation vacancy",
        }
        text = text_by_id[document_id]
        return httpx.Response(200, content=f"%PDF {text}".encode(), request=request)

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(board, client)

    assert result == {f"{base_url}/{document_id}.pdf" for document_id in owned_ids}
    assert result.isdisjoint({f"{base_url}/{document_id}.pdf" for document_id in member_ids})


async def test_unclassified_linked_document_fails_closed(monkeypatch) -> None:
    board, _ = _board()
    html = """
    <div class="container"><div class="highlight"><div class="highlight__btn">
      <a class="btn btn-secondary" href="/publicdir/opportunities/files/999.pdf">FILE</a>
    </div></div></div>
    """
    monkeypatch.setattr("pypdf.PdfReader", _fake_reader)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"%PDF Newly branded federation document",
            request=request,
        )
    )

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="neither include nor exclude"):
                await dom_discover(board, client)


@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        (
            FIG_HEADER
            + "Communications & Media Relations Manager\n"
            + "Full-time | Lausanne\nYour Mission",
            "Communications & Media Relations Manager",
        ),
        (
            FIG_HEADER
            + "Junior IT Generalist\n"
            + "Temporary - Full-time - Starting date : June 1, 2026 latest | Lausanne\n"
            + "Your Mission",
            "Junior IT Generalist",
        ),
        (
            "FÉDÉRATION INTERNATIONALE DE GYMNASTIQUE\n"
            + "AVENUE DE LA GARE 12A, CASE POSTALE 630, 1001 LAUSANNE, SWITZERLAND\n"
            + "www.gymnastics.sport – info@fig-gymnastics.org\n"
            + "The International Gymnastics Federation (FIG) is seeking a\n"
            + "Sports Administrative Assistant\n"
            + "to join its Sports Department at the earliest possible commencing date.",
            "Sports Administrative Assistant",
        ),
        (
            "FÉDÉRATION INTERNATIONALE DE GYMNASTIQUE\n"
            + "AVENUE DE LA GARE 12A, CASE POSTALE 630, 1001 LAUSANNE, SWITZERLAND\n"
            + "www.gymnastics.sport – info@fig-gymnastics.org\n"
            + "The International Gymnastics Federation (FIG) is seeking a\n"
            + "Communications Manager (Institutional)\n"
            + "to join its Communications Department at the earliest possible commencing date.",
            "Communications Manager (Institutional)",
        ),
        (
            "WORLD GYMNASTICS\n"
            + "Platform Engineer\n"
            + "Full-time | Lausanne\n"
            + "Your Mission",
            "Platform Engineer",
        ),
    ],
)
async def test_known_official_pdf_layouts_extract_title(
    monkeypatch,
    text: str,
    expected_title: str,
) -> None:
    _, scraper_config = _board()
    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda _stream: SimpleNamespace(
            pages=[SimpleNamespace(extract_text=lambda: text)],
        ),
    )

    result = await parse_bytes(
        b"%PDF fixture",
        "https://www.gymnastics.sport/publicdir/opportunities/files/fixture.pdf",
        scraper_config,
    )

    assert result.title == expected_title
    assert result.locations and result.locations[0].casefold() == "lausanne"


def test_requested_fig_acronym_and_identity_are_structured_metadata() -> None:
    row = _csv_row("companies.csv", "slug", COMPANY_SLUG)
    extras = json.loads(row["extras"])

    assert extras["alternateName"] == "FIG"
    assert extras["legalName"] == "Fédération Internationale de Gymnastique"
    assert extras["wikidataId"] == "Q379079"
    assert all(value.startswith("https://") for value in extras["sameAs"])
