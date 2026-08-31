from __future__ import annotations

import json

import httpx
import pytest

from src.config import settings
from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError
from src.core.monitors.avature import can_handle, discover, stream
from src.core.scrapers.dom import parse_html as parse_dom_html
from src.redis_queue import delay_for_domain
from src.shared.avature import (
    AvatureBoard,
    avature_board_from_metadata,
    avature_board_from_url,
    avature_pagination_url,
    avature_request_host,
    parse_avature_page,
)
from src.shared.tdm import TDMReservedError
from src.sync import _compute_throttle_key
from src.workers.pipeline import _configured_egress_host
from src.workspace._compat import auto_scraper_type


def _listing(
    *,
    url: str = "https://acme.avature.net/careers/SearchJobs",
    portal_id: str = "4",
    jobs: list[str] | None = None,
    start: int | None = 1,
    total: str = "2",
    next_url: str | None = None,
    page: str = "SearchJobs",
    nested_next: bool = False,
    displayed: int | None = None,
    legend_class: str = "list-controls__text__legend",
) -> str:
    jobs = jobs or []
    count = displayed if displayed is not None else len(jobs)
    end = start + count - 1 if start is not None else None
    legend = ""
    if start is not None:
        legend = (
            f'<div class="{legend_class}" aria-label="{total} results">'
            f"{start}-{end} of {total} results</div>"
        )
    links = "".join(f'<a class="job" href="{job}">Job</a>' for job in jobs)
    if next_url and nested_next:
        next_link = (
            '<li class="list-controls__pagination__item paginationNextLink">'
            f'<a href="{next_url}">Next</a></li>'
        )
    elif next_url:
        next_link = f'<a class="paginationNextLink" href="{next_url}">Next</a>'
    else:
        next_link = ""
    return f"""
        <html><head>
          <meta content="{portal_id}" name="avature.portal.id">
          <meta name="avature.portal.page" content="{page}">
          <meta property="og:url" content="{url}">
        </head><body>{legend}{links}{next_link}</body></html>
    """


@pytest.mark.parametrize(
    ("url", "listing"),
    [
        (
            "https://acme.avature.net/careers/SearchJobs",
            "https://acme.avature.net/careers/SearchJobs",
        ),
        (
            "https://acme.avature.net/careers/JobDetail/Engineer/123",
            "https://acme.avature.net/careers/SearchJobs",
        ),
        (
            "https://acme.avature.net/careers/JobDetail?jobId=123",
            "https://acme.avature.net/careers/SearchJobs",
        ),
        (
            "https://acme.avature.net/careers/FolderDetail/Engineer/123",
            "https://acme.avature.net/careers/SearchJobs",
        ),
        (
            "https://acme.avature.net/en_US/jobs/PipelineDetail?pipelineId=123",
            "https://acme.avature.net/en_US/jobs/SearchJobsMaps",
        ),
    ],
)
def test_board_identity_from_vendor_urls(url: str, listing: str):
    board = avature_board_from_url(url)
    assert board is not None
    assert board.listing_url == listing


@pytest.mark.parametrize(
    "url",
    [
        "http://acme.avature.net/careers/SearchJobs",
        "https://avature.net/careers/SearchJobs",
        "https://acme.avature.net/careers/SearchJobs?keyword=engineer",
        "https://acme.avature.net/careers/JobDetail?jobId=0",
        "https://localhost/careers/SearchJobs",
        "https://127.0.0.1/careers/SearchJobs",
    ],
)
def test_board_identity_rejects_unsafe_or_scoped_urls(url: str):
    assert avature_board_from_url(url, allow_custom_host=True) is None


def test_custom_host_requires_explicit_opt_in_or_metadata():
    url = "https://jobs.example.com/en_US/careers/SearchJobs"
    assert avature_board_from_url(url) is None
    assert avature_board_from_url(url, allow_custom_host=True) is not None
    assert avature_board_from_metadata({"listing_url": url}) == AvatureBoard(
        host="jobs.example.com",
        prefix="/en_US/careers",
    )


def test_configured_listing_controls_scheduler_and_circuit_hosts():
    board_url = "https://www.example.com/careers"
    listing_url = "https://jobs.example.com/en_US/careers/SearchJobs"
    metadata = {"listing_url": listing_url}

    assert avature_request_host(board_url, metadata) == "jobs.example.com"
    assert _compute_throttle_key("avature", board_url, metadata) == "jobs.example.com"
    assert (
        _configured_egress_host(
            {
                "crawler_type": "avature",
                "board_url": board_url,
                "metadata": json.dumps(metadata),
            }
        )
        == "jobs.example.com"
    )


