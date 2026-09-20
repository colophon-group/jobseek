from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.pcrecruiter import can_handle, discover
from src.probe_boards import PROBES
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.pcrecruiter import PCRecruiterBoard, pcrecruiter_board_from_url
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

UID = "Staff Financial.npsg"
LISTING_URL = "https://host.pcrecruiter.net/pcrbin/jobboard.aspx?uid=Staff%20Financial.npsg"


def _page(start: int, end: int, total: int, record_ids: list[str]) -> str:
    jobs = "".join(
        (
            '<tr><td class="td_jobtitle"><a '
            'href="/pcrbin/jobboard.aspx?action=detail&amp;'
            f'recordid={record_id}&amp;pcr-id=token">Job {record_id}</a></td></tr>'
        )
        for record_id in record_ids
    )
    return f"""
    <html><body>
      <h1 id="resultcount">{start}-{end} of {total}</h1>
      <table>{jobs}</table>
      <form id="googlePage" action="/pcrbin/jobboard.aspx" method="post">
        <input name="action" value="">
        <input name="showjobs" value="Y">
        <input name="pcr-id" value="provider-token">
        <input name="morecount" value="{end}$${end // 12}">
        <input name="sortorder" value="">
        <input name="unifiedsearch" value="opaque-search-state">
      </form>
    </body></html>
    """


def test_board_identity_and_canonical_job_url():
    board = pcrecruiter_board_from_url(LISTING_URL)

    assert board == PCRecruiterBoard(UID)
    assert board.listing_url == LISTING_URL
    assert board.job_url("123") == (
        "https://host.pcrecruiter.net/pcrbin/jobboard.aspx?"
        "uid=Staff+Financial.npsg&action=detail&recordid=123"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://host.pcrecruiter.net/pcrbin/jobboard.aspx?uid=example",
        "https://evil.example/pcrbin/jobboard.aspx?uid=example",
        "https://host.pcrecruiter.net/pcrbin/jobboard.aspx?uid=example&filter=atlanta",
        "https://host.pcrecruiter.net/pcrbin/jobboard.aspx?uid=example&action=detail",
        "https://host.pcrecruiter.net/pcrbin/jobboard.aspx?uid=../bad",
    ],
)
def test_board_identity_rejects_untrusted_or_filtered_urls(url: str):
    assert pcrecruiter_board_from_url(url) is None


@pytest.mark.asyncio
async def test_discover_posts_provider_form_and_verifies_all_ranges():
    first_ids = [str(number) for number in range(100, 112)]
    last_ids = ["112", "113"]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, text=_page(1, 12, 14, first_ids))
        form = parse_qs(request.content.decode(), keep_blank_values=True)
        assert form["morecount"] == ["12$$1"]
        assert form["pcr-id"] == ["provider-token"]
        assert form["unifiedsearch"] == ["opaque-search-state"]
        return httpx.Response(200, text=_page(13, 14, 14, last_ids))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        urls = await discover({"board_url": LISTING_URL, "metadata": {}}, client)

    assert len(urls) == 14
    assert requests[0].url == LISTING_URL
    assert requests[1].url == "https://host.pcrecruiter.net/pcrbin/jobboard.aspx"
    assert requests[1].method == "POST"
    assert all("pcr-id" not in url for url in urls)


@pytest.mark.asyncio
async def test_discover_fails_closed_when_total_drifts():
    first_ids = [str(number) for number in range(100, 112)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_page(1, 12, 14, first_ids))
        return httpx.Response(200, text=_page(13, 14, 15, ["112", "113"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="advertised total drifted"):
            await discover({"board_url": LISTING_URL, "metadata": {}}, client)


@pytest.mark.asyncio
async def test_can_handle_uses_advertised_count_without_draining_pages():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            text=_page(1, 12, 2666, [str(number) for number in range(100, 112)]),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(LISTING_URL, client)

    assert result == {"uid": UID, "jobs": 2666, "page_size": 12}
    assert requests == 1


@pytest.mark.asyncio
async def test_ci_probe_validates_listing_contract_and_reports_count():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_page(1, 12, 2666, [str(number) for number in range(100, 112)]),
        )

    row = {
        "board_slug": "northpoint-search-group-pcrecruiter",
        "board_url": LISTING_URL,
        "monitor_config": '{"uid": "Staff Financial.npsg"}',
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PROBES["pcrecruiter"](row, client)

    assert result.status == "ok"
    assert result.message == "200, 2666 jobs"


def test_provider_is_wired_into_workspace_and_runtime_registries():
    assert "pcrecruiter" in all_monitor_types()
    assert detect_ats_from_url(LISTING_URL) == "pcrecruiter"
    assert auto_scraper_type("pcrecruiter") == ("json-ld", None)
    assert "pcrecruiter" in MONITOR_CARDS
    assert "pcrecruiter" in _MONITOR_CONFIG_HINTS
    assert "host.pcrecruiter.net" in _KNOWN_ATS_DOMAINS
    assert "pcrecruiter" in PROBES
