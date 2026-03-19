from __future__ import annotations

import httpx
import pytest

from src.core.monitors.ycombinator import (
    _JOB_HREF_RE,
    _slug_from_url,
    can_handle,
    discover,
)

# ── URL helpers ──────────────────────────────────────────────────────────


class TestSlugFromUrl:
    def test_standard(self):
        assert _slug_from_url("https://www.ycombinator.com/companies/posthog/jobs") == "posthog"

    def test_without_www(self):
        assert _slug_from_url("https://ycombinator.com/companies/posthog/jobs") == "posthog"

    def test_trailing_slash(self):
        assert _slug_from_url("https://www.ycombinator.com/companies/posthog/jobs/") == "posthog"

    def test_with_query(self):
        assert (
            _slug_from_url("https://www.ycombinator.com/companies/posthog/jobs?ref=1") == "posthog"
        )

    def test_non_yc_domain(self):
        assert _slug_from_url("https://example.com/companies/posthog/jobs") is None

    def test_no_jobs_path(self):
        assert _slug_from_url("https://www.ycombinator.com/companies/posthog") is None

    def test_empty(self):
        assert _slug_from_url("") is None


# ── Job href regex ───────────────────────────────────────────────────────


class TestJobHrefRegex:
    def test_standard_href(self):
        html = '<a href="/companies/typewise/jobs/AWjvSXR-full-stack-engineer">'
        matches = _JOB_HREF_RE.findall(html)
        assert len(matches) == 1
        assert matches[0] == ("typewise", "AWjvSXR-full-stack-engineer")

    def test_multiple_jobs(self):
        html = (
            '<a href="/companies/acme/jobs/ABC123-role-one">'
            '<a href="/companies/acme/jobs/DEF456-role-two">'
        )
        matches = _JOB_HREF_RE.findall(html)
        assert len(matches) == 2

    def test_no_match_listing_page(self):
        """The listing page URL itself (no ID- prefix) must not match."""
        html = '<a href="/companies/acme/jobs">'
        assert _JOB_HREF_RE.findall(html) == []

    def test_no_match_company_link(self):
        html = '<a href="/companies/acme">'
        assert _JOB_HREF_RE.findall(html) == []


# ── Discover ─────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_job_urls(self):
        listing_html = """
        <a href="/companies/acme/jobs/ABC12-engineer">Engineer</a>
        <a href="/companies/acme/jobs/DEF34-designer">Designer</a>
        """

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://www.ycombinator.com/companies/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert "https://www.ycombinator.com/companies/acme/jobs/ABC12-engineer" in urls
            assert "https://www.ycombinator.com/companies/acme/jobs/DEF34-designer" in urls

    async def test_empty_board(self):
        def handler(request):
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://www.ycombinator.com/companies/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert urls == set()

    async def test_ignores_cross_company_links(self):
        listing_html = """
        <a href="/companies/acme/jobs/ABC12-engineer">Ours</a>
        <a href="/companies/other/jobs/XYZ99-manager">Theirs</a>
        """

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://www.ycombinator.com/companies/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert len(urls) == 1
            assert all("acme" in u for u in urls)

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive YCombinator"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myco" in str(request.url)
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"slug": "myco"},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_deduplicates(self):
        listing_html = """
        <a href="/companies/acme/jobs/ABC12-engineer">Link 1</a>
        <a href="/companies/acme/jobs/ABC12-engineer">Link 2</a>
        """

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://www.ycombinator.com/companies/acme/jobs",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert len(urls) == 1


# ── Can handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_yc_url_without_client(self):
        result = await can_handle("https://www.ycombinator.com/companies/posthog/jobs")
        assert result is not None
        assert result["slug"] == "posthog"
        assert "jobs" not in result

    async def test_yc_url_with_client_and_jobs(self):
        listing_html = """
        <a href="/companies/posthog/jobs/A1b-role-one">One</a>
        <a href="/companies/posthog/jobs/C2d-role-two">Two</a>
        """

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.ycombinator.com/companies/posthog/jobs", client)
            assert result is not None
            assert result["slug"] == "posthog"
            assert result["jobs"] == 2

    async def test_yc_url_with_client_empty_board(self):
        def handler(request):
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.ycombinator.com/companies/acme/jobs", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 0

    async def test_non_yc_url(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_fetch_failure_returns_none(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.ycombinator.com/companies/broken/jobs", client)
            assert result is None
