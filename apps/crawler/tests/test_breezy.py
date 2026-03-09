from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.breezy import (
    _breezy_portal_from_url,
    _parse_detail,
    _parse_locations,
    _parse_salary_text,
    can_handle,
    discover,
)


class TestPortalDetection:
    def test_breezy_portal_from_url(self):
        assert _breezy_portal_from_url("https://acme.breezy.hr") == "https://acme.breezy.hr"
        assert (
            _breezy_portal_from_url("https://acme.breezy.hr/p/123-role") == "https://acme.breezy.hr"
        )

    def test_ignores_non_portal_hosts(self):
        assert _breezy_portal_from_url("https://api.breezy.hr") is None
        assert _breezy_portal_from_url("https://breezy.hr") is None
        assert _breezy_portal_from_url("https://example.com/careers") is None


class TestParsingHelpers:
    def test_parse_locations_prefers_locations_array(self):
        opening = {
            "locations": [
                {"name": "Berlin, DE"},
                {"city": "Berlin", "country": {"id": "DE"}},
                {"name": "Berlin, DE"},
            ],
            "location": {"name": "Fallback"},
        }
        assert _parse_locations(opening) == ["Berlin, DE"]

    def test_parse_locations_fallback(self):
        opening = {"location": {"city": "Stockholm", "country": {"id": "SE"}}}
        assert _parse_locations(opening) == ["Stockholm, SE"]

    def test_parse_salary_text(self):
        salary = _parse_salary_text("$75.00 - $95.00 / hr")
        assert salary == {"currency": "USD", "min": 75, "max": 95, "unit": "hour"}

    def test_parse_salary_text_k_suffix(self):
        salary = _parse_salary_text("$60k - $90k")
        assert salary == {"currency": "USD", "min": 60000, "max": 90000, "unit": "year"}

    def test_parse_detail_prefers_jsonld(self):
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@context": "https://schema.org",
                "@type": "JobPosting",
                "description": "<p>JSON-LD description</p>",
                "employmentType": "FULL_TIME",
                "datePosted": "2026-03-01",
                "jobLocationType": "TELECOMMUTE",
                "jobLocation": {
                  "@type": "Place",
                  "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "Berlin",
                    "addressRegion": "BE",
                    "addressCountry": "DE"
                  }
                },
                "baseSalary": {
                  "@type": "MonetaryAmount",
                  "currency": "EUR",
                  "value": {
                    "@type": "QuantitativeValue",
                    "unitText": "YEAR",
                    "minValue": 100000,
                    "maxValue": 120000
                  }
                }
              }
            </script>
          </head>
          <body><div class="description"><p>HTML fallback</p></div></body>
        </html>
        """
        detail = _parse_detail(html)
        assert detail["description"] == "<p>JSON-LD description</p>"
        assert detail["employment_type"] == "Full-time"
        assert detail["job_location_type"] == "remote"
        assert detail["date_posted"] == "2026-03-01"
        assert detail["locations"] == ["Berlin, BE, DE"]
        assert detail["base_salary"] == {
            "currency": "EUR",
            "min": 100000,
            "max": 120000,
            "unit": "year",
        }

    def test_parse_detail_html_fallback(self):
        html = """
        <html><body>
          <div class="description"><p>Fallback description</p><ul><li>A</li></ul></div>
        </body></html>
        """
        detail = _parse_detail(html)
        assert detail["description"] == "<p>Fallback description</p><ul><li>A</li></ul>"


class TestDiscover:
    async def test_discover_merges_listing_and_detail_fields(self):
        listing = [
            {
                "id": "abc",
                "friendly_id": "abc-platform-engineer",
                "name": "Platform Engineer",
                "url": "https://acme.breezy.hr/p/abc-platform-engineer",
                "published_date": "2026-03-01T10:00:00.000Z",
                "type": {"id": "fullTime", "name": "Full-Time"},
                "location": {"name": "Berlin, DE", "is_remote": False},
                "department": "Engineering",
                "salary": "$100k - $120k",
                "company": {"name": "Acme", "friendly_id": "acme"},
            },
            {
                "id": "def",
                "friendly_id": "def-support-specialist",
                "name": "Support Specialist",
                "url": "https://acme.breezy.hr/p/def-support-specialist",
                "published_date": "2026-03-02T10:00:00.000Z",
                "type": {"id": "partTime", "name": "Part-Time"},
                "location": {"name": "Remote", "is_remote": True},
                "salary": "$30 - $40 / hr",
                "company": {"name": "Acme", "friendly_id": "acme"},
            },
        ]

        detail_jsonld = """
        <html><head><script type="application/ld+json">
          {
            "@context":"https://schema.org",
            "@type":"JobPosting",
            "description":"<p>Build and scale platforms.</p>",
            "employmentType":"FULL_TIME",
            "datePosted":"2026-03-01",
            "baseSalary":{
              "@type":"MonetaryAmount",
              "currency":"USD",
              "value":{
                "@type":"QuantitativeValue",
                "unitText":"YEAR",
                "minValue":100000,
                "maxValue":120000
              }
            }
          }
        </script></head><body><div class="description"><p>Fallback</p></div></body></html>
        """
        detail_html = """
        <html><body>
          <div class="description"><p>Help customers solve issues.</p></div>
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "acme.breezy.hr" and path == "/json":
                return httpx.Response(200, json=listing)
            if host == "acme.breezy.hr" and path == "/p/abc-platform-engineer":
                return httpx.Response(200, text=detail_jsonld)
            if host == "acme.breezy.hr" and path == "/p/def-support-specialist":
                return httpx.Response(200, text=detail_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.breezy.hr", "metadata": {}}
            jobs = await discover(board, client)

        assert len(jobs) == 2
        assert all(isinstance(j, DiscoveredJob) for j in jobs)

        by_url = {job.url: job for job in jobs}
        first = by_url["https://acme.breezy.hr/p/abc-platform-engineer"]
        assert first.title == "Platform Engineer"
        assert first.description == "<p>Build and scale platforms.</p>"
        assert first.employment_type == "Full-time"
        assert first.date_posted == "2026-03-01"
        assert first.base_salary == {
            "currency": "USD",
            "min": 100000,
            "max": 120000,
            "unit": "year",
        }
        assert first.metadata is not None
        assert first.metadata["department"] == "Engineering"
        assert first.metadata["company"] == "Acme"

        second = by_url["https://acme.breezy.hr/p/def-support-specialist"]
        assert second.title == "Support Specialist"
        assert second.description == "<p>Help customers solve issues.</p>"
        assert second.job_location_type == "remote"
        assert second.employment_type == "Part-time"
        assert second.base_salary == {"currency": "USD", "min": 30, "max": 40, "unit": "hour"}

    async def test_discover_requires_portal_derivation(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Breezy portal URL"):
                await discover(board, client)


class TestCanHandle:
    async def test_direct_breezy_url_without_client(self):
        result = await can_handle("https://acme.breezy.hr")
        assert result == {"portal_url": "https://acme.breezy.hr", "slug": "acme"}

    async def test_direct_breezy_url_with_client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "acme.breezy.hr" and request.url.path == "/json":
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.breezy.hr", client)
            assert result == {"portal_url": "https://acme.breezy.hr", "slug": "acme", "jobs": 0}

    async def test_redirect_to_marketing_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "retired.breezy.hr" and path == "/json":
                return httpx.Response(302, headers={"Location": "https://breezy.hr/"})
            if host == "breezy.hr":
                return httpx.Response(200, text="<html>marketing</html>")
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://retired.breezy.hr", client)
            assert result is None

    async def test_detects_embedded_breezy_portal_from_custom_domain(self):
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "example.com" and path == "/careers":
                return httpx.Response(
                    200,
                    text='<html><a href="https://acme.breezy.hr/?">Powered by Breezy</a></html>',
                )
            if host == "acme.breezy.hr" and path == "/json":
                return httpx.Response(200, json=[{"id": "1", "url": "https://acme.breezy.hr/p/1"}])
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["portal_url"] == "https://acme.breezy.hr"
            assert result["slug"] == "acme"
            assert result["jobs"] == 1

    async def test_detects_same_origin_custom_domain_portal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "jobs.example.com" and path == "/careers":
                return httpx.Response(
                    200,
                    text="<html><body class='breezy-portal'>powered by breezy</body></html>",
                )
            if host == "jobs.example.com" and path == "/json":
                return httpx.Response(
                    200,
                    json=[{"id": "1", "url": "https://jobs.example.com/p/1"}],
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.example.com/careers", client)
            assert result == {"portal_url": "https://jobs.example.com", "jobs": 1}

    async def test_non_breezy_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no breezy markers</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/jobs", client)
            assert result is None
