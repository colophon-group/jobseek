from __future__ import annotations

import httpx
import pytest

from src.core.monitors.talemetry import _page_url, _parse_page, can_handle, discover


def _html(
    *,
    start: int,
    end: int,
    total: int,
    links: list[str],
    extra: str = "",
    provider_marker: bool = True,
) -> str:
    items = "".join(
        f'<div class="jobs-section__item"><h2><a href="{href}">Role</a></h2></div>'
        for href in links
    )
    marker = ""
    if provider_marker:
        marker = "<script>window.talemetry = window.talemetry || {};</script>"
    return f"""
    <html><head>{marker}</head><body>
      <div class="jobs-heading">Showing {start}-{end} of {total} results</div>
      <div class="jobs-section__list">{items}</div>
      {extra}
    </body></html>
    """


class TestParsePage:
    def test_extracts_only_same_origin_numeric_job_routes_from_result_list(self):
        parsed = _parse_page(
            _html(
                start=1,
                end=2,
                total=2,
                links=[
                    "/jobs/17192795-sanitation-team-member?tracking=1#details",
                    "https://careers.example.com/jobs/17872728-shipping-clerk",
                    "https://attacker.example/jobs/999999-injected",
                    "/search/jobs?page=2",
                ],
                extra='<a href="/jobs/111111-navigation-role">Outside list</a>',
            ),
            "https://careers.example.com/search/jobs",
        )

        assert parsed.provider_marked is True
        assert parsed.saw_results_list is True
        assert parsed.range_start == 1
        assert parsed.range_end == 2
        assert parsed.total_jobs == 2
        assert parsed.urls == {
            "https://careers.example.com/jobs/17192795-sanitation-team-member",
            "https://careers.example.com/jobs/17872728-shipping-clerk",
        }


class TestPageUrl:
    def test_adds_and_replaces_page_parameter(self):
        assert _page_url("https://careers.example.com/search/jobs", 2) == (
            "https://careers.example.com/search/jobs?page=2"
        )
        assert _page_url("https://careers.example.com/search/jobs?q=a&page=2#top", 3) == (
            "https://careers.example.com/search/jobs?q=a&page=3"
        )


class TestCanHandle:
    async def test_detects_talemetry_listing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_html(
                    start=1,
                    end=2,
                    total=3,
                    links=["/jobs/1-first", "/jobs/2-second"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.example.com/search/jobs", client)

        assert result == {"urls": 2, "jobs": 3, "pages": 2}

    async def test_accepts_authoritative_empty_listing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_html(start=0, end=0, total=0, links=[]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.example.com/search/jobs", client)

        assert result == {"urls": 0, "jobs": 0, "pages": 1}

    @pytest.mark.parametrize(
        "html",
        [
            _html(start=1, end=1, total=1, links=["/jobs/1-role"], provider_marker=False),
            _html(start=1, end=2, total=2, links=["/jobs/1-role"]),
            "<script>window.talemetry = {};</script>",
        ],
    )
    async def test_rejects_incomplete_or_unmarked_pages(self, html: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://careers.example.com/search/jobs", client) is None


class TestDiscover:
    async def test_paginates_complete_inventory(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            page = request.url.params.get("page")
            if page == "2":
                return httpx.Response(
                    200,
                    text=_html(start=3, end=3, total=3, links=["/jobs/3-third"]),
                )
            return httpx.Response(
                200,
                text=_html(
                    start=1,
                    end=2,
                    total=3,
                    links=["/jobs/1-first", "/jobs/2-second"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(
                {"board_url": "https://careers.example.com/search/jobs", "metadata": {}},
                client,
            )

        assert urls == {
            "https://careers.example.com/jobs/1-first",
            "https://careers.example.com/jobs/2-second",
            "https://careers.example.com/jobs/3-third",
        }
        assert seen == [
            "https://careers.example.com/search/jobs",
            "https://careers.example.com/search/jobs?page=2",
        ]

    async def test_later_page_failure_does_not_return_partial_inventory(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page") == "2":
                return httpx.Response(404)
            return httpx.Response(
                200,
                text=_html(
                    start=1,
                    end=2,
                    total=3,
                    links=["/jobs/1-first", "/jobs/2-second"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="page 2 returned no content"):
                await discover(
                    {"board_url": "https://careers.example.com/search/jobs", "metadata": {}},
                    client,
                )

    async def test_count_or_range_drift_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    text=_html(start=3, end=3, total=4, links=["/jobs/3-third"]),
                )
            return httpx.Response(
                200,
                text=_html(
                    start=1,
                    end=2,
                    total=3,
                    links=["/jobs/1-first", "/jobs/2-second"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="total changed"):
                await discover(
                    {"board_url": "https://careers.example.com/search/jobs", "metadata": {}},
                    client,
                )

    async def test_max_pages_guard_fails_instead_of_returning_a_truncated_set(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_html(
                    start=1,
                    end=2,
                    total=3,
                    links=["/jobs/1-first", "/jobs/2-second"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="above max_pages=1"):
                await discover(
                    {
                        "board_url": "https://careers.example.com/search/jobs",
                        "metadata": {"max_pages": 1},
                    },
                    client,
                )
