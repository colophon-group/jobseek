from __future__ import annotations

import httpx

from src.core.scrapers import JobContent
from src.core.scrapers.jsonld import (
    _extract_locations,
    _extract_salary,
    _find_job_posting,
    _JsonLdExtractor,
    _parse_posting,
    _strip_html,
    _text_or_list,
    probe,
    scrape,
)


class TestJsonLdExtractor:
    def test_extracts_single_block(self):
        html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "Engineer"}</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 1
        assert extractor.results[0]["@type"] == "JobPosting"

    def test_extracts_multiple_blocks(self):
        html = """<html><head>
        <script type="application/ld+json">{"@type": "Organization"}</script>
        <script type="application/ld+json">{"@type": "JobPosting"}</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 2

    def test_ignores_non_jsonld_scripts(self):
        html = """<html><head>
        <script type="text/javascript">var x = 1;</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0

    def test_handles_invalid_json(self):
        html = """<html><head>
        <script type="application/ld+json">not valid json</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0

    def test_handles_empty_script(self):
        html = """<html><head>
        <script type="application/ld+json">  </script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0


class TestFindJobPosting:
    def test_direct_match(self):
        data = {"@type": "JobPosting", "title": "Engineer"}
        result = _find_job_posting(data)
        assert result["title"] == "Engineer"

    def test_in_list(self):
        data = [{"@type": "Organization"}, {"@type": "JobPosting", "title": "X"}]
        result = _find_job_posting(data)
        assert result["title"] == "X"

    def test_in_graph(self):
        data = {"@graph": [{"@type": "Organization"}, {"@type": "JobPosting", "title": "Y"}]}
        result = _find_job_posting(data)
        assert result["title"] == "Y"

    def test_type_as_list(self):
        data = {"@type": ["JobPosting", "Thing"], "title": "Z"}
        result = _find_job_posting(data)
        assert result["title"] == "Z"

    def test_not_found_dict(self):
        assert _find_job_posting({"@type": "Organization"}) is None

    def test_not_found_list(self):
        assert _find_job_posting([{"@type": "Organization"}]) is None

    def test_empty_list(self):
        assert _find_job_posting([]) is None

    def test_nested_graph(self):
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebPage"},
                {"@type": "JobPosting", "title": "Nested"},
            ],
        }
        result = _find_job_posting(data)
        assert result["title"] == "Nested"

    def test_pascalcase_keys_normalized(self):
        """CSOD-style PascalCase keys are normalized to camelCase."""
        data = {
            "@type": "JobPosting",
            "Title": "Manager",
            "Description": "A role",
            "DatePosted": "2026-01-01",
            "ValidThrough": "2026-06-01",
            "jobLocation": [
                {
                    "@type": "Place",
                    "Address": {
                        "@type": "PostalAddress",
                        "addressLocality": "Geneva",
                    },
                }
            ],
        }
        result = _find_job_posting(data)
        assert result["title"] == "Manager"
        assert result["description"] == "A role"
        assert result["datePosted"] == "2026-01-01"
        assert result["validThrough"] == "2026-06-01"
        # Nested keys are also normalized
        assert result["jobLocation"][0]["address"]["addressLocality"] == "Geneva"


class TestExtractLocations:
    def test_with_name(self):
        posting = {"jobLocation": {"name": "New York"}}
        assert _extract_locations(posting) == ["New York"]

    def test_with_address(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressLocality": "San Francisco",
                    "addressRegion": "CA",
                    "addressCountry": "US",
                }
            }
        }
        result = _extract_locations(posting)
        assert result == ["San Francisco, CA, US"]

    def test_multiple_locations(self):
        posting = {
            "jobLocation": [
                {"name": "NYC"},
                {"name": "London"},
            ]
        }
        result = _extract_locations(posting)
        assert result == ["NYC", "London"]

    def test_none_returns_none(self):
        assert _extract_locations({}) is None

    def test_country_as_dict(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressCountry": {"name": "United States"},
                }
            }
        }
        result = _extract_locations(posting)
        assert result == ["United States"]

    def test_empty_location_returns_none(self):
        posting = {"jobLocation": {}}
        assert _extract_locations(posting) is None

    def test_non_dict_location_skipped(self):
        posting = {"jobLocation": ["string_location"]}
        assert _extract_locations(posting) is None


