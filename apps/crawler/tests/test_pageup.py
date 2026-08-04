from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from src.config import settings
from src.core.monitors import BoardGoneError, all_monitor_types, api_monitor_types
from src.core.monitors import pageup as monitor
from src.core.scrapers.dom import parse_html as parse_dom_html
from src.probe_boards import PROBES, _probe_pageup
from src.processing.board import _throttle_key
from src.redis_queue import _KNOWN_ATS_DOMAINS, delay_for_domain
from src.shared.pageup import (
    PageUpBoard,
    pageup_board_from_metadata,
    pageup_board_from_url,
    pageup_job_identity,
    pageup_listing_board_from_html,
    pageup_pagination_identity,
)
from src.sync import _compute_throttle_key
from src.workers.pipeline import _configured_egress_host
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

BOARD = PageUpBoard(873, "cw", "en-us")
LISTING_URL = BOARD.listing_url
JOB_1 = f"{LISTING_URL}/job/560566/plumber"
JOB_2 = f"{LISTING_URL}/job/560567/electrician"
JOB_3 = f"{LISTING_URL}/job/560568/carpenter"


def _page(
    jobs: list[tuple[int, str, str]],
    *,
    total: int,
    page: int,
    page_size: int,
    board: PageUpBoard = BOARD,
    duplicate_layout: bool = True,
    source_board: PageUpBoard | None = None,
    next_page: int | None | str = "auto",
) -> str:
    asserted = source_board or board
    source = json.dumps(
        {
            "instId": asserted.instance,
            "sourcePointer": asserted.source_pointer,
            "language": asserted.locale,
            "baseDomain": "https://careers.pageuppeople.com",
            "dynamicTemplate": True,
            "action": "Listing",
        },
        separators=(",", ":"),
    )
    anchors = "".join(
        f'<a class="job-link" href="/{board.instance}/{board.source_pointer}/'
        f'{board.locale}/job/{job_id}/{slug}">{title}</a>'
        for job_id, slug, title in jobs
    )
    if duplicate_layout:
        anchors += anchors
    if next_page == "auto":
        next_page = page + 1 if page * page_size < total else None
    more = ""
    if isinstance(next_page, int):
        remaining = total - page * page_size
        link = (
            f'<a class="more-link button" href="/{board.instance}/'
            f"{board.source_pointer}/{board.locale}/listing/?page={next_page}"
            f'&amp;page-items={page_size}" data-page="{next_page}" '
            f'data-page-items="{page_size}">More Jobs <span class="count">'
            f"{remaining}</span></a>"
        )
        more = link + (link if duplicate_layout else "")
    return (
        f"<html><script>PU.Jobs.source = {source};</script><body>{anchors}{more}"
        f'<p>Search Results: <span class="result-count">{total}</span></p>'
        "</body></html>"
    )


class TestIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            LISTING_URL,
            f"{LISTING_URL}/",
            f"{LISTING_URL}/listing/",
            f"{LISTING_URL}/listing/?page=2&page-items=500",
            JOB_1,
            f"{LISTING_URL}/job/527840/parttime-pool_special-session",
            f"{LISTING_URL}/job/696784/phd-scholarship-c%C3%A9cile-parrish",
        ],
    )
    def test_parses_supported_routes(self, url: str):
        assert pageup_board_from_url(url) == BOARD

    @pytest.mark.parametrize(
        "url",
        [
            LISTING_URL.replace("https://", "http://"),
            "https://user@careers.pageuppeople.com/873/cw/en-us",
            "https://careers.pageuppeople.com:444/873/cw/en-us",
            "https://careers.pageuppeople.com/0/cw/en-us",
            "https://careers.pageuppeople.com/873/cw/en-us/listing/?keyword=data",
            "https://careers.pageuppeople.com/873/cw/en-us/listing/?page=2",
            f"{JOB_1}?source=test",
            "https://evil.example/873/cw/en-us",
        ],
    )
    def test_rejects_untrusted_or_filtered_routes(self, url: str):
        assert pageup_board_from_url(url) is None

    def test_metadata_is_fail_closed(self):
        config = {
            "instance": 873,
            "source_pointer": "cw",
            "locale": "en-us",
            "listing_url": LISTING_URL,
        }
        assert pageup_board_from_metadata(config) == BOARD
        assert pageup_board_from_metadata({**config, "instance": 999}) is None
        assert pageup_board_from_metadata({"instance": 873, "locale": "en-us"}) is None

    def test_job_and_page_identity(self):
        assert pageup_job_identity(JOB_1, BOARD) == (560566, "plumber")
        assert pageup_job_identity(JOB_1, PageUpBoard(999, "cw", "en-us")) is None
        assert pageup_pagination_identity(
            f"{LISTING_URL}/listing/?page=2&page-items=500", BOARD
        ) == (2, 500)

    def test_first_party_marker_is_unique_and_exact(self):
        document = _page([], total=0, page=1, page_size=500)
        assert pageup_listing_board_from_html(document) == BOARD
        assert (
            pageup_listing_board_from_html(
                document
                + _page([], total=0, page=1, page_size=500, board=PageUpBoard(999, "cw", "en"))
            )
            is None
        )


