from __future__ import annotations

import json

import httpx
import pytest

from src.core.scrapers.onlyfy import (
    _candidate_url,
    _listing_url,
    _location_from_listing,
    can_handle,
    parse_html,
    scrape,
)

ONLYFY_SHELL = """
<html>
  <head>
    <title>Senior Test Engineer (m/w/d)</title>
    <meta name="description"
          content="Senior Test Engineer (m/w/d)Standort: HofZeitpunkt: As of now" />
    <link rel="canonical" href="https://example.onlyfy.jobs/en/job/abc123" />
  </head>
  <body><a href="/candidate/job/print/abc123">Print</a></body>
</html>
"""


ONLYFY_CANDIDATE = """
<html><body>
  <div class="job-ad-component">
    <div class="text-element">
      <h1 class="text-element-header_text">Senior Test Engineer (m/w/d)</h1>
    </div>
    <div class="text-element">
      <p class="text-element-body_text">
        <strong>Standort</strong>: Hof<br><strong>Zeitpunkt</strong>: As of now
      </p>
    </div>
    <div class="text-element">
      <ul>
        <li class="text-element-body_text">Plan and automate regression tests.</li>
        <li class="text-element-body_text">Work closely with product engineering.</li>
      </ul>
    </div>
    <div class="text-element">
      <p class="text-element-body_text">Flexible working hours and mobile work.</p>
    </div>
  </div>
</body></html>
"""


def _rsc_listing_html() -> str:
    payload = json.dumps(
        [
            "$",
            "$L1",
            None,
            {
                "jobsData": {
                    "data": [
                        {
                            "jobAdUrl": "https://example.onlyfy.jobs/job/abc123",
                            "cityName": "Hof",
                        }
                    ]
                }
            },
        ]
    )
    chunk = json.dumps(f"5:{payload}\n")[1:-1]
    return f'<html><script>self.__next_f.push([1,"{chunk}"])</script></html>'


def test_candidate_url_uses_handle_and_route_locale():
    assert _candidate_url("https://example.onlyfy.jobs/de/job/abc123") == (
        "https://example.onlyfy.jobs/job/show/abc123/full?lang=de&mode=candidate"
    )


def test_candidate_url_allows_language_override():
    assert _candidate_url("https://example.onlyfy.jobs/en/job/abc123", "de") == (
        "https://example.onlyfy.jobs/job/show/abc123/full?lang=de&mode=candidate"
    )


def test_listing_url_and_rsc_location_fallback():
    assert _listing_url("https://example.onlyfy.jobs/de/job/abc123") == (
        "https://example.onlyfy.jobs/de"
    )
    assert _location_from_listing(_rsc_listing_html(), "abc123") == ["Hof"]


def test_can_handle_and_parse_shell_metadata():
    assert can_handle([ONLYFY_SHELL]) == {}
    content = parse_html(ONLYFY_SHELL)
    assert content.title == "Senior Test Engineer (m/w/d)"
    assert content.locations == ["Hof"]
    assert content.description is None


def test_parse_candidate_page():
    content = parse_html(ONLYFY_CANDIDATE, {"language": "de"})
    assert content.title == "Senior Test Engineer (m/w/d)"
    assert content.locations == ["Hof"]
    assert content.language == "de"
    assert "Plan and automate regression tests" in content.description
    assert "Flexible working hours" in content.description
    assert "Standort" not in content.description


@pytest.mark.asyncio
async def test_scrape_fetches_server_rendered_candidate_endpoint():
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, text=ONLYFY_CANDIDATE, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://example.onlyfy.jobs/en/job/abc123",
            {},
            client,
        )

    assert requested_urls == [
        "https://example.onlyfy.jobs/job/show/abc123/full?lang=en&mode=candidate"
    ]
    assert content.title == "Senior Test Engineer (m/w/d)"
    assert content.locations == ["Hof"]
    assert content.description


@pytest.mark.asyncio
async def test_scrape_uses_listing_location_when_detail_omits_it():
    candidate_without_location = ONLYFY_CANDIDATE.replace(
        '<p class="text-element-body_text">\n'
        "        <strong>Standort</strong>: Hof<br><strong>Zeitpunkt</strong>: As of now\n"
        "      </p>",
        "",
    )
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        text = _rsc_listing_html() if request.url.path == "/en" else candidate_without_location
        return httpx.Response(200, text=text, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://example.onlyfy.jobs/en/job/abc123",
            {},
            client,
        )

    assert requested_urls == [
        "https://example.onlyfy.jobs/job/show/abc123/full?lang=en&mode=candidate",
        "https://example.onlyfy.jobs/en",
    ]
    assert content.locations == ["Hof"]
