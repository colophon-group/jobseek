from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.prospective import can_handle, discover
from src.workspace._compat import auto_scraper_type
from src.workspace.commands.help import MONITOR_CARDS

BOARD_URL = "https://jobs.example.com/?lang=de"
IDENTITY_CONFIG = {
    "link_texts": ["Apply", "Bewerben"],
    "source_url_allowlist": r"^https://apply\.example\.com/jobs/[0-9a-f-]{36}$",
    "canonical_url_allowlist": r"^https://apply\.example\.com/jobs/[0-9a-f-]{36}$",
    "locale_priority": ["en", "de", "fr", "it"],
    "concurrency": 3,
}


def test_registration_as_rich_monitor_without_scraper():
    assert "prospective" in all_monitor_types()
    assert auto_scraper_type("prospective") == ("skip", None)
    assert "prospective" in MONITOR_CARDS


def _board(*, filters: dict | None = None) -> dict:
    metadata: dict = {
        "medium_id": "1000613",
        "application_identity": IDENTITY_CONFIG,
    }
    if filters is not None:
        metadata["filters"] = filters
    return {"board_url": BOARD_URL, "metadata": metadata}


def _detail(job_id: str, *, locale: str = "en", application_id: str | None = None) -> str:
    application_id = application_id or job_id
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": f"{locale.upper()} role {job_id}",
        "description": f"<p>{locale.upper()} description {job_id}</p>",
        "datePosted": "2026-08-20",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Basel",
                "addressCountry": "Switzerland",
            },
        },
    }
    return (
        f'<html lang="{locale}"><script type="application/ld+json">'
        f"{json.dumps(payload)}</script>"
        f'<a href="https://apply.example.com/jobs/{application_id}">Apply</a></html>'
    )


