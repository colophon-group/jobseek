"""Tests for the eightfold job-detail scraper (JSON-LD + position API fallback)."""

from __future__ import annotations

import httpx
import pytest

from src.core.scrapers.eightfold import (
    _api_url,
    _parse_domain,
    _parse_job_id,
    _parse_position_api,
    scrape,
)


class TestParseJobId:
    def test_numeric_id_with_slug(self):
        assert (
            _parse_job_id(
                "https://citi.eightfold.ai/careers/job/859033176537-esg-controller?domain=citi"
            )
            == "859033176537"
        )

    def test_numeric_id_bare(self):
        assert (
            _parse_job_id("https://citi.eightfold.ai/careers/job/859033176537?domain=citi")
            == "859033176537"
        )

    def test_non_eightfold_url_returns_none(self):
        assert _parse_job_id("https://example.com/careers/job-1234") is None

    def test_url_without_job_id(self):
        assert _parse_job_id("https://citi.eightfold.ai/careers") is None


class TestParseDomain:
    def test_from_query_param(self):
        assert (
            _parse_domain("https://citi.eightfold.ai/careers/job/1?domain=citi.com") == "citi.com"
        )

    def test_from_subdomain_fallback(self):
        assert _parse_domain("https://bayer.eightfold.ai/careers/job/1") == "bayer"

    def test_query_param_wins_over_subdomain(self):
        assert _parse_domain("https://hsbc.eightfold.ai/careers/job/1?domain=hsbc") == "hsbc"

    def test_kering_custom_host(self):
        """Kering uses a branded host; no subdomain fallback but query param works."""
        assert _parse_domain("https://careers.kering.com/careers/job/111?domain=kering") == "kering"

    def test_no_domain_returns_none(self):
        assert _parse_domain("https://careers.kering.com/careers/job/111") is None


class TestApiUrl:
    def test_basic(self):
        assert (
            _api_url("citi.eightfold.ai", "citi.com", "859033176537")
            == "https://citi.eightfold.ai/api/apply/v2/jobs/859033176537?domain=citi.com"
        )


class TestParsePositionApi:
    def test_full_payload(self):
        data = {
            "id": 859033176537,
            "name": "ESG Controller - Assistant Vice President",
            "posting_name": "ESG Controller - Assistant Vice President",
            "location": "MUMBAI, Mahārāshtra, India",
            "locations": ["MUMBAI, Mahārāshtra, India"],
            "job_description": "<p>Responsibilities...</p>",
            # 2026-01-22 00:00 UTC = 1769040000
            "t_create": 1769040000,
            "department": "Finance",
            "business_unit": "Finance [L5]",
            "ats_job_id": "26933261",
            "display_job_id": "26933261",
        }
        content = _parse_position_api(data)
        assert content.title == "ESG Controller - Assistant Vice President"
        assert content.description == "<p>Responsibilities...</p>"
        assert content.locations == ["MUMBAI, Mahārāshtra, India"]
        assert content.date_posted == "2026-01-22"
        assert content.metadata == {
            "ats_job_id": "26933261",
            "display_job_id": "26933261",
            "department": "Finance",
            "business_unit": "Finance [L5]",
        }

    def test_posting_name_preferred_over_name(self):
        """When both present, ``posting_name`` is chosen (matches current API
        ordering — see ``_parse_position_api``)."""
        data = {"name": "canonical", "posting_name": "display"}
        assert _parse_position_api(data).title == "display"

    def test_scalar_location_fallback(self):
        """Missing ``locations`` list → use ``location`` scalar."""
        data = {"location": "Tampa Florida United States"}
        assert _parse_position_api(data).locations == ["Tampa Florida United States"]

    def test_empty_locations_list_falls_back_to_scalar(self):
        data = {"location": "London", "locations": []}
        assert _parse_position_api(data).locations == ["London"]

    def test_no_location_at_all(self):
        assert _parse_position_api({}).locations is None

    def test_ignores_invalid_timestamp(self):
        assert _parse_position_api({"t_create": 0}).date_posted is None
        assert _parse_position_api({"t_create": None}).date_posted is None
        assert _parse_position_api({"t_create": "bogus"}).date_posted is None

    def test_metadata_none_when_empty(self):
        assert _parse_position_api({"name": "X"}).metadata is None


