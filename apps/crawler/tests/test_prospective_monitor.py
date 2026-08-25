from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.prospective import can_handle, discover
from src.workspace._compat import auto_scraper_type
from src.workspace.commands.help import MONITOR_CARDS

BOARD_URL = "https://jobs.example.com/?lang=de"


def test_registration_and_jsonld_scraper_default():
    assert "prospective" in all_monitor_types()
    assert auto_scraper_type("prospective") == ("json-ld", None)
    assert "prospective" in MONITOR_CARDS


def _page(
    *,
    offset: int = 0,
    jobs: tuple[str, ...] = (),
    next_offset: int | None = None,
    filter_values: tuple[str, ...] = ("owned", "affiliate"),
) -> str:
    options = "".join(f'<option value="{value}">{value}</option>' for value in filter_values)
    links = "".join(
        f'<a class="job-title" href="https://jobs.example.com/offene-stellen/{job}">{job}</a>'
        for job in jobs
    )
    pagination = f'<a class="page active" onclick="sendPagination({offset})">current</a>'
    if next_offset is not None:
        pagination += f'<a onclick="sendPagination({next_offset}); return false">next</a>'
    return f"""
        <html>
          <link href="/careercenter/1000613/assets/site.css">
          <form id="careercenter-form" method="post">
            <input name="offset" value="{offset}">
            <input name="limit" value="10">
            <input name="lang" value="de">
            <select name="filter_10">{options}</select>
          </form>
          <div id="jobs-list">{links}</div>
          <div id="pagination">{pagination}</div>
        </html>
    """


@pytest.mark.asyncio
async def test_can_handle_recognizes_careercenter_form():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, text=_page(jobs=("one",)), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = await can_handle(BOARD_URL, client)

    assert config == {"medium_id": "1000613", "page_size": 10, "urls": 1}


@pytest.mark.asyncio
async def test_discover_posts_allowlisted_filters_and_paginates():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            html = _page(jobs=("unfiltered",))
        else:
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            assert form["filter_10"] == ["owned"]
            assert form["limit"] == ["10"]
            if form["offset"] == ["0"]:
                html = _page(offset=0, jobs=("one", "two"), next_offset=10)
            else:
                assert form["offset"] == ["10"]
                html = _page(offset=10, jobs=("three",))
        return httpx.Response(200, text=html, request=request)

    board = {
        "board_url": BOARD_URL,
        "metadata": {"medium_id": "1000613", "filters": {"filter_10": ["owned"]}},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await discover(board, client)

    assert urls == {
        "https://jobs.example.com/offene-stellen/one",
        "https://jobs.example.com/offene-stellen/two",
        "https://jobs.example.com/offene-stellen/three",
    }
    assert [request.method for request in requests] == ["GET", "POST", "POST"]


@pytest.mark.asyncio
async def test_discover_fails_closed_when_allowlisted_filter_disappears():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_page(jobs=("one",), filter_values=("affiliate",)),
            request=request,
        )

    board = {
        "board_url": BOARD_URL,
        "metadata": {"medium_id": "1000613", "filters": {"filter_10": ["owned"]}},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unavailable values"):
            await discover(board, client)


@pytest.mark.asyncio
async def test_discover_rejects_cross_origin_job_links():
    html = _page().replace(
        '<div id="jobs-list"></div>',
        '<div id="jobs-list"><a class="job-title" '
        'href="https://evil.example/offene-stellen/one">one</a></div>',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unexpected job URL"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)