def _page(
    *,
    offset: int = 0,
    jobs: tuple[str, ...] = (),
    next_offset: int | None = None,
    filter_values: tuple[str, ...] = ("owned", "affiliate"),
    authoritative_empty: bool = False,
) -> str:
    options = "".join(f'<option value="{value}">{value}</option>' for value in filter_values)
    links = "".join(
        f'<a class="job-title" '
        f'href="https://jobs.example.com/offene-stellen/title-{job}/{job}">{job}</a>'
        for job in jobs
    )
    if authoritative_empty:
        links = '<p id="no-results">There are currently no open positions.</p>'
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
        return httpx.Response(
            200,
            text=_page(jobs=("11111111-1111-4111-8111-111111111111",)),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = await can_handle(BOARD_URL, client)

    assert config == {"medium_id": "1000613", "page_size": 10, "urls": 1}


@pytest.mark.asyncio
async def test_discover_posts_allowlisted_filters_and_paginates():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "apply.example.com":
            return httpx.Response(200, text="<html>Application</html>", request=request)
        if request.url.path.startswith("/offene-stellen/job/"):
            return httpx.Response(
                200,
                text=_detail(request.url.path.rsplit("/", 1)[-1]),
                request=request,
            )
        if request.method == "GET":
            html = _page(jobs=("99999999-9999-4999-8999-999999999999",))
        else:
            form = parse_qs(request.content.decode(), keep_blank_values=True)
            assert form["filter_10"] == ["owned"]
            assert form["limit"] == ["10"]
            if form["offset"] == ["0"]:
                html = _page(
                    offset=0,
                    jobs=(
                        "11111111-1111-4111-8111-111111111111",
                        "22222222-2222-4222-8222-222222222222",
                    ),
                    next_offset=10,
                )
            else:
                assert form["offset"] == ["10"]
                html = _page(
                    offset=10,
                    jobs=("33333333-3333-4333-8333-333333333333",),
                )
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(_board(filters={"filter_10": ["owned"]}), client)

    assert {job.url for job in jobs} == {
        "https://apply.example.com/jobs/11111111-1111-4111-8111-111111111111",
        "https://apply.example.com/jobs/22222222-2222-4222-8222-222222222222",
        "https://apply.example.com/jobs/33333333-3333-4333-8333-333333333333",
    }
    listing_requests = [request for request in requests if request.url.path == "/"]
    assert [request.method for request in listing_requests] == ["GET", "POST", "POST"]


@pytest.mark.asyncio
async def test_discover_fails_closed_when_allowlisted_filter_disappears():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_page(
                jobs=("11111111-1111-4111-8111-111111111111",),
                filter_values=("affiliate",),
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unavailable values"):
            await discover(_board(filters={"filter_10": ["owned"]}), client)


@pytest.mark.asyncio
async def test_discover_rejects_cross_origin_job_links():
    html = _page(authoritative_empty=True).replace(
        '<p id="no-results">There are currently no open positions.</p>',
        '<a class="job-title" '
        'href="https://evil.example/offene-stellen/title/'
        '11111111-1111-4111-8111-111111111111">one</a>',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unexpected job URL"):
            await discover(_board(), client)


@pytest.mark.asyncio
async def test_discover_collapses_locale_aliases_to_application_identity() -> None:
    first_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    application_id = "99999999-9999-4999-8999-999999999999"
    first = f"https://jobs.example.com/offene-stellen/german-title/{first_id}"
    second = f"https://jobs.example.com/offene-stellen/english-title/{second_id}"
    html = _page(authoritative_empty=True).replace(
        '<p id="no-results">There are currently no open positions.</p>',
        f'<a class="job-title" href="{first}">German</a>'
        f'<a class="job-title" href="{second}">English</a>',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "apply.example.com":
            return httpx.Response(200, text="<html>Application</html>", request=request)
        if request.url.path.endswith(first_id):
            return httpx.Response(
                200,
                text=_detail(first_id, locale="de", application_id=application_id),
                request=request,
            )
        if request.url.path.endswith(second_id):
            return httpx.Response(
                200,
                text=_detail(second_id, locale="en", application_id=application_id),
                request=request,
            )
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(_board(), client)

    assert len(jobs) == 1
    assert jobs[0].url == f"https://apply.example.com/jobs/{application_id}"
    assert jobs[0].language == "en"
    assert jobs[0].title == f"EN role {second_id}"
    assert set(jobs[0].localizations or {}) == {"de", "en"}


@pytest.mark.asyncio
async def test_discover_rejects_non_provider_job_path() -> None:
    html = _page(authoritative_empty=True).replace(
        '<p id="no-results">There are currently no open positions.</p>',
        '<a class="job-title" href="https://jobs.example.com/offene-stellen/title/not-a-uuid">'
        "bad</a>",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="unexpected job URL"):
            await discover(_board(), client)


@pytest.mark.asyncio
async def test_discover_accepts_only_authoritative_zero_inventory() -> None:
    responses = iter((_page(authoritative_empty=True), _page(authoritative_empty=True)))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=next(responses), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(_board(), client)

    assert jobs == []


@pytest.mark.asyncio
async def test_discover_rejects_unproved_zero_inventory() -> None:
    html = _page(authoritative_empty=True).replace(
        '<p id="no-results">There are currently no open positions.</p>',
        "",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="authoritative empty marker"):
            await discover(_board(), client)


@pytest.mark.asyncio
async def test_discover_rejects_untrusted_application_identity() -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    detail = _detail(job_id).replace(
        f"https://apply.example.com/jobs/{job_id}",
        f"https://evil.example/jobs/{job_id}",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/offene-stellen/job/"):
            return httpx.Response(200, text=detail, request=request)
        return httpx.Response(200, text=_page(jobs=(job_id,)), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="untrusted application URL"):
            await discover(_board(), client)


@pytest.mark.asyncio
async def test_discover_rejects_untrusted_application_redirect_before_request() -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    final_id = "22222222-2222-4222-8222-222222222222"
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "apply.example.com":
            if request.url.path.endswith(final_id):
                return httpx.Response(200, text="<html>Application</html>", request=request)
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/bounce"},
                request=request,
            )
        if request.url.host == "evil.example":
            return httpx.Response(
                302,
                headers={"Location": f"https://apply.example.com/jobs/{final_id}"},
                request=request,
            )
        if request.url.path.startswith("/offene-stellen/job/"):
            return httpx.Response(200, text=_detail(job_id), request=request)
        return httpx.Response(200, text=_page(jobs=(job_id,)), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="redirect left its URL allowlists"):
            await discover(_board(), client)

    assert "evil.example" not in requested_hosts


@pytest.mark.asyncio
async def test_discover_accepts_trusted_application_redirect() -> None:
    job_id = "11111111-1111-4111-8111-111111111111"
    final_id = "22222222-2222-4222-8222-222222222222"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "apply.example.com":
            if request.url.path.endswith(final_id):
                return httpx.Response(200, text="<html>Application</html>", request=request)
            return httpx.Response(
                302,
                headers={"Location": f"https://apply.example.com/jobs/{final_id}"},
                request=request,
            )
        if request.url.path.startswith("/offene-stellen/job/"):
            return httpx.Response(200, text=_detail(job_id), request=request)
        return httpx.Response(200, text=_page(jobs=(job_id,)), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(_board(), client)

    assert [job.url for job in jobs] == [f"https://apply.example.com/jobs/{final_id}"]


@pytest.mark.asyncio
async def test_discover_rejects_duplicate_locale_for_one_application_identity() -> None:
    first_id = "11111111-1111-4111-8111-111111111111"
    second_id = "22222222-2222-4222-8222-222222222222"
    application_id = "99999999-9999-4999-8999-999999999999"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "apply.example.com":
            return httpx.Response(200, text="<html>Application</html>", request=request)
        if request.url.path.startswith("/offene-stellen/job/"):
            job_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                text=_detail(job_id, locale="en", application_id=application_id),
                request=request,
            )
        return httpx.Response(200, text=_page(jobs=(first_id, second_id)), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="same locale"):
            await discover(_board(), client)
