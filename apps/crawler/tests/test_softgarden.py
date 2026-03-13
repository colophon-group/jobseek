from __future__ import annotations

import httpx
import pytest

from src.core.monitors.softgarden import (
    _board_url,
    _extract_job_ids,
    _job_url,
    _slug_from_url,
    can_handle,
    discover,
)

# ── URL helpers ──────────────────────────────────────────────────────────


class TestSlugFromUrl:
    def test_standard(self):
        assert _slug_from_url("https://hapaglloyd.softgarden.io") == "hapaglloyd"

    def test_with_path(self):
        assert _slug_from_url("https://ctseventim.softgarden.io/job/12345") == "ctseventim"

    def test_ignored_slugs(self):
        assert _slug_from_url("https://www.softgarden.io") is None
        assert _slug_from_url("https://api.softgarden.io") is None
        assert _slug_from_url("https://app.softgarden.io") is None
        assert _slug_from_url("https://static.softgarden.io") is None
        assert _slug_from_url("https://cdn.softgarden.io") is None

    def test_non_softgarden(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_empty(self):
        assert _slug_from_url("") is None


class TestBoardUrl:
    def test_basic(self):
        assert _board_url("hapaglloyd") == "https://hapaglloyd.softgarden.io"


class TestJobUrl:
    def test_default_pattern(self):
        url = _job_url("https://hapaglloyd.softgarden.io", 12345)
        assert url == "https://hapaglloyd.softgarden.io/job/12345?l=en"

    def test_custom_pattern(self):
        url = _job_url(
            "https://hapaglloyd.softgarden.io",
            12345,
            "{base}/job/{id}?l=de",
        )
        assert url == "https://hapaglloyd.softgarden.io/job/12345?l=de"


# ── Listing parsing ─────────────────────────────────────────────────────


class TestExtractJobIds:
    def test_standard(self):
        html = "<script>var complete_job_id_list = [111, 222, 333];</script>"
        assert _extract_job_ids(html) == [111, 222, 333]

    def test_with_jobs_selected(self):
        html = "<script>var complete_job_id_list = jobs_selected = [48677018, 53688446];</script>"
        assert _extract_job_ids(html) == [48677018, 53688446]

    def test_empty_array(self):
        html = "<script>var complete_job_id_list = [];</script>"
        assert _extract_job_ids(html) == []

    def test_no_match(self):
        html = "<html><body>No jobs here</body></html>"
        assert _extract_job_ids(html) == []


# ── Discover ─────────────────────────────────────────────────────────────


class TestDiscover:
    async def test_returns_urls(self):
        listing_html = "<script>var complete_job_id_list = [111, 222];</script>"

        def handler(request):
            url = str(request.url)
            if url == "https://acme.softgarden.io":
                return httpx.Response(200, text=listing_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert "https://acme.softgarden.io/job/111?l=en" in urls
            assert "https://acme.softgarden.io/job/222?l=en" in urls

    async def test_empty_ids(self):
        def handler(request):
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme"},
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Softgarden"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "myslug" in str(request.url)
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"slug": "myslug"},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_slug_from_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, text="<html>No jobs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://testco.softgarden.io",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_custom_pattern(self):
        listing_html = "<script>var complete_job_id_list = [999];</script>"

        def handler(request):
            url = str(request.url)
            if url == "https://acme.softgarden.io":
                return httpx.Response(200, text=listing_html)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.softgarden.io",
                "metadata": {"slug": "acme", "job_url_pattern": "{base}/job/{id}?l=de"},
            }
            urls = await discover(board, client)
            assert len(urls) == 1
            assert "https://acme.softgarden.io/job/999?l=de" in urls


# ── Can handle ───────────────────────────────────────────────────────────


class TestCanHandle:
    async def test_softgarden_url_without_client(self):
        result = await can_handle("https://hapaglloyd.softgarden.io")
        assert result is not None
        assert result["slug"] == "hapaglloyd"

    async def test_softgarden_url_with_client(self):
        listing_html = "<script>var complete_job_id_list = [1, 2, 3];</script>"

        def handler(request):
            return httpx.Response(200, text=listing_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://hapaglloyd.softgarden.io", client)
            assert result is not None
            assert result["slug"] == "hapaglloyd"
            assert result["jobs"] == 3

    async def test_html_markers(self):
        page_html = '<html><script src="https://acme.softgarden.io/assets/app.js"></script></html>'
        listing_html = "<script>var complete_job_id_list = [10, 20];</script>"

        def handler(request):
            url = str(request.url)
            if "acme.softgarden.io" in url:
                return httpx.Response(200, text=listing_html)
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "acme"
            assert result["jobs"] == 2

    async def test_no_match(self):
        def handler(request):
            return httpx.Response(200, text="<html>plain page</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None
