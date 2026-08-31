from __future__ import annotations

import json

import httpx
import pytest

from src.config import settings
from src.core.monitors import BoardGoneError, all_monitor_types, jobvite
from src.core.monitors.jobvite import can_handle, discover
from src.core.scrapers.jsonld import parse_html as parse_jsonld_html
from src.probe_boards import PROBES, _probe_jobvite
from src.redis_queue import _KNOWN_ATS_DOMAINS, delay_for_domain
from src.shared.jobvite import (
    JobviteBoard,
    jobvite_board_from_metadata,
    jobvite_board_from_url,
    jobvite_job_url,
    jobvite_page_tenant,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "enverus"
LISTING_URL = f"https://jobs.jobvite.com/{TENANT}"
JOB_URL = f"https://jobs.jobvite.com/{TENANT}/job/oaGwAfwG"


def _listing(*job_ids: str, tenant: str = TENANT, extra: str = "") -> str:
    links = "".join(
        f'<div class="jv-job-item"><a href="/{tenant}/job/{job_id}">Role</a></div>'
        for job_id in job_ids
    )
    return f"""
      <html><body class="jv-desktop jv-page-jobs"
        ng-app="jv.careersite.desktop.app">
        <div class="jv-job-list">{links}</div>{extra}
        <script>window.jv = {{careersiteName: '{tenant}'}};</script>
      </body></html>
    """


def _search_listing(
    *job_ids: str,
    category: str,
    start: int,
    end: int,
    total: int,
    next_page: int | None = None,
) -> str:
    next_link = (
        f'<a class="jv-pagination-next" '
        f'href="/{TENANT}/search/?p={next_page}&amp;c={category}">Next</a>'
        if next_page is not None
        else ""
    )
    return _listing(
        *job_ids,
        extra=(
            '<div class="jv-pagination">'
            f'<div class="jv-pagination-text">{start}-{end} of {total}</div>'
            f"{next_link}</div>"
        ),
    )


class TestIdentity:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (LISTING_URL, JobviteBoard(TENANT, f"/{TENANT}")),
            (
                f"https://jobs.jobvite.com/careers/{TENANT}",
                JobviteBoard(TENANT, f"/careers/{TENANT}"),
            ),
            (
                f"https://jobs.jobvite.com/{TENANT}/jobs",
                JobviteBoard(TENANT, f"/{TENANT}/jobs"),
            ),
            (
                f"https://jobs.jobvite.com/{TENANT}/jobs/positions?d=Engineering",
                JobviteBoard(TENANT, f"/{TENANT}/jobs/positions"),
            ),
            (
                f"https://jobs.jobvite.com/careers/{TENANT}/jobs?q=data",
                JobviteBoard(TENANT, f"/careers/{TENANT}/jobs"),
            ),
            (f"{JOB_URL}?source=careers", JobviteBoard(TENANT, f"/{TENANT}")),
        ],
    )
    def test_parses_supported_routes(self, url: str, expected: JobviteBoard):
        assert jobvite_board_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            f"http://jobs.jobvite.com/{TENANT}",
            f"https://user@jobs.jobvite.com/{TENANT}",
            f"https://jobs.jobvite.com:444/{TENANT}",
            "https://jobs.jobvite.com/careers",
            f"https://jobs.jobvite.com/{TENANT}/admin",
            f"https://evil.example/{TENANT}",
        ],
    )
    def test_rejects_untrusted_routes(self, url: str):
        assert jobvite_board_from_url(url) is None

    def test_metadata_checks_tenant_assertion(self):
        assert jobvite_board_from_metadata(
            {"tenant": TENANT, "listing_url": LISTING_URL}
        ) == JobviteBoard(TENANT, f"/{TENANT}")
        assert jobvite_board_from_metadata({"tenant": "other", "listing_url": LISTING_URL}) is None
        assert jobvite_board_from_metadata({"tenant": "../bad", "listing_url": LISTING_URL}) is None

    def test_page_and_job_identity_helpers(self):
        page = _listing("oaGwAfwG")
        assert jobvite_page_tenant(page) == TENANT
        assert jobvite_job_url(f"{JOB_URL}?source=careers", TENANT) == JOB_URL
        assert jobvite_job_url(JOB_URL, "other") is None


