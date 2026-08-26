from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.api_sniffer import _format_url_template
from src.core.monitors.dom import (
    _filter_unexpired_pdf_urls,
    _validated_unexpired_pdf_config,
)
from src.core.monitors.inline import discover as inline_discover
from src.probe_boards import _probe_static_page
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
    assert config["url_template"] == "https://jobs.admin.ch/offene-stellen/job/{viewkey}"
    assert "slug_fields" not in config
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

    original = {"viewkey": "stable-provider-id", "title": "Original title"}
    renamed = {**original, "title": "Renamed title in another locale"}
    assert _format_url_template(original, config["url_template"], None) == (
        _format_url_template(renamed, config["url_template"], None)
    )


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
        active, classified = await _filter_unexpired_pdf_urls(
            set(texts), client, config, return_classified_currentness=True
        )
        active_again, active_deadlines = await _filter_unexpired_pdf_urls(
            set(texts), client, config, return_deadlines=True
        )

    assert active == {"https://example.com/open-window.pdf"}
    assert active_again == active
    assert set(classified) == set(texts)
    assert set(active_deadlines) == active


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

    assert isinstance(jobs, MonitorResult)
    assert jobs.urls == set()
    assert jobs.verified_empty_reason == "all extracted jobs are past their verified deadline"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("slug", "original_title", "renamed_title", "html_template", "expected_identity"),
    [
        (
            "swiss-confederation-swissnex",
            "Finance and Human Resource Specialist",
            "Finance and People Operations Specialist",
            """
            <h3>India</h3>
            <li data-site-id="7"><dl><h4>{title}</h4><p>Manage finance and people.</p>
            <a href="https://swissnex.zohorecruit.eu/jobs/Careers/76605000001244001/old-title?source=CareerSite">Apply</a>
            </dl></li><h3>Boston and New York</h3>
            """,
            "76605000001244001",
        ),
        (
            "swiss-confederation-thailand",
            "Regional Programme Officer",
            "Regional Climate Programme Officer",
            """
            <p id="doc-1k098ljce1"><strong>{title} (100%)</strong></p>
            <p><strong>Duty station: Bangkok, Thailand</strong></p>
            <p>The Regional Programme Officer will advance climate adaptation.</p>
            <p><strong>Application deadline:</strong> Apply by 2 September 2026.</p>
            <p>The Federal Department of Foreign Affairs (FDFA) regulary seeks employees.</p>
            """,
            "1k098ljce1",
        ),
    ],
)
async def test_inline_provider_identity_survives_title_edits(
    slug, original_title, renamed_title, html_template, expected_identity
):
    row = _board(slug)
    config = json.loads(row["monitor_config"])

    async def run(title: str):
        html = html_template.format(title=title)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await inline_discover(
                {"board_url": row["board_url"], "metadata": config},
                client,
            )

    original = await run(original_title)
    renamed = await run(renamed_title)
    assert isinstance(original, list) and isinstance(renamed, list)
    assert len(original) == len(renamed) == 1
    assert original[0].url == renamed[0].url
    assert original[0].url.endswith(f"?_jid={expected_identity}")


@pytest.mark.asyncio
async def test_all_four_live_zero_sources_pass_probe_with_authoritative_proof(monkeypatch):
    australia_pdf = (
        "https://www.eda.admin.ch/content/dam/countries/countries-content/"
        "australia/en/expired-role.pdf"
    )
    us_pdf = (
        "https://www.eda.admin.ch/content/dam/countries/countries-content/"
        "united-states-of-america/en/future-internship.pdf"
    )
    pages = {
        _board("swiss-confederation-australia")["board_url"]: (
            f'<main><a href="{australia_pdf}">Expired vacancy PDF</a></main>'
        ),
        _board("swiss-confederation-united-kingdom")["board_url"]: (
            '<main><p class="font--regular">The Embassy of Switzerland in London '
            "currently has no job vacancies.</p></main>"
        ),
        _board("swiss-confederation-united-states")["board_url"]: (
            f'<main><a href="{us_pdf}">Future recruitment window</a></main>'
        ),
        _board("swiss-confederation-taiwan")["board_url"]: """
            <h3>Academic internships</h3>
            <p>The Trade Office of Swiss Industries offers one internship.</p>
            <p>The Federal Department of Foreign Affairs (FDFA) regulary seeks employees.</p>
        """,
    }
    pdf_text = {
        australia_pdf: "Applications must be submitted by 26 July 2026",
        us_pdf: "Recruitment period: October 1–31, 2026",
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
    monkeypatch.setattr("src.core.monitors.inline.datetime", FixedDateTime)
    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda stream: type("Reader", (), {"pages": [Page(stream.read()[5:].decode())]})(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in pages:
            return httpx.Response(200, text=pages[url], request=request)
        if url in pdf_text:
            return httpx.Response(
                200,
                content=b"%PDF " + pdf_text[url].encode(),
                request=request,
            )
        raise AssertionError(f"unexpected probe request: {url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for slug in (
            "swiss-confederation-australia",
            "swiss-confederation-taiwan",
            "swiss-confederation-united-kingdom",
            "swiss-confederation-united-states",
        ):
            result = await _probe_static_page(_board(slug), client)
            assert result.status == "ok", (slug, result.message)
            assert result.message.startswith("production extractor: 0 jobs")


@pytest.mark.asyncio
async def test_probe_still_rejects_swiss_zero_without_proof():
    row = dict(_board("swiss-confederation-australia"))
    config = json.loads(row["monitor_config"])
    config.pop("require_unexpired_pdf")
    row["monitor_config"] = json.dumps(config)

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text="<main>No classified vacancies</main>",
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _probe_static_page(row, client)

    assert result.status == "fail"
    assert "without an explicit empty-state contract" in result.message
