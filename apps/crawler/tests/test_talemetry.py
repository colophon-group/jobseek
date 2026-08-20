from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def _json_page(
    *,
    page: int,
    total: int,
    per_page: int,
    jobs: list[tuple[str, str]],
) -> dict:
    return {
        "total_entries": total,
        "per_page": per_page,
        "current_page": page,
        "entries": [
            {
                "id": job_id,
                "talemetry_job_id": job_id,
                "permalink": permalink,
                "title": f"Role {job_id}",
            }
            for job_id, permalink in jobs
        ],
    }


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

    def test_accepts_ttc_portals_viewing_result_range(self):
        parsed = _parse_page(
            _html(
                start=1,
                end=1,
                total=1,
                links=["/jobs/18023449-assembly-and-test-a-2nd-shift"],
            ).replace("Showing 1-1", "Viewing 1-1"),
            "https://parkercareers.ttcportals.com/search/jobs",
        )

        assert parsed.range_start == 1
        assert parsed.range_end == 1
        assert parsed.total_jobs == 1


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
    async def test_jobs_json_transport_paginates_complete_inventory(self):
        seen: list[tuple[str, str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(
                (
                    str(request.url),
                    request.headers["accept"],
                    request.headers["x-requested-with"],
                )
            )
            if request.url.params["page"] == "2":
                payload = _json_page(
                    page=2,
                    total=3,
                    per_page=2,
                    jobs=[("3", "third-role")],
                )
            else:
                payload = _json_page(
                    page=1,
                    total=3,
                    per_page=2,
                    jobs=[("1", "first-role"), ("2", "second-role")],
                )
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(
                {
                    "board_url": "https://careers.example.com/search/jobs",
                    "metadata": {"transport": "jobs_json"},
                },
                client,
            )

        assert urls == {
            "https://careers.example.com/jobs/1-first-role",
            "https://careers.example.com/jobs/2-second-role",
            "https://careers.example.com/jobs/3-third-role",
        }
        assert seen == [
            (
                "https://careers.example.com/search/jobs.json?page=1",
                "application/json",
                "XMLHttpRequest",
            ),
            (
                "https://careers.example.com/search/jobs.json?page=2",
                "application/json",
                "XMLHttpRequest",
            ),
        ]

    async def test_jobs_json_transport_accepts_authoritative_empty_inventory(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_json_page(page=1, total=0, per_page=25, jobs=[]),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(
                {
                    "board_url": "https://careers.example.com/search/jobs",
                    "metadata": {"transport": "jobs_json"},
                },
                client,
            )

        assert urls == set()

    async def test_jobs_json_transport_retries_total_drift_then_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params["page"] == "2":
                return httpx.Response(
                    200,
                    json=_json_page(
                        page=2,
                        total=4,
                        per_page=2,
                        jobs=[("3", "third-role"), ("4", "fourth-role")],
                    ),
                )
            return httpx.Response(
                200,
                json=_json_page(
                    page=1,
                    total=3,
                    per_page=2,
                    jobs=[("1", "first-role"), ("2", "second-role")],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch("src.core.monitors.talemetry.asyncio.sleep", new=AsyncMock()),
                pytest.raises(ValueError, match="jobs JSON total changed"),
            ):
                await discover(
                    {
                        "board_url": "https://careers.example.com/search/jobs",
                        "metadata": {"transport": "jobs_json"},
                    },
                    client,
                )

    async def test_jobs_json_transport_rejects_conflicting_job_ids(self):
        payload = _json_page(
            page=1,
            total=1,
            per_page=25,
            jobs=[("1", "first-role")],
        )
        payload["entries"][0]["talemetry_job_id"] = "2"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch("src.core.monitors.talemetry.asyncio.sleep", new=AsyncMock()),
                pytest.raises(ValueError, match="conflicting talemetry_job_id"),
            ):
                await discover(
                    {
                        "board_url": "https://careers.example.com/search/jobs",
                        "metadata": {"transport": "jobs_json"},
                    },
                    client,
                )

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
            with (
                patch("src.core.monitors.talemetry.asyncio.sleep", new=AsyncMock()),
                pytest.raises(ValueError, match="total changed"),
            ):
                await discover(
                    {"board_url": "https://careers.example.com/search/jobs", "metadata": {}},
                    client,
                )

    async def test_retries_repeated_page_snapshot_and_recovers(self):
        first_page_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal first_page_calls
            if request.url.params.get("page") != "2":
                first_page_calls += 1
                return httpx.Response(
                    200,
                    text=_html(
                        start=1,
                        end=2,
                        total=3,
                        links=["/jobs/1-first", "/jobs/2-second"],
                    ),
                )
            links = ["/jobs/2-second"] if first_page_calls == 1 else ["/jobs/3-third"]
            return httpx.Response(200, text=_html(start=3, end=3, total=3, links=links))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.core.monitors.talemetry.asyncio.sleep", new=AsyncMock()) as sleep:
                urls = await discover(
                    {"board_url": "https://careers.example.com/search/jobs", "metadata": {}},
                    client,
                )

        assert urls == {
            "https://careers.example.com/jobs/1-first",
            "https://careers.example.com/jobs/2-second",
            "https://careers.example.com/jobs/3-third",
        }
        assert first_page_calls == 2
        sleep.assert_awaited_once()

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
