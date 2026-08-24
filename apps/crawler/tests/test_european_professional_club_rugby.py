"""Regression coverage for the EPCR careers board."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.monitors.dom import dom_discover
from src.core.scrapers.pdf import parse_bytes

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


def _pdf_with_text(text: str) -> bytes:
    """Build a real text PDF so regressions exercise pypdf and the EPCR config."""
    import pypdf
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def _literal(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    commands = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for line in text.splitlines():
        commands.extend((f"({_literal(line)}) Tj", "T*"))
    commands.append("ET")

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("latin-1"))
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Contents")] = writer._add_object(stream)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _scraper_config() -> dict:
    row = _csv_row("boards.csv", "board_slug", BOARD_SLUG)
    return json.loads(row["scraper_config"])


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


@pytest.mark.parametrize(
    ("url", "text", "expected_title", "expected_location"),
    [
        (
            "https://d2cx26qpfwuhvu.cloudfront.net/epcr/wp-content/uploads/2018/03/"
            "12220007/Job-Description-Event-Operations-Executive-at-EO-Department.pdf",
            "Job Description for Events & Operations Executive in Events & Operations "
            "(E&O) Department  \n"
            "Company: European Professional Club Rugby\n"
            "Job Title: Events & Operations Executive reporting into the Event & "
            "Operations Manager\n"
            "Location: Lausanne, Switzerland\n"
            "Starting Date: 1st February 2019",
            "Events & Operations Executive",
            "Lausanne, Switzerland",
        ),
        (
            "https://djl5pr6hub29v.cloudfront.net/epcr/wp-content/uploads/2021/07/"
            "20122353/210720_EPCR_Event_and_Operations_Exec._Job_Description_FINAL.pdf",
            "Job Description for Events and Operations Executive in Events and Operations \n"
            "Department\n"
            "Company: European Professional Club Rugby\n"
            "Job Title: Events and Operations Executive reporting to the Head of Events "
            "and Operations\n"
            "Location: Lausanne, Switzerland\n"
            "Starting Date: September 2021",
            "Events and Operations Executive",
            "Lausanne, Switzerland",
        ),
        (
            "https://d2cx26qpfwuhvu.cloudfront.net/epcr/wp-content/uploads/2018/03/"
            "23103711/EPCR-Job-description-MPR-officer-1.pdf",
            "European Professional Club Rugby 2018/19\n"
            "Job advertisement: Media & Public Relations Officer\n"
            "Job information:\n"
            "Department: Communications & PR\n"
            "Employment type: Permanent\n"
            "Location: Lausanne",
            "Media & Public Relations Officer",
            "Lausanne",
        ),
        (
            "https://d2cx26qpfwuhvu.cloudfront.net/epcr/wp-content/uploads/2018/03/"
            "17154321/EPCR-Marketing-and-Commercial-Director-Job-description.pdf",
            "Marketing and Commercial Director | EPCR\n"
            "The Role\n"
            "Job Title: Marketing and Commercial Director\n"
            "Reporting into: CEO\n"
            "Location: Lausanne, Switzerland",
            "Marketing and Commercial Director",
            "Lausanne, Switzerland",
        ),
    ],
)
async def test_historical_official_pdf_formats_extract_bounded_fields(
    url: str,
    text: str,
    expected_title: str,
    expected_location: str,
) -> None:
    result = await parse_bytes(_pdf_with_text(text), url, _scraper_config())

    assert result.title == expected_title
    assert result.locations == [expected_location]


async def test_director_of_communications_pdf_extracts_unlabelled_header_and_prose_location() -> (
    None
):
    text = (
        "European Professional Club Rugby\n"
        "(EPCR)\n"
        "Director of Communications & PR\n"
        "European Professional Club Rugby (EPCR) is the organiser of the Heineken "
        "Champions Cup and European Rugby Challenge Cup.\n"
        "The role will be based at EPCR headquarters in Lausanne.\n"
        "Remuneration: a competitive basic salary with an excellent executive & benefits "
        "package."
    )

    result = await parse_bytes(
        _pdf_with_text(text),
        "https://d2cx26qpfwuhvu.cloudfront.net/epcr/wp-content/uploads/2021/05/"
        "14101315/Advert-EPCR-Director-of-Comms-PR.pdf",
        _scraper_config(),
    )

    assert result.title == "Director of Communications & PR"
    assert result.locations == ["Lausanne"]


async def test_unrecognised_epcr_pdf_title_fails_closed_before_heading_or_url_fallback() -> None:
    text = (
        "European Professional Club Rugby\n"
        "Recruitment information\n"
        "The role will be based at EPCR headquarters in Lausanne."
    )

    with pytest.raises(ValueError, match="title_pattern did not match"):
        await parse_bytes(
            _pdf_with_text(text),
            "https://media-cdn.incrowdsports.com/Director-General.pdf",
            _scraper_config(),
        )


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
