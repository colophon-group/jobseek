from __future__ import annotations

import httpx
import pytest

from src.core.monitors.wp_job_openings import _origin, can_handle, discover


class TestOrigin:
    def test_normalizes_page_url(self):
        assert _origin("https://Jobs.Example.com/careers/") == "https://jobs.example.com"

    def test_rejects_non_http_url(self):
        assert _origin("mailto:jobs@example.com") is None


class TestCanHandle:
    async def test_detects_valid_empty_board(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/careers/":
                return httpx.Response(
                    200,
                    text=(
                        '<link href="/wp-content/plugins/wp-job-openings/assets/style.css">'
                        '<div class="awsm-job-listings"></div>'
                    ),
                    request=request,
                )
            if request.url.path == "/wp-json/wp/v2/types/awsm_job_openings":
                return httpx.Response(
                    200,
                    json={"slug": "awsm_job_openings", "rest_base": "awsm_job_openings"},
                    request=request,
                )
            if request.url.path == "/wp-json/wp/v2/awsm_job_openings":
                return httpx.Response(
                    200,
                    json=[],
                    headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
                    request=request,
                )
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers/", client)

        assert result == {
            "rest_url": "https://example.com/wp-json/wp/v2/awsm_job_openings",
            "jobs": 0,
        }

    async def test_rejects_page_without_plugin_marker(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text="<html>ordinary careers page</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers/", client)

        assert result is None
        assert calls == 1

    async def test_requires_confirmed_post_type(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/careers/":
                return httpx.Response(200, text="wp-job-openings", request=request)
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers/", client)

        assert result is None


class TestDiscover:
    async def test_returns_empty_set_for_valid_empty_collection(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[],
                headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
                request=request,
            )

        board = {
            "board_url": "https://example.com/careers/",
            "metadata": {"rest_url": "https://example.com/wp-json/wp/v2/awsm_job_openings"},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert result == set()

    async def test_paginates_and_collects_canonical_links(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(
                    200,
                    json=[
                        {"link": "https://example.com/jobs/platform-engineer/"},
                        {"link": "https://example.com/jobs/data-engineer/"},
                    ],
                    headers={"X-WP-Total": "3", "X-WP-TotalPages": "2"},
                    request=request,
                )
            if page == 2:
                return httpx.Response(
                    200,
                    json=[{"link": "https://example.com/jobs/product-manager/"}],
                    request=request,
                )
            return httpx.Response(400, request=request)

        board = {"board_url": "https://example.com/careers/", "metadata": {}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert result == {
            "https://example.com/jobs/platform-engineer/",
            "https://example.com/jobs/data-engineer/",
            "https://example.com/jobs/product-manager/",
        }

    async def test_rejects_non_list_payload(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": []}, request=request)
        )
        board = {"board_url": "https://example.com/careers/", "metadata": {}}
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="did not return a JSON list"):
                await discover(board, client)
