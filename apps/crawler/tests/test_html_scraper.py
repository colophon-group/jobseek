from __future__ import annotations

import httpx

from src.core.scrapers import JobContent
from src.core.scrapers.html import _SelectorExtractor, _SimpleSelector, scrape


class TestSimpleSelector:
    def test_tag_match(self):
        sel = _SimpleSelector("h1")
        assert sel.matches("h1", {}) is True

    def test_tag_no_match(self):
        sel = _SimpleSelector("h1")
        assert sel.matches("h2", {}) is False

    def test_class_match(self):
        sel = _SimpleSelector(".job-title")
        assert sel.matches("h1", {"class": "job-title"}) is True

    def test_class_no_match(self):
        sel = _SimpleSelector(".job-title")
        assert sel.matches("h1", {"class": "other"}) is False

    def test_class_among_multiple(self):
        sel = _SimpleSelector(".target")
        assert sel.matches("div", {"class": "foo target bar"}) is True

    def test_id_match(self):
        sel = _SimpleSelector("#main")
        assert sel.matches("div", {"id": "main"}) is True

    def test_id_no_match(self):
        sel = _SimpleSelector("#main")
        assert sel.matches("div", {"id": "other"}) is False

    def test_tag_class_match(self):
        sel = _SimpleSelector("h1.title")
        assert sel.matches("h1", {"class": "title"}) is True

    def test_tag_class_wrong_tag(self):
        sel = _SimpleSelector("h1.title")
        assert sel.matches("h2", {"class": "title"}) is False

    def test_tag_class_wrong_class(self):
        sel = _SimpleSelector("h1.title")
        assert sel.matches("h1", {"class": "other"}) is False

    def test_attribute_presence(self):
        sel = _SimpleSelector("[data-field]")
        assert sel.matches("div", {"data-field": "title"}) is True

    def test_attribute_absence(self):
        sel = _SimpleSelector("[data-field]")
        assert sel.matches("div", {}) is False

    def test_attribute_value_match(self):
        sel = _SimpleSelector("[data-field=title]")
        assert sel.matches("div", {"data-field": "title"}) is True

    def test_attribute_value_no_match(self):
        sel = _SimpleSelector("[data-field=title]")
        assert sel.matches("div", {"data-field": "other"}) is False

    def test_attribute_value_with_quotes(self):
        sel = _SimpleSelector("[data-field='title']")
        assert sel.matches("div", {"data-field": "title"}) is True

    def test_no_class_attr(self):
        sel = _SimpleSelector(".missing")
        assert sel.matches("div", {}) is False


class TestSelectorExtractor:
    def test_extracts_text(self):
        html = '<div><h1 class="title">Software Engineer</h1><span class="loc">NYC</span></div>'
        extractor = _SelectorExtractor({"title": ".title", "location": ".loc"})
        extractor.feed(html)
        assert extractor.results["title"] == "Software Engineer"
        assert extractor.results["location"] == "NYC"

    def test_missing_selector(self):
        html = "<div><h1>Title</h1></div>"
        extractor = _SelectorExtractor({"title": ".missing"})
        extractor.feed(html)
        assert "title" not in extractor.results

    def test_first_match_wins(self):
        html = '<div><h1 class="title">First</h1><h2 class="title">Second</h2></div>'
        extractor = _SelectorExtractor({"title": ".title"})
        extractor.feed(html)
        assert extractor.results["title"] == "First"

    def test_nested_text(self):
        html = '<div class="desc"><p>Hello</p><p>World</p></div>'
        extractor = _SelectorExtractor({"description": ".desc"})
        extractor.feed(html)
        assert "Hello" in extractor.results["description"]
        assert "World" in extractor.results["description"]

    def test_whitespace_collapsed(self):
        html = '<span class="t">  hello   world  </span>'
        extractor = _SelectorExtractor({"title": ".t"})
        extractor.feed(html)
        assert extractor.results["title"] == "hello world"


class TestScrape:
    async def test_extracts_fields(self):
        page_html = """<html><body>
        <h1 class="job-title">Data Engineer</h1>
        <span class="location">Berlin</span>
        <div class="description">Build data pipelines</div>
        </body></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job",
                {"title": ".job-title", "location": ".location", "description": ".description"},
                client,
            )
            assert isinstance(result, JobContent)
            assert result.title == "Data Engineer"
            assert result.locations == ["Berlin"]
            assert result.description == "Build data pipelines"

    async def test_no_selectors_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title is None
            assert result.locations is None

    async def test_employment_type(self):
        page_html = '<html><body><span class="et">Full-time</span></body></html>'

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job",
                {"employment_type": ".et"},
                client,
            )
            assert result.employment_type == "Full-time"

    async def test_skips_non_string_config(self):
        page_html = '<html><body><h1 class="t">Title</h1></body></html>'

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://example.com/job",
                {"title": ".t", "bad": 123, "empty": ""},
                client,
            )
            assert result.title == "Title"
