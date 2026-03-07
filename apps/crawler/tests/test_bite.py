from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.bite import (
    _build_location,
    _extract_hash_from_url,
    _extract_key_from_js,
    _normalize_employment_type,
    _parse_detail,
    _parse_salary,
    can_handle,
    discover,
)

# ── Key extraction ───────────────────────────────────────────────────────


class TestExtractKeyFromJs:
    def test_standard_pattern(self):
        js = 'var r="9d6d3e33a4d7cc7c319d0ccb38cf695f6c3c4172",p=o.createClient({key:r})'
        assert _extract_key_from_js(js) == "9d6d3e33a4d7cc7c319d0ccb38cf695f6c3c4172"

    def test_alternate_variable(self):
        js = 'var s="8c081ef2dfee73595fa1f82847364b15e4b6bb32",b=i.createClient({key:s})'
        assert _extract_key_from_js(js) == "8c081ef2dfee73595fa1f82847364b15e4b6bb32"

    def test_no_key(self):
        js = 'var x = "hello"; console.log(x);'
        assert _extract_key_from_js(js) is None

    def test_hex_without_createclient(self):
        # 40-char hex that's a URL hash, not near createClient
        js = 'var url = "https://example.com/jobposting/7983b60c143458fc1de2673590f864d17ae52c320?ref=homepage"'
        assert _extract_key_from_js(js) is None


# ── Hash extraction ──────────────────────────────────────────────────────


class TestExtractHashFromUrl:
    def test_standard(self):
        url = "https://bewerbung.augustinum-gruppe.de/jobposting/568bad80dab44c490d1190dd36c5801da907e2f70"
        assert _extract_hash_from_url(url) == "568bad80dab44c490d1190dd36c5801da907e2f70"

    def test_with_query(self):
        url = "https://example.com/jobposting/aabbccdd00112233445566778899aabbccddeeff0?ref=site"
        assert _extract_hash_from_url(url) == "aabbccdd00112233445566778899aabbccddeeff0"

    def test_no_hash(self):
        assert _extract_hash_from_url("https://example.com/careers") is None


# ── Location ─────────────────────────────────────────────────────────────


class TestBuildLocation:
    def test_city_and_country(self):
        assert _build_location({"city": "Braunschweig", "country": "de"}) == ["Braunschweig, DE"]

    def test_city_only(self):
        assert _build_location({"city": "Munich"}) == ["Munich"]

    def test_no_city(self):
        assert _build_location({"country": "de"}) is None

    def test_none(self):
        assert _build_location(None) is None

    def test_empty(self):
        assert _build_location({}) is None


# ── Employment type ──────────────────────────────────────────────────────


class TestNormalizeEmploymentType:
    def test_full_time(self):
        assert _normalize_employment_type(["full_time"]) == "full-time"

    def test_part_time(self):
        assert _normalize_employment_type(["part_time"]) == "part-time"

    def test_multiple_picks_first(self):
        assert _normalize_employment_type(["full_time", "part_time"]) == "full-time"

    def test_unknown(self):
        assert _normalize_employment_type(["freelance"]) is None

    def test_none(self):
        assert _normalize_employment_type(None) is None

    def test_empty(self):
        assert _normalize_employment_type([]) is None


# ── Salary ───────────────────────────────────────────────────────────────


class TestParseSalary:
    def test_monthly(self):
        detail = {"baseSalary": {"currency": "EUR", "unitText": "MONTH"}}
        # No min/max → None
        assert _parse_salary(detail) is None

    def test_with_values(self):
        detail = {
            "baseSalary": {
                "currency": "EUR",
                "unitText": "YEAR",
                "minValue": 50000,
                "maxValue": 80000,
            }
        }
        result = _parse_salary(detail)
        assert result == {"currency": "EUR", "min": 50000, "max": 80000, "unit": "year"}

    def test_hourly(self):
        detail = {
            "baseSalary": {
                "currency": "USD",
                "unitText": "HOUR",
                "minValue": 20,
                "maxValue": 30,
            }
        }
        assert _parse_salary(detail)["unit"] == "hour"

    def test_no_currency(self):
        detail = {"baseSalary": {"unitText": "MONTH", "minValue": 3000}}
        assert _parse_salary(detail) is None

    def test_missing(self):
        assert _parse_salary({}) is None
        assert _parse_salary({"baseSalary": None}) is None


# ── Detail parsing ───────────────────────────────────────────────────────