class TestParser:
    def test_collapses_duplicate_layouts_and_preserves_titles(self):
        document = _page(
            [(560566, "plumber", "Plumber"), (560567, "electrician", "Electrician")],
            total=3,
            page=1,
            page_size=2,
        )
        jobs, total, has_next = monitor._parse_listing_page(
            document,
            BOARD.page_url(1, page_size=2),
            BOARD,
            page=1,
            page_size=2,
            expected_total=None,
        )
        assert total == 3
        assert has_next is True
        assert {(job.url, job.title) for job in jobs} == {
            (JOB_1, "Plumber"),
            (JOB_2, "Electrician"),
        }

    def test_authoritative_empty_page(self):
        jobs, total, has_next = monitor._parse_listing_page(
            _page([], total=0, page=1, page_size=20),
            BOARD.page_url(1, page_size=20),
            BOARD,
            page=1,
            page_size=20,
            expected_total=None,
        )
        assert jobs == []
        assert total == 0
        assert has_next is False

    def test_derives_total_from_pageup_remaining_count_without_result_marker(self):
        document = _page(
            [(560566, "plumber", "Plumber"), (560567, "electrician", "Electrician")],
            total=5,
            page=1,
            page_size=2,
        ).replace('<span class="result-count">5</span>', "")
        jobs, total, has_next = monitor._parse_listing_page(
            document,
            BOARD.page_url(1, page_size=2),
            BOARD,
            page=1,
            page_size=2,
            expected_total=None,
        )
        assert len(jobs) == 2
        assert total == 5
        assert has_next is True

    def test_accepts_nonempty_markerless_proxy_but_not_markerless_empty(self):
        nonempty = _page(
            [(560566, "plumber", "Plumber")],
            total=1,
            page=1,
            page_size=20,
        )
        nonempty = nonempty.replace(
            nonempty[nonempty.index("<script>") : nonempty.index("</script>") + 9], ""
        ).replace('<span class="result-count">1</span>', "")
        jobs, total, _ = monitor._parse_listing_page(
            nonempty,
            BOARD.page_url(1, page_size=20),
            BOARD,
            page=1,
            page_size=20,
            expected_total=None,
        )
        assert len(jobs) == total == 1

        empty = _page([], total=0, page=1, page_size=20)
        empty = empty.replace(empty[empty.index("<script>") : empty.index("</script>") + 9], "")
        with pytest.raises(ValueError, match="cannot prove an empty"):
            monitor._parse_listing_page(
                empty,
                BOARD.page_url(1, page_size=20),
                BOARD,
                page=1,
                page_size=20,
                expected_total=None,
            )

    def test_ignores_generic_duplicate_action_titles(self):
        document = _page(
            [(560566, "plumber", "Plumber")],
            total=1,
            page=1,
            page_size=20,
            duplicate_layout=False,
        ).replace(
            "<p>Search Results:",
            f'<a class="job-link" href="{JOB_1}">Apply</a><p>Search Results:',
        )
        jobs, _total, _ = monitor._parse_listing_page(
            document,
            BOARD.page_url(1, page_size=20),
            BOARD,
            page=1,
            page_size=20,
            expected_total=None,
        )
        assert jobs[0].title == "Plumber"

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            (_page([], total=1, page=1, page_size=20), "exposed 0 unique jobs"),
            (
                _page(
                    [(560566, "plumber", "Plumber")],
                    total=1,
                    page=1,
                    page_size=20,
                    source_board=PageUpBoard(999, "cw", "en-us"),
                ),
                "identity",
            ),
            (
                _page(
                    [(560566, "plumber", "Plumber")],
                    total=1,
                    page=1,
                    page_size=20,
                    next_page=2,
                ),
                "next-page",
            ),
        ],
    )
    def test_rejects_incomplete_or_wrong_identity_pages(self, document: str, message: str):
        with pytest.raises(ValueError, match=message):
            monitor._parse_listing_page(
                document,
                BOARD.page_url(1, page_size=20),
                BOARD,
                page=1,
                page_size=20,
                expected_total=None,
            )