class TestExtractSalary:
    def test_range(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}

    def test_single_value(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": 100000,
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 100000, "max": 100000, "unit": None}

    def test_no_salary(self):
        assert _extract_salary({}) is None

    def test_non_dict_salary(self):
        assert _extract_salary({"baseSalary": "competitive"}) is None

    def test_float_value(self):
        posting = {
            "baseSalary": {
                "currency": "EUR",
                "value": 75000.50,
            }
        }
        result = _extract_salary(posting)
        assert result["min"] == 75000.50

    def test_empty_unit_text(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100, "maxValue": 200, "unitText": ""},
            }
        }
        result = _extract_salary(posting)
        assert result["unit"] is None


class TestTextOrList:
    def test_string(self):
        assert _text_or_list("Python") == ["Python"]

    def test_list(self):
        assert _text_or_list(["A", "B"]) == ["A", "B"]

    def test_empty_string(self):
        assert _text_or_list("  ") is None

    def test_none(self):
        assert _text_or_list(None) is None

    def test_empty_list(self):
        assert _text_or_list([]) is None

    def test_list_with_falsy(self):
        assert _text_or_list(["A", "", None]) == ["A"]


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_no_tags(self):
        assert _strip_html("plain text") == "plain text"

    def test_self_closing_tags(self):
        assert _strip_html("Hello<br/>world") == "Helloworld"


class TestParsePosting:
    def test_full_posting(self):
        posting = {
            "@type": "JobPosting",
            "title": "Engineer",
            "description": "Great role",
            "jobLocation": {"name": "NYC"},
            "employmentType": "FULL_TIME",
            "jobLocationType": "TELECOMMUTE",
            "datePosted": "2024-01-01",
            "validThrough": "2024-12-31",
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
            },
            "skills": ["Python", "SQL"],
            "responsibilities": "Build software",
            "qualifications": "CS degree",
        }
        result = _parse_posting(posting)
        assert result.title == "Engineer"
        assert result.description == "Great role"
        assert result.locations == ["NYC"]
        assert result.employment_type == "FULL_TIME"
        assert result.job_location_type == "TELECOMMUTE"
        assert result.date_posted == "2024-01-01"
        assert result.base_salary is not None
        assert result.extras is not None
        assert result.extras["valid_through"] == "2024-12-31"
        assert result.extras["skills"] == ["Python", "SQL"]
        assert result.extras["responsibilities"] == ["Build software"]
        assert result.extras["qualifications"] == ["CS degree"]

    def test_uses_name_fallback(self):
        posting = {"name": "Designer"}
        result = _parse_posting(posting)
        assert result.title == "Designer"

    def test_title_takes_precedence_over_name(self):
        posting = {"title": "Engineer", "name": "Designer"}
        result = _parse_posting(posting)
        assert result.title == "Engineer"

    def test_education_requirements_fallback(self):
        posting = {"educationRequirements": "Bachelor's degree"}
        result = _parse_posting(posting)
        assert result.extras is not None
        assert result.extras["qualifications"] == ["Bachelor's degree"]

    def test_minimal_posting(self):
        result = _parse_posting({})
        assert isinstance(result, JobContent)
        assert result.title is None


