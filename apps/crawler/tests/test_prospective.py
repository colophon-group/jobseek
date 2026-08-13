from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError
from src.core.monitors.prospective import (
    ProspectiveBoard,
    can_handle,
    discover,
    parse_prospective_page,
    prospective_board_from_metadata,
    prospective_board_from_url,
    prospective_request_host,
)
from src.redis_queue import delay_for_domain
from src.sync import _compute_throttle_key
from src.workers.pipeline import _configured_egress_host


def _listing(
    jobs: list[tuple[str, str]],
    *,
    offset: int = 0,
    limit: int = 2,
    lang: str = "de",
) -> str:
    links = "".join(
        f'<div class="job job-{index}"><a class="job job-{index}" href="{url}">'
        f'<div class="job-title">{title}</div></a></div>'
        for index, (url, title) in enumerate(jobs)
    )
    return f"""
        <html><body>
          <form id="careercenter-form" method="post">
            <input type="hidden" name="offset" value="{offset}">
            <input type="hidden" name="limit" value="{limit}">
            <input type="hidden" name="lang" value="{lang}">
          </form>
          <div id="jobs-list">{links}</div>
          <a onclick="sendPagination(0)">1</a>
          <a onclick="sendPagination({limit})">2</a>
          <a href="/public/v1/careercenter/1000394/jobabo">Job alert</a>
        </body></html>
    """


_JOB_1 = "https://jobs.example.ch/offene-stellen/engineer/11111111-1111-4111-8111-111111111111"
_JOB_2 = "https://jobs.example.ch/offene-stellen/analyst/22222222-2222-4222-8222-222222222222"
_JOB_3 = "https://jobs.example.ch/emplois-vacantes/conseiller/33333333-3333-4333-8333-333333333333"


@pytest.mark.parametrize(
    "url",
    [
        "https://ohws.prospective.ch/public/v1/careercenter/1000394/?lang=de",
        "https://jobs.example.ch/public/v1/careercenter/1000394/?lang=fr",
    ],
)
def test_board_identity_accepts_canonical_and_branded_urls(url: str):
    board = prospective_board_from_url(url)
    assert board is not None
    assert board.tenant == "1000394"


@pytest.mark.parametrize(
    "url",
    [
        "http://ohws.prospective.ch/public/v1/careercenter/1000394/",
        "https://ohws.prospective.ch/public/v2/careercenter/1000394/",
        "https://ohws.prospective.ch/public/v1/careercenter/1000394/?filter_80=1",
        "https://ohws.prospective.ch/public/v1/careercenter/not-a-tenant/",
        "https://user@ohws.prospective.ch/public/v1/careercenter/1000394/",
    ],
)
def test_board_identity_rejects_unsafe_filtered_or_non_v1_urls(url: str):
    assert prospective_board_from_url(url) is None


def test_metadata_builds_canonical_listing_url():
    board = prospective_board_from_metadata({"tenant": "1000394", "lang": "fr"})
    assert board == ProspectiveBoard("1000394", "fr")
    assert board.listing_url == (
        "https://ohws.prospective.ch/public/v1/careercenter/1000394/?lang=fr"
    )


def test_canonical_request_host_controls_throttle_and_circuit_keys():
    board_url = "https://jobs.example.ch/public/v1/careercenter/1000394/?lang=de"
    metadata = {"tenant": "1000394", "lang": "de"}
    assert prospective_request_host(board_url, metadata) == "ohws.prospective.ch"
    assert _compute_throttle_key("prospective", board_url, metadata) == "ohws.prospective.ch"
    assert (
        _configured_egress_host(
            {
                "crawler_type": "prospective",
                "board_url": board_url,
                "metadata": metadata,
            }
        )
        == "ohws.prospective.ch"
    )
    assert delay_for_domain("ohws.prospective.ch") == settings.throttle_delay_ats


def test_parse_listing_keeps_only_stable_job_links():
    html = _listing([(_JOB_1 + "?tracking=x", "Engineer"), (_JOB_2, "Analyst")])
    html = html.replace(
        "</div>\n          <a onclick",
        '<a class="job" href="/job-alert">Alert</a></div>\n          <a onclick',
    )
    page = parse_prospective_page(
        html,
        "https://ohws.prospective.ch/public/v1/careercenter/1000394/?lang=de",
    )
    assert page is not None
    assert page.jobs == (_JOB_1, _JOB_2)
    assert page.offset == 0
    assert page.limit == 2
    assert page.pagination_offsets == (0, 2)


async def test_discover_uses_canonical_host_and_form_post_pagination():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "ohws.prospective.ch"
        if request.method == "GET":
            return httpx.Response(200, text=_listing([(_JOB_1, "One"), (_JOB_2, "Two")]))
        body = parse_qs(request.content.decode())
        assert body == {"offset": ["2"], "limit": ["2"], "lang": ["de"]}
        return httpx.Response(200, text=_listing([(_JOB_3, "Three")], offset=2))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {
                "board_url": ("https://jobs.example.ch/public/v1/careercenter/1000394/?lang=de"),
                "metadata": {"tenant": "1000394", "lang": "de"},
            },
            client,
        )

    assert result == {_JOB_1, _JOB_2, _JOB_3}
    assert [request.method for request in requests] == ["GET", "POST"]


async def test_discover_accepts_verified_empty_board():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=_listing([], limit=10))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await discover(
            {
                "board_url": (
                    "https://ohws.prospective.ch/public/v1/careercenter/1000394/?lang=de"
                ),
                "metadata": {},
            },
            client,
        )
    assert result == set()


async def test_discover_marks_repeated_pagination_as_truncated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_listing([(_JOB_1, "One"), (_JOB_2, "Two")]),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await discover(
            {
                "board_url": (
                    "https://ohws.prospective.ch/public/v1/careercenter/1000394/?lang=de"
                ),
                "metadata": {},
            },
            client,
        )
    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert result.urls == {_JOB_1, _JOB_2}


async def test_discover_raises_board_gone_for_retired_tenant():
    transport = httpx.MockTransport(lambda _request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BoardGoneError):
            await discover(
                {
                    "board_url": ("https://ohws.prospective.ch/public/v1/careercenter/1000394/"),
                    "metadata": {},
                },
                client,
            )


async def test_can_handle_branded_url_without_loading_blocked_cname():
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return httpx.Response(200, text=_listing([(_JOB_1, "One")], limit=10))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(
            "https://jobs.example.ch/public/v1/careercenter/1000394/?lang=de",
            client,
        )

    assert result == {"tenant": "1000394", "lang": "de", "jobs": 1}
    assert seen_hosts == ["ohws.prospective.ch"]


async def test_can_handle_rejects_page_without_listing_markers():
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text="<html></html>"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (
            await can_handle(
                "https://jobs.example.ch/public/v1/careercenter/1000394/?lang=de",
                client,
            )
            is None
        )
