from __future__ import annotations

import httpx

from src.core.scrapers.jsonld import (
    _JsonLdExtractor,
    _find_job_posting,
    _extract_locations,
    _extract_salary,
    _text_or_list,
    _strip_html,
    _parse_posting,
    scrape,
    probe,
)
from src.core.scrapers import JobContent


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
        assert _find_job_posting(data) == data

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
        assert _find_job_posting(data) == data

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
        assert result.valid_through == "2024-12-31"
        assert result.base_salary is not None
        assert result.skills == ["Python", "SQL"]
        assert result.responsibilities == ["Build software"]
        assert result.qualifications == ["CS degree"]

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
        assert result.qualifications == ["Bachelor's degree"]

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