class TestScrape:
    async def test_extracts_from_page(self):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Engineer", "description": "Build stuff"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Engineer"
            assert result.description == "Build stuff"

    async def test_no_jsonld_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="<html><body>No JSON-LD</body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title is None

    async def test_multiple_blocks_finds_job_posting(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "Organization", "name": "Acme"}</script>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "Dev"}</script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Dev"

    async def test_graph_format(self):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage"},
            {"@type": "JobPosting", "title": "GraphJob"}
        ]}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "GraphJob"

    async def test_render_uses_playwright(self):
        """When render=true, scrape should use browser rendering instead of HTTP."""
        from unittest.mock import AsyncMock, patch

        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Rendered"}
        </script>
        </head></html>"""

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = page_html
            transport = httpx.MockTransport(lambda r: httpx.Response(500))
            async with httpx.AsyncClient(transport=transport) as client:
                result = await scrape(
                    "https://example.com/job",
                    {"render": True},
                    client,
                    pw="fake_pw",
                )
                assert result.title == "Rendered"
                mock_render.assert_called_once_with("https://example.com/job", {}, pw="fake_pw")

    async def test_render_false_uses_http(self):
        """When render is false/absent, scrape should use static HTTP."""
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Static"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {"render": False}, client)
            assert result.title == "Static"

    async def test_pascalcase_csod_style(self):
        """CSOD-style PascalCase JSON-LD is extracted correctly."""
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@context":"http://schema.org","@type":"JobPosting",
         "Title":"Senior Engineer","Description":"<p>Build things</p>",
         "DatePosted":"2026-01-01","ValidThrough":"2026-06-01",
         "jobLocation":[{"@type":"Place","Address":{"@type":"PostalAddress",
         "addressLocality":"Geneva","addressCountry":"CH"}}],
         "HiringOrganization":{"@type":"Organization","Name":"IATA"}}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Senior Engineer"
            assert result.description == "<p>Build things</p>"
            assert result.locations == ["Geneva, CH"]
            assert result.date_posted == "2026-01-01"


class TestProbe:
    async def test_found(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting"}</script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is True

    async def test_not_found(self):
        def handler(request):
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is False

    async def test_error_returns_false(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is False


class TestFetchRetry403:
    """``_fetch_html`` retries once on 403 to tolerate soft-WAF warmups."""

    async def test_retries_once_on_403_then_succeeds(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(403, text="blocked")
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert calls["n"] == 2
            assert result.title == "T"

    async def test_does_not_retry_on_200(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await scrape("https://example.com/job", {}, client)
            assert calls["n"] == 1

    async def test_does_not_retry_on_410(self):
        """4xx statuses other than 403 should surface immediately so the pipeline
        can distinguish a permanently-gone job from a transient block."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(410, text="gone")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await scrape("https://example.com/job", {}, client)
                raise AssertionError("expected HTTPStatusError")
            except httpx.HTTPStatusError as e:
                assert e.response.status_code == 410
        assert calls["n"] == 1

    async def test_raises_if_retry_also_403(self):
        """A persistent 403 still raises after the single retry."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(403, text="blocked")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await scrape("https://example.com/job", {}, client)
                raise AssertionError("expected HTTPStatusError")
            except httpx.HTTPStatusError as e:
                assert e.response.status_code == 403
        assert calls["n"] == 2

    async def test_retry_carries_challenge_cookies(self):
        """The whole point of retrying on the same client is that challenge
        cookies set by the first response are attached to the retry. This
        pins that invariant — the RTX soft-WAF pattern only recovers if the
        challenge cookie set on the 403 makes it back on the retry."""
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls: list[str] = []  # cookie header captured per call

        def handler(request):
            calls.append(request.headers.get("cookie", ""))
            if len(calls) == 1:
                # First response: 403 + sets a challenge cookie
                resp = httpx.Response(
                    403,
                    text="blocked",
                    headers={"set-cookie": "challenge=solved; Path=/"},
                )
                return resp
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
        assert len(calls) == 2
        # First call has no cookies, second call carries the challenge cookie
        assert calls[0] == ""
        assert "challenge=solved" in calls[1]
        assert result.title == "T"