class TestMonitor:
    async def test_discovers_canonical_urls_via_shared_dom_extractor(self):
        page = _listing(
            "oaGwAfwG",
            "o57bAfwH",
            extra=(
                f'<a href="{JOB_URL}?source=duplicate">Duplicate</a>'
                '<a href="https://jobs.jobvite.com/other/job/o1234567">Foreign</a>'
            ),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": LISTING_URL}, client)
        assert result == {
            JOB_URL,
            f"https://jobs.jobvite.com/{TENANT}/job/o57bAfwH",
        }

    async def test_configured_listing_is_used_for_non_jobvite_board_url(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {"tenant": TENANT, "listing_url": LISTING_URL},
                },
                client,
            )
        assert result == {JOB_URL}
        assert seen == [LISTING_URL]

    async def test_configured_tenant_mismatch_fails_before_fetch(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match"):
                await discover(
                    {
                        "board_url": LISTING_URL,
                        "metadata": {
                            "tenant": "other",
                            "listing_url": "https://jobs.jobvite.com/other",
                        },
                    },
                    client,
                )
        assert calls == 0

    async def test_empty_first_party_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": LISTING_URL}, client) == set()

    async def test_branded_landing_resolves_explicit_same_tenant_jobs_link(self):
        landing = f"https://jobs.jobvite.com/careers/{TENANT}"
        positions = f"https://jobs.jobvite.com/{TENANT}/jobs/positions"
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == landing:
                return httpx.Response(
                    200,
                    text=_listing(extra=f'<a href="/{TENANT}/jobs/positions">Jobs</a>'),
                    request=request,
                )
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": landing}, client)
        assert result == {JOB_URL}
        assert requested == [landing, positions]

    async def test_branded_landing_resolves_tenant_jobs_link(self):
        landing = f"https://jobs.jobvite.com/careers/{TENANT}"
        jobs_listing = f"https://jobs.jobvite.com/{TENANT}/jobs"
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == landing:
                return httpx.Response(
                    200,
                    text=_listing(extra=f'<a href="/{TENANT}/jobs">Jobs</a>'),
                    request=request,
                )
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": landing}, client)
        assert result == {JOB_URL}
        assert requested == [landing, jobs_listing]

    async def test_expands_paginated_category_links(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.path == f"/{TENANT}/search":
                page = int(request.url.params["p"])
                if page == 0:
                    return httpx.Response(
                        200,
                        text=_search_listing(
                            "oaGwAfwG",
                            "o57bAfwH",
                            category="Nursing",
                            start=1,
                            end=2,
                            total=3,
                            next_page=1,
                        ),
                        request=request,
                    )
                return httpx.Response(
                    200,
                    text=_search_listing(
                        "oABcDefG",
                        category="Nursing",
                        start=3,
                        end=3,
                        total=3,
                    ),
                    request=request,
                )
            return httpx.Response(
                200,
                text=_listing(
                    "oaGwAfwG",
                    extra=f'<a href="/{TENANT}/search?c=Nursing&amp;p=0">Show More</a>',
                ),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": LISTING_URL}, client)

        assert result == {
            JOB_URL,
            f"https://jobs.jobvite.com/{TENANT}/job/o57bAfwH",
            f"https://jobs.jobvite.com/{TENANT}/job/oABcDefG",
        }
        assert requested == [
            LISTING_URL,
            f"https://jobs.jobvite.com/{TENANT}/search?c=Nursing&p=0",
            f"https://jobs.jobvite.com/{TENANT}/search?c=Nursing&p=1",
        ]

    async def test_category_pagination_fails_closed_without_next_link(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == f"/{TENANT}/search":
                return httpx.Response(
                    200,
                    text=_search_listing(
                        "oaGwAfwG",
                        "o57bAfwH",
                        category="Nursing",
                        start=1,
                        end=2,
                        total=3,
                    ),
                    request=request,
                )
            return httpx.Response(
                200,
                text=_listing(extra=f'<a href="/{TENANT}/search?c=Nursing&amp;p=0">Show More</a>'),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="omitted its next link"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_category_pagination_fails_closed_on_row_count_drift(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == f"/{TENANT}/search":
                return httpx.Response(
                    200,
                    text=_search_listing(
                        "oaGwAfwG",
                        category="Nursing",
                        start=1,
                        end=2,
                        total=2,
                    ),
                    request=request,
                )
            return httpx.Response(
                200,
                text=_listing(extra=f'<a href="/{TENANT}/search?c=Nursing&amp;p=0">Show More</a>'),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="pagination drifted"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_category_pagination_fails_closed_on_repeated_rows(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == f"/{TENANT}/search":
                page = int(request.url.params["p"])
                if page == 0:
                    return httpx.Response(
                        200,
                        text=_search_listing(
                            "oaGwAfwG",
                            "o57bAfwH",
                            category="Nursing",
                            start=1,
                            end=2,
                            total=3,
                            next_page=1,
                        ),
                        request=request,
                    )
                return httpx.Response(
                    200,
                    text=_search_listing(
                        "o57bAfwH",
                        category="Nursing",
                        start=3,
                        end=3,
                        total=3,
                    ),
                    request=request,
                )
            return httpx.Response(
                200,
                text=_listing(extra=f'<a href="/{TENANT}/search?c=Nursing&amp;p=0">Show More</a>'),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="repeated jobs"):
                await discover({"board_url": LISTING_URL}, client)

    async def test_marketing_or_cross_tenant_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing(tenant="other"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="identity mismatch"):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": LISTING_URL}, client)

    async def test_invalid_tenant_redirect_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={
                    "location": "https://www.jobvite.com/support/job-seeker-support/?invalid=1"
                },
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("status", [202, 403, 429, 503])
    async def test_retries_transient_statuses(
        self,
        status: int,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls = 0

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(status, request=request)
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": LISTING_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_html_cap_fails_instead_of_tombstoning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        page = _listing("oaGwAfwG")
        monkeypatch.setattr(jobvite, "MAX_HTML_CHARS", len(page) - 1)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="HTML safety cap"):
                await discover({"board_url": LISTING_URL}, client)


class TestDetection:
    async def test_direct_url_without_client_returns_safe_config(self):
        assert await can_handle(LISTING_URL) == {
            "tenant": TENANT,
            "listing_url": LISTING_URL,
        }

    async def test_direct_url_is_verified_and_counted(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("oaGwAfwG", "o57bAfwH"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle(LISTING_URL, client)
        assert result == {"tenant": TENANT, "listing_url": LISTING_URL, "jobs": 2}

    async def test_tenant_jobs_route_is_verified_and_counted(self):
        listing_url = f"https://jobs.jobvite.com/{TENANT}/jobs"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("oaGwAfwG", "o57bAfwH"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle(listing_url, client)
        assert result == {"tenant": TENANT, "listing_url": listing_url, "jobs": 2}

    async def test_explicit_link_is_detected_without_slug_guessing(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="{LISTING_URL}">Open roles</a>',
                    request=request,
                )
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result == {"tenant": TENANT, "listing_url": LISTING_URL, "jobs": 1}
        assert requested == ["https://example.com/careers", LISTING_URL]

    async def test_does_not_blind_probe_company_slug(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None
        assert requested == ["https://example.com/careers"]


class TestScheduledProbe:
    async def test_validates_configured_listing_identity_and_count(self):
        row = {
            "board_slug": "enverus-jobvite",
            "board_url": "https://example.com/careers",
            "monitor_type": "jobvite",
            "monitor_config": json.dumps({"tenant": TENANT, "listing_url": LISTING_URL}),
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("oaGwAfwG", "o57bAfwH"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _probe_jobvite(row, client)
        assert result.status == "ok"
        assert result.message == "200 (2 jobs)"
        assert result.probe_url == LISTING_URL

    async def test_invalid_tenant_redirect_is_failed(self):
        row = {
            "board_slug": "retired-jobvite",
            "board_url": LISTING_URL,
            "monitor_type": "jobvite",
            "monitor_config": "",
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={
                    "location": "https://www.jobvite.com/support/job-seeker-support/?invalid=1"
                },
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _probe_jobvite(row, client)
        assert result.status == "fail"

    async def test_branded_landing_resolves_jobs_destination(self):
        landing = f"https://jobs.jobvite.com/careers/{TENANT}"
        positions = f"https://jobs.jobvite.com/{TENANT}/jobs/positions"
        row = {
            "board_slug": "branded-jobvite",
            "board_url": landing,
            "monitor_type": "jobvite",
            "monitor_config": "",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == landing:
                return httpx.Response(
                    200,
                    text=_listing(extra=f'<a href="/{TENANT}/jobs/positions">Jobs</a>'),
                    request=request,
                )
            assert str(request.url) == positions
            return httpx.Response(200, text=_listing("oaGwAfwG"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _probe_jobvite(row, client)
        assert result.status == "ok"
        assert result.message == "200 (1 jobs)"
        assert result.probe_url == positions

    async def test_terminal_410_is_failed(self):
        row = {
            "board_slug": "retired-jobvite",
            "board_url": LISTING_URL,
            "monitor_type": "jobvite",
            "monitor_config": "",
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(410, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _probe_jobvite(row, client)
        assert result.status == "fail"


def test_existing_jsonld_scraper_extracts_jobvite_detail():
    payload = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Senior Data Engineer",
        "description": "<p>Build reliable data products.</p>",
        "datePosted": "2026-07-29",
        "employmentType": "FULL_TIME",
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Calgary", "addressCountry": "CA"},
        },
    }
    body = f'<script type="application/ld+json">{json.dumps(payload)}</script>'
    content = parse_jsonld_html(body)
    assert content.title == "Senior Data Engineer"
    assert content.description == "<p>Build reliable data products.</p>"
    assert content.locations == ["Calgary, CA"]
    assert content.date_posted == "2026-07-29"


def test_workspace_and_runtime_integration():
    assert "jobvite" in all_monitor_types()
    assert "jobs.jobvite.com" in _KNOWN_ATS_DOMAINS
    assert delay_for_domain("jobs.jobvite.com") == settings.throttle_delay_ats
    assert detect_ats_from_url(LISTING_URL) == "jobvite"
    assert detect_ats_from_url(f"https://jobs.jobvite.com/{TENANT}/admin") is None
    assert auto_scraper_type("jobvite") == ("json-ld", None)
    assert "jobvite" in MONITOR_CARDS
    assert "jobvite" in _MONITOR_CONFIG_HINTS
    assert "jobvite" in PROBES


def test_career_discovery_finds_listing_and_detail_links():
    html = f'<a href="{LISTING_URL}">Jobs</a><a href="{JOB_URL}">Role</a>'
    assert [candidate.url for candidate in _scan_ats_urls_in_html(html)] == [
        LISTING_URL,
        JOB_URL,
    ]
