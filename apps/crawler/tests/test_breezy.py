from __future__ import annotations

import httpx
import pytest

from src.core.monitors.breezy import (
    _breezy_portal_from_url,
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


class TestDiscover:
    async def test_returns_urls(self):
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

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "acme.breezy.hr" and path == "/json":
                return httpx.Response(200, json=listing)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.breezy.hr", "metadata": {}}
            urls = await discover(board, client)

        assert isinstance(urls, set)
        assert len(urls) == 2
        assert "https://acme.breezy.hr/p/abc-platform-engineer" in urls
        assert "https://acme.breezy.hr/p/def-support-specialist" in urls

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