# ── Full scrape flow (HTML + API via MockTransport) ─────────────────

_JSONLD_HTML = """\
<html>
  <head>
    <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "JobPosting",
       "title": "Software Engineer",
       "description": "<p>Build things</p>",
       "jobLocation": {"@type": "Place",
                       "address": {"addressLocality": "Jersey City",
                                   "addressRegion": "NJ",
                                   "addressCountry": "US"}}}
    </script>
  </head>
</html>
"""

_EMPTY_HTML = "<html><head><title>Job Opportunities at Citi</title></head><body></body></html>"


@pytest.mark.asyncio
class TestScrapeFullFlow:
    async def test_jsonld_fast_path_skips_api(self):
        """When JSON-LD parses a JobPosting, the API must NOT be called."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/api/apply/v2/jobs/" in url:
                api_calls.append(url)
                return httpx.Response(500)  # would blow up if reached
            return httpx.Response(200, text=_JSONLD_HTML, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://citi.eightfold.ai/careers/job/123?domain=citi.com",
                {},
                client,
            )
        assert content.title == "Software Engineer"
        assert content.description == "<p>Build things</p>"
        assert api_calls == [], "API must not be called on JSON-LD success"

    async def test_fallback_to_position_api(self):
        """No JSON-LD in HTML → scraper must fetch the position API."""
        api_calls: list[str] = []
        api_payload = {
            "name": "Apps Dev Programmer",
            "posting_name": "Apps Dev Programmer",
            "location": "Tampa Florida United States",
            "locations": ["Tampa Florida United States"],
            "job_description": "<div>Role details</div>",
            "t_create": 1769040000,
            "ats_job_id": "25897562",
            "department": "Technology",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/api/apply/v2/jobs/" in url:
                api_calls.append(url)
                return httpx.Response(200, json=api_payload)
            return httpx.Response(200, text=_EMPTY_HTML, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://citi.eightfold.ai/careers/job/25897562?domain=citi.com",
                {},
                client,
            )
        assert len(api_calls) == 1
        assert "/api/apply/v2/jobs/25897562" in api_calls[0]
        assert "domain=citi.com" in api_calls[0]
        assert content.title == "Apps Dev Programmer"
        assert content.description == "<div>Role details</div>"
        assert content.locations == ["Tampa Florida United States"]
        assert content.date_posted == "2026-01-22"
        assert content.metadata is not None
        assert content.metadata["ats_job_id"] == "25897562"

    async def test_both_paths_fail_gracefully(self):
        """No JSON-LD + API returns 500 → scraper returns empty content, no exception."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "/api/apply/v2/jobs/" in url:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, text=_EMPTY_HTML, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://citi.eightfold.ai/careers/job/999?domain=citi.com",
                {},
                client,
            )
        assert content.title is None
        assert content.description is None

    async def test_html_fetch_error_still_tries_api(self):
        """HTTP error on HTML fetch should not prevent the API fallback."""
        api_called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal api_called
            url = str(request.url)
            if "/api/apply/v2/jobs/" in url:
                api_called = True
                return httpx.Response(
                    200,
                    json={"name": "Backup Title", "job_description": "<p>ok</p>"},
                )
            # HTML fetch fails with a 503
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://citi.eightfold.ai/careers/job/42?domain=citi.com",
                {},
                client,
            )
        assert api_called
        assert content.title == "Backup Title"

    async def test_unparseable_url_skips_api(self):
        """A URL without a job id must not attempt the API call."""
        api_called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal api_called
            if "/api/apply/v2/jobs/" in str(request.url):
                api_called = True
                return httpx.Response(200, json={})
            return httpx.Response(200, text=_EMPTY_HTML)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://citi.eightfold.ai/careers?domain=citi.com",
                {},
                client,
            )
        assert not api_called
        assert content.title is None