def test_vendor_hosts_use_shared_ats_throttle():
    assert delay_for_domain("acme.avature.net") == settings.throttle_delay_ats
    assert delay_for_domain("avature.net.evil.test") == settings.throttle_delay_default


def test_parse_listing_canonicalizes_supported_detail_variants_and_nested_next():
    page = parse_avature_page(
        _listing(
            jobs=[
                "/careers/JobDetail/Engineer/101?tracking=x",
                "/careers/JobDetail?jobId=102&utm=x",
                "/careers/FolderDetail/Analyst/103",
                "https://other.example/careers/JobDetail/Evil/104",
            ],
            total="3",
            next_url="/careers/SearchJobs/?jobRecordsPerPage=3&jobOffset=3",
            nested_next=True,
            displayed=3,
        ),
        "https://acme.avature.net/careers/SearchJobs",
    )
    assert page is not None
    assert page.portal_id == "4"
    assert page.total == 3
    assert page.total_exact is True
    assert (page.range_start, page.range_end) == (1, 3)
    # Query-form details are accepted only with their sole stable ID query.
    assert set(page.jobs.values()) == {
        "https://acme.avature.net/careers/JobDetail/Engineer/101",
        "https://acme.avature.net/careers/FolderDetail/Analyst/103",
    }
    assert page.next_urls == (
        "https://acme.avature.net/careers/SearchJobs/?jobRecordsPerPage=3&jobOffset=3",
    )


def test_parse_listing_prefers_path_form_for_duplicate_identity():
    page = parse_avature_page(
        _listing(
            jobs=[
                "/careers/JobDetail?jobId=101",
                "/careers/JobDetail/Engineer/101",
            ],
            total="1",
        ),
        "https://acme.avature.net/careers/SearchJobs",
    )
    assert page is not None
    assert page.jobs == {"jobdetail:101": "https://acme.avature.net/careers/JobDetail/Engineer/101"}


def test_parse_listing_recognizes_lower_bound_count():
    page = parse_avature_page(
        _listing(jobs=["/careers/JobDetail/Engineer/101"], total="999+"),
        "https://acme.avature.net/careers/SearchJobs",
    )
    assert page is not None
    assert page.total == 999
    assert page.total_exact is False


def test_parse_listing_accepts_avature_double_escaped_pagination_og_url():
    body = _listing(
        url=(
            "https://acme.avature.net/careers/SearchJobs/?jobRecordsPerPage=1&amp;amp;jobOffset=1"
        ),
        jobs=["/careers/JobDetail/Engineer/102"],
        start=2,
        total="2",
    )
    page = parse_avature_page(
        body,
        "https://acme.avature.net/careers/SearchJobs/?jobRecordsPerPage=1&jobOffset=1",
    )
    assert page is not None
    assert page.board.listing_url == "https://acme.avature.net/careers/SearchJobs"


def test_pagination_url_is_strict_and_canonical():
    board = AvatureBoard("acme.avature.net", "/careers")
    assert avature_pagination_url("/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6", board) == (
        "https://acme.avature.net/careers/SearchJobs/?jobRecordsPerPage=6&jobOffset=6",
        6,
    )
    assert (
        avature_pagination_url(
            "https://evil.example/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6",
            board,
        )
        is None
    )
    assert (
        avature_pagination_url(
            "/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6&keyword=x", board
        )
        is None
    )
    map_board = AvatureBoard("acme.avature.net", "/careers", page="SearchJobsMaps")
    assert avature_pagination_url("/careers/SearchJobsMaps?pipelineOffset=30", map_board) == (
        "https://acme.avature.net/careers/SearchJobsMaps/?pipelineOffset=30",
        30,
    )
    assert avature_pagination_url(
        "/careers/SearchJobsMaps?pipelineRecordsPerPage=10&pipelineOffset=30",
        map_board,
    ) == (
        "https://acme.avature.net/careers/SearchJobsMaps/"
        "?pipelineRecordsPerPage=10&pipelineOffset=30",
        30,
    )