class TestMonitor:
    async def test_streams_complete_pagination(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(monitor, "PAGE_SIZE", 2)
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            page = int(parse_qs(request.url.query.decode())["page"][0])
            if page == 1:
                body = _page(
                    [(560566, "plumber", "Plumber"), (560567, "electrician", "Electrician")],
                    total=3,
                    page=1,
                    page_size=2,
                )
            else:
                body = _page(
                    [(560568, "carpenter", "Carpenter")],
                    total=3,
                    page=2,
                    page_size=2,
                )
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await monitor.discover({"board_url": LISTING_URL}, client)
        assert {job.url for job in jobs} == {JOB_1, JOB_2, JOB_3}
        assert requested == [BOARD.page_url(1, page_size=2), BOARD.page_url(2, page_size=2)]

    async def test_overlap_fails_without_partial_completion(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(monitor, "PAGE_SIZE", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(parse_qs(request.url.query.decode())["page"][0])
            body = _page(
                [(560566, "plumber", "Plumber")],
                total=2,
                page=page,
                page_size=1,
            )
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="repeated 1 jobs"):
                await monitor.discover({"board_url": LISTING_URL}, client)

    async def test_count_drift_fails(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(monitor, "PAGE_SIZE", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(parse_qs(request.url.query.decode())["page"][0])
            total = 2 if page == 1 else 3
            job_id = 560565 + page
            return httpx.Response(
                200,
                text=_page(
                    [(job_id, f"job-{page}", f"Job {page}")],
                    total=total,
                    page=page,
                    page_size=1,
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="changed during pagination"):
                await monitor.discover({"board_url": LISTING_URL}, client)

    async def test_first_page_404_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(404, text="missing", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await monitor.discover({"board_url": LISTING_URL}, client)

    async def test_config_mismatch_fails_before_fetch(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text="unused", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match"):
                await monitor.discover(
                    {
                        "board_url": LISTING_URL,
                        "metadata": {
                            "instance": 999,
                            "source_pointer": "cw",
                            "locale": "en-us",
                        },
                    },
                    client,
                )
        assert calls == 0


class TestDetectionAndWorkflow:
    async def test_direct_detection_live_probes_small_page(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                200,
                text=_page(
                    [(560566, "plumber", "Plumber")],
                    total=1,
                    page=1,
                    page_size=monitor.PROBE_PAGE_SIZE,
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await monitor.can_handle(JOB_1, client)
        assert result == {
            "instance": 873,
            "source_pointer": "cw",
            "locale": "en-us",
            "listing_url": LISTING_URL,
            "jobs": 1,
        }
        assert seen == [BOARD.page_url(1, page_size=monitor.PROBE_PAGE_SIZE)]

    async def test_detects_explicit_link_without_guessing(self):
        marketing = "https://example.com/careers"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == marketing:
                return httpx.Response(
                    200,
                    text=f'<a href="{LISTING_URL}">Careers</a>',
                    request=request,
                )
            return httpx.Response(
                200,
                text=_page([], total=0, page=1, page_size=monitor.PROBE_PAGE_SIZE),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await monitor.can_handle(marketing, client)
        assert result and result["listing_url"] == LISTING_URL
        assert result["jobs"] == 0

    async def test_scheduled_probe_reuses_runtime_parser(self):
        row = {
            "board_slug": "cal-state",
            "board_url": LISTING_URL,
            "monitor_config": json.dumps(
                {"instance": 873, "source_pointer": "cw", "locale": "en-us"}
            ),
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_page([], total=0, page=1, page_size=monitor.PROBE_PAGE_SIZE),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _probe_pageup(row, client)
        assert result.status == "ok"
        assert result.message == "200 (0 jobs)"

    def test_dom_description_enrichment_ignores_template_headings(self):
        scraper_type, config = auto_scraper_type("pageup") or (None, None)
        assert scraper_type == "dom"
        assert config is not None
        detail = """
          <h2>Job Search</h2><div id="job-content"><h2>Plumber</h2>
          <p>Apply now Job no: 560566 Work type: Staff Location: Sacramento
             Categories: Facilities</p>
          <div id="job-details"><p>Install and maintain campus plumbing.</p>
          <ul><li>Valid trade qualification</li></ul></div>
          <p>Advertised: Aug 03 2026</p></div><h2>Current opportunities</h2>
        """
        content = parse_dom_html(detail, config)
        assert content.description == (
            "<p>Install and maintain campus plumbing.</p>"
            "<ul><li>Valid trade qualification</li></ul>"
        )

    def test_registry_ws_discovery_and_pacing_are_wired(self):
        assert "pageup" in all_monitor_types()
        assert "pageup" in api_monitor_types()
        assert detect_ats_from_url(LISTING_URL) == "pageup"
        assert "pageup" in MONITOR_CARDS
        assert "pageup" in _MONITOR_CONFIG_HINTS
        assert "pageup" in PROBES
        assert "careers.pageuppeople.com" in _KNOWN_ATS_DOMAINS
        assert delay_for_domain("careers.pageuppeople.com") == settings.throttle_delay_ats
        assert _compute_throttle_key("pageup", LISTING_URL, {}) == "careers.pageuppeople.com"
        assert (
            _throttle_key(
                {
                    "crawler_type": "pageup",
                    "board_url": "https://example.com/careers",
                    "metadata": {},
                }
            )
            == "careers.pageuppeople.com"
        )
        assert (
            _configured_egress_host(
                {
                    "crawler_type": "pageup",
                    "board_url": "https://example.com/careers",
                    "metadata": json.dumps(
                        {"instance": 873, "source_pointer": "cw", "locale": "en-us"}
                    ),
                }
            )
            == "careers.pageuppeople.com"
        )
        found = _scan_ats_urls_in_html(f'<a href="{LISTING_URL}">Jobs</a>')
        assert any(item.url == LISTING_URL for item in found)

    def test_company_pr_label_allowlist_supports_pageup(self):
        script = Path(__file__).parents[3] / ".github" / "scripts" / "label-pr.sh"
        assert "|pageup|" in script.read_text()
