from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.core.monitors.dom import (
    _filter_unexpired_pdf_urls,
    _validated_unexpired_pdf_config,
)
from src.core.monitors.inline import discover as inline_discover
from src.shared.nextdata import extract_field

DATA = Path(__file__).resolve().parents[1] / "data"


def _board(slug: str) -> dict[str, str]:
    with (DATA / "boards.csv").open(newline="", encoding="utf-8") as source:
        return next(row for row in csv.DictReader(source) if row["board_slug"] == slug)


def test_source_inventory_is_exactly_seven_bounded_first_party_sources():
    expected = {
        "swiss-confederation-australia",
        "swiss-confederation-federal",
        "swiss-confederation-swissnex",
        "swiss-confederation-taiwan",
        "swiss-confederation-thailand",
        "swiss-confederation-united-kingdom",
        "swiss-confederation-united-states",
    }
    with (DATA / "boards.csv").open(newline="", encoding="utf-8") as source:
        rows = [
            row for row in csv.DictReader(source) if row["company_slug"] == "swiss-confederation"
        ]

    assert {row["board_slug"] for row in rows} == expected
    assert len(rows) == len(expected)
    for row in rows:
        assert row["board_url"].startswith("https://")


def test_federal_source_uses_one_locale_and_provider_stable_viewkey_identity():
    config = json.loads(_board("swiss-confederation-federal")["monitor_config"])

    assert config["api_url"] == "https://ohws.prospective.ch/public/v1/medium/1000624/jobs"
    assert config["params"]["lang"] == "de"
    assert config["url_template"].endswith("/{viewkey}")
    assert config["fields"]["metadata.ats_job_id"] == "id"

    item = {
        "attributes": {
            "verwaltungseinheit": ["Federal Department of Foreign Affairs FDFA"],
            "verwaltungseinheit_1083359": ["Directorate of Resources"],
        }
    }
    assert extract_field(item, config["fields"]["metadata.department"]) == (
        "Federal Department of Foreign Affairs FDFA"
    )
    assert extract_field(item, config["fields"]["metadata.office"]) == ("Directorate of Resources")


def test_pdf_sources_fail_closed_on_title_and_currentness_contracts():
    for slug in (
        "swiss-confederation-australia",
        "swiss-confederation-united-kingdom",
        "swiss-confederation-united-states",
    ):
        row = _board(slug)
        monitor = json.loads(row["monitor_config"])
        scraper = json.loads(row["scraper_config"])
        assert monitor["request_headers"] == {
            "User-Agent": "jobseek-crawler (+https://jseek.co/)",
            "Accept": "text/html,application/pdf",
        }
        assert "require_unexpired_pdf" in monitor
        assert scraper["require_title_pattern"] is True
        assert scraper["request_headers"]["Accept"] == "application/pdf"


@pytest.mark.asyncio
async def test_united_states_heterogeneous_deadlines_and_windows(monkeypatch):
    config = _validated_unexpired_pdf_config(
        json.loads(_board("swiss-confederation-united-states")["monitor_config"])[
            "require_unexpired_pdf"
        ]
    )
    assert config is not None
    texts = {
        "https://example.com/finance.pdf": "Apply by 14 July 2026",
        "https://example.com/atlanta.pdf": "Apply no later than August 9, 2026",
        "https://example.com/future-window.pdf": (
            "Recruitment period: October 1–31, 2026\nInternship period: March–August 2027"
        ),
        "https://example.com/open-window.pdf": (
            "Recruitment period: August 1–31, 2026\nInternship period: March–August 2027"
        ),
    }

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 26, tzinfo=tz or UTC)

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    monkeypatch.setattr("src.core.monitors.dom.datetime", FixedDateTime)
    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda stream: type("Reader", (), {"pages": [Page(stream.read()[5:].decode())]})(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"%PDF " + texts[str(request.url)].encode(),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        active = await _filter_unexpired_pdf_urls(set(texts), client, config)

    assert active == {"https://example.com/open-window.pdf"}


@pytest.mark.asyncio
async def test_taiwan_annual_cycle_is_not_kept_after_its_committed_deadline(monkeypatch):
    row = _board("swiss-confederation-taiwan")
    html = """
    <h3>Academic internships</h3>
    <p>The Trade Office of Swiss Industries offers one internship.</p>
    <p>The Federal Department of Foreign Affairs (FDFA) regulary seeks employees.</p>
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 26, tzinfo=tz or UTC)

    monkeypatch.setattr("src.core.monitors.inline.datetime", FixedDateTime)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await inline_discover(
            {"board_url": row["board_url"], "metadata": json.loads(row["monitor_config"])},
            client,
        )

    assert jobs == []


def test_inline_source_titles_are_exact_and_single_locale():
    expected = {
        "swiss-confederation-swissnex": "Finance and Human Resource Specialist",
        "swiss-confederation-taiwan": "Academic internships",
        "swiss-confederation-thailand": "Regional Programme Officer",
    }
    for slug, title in expected.items():
        config = json.loads(_board(slug)["monitor_config"])
        assert config["fetch_contains"] == title
        title_step = next(step for step in config["steps"] if step["field"] == "title")
        assert title_step.get("text") == title