@pytest.mark.asyncio
async def test_discover_follows_explicit_pagination_and_updates_identity():
    first = _listing(
        jobs=[
            "/careers/JobDetail/Engineer/101",
            "/careers/JobDetail?jobId=102",
        ],
        total="3",
        next_url="/careers/SearchJobs/?jobRecordsPerPage=2&jobOffset=2",
    )
    second = _listing(
        jobs=["/careers/FolderDetail/Analyst/103"],
        start=3,
        total="3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("jobOffset") == "2":
            return httpx.Response(200, text=second)
        return httpx.Response(200, text=first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
            client,
        )
    assert isinstance(result, MonitorResult)
    assert result.truncated is False
    assert result.urls == {
        "https://acme.avature.net/careers/JobDetail/Engineer/101",
        "https://acme.avature.net/careers/JobDetail?jobId=102",
        "https://acme.avature.net/careers/FolderDetail/Analyst/103",
    }
    assert result.metadata_updates == {
        "listing_url": "https://acme.avature.net/careers/SearchJobs",
        "portal_id": "4",
    }


@pytest.mark.asyncio
async def test_discover_supports_folder_pagination_parameters():
    first = _listing(
        jobs=["/careers/FolderDetail/Engineer/101"],
        total="2",
        next_url="/careers/SearchJobs/?folderRecordsPerPage=1&folderOffset=1",
    )
    second = _listing(
        jobs=["/careers/FolderDetail/Analyst/102"],
        start=2,
        total="2",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=second if request.url.params else first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
            client,
        )
    assert len(result.urls) == 2
    assert result.truncated is False


@pytest.mark.asyncio
async def test_discover_supports_pipeline_page_size_and_offset_parameters():
    first = _listing(
        url="https://acme.avature.net/careers/SearchJobsMaps",
        page="SearchJobsMaps",
        jobs=["/careers/PipelineDetail/Engineer/101"],
        total="2",
        next_url=("/careers/SearchJobsMaps/?pipelineRecordsPerPage=1&pipelineOffset=1"),
    )
    second = _listing(
        url="https://acme.avature.net/careers/SearchJobsMaps",
        page="SearchJobsMaps",
        jobs=["/careers/PipelineDetail/Analyst/102"],
        start=2,
        total="2",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=second if request.url.params else first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {
                "board_url": "https://acme.avature.net/careers/SearchJobsMaps",
                "metadata": {},
            },
            client,
        )
    assert len(result.urls) == 2
    assert result.truncated is False


@pytest.mark.asyncio
async def test_stream_yields_each_validated_page_for_worker_heartbeats():
    first = _listing(
        jobs=["/careers/JobDetail/Engineer/101"],
        total="2",
        next_url="/careers/SearchJobs/?jobRecordsPerPage=1&jobOffset=1",
    )
    second = _listing(
        jobs=["/careers/JobDetail/Analyst/102"],
        start=2,
        total="2",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=second if request.url.params else first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batches = [
            result
            async for result in stream(
                {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
                client,
            )
        ]
    assert [len(result.urls) for result in batches] == [1, 1]
    assert batches[0].metadata_updates == {
        "listing_url": "https://acme.avature.net/careers/SearchJobs",
        "portal_id": "4",
    }
    assert batches[-1].truncated is False


@pytest.mark.asyncio
async def test_discover_marks_total_drift_partial_without_delisting():
    first = _listing(
        jobs=["/careers/JobDetail/Engineer/101"],
        total="2",
        next_url="/careers/SearchJobs/?jobRecordsPerPage=1&jobOffset=1",
    )
    second = _listing(
        jobs=["/careers/JobDetail/Analyst/102"],
        start=2,
        total="3",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=second if request.url.params else first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
            client,
        )
    assert result.urls == {
        "https://acme.avature.net/careers/JobDetail/Engineer/101",
        "https://acme.avature.net/careers/JobDetail/Analyst/102",
    }
    assert result.truncated is True


@pytest.mark.asyncio
async def test_discover_rejects_incomplete_advertised_page():
    body = _listing(
        jobs=["/careers/JobDetail/Engineer/101"],
        total="2",
    ).replace("1-1 of 2", "1-2 of 2")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    ) as client:
        with pytest.raises(ValueError, match="advertised 2 visible jobs"):
            await discover(
                {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
                client,
            )


@pytest.mark.asyncio
async def test_discover_classifies_first_page_404_as_board_gone():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        with pytest.raises(BoardGoneError) as exc_info:
            await discover(
                {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
                client,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_discover_propagates_tdm_reservation():
    body = _listing(jobs=[], start=None, total="0")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=body,
                headers={"TDM-Reservation": "1"},
            )
        )
    ) as client:
        with pytest.raises(TDMReservedError):
            await discover(
                {"board_url": "https://acme.avature.net/careers/SearchJobs", "metadata": {}},
                client,
            )


@pytest.mark.asyncio
async def test_discover_streams_all_map_pages_with_lower_bound_count():
    listing_url = "https://premium.avature.net/en_US/jobs/SearchJobsMaps"
    first_jobs = [f"/en_US/jobs/PipelineDetail?pipelineId={job_id}" for job_id in range(1, 501)]
    second_jobs = [f"/en_US/jobs/PipelineDetail?pipelineId={job_id}" for job_id in range(501, 1001)]
    first = _listing(
        url=listing_url,
        page="SearchJobsMaps",
        jobs=first_jobs,
        total="999+",
        next_url="/en_US/jobs/SearchJobsMaps/?pipelineOffset=500",
        legend_class="pagination__legend",
    )
    second_url = f"{listing_url}/?pipelineOffset=500"
    second = _listing(
        url=second_url,
        page="SearchJobsMaps",
        jobs=second_jobs,
        start=501,
        total="999+",
        legend_class="pagination__legend",
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text=second if request.url.query else first)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batches = [
            batch async for batch in stream({"board_url": listing_url, "metadata": {}}, client)
        ]
    assert requested == [listing_url, second_url]
    assert [len(batch.urls) for batch in batches] == [500, 500]
    assert len(set().union(*(batch.urls for batch in batches))) == 1000
    assert batches[-1].truncated is False


@pytest.mark.asyncio
async def test_can_handle_validates_custom_host_marker_and_count():
    url = "https://jobs.example.com/en_US/careers/SearchJobs"
    body = _listing(
        url=url,
        jobs=["/en_US/careers/JobDetail/Engineer/101"],
        total="1",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    ) as client:
        result = await can_handle(url, client)
    assert result == {"listing_url": url, "portal_id": "4", "jobs": 1}


@pytest.mark.asyncio
async def test_can_handle_resolves_vendor_redirect_to_canonical_portal():
    source = "https://old.avature.net/careers/SearchJobs"
    target = "https://jobs.example.com/careers/SearchJobs"
    body = _listing(
        url=target,
        jobs=["/careers/JobDetail/Engineer/101"],
        total="1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "old.avature.net":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(source, client)
    assert result == {"listing_url": target, "portal_id": "4", "jobs": 1}


@pytest.mark.asyncio
async def test_can_handle_finds_explicitly_linked_portal_without_slug_guessing():
    website = "https://company.example/careers"
    listing_url = "https://acme.avature.net/careers/SearchJobs"
    company_page = f'<a href="{listing_url}">Open roles</a>'
    listing = _listing(
        jobs=["/careers/JobDetail/Engineer/101"],
        total="1",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=company_page if str(request.url) == website else listing)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(website, client)
    assert result == {"listing_url": listing_url, "portal_id": "4", "jobs": 1}


@pytest.mark.asyncio
async def test_can_handle_rejects_markerless_custom_searchjobs_page():
    url = "https://jobs.example.com/careers/SearchJobs"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>not Avature</html>")
        )
    ) as client:
        assert await can_handle(url, client) is None


def test_auto_scraper_reuses_dom_and_extracts_standard_avature_layout():
    preset = auto_scraper_type("avature")
    assert preset is not None
    scraper_type, config = preset
    assert scraper_type == "dom"
    assert config is not None
    content = parse_dom_html(
        """
        <h2 class="banner__title">Senior Engineer</h2>
        <h3>General information</h3>
        <div>Work Location(s)</div><div>Zurich, Switzerland</div>
        <div>Posted Date</div><div>03-Aug-2026</div>
        <div>Working time</div><div>Full time</div>
        <h3>The Opportunity</h3><p>Build resilient systems.</p>
        <div>Apply</div>
        """,
        config,
    )
    assert content.title == "Senior Engineer"
    assert content.locations == ["Zurich, Switzerland"]
    assert content.date_posted == "03-Aug-2026"
    assert content.employment_type == "Full time"
    assert content.description is not None
    assert "Build resilient systems" in content.description