class TestParseDetail:
    def test_full_detail(self):
        detail = {
            "title": "Software Developer (m/f/d)",
            "content": {"html": {"rendered": "<p>Build cool stuff</p>"}},
            "address": {"city": "Munich", "country": "de"},
            "employmentType": ["full_time"],
            "createdAt": "2025-01-15T10:00:00.000Z",
            "baseSalary": {
                "currency": "EUR",
                "unitText": "YEAR",
                "minValue": 60000,
                "maxValue": 90000,
            },
            "identification": "REF-123",
            "employer": {"name": "Acme GmbH"},
            "locale": "de",
        }
        result = _parse_detail(detail, "https://example.com/jobposting/abc123")
        assert result is not None
        assert result.url == "https://example.com/jobposting/abc123"
        assert result.title == "Software Developer (m/f/d)"
        assert result.description == "<p>Build cool stuff</p>"
        assert result.locations == ["Munich, DE"]
        assert result.employment_type == "full-time"
        assert result.date_posted == "2025-01-15"
        assert result.base_salary["currency"] == "EUR"
        assert result.language == "de"
        assert result.metadata["reference"] == "REF-123"
        assert result.metadata["employer"] == "Acme GmbH"

    def test_no_title(self):
        assert _parse_detail({}, "https://example.com/job") is None

    def test_html_string_content(self):
        detail = {
            "title": "Test",
            "content": {"html": "<p>Direct HTML</p>"},
        }
        result = _parse_detail(detail, "https://example.com/job")
        assert result.description == "<p>Direct HTML</p>"

    def test_no_content(self):
        detail = {"title": "Test"}
        result = _parse_detail(detail, "https://example.com/job")
        assert result is not None
        assert result.description is None


# ── Discover ─────────────────────────────────────────────────────────────


def _search_response(postings, total=None):
    if total is None:
        total = len(postings)
    return {
        "fields": {},
        "jobPostings": postings,
        "page": {"offset": 0, "total": total},
    }


def _detail_response(title="Test Job", description="<p>Desc</p>"):
    return {
        "title": title,
        "content": {"html": {"rendered": description}},
        "address": {"city": "Berlin", "country": "de"},
        "createdAt": "2025-06-01T00:00:00Z",
    }


class TestDiscover:
    async def test_returns_jobs(self):
        search_data = _search_response(
            [
                {
                    "url": "https://example.com/jobposting/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0",
                    "title": "Job A",
                },
                {
                    "url": "https://example.com/jobposting/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb0",
                    "title": "Job B",
                },
            ]
        )

        def handler(request):
            url = str(request.url)
            if "postings/search" in url:
                return httpx.Response(200, json=search_data)
            if "/jobposting/" in url and "/json" in url:
                return httpx.Response(200, json=_detail_response())
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"key": "a" * 40},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_results(self):
        def handler(request):
            return httpx.Response(200, json=_search_response([]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"key": "a" * 40},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_key_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="requires a 'key'"):
                await discover(board, client)

    async def test_failed_detail_skipped(self):
        search_data = _search_response(
            [
                {
                    "url": "https://example.com/jobposting/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0",
                },
                {
                    "url": "https://example.com/jobposting/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb0",
                },
            ]
        )

        def handler(request):
            url = str(request.url)
            if "postings/search" in url:
                return httpx.Response(200, json=search_data)
            if "aaaa" in url:
                return httpx.Response(500)
            if "bbbb" in url:
                return httpx.Response(200, json=_detail_response("Good Job"))
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"key": "a" * 40},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Good Job"

    async def test_pagination(self):
        page1 = {
            "fields": {},
            "jobPostings": [
                {
                    "url": "https://example.com/jobposting/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0",
                }
            ],
            "page": {"offset": 0, "total": 2},
        }
        page2 = {
            "fields": {},
            "jobPostings": [
                {
                    "url": "https://example.com/jobposting/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb0",
                }
            ],
            "page": {"offset": 1, "total": 2},
        }

        call_count = {"search": 0}

        def handler(request):
            url = str(request.url)
            if "postings/search" in url:
                call_count["search"] += 1
                body = json.loads(request.content)
                offset = body.get("page", {}).get("offset", 0)
                return httpx.Response(200, json=page1 if offset == 0 else page2)
            if "/jobposting/" in url and "/json" in url:
                return httpx.Response(200, json=_detail_response())
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"key": "a" * 40},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert call_count["search"] == 2


# ── Can handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_no_markers(self):
        def handler(request):
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_widget_with_key(self):
        page_html = (
            '<html><div data-bite-jobs-api-listing="acme:main-listing"></div>'
            '<script src="https://static.b-ite.com/jobs-api/loader-v1/api-loader-v1.min.js"></script>'
            "</html>"
        )
        listing_js = 'var r="abcdef0123456789abcdef0123456789abcdef01",p=o.createClient({key:r})'

        def handler(request):
            url = str(request.url)
            if "cs-assets.b-ite.com" in url:
                return httpx.Response(200, text=listing_js)
            if "postings/search" in url:
                return httpx.Response(
                    200,
                    json={"page": {"total": 5}, "jobPostings": [], "fields": {}},
                )
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["key"] == "abcdef0123456789abcdef0123456789abcdef01"
            assert result["customer"] == "acme"
            assert result["jobs"] == 5

    async def test_marker_without_widget_attr(self):
        page_html = '<html><script src="https://static.b-ite.com/something.js"></script></html>'

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None
