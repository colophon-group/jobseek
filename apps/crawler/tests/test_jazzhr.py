from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, jazzhr
from src.core.monitors.jazzhr import _tenant_from_url, can_handle, discover
from src.core.scrapers import _REGISTRY as scraper_registry
from src.core.scrapers.jazzhr import parse_html as parse_jazzhr_html
from src.core.scrapers.jazzhr import scrape as scrape_jazzhr
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import all_scraper_types, auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS, SCRAPER_CARDS

TENANT = "bluevoyant"
LISTING_URL = f"https://{TENANT}.applytojob.com/apply/jobs"
JOB_URL = f"https://{TENANT}.applytojob.com/apply/jobs/details/FFZLDWvD0h"


def _listing(*job_ids: str) -> str:
    links = "".join(
        f'<a class="job_title_link" href="/apply/jobs/details/{job_id}?&">Role</a>'
        for job_id in job_ids
    )
    return f'<html><div id="job_listings_wrapper">{links}</div></html>'


def _jsonld_detail() -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Client Success Manager III",
        "description": "<p>Help customers succeed.</p>",
        "employmentType": "FULL_TIME",
        "jobLocationType": "TELECOMMUTE",
        "datePosted": "2026-07-29",
        "jobLocation": {
            "@type": "Place",
            "address": {
                "addressLocality": "Dublin",
                "addressCountry": "IE",
            },
        },
        "baseSalary": {
            "currency": "EUR",
            "value": {"minValue": 70_000, "maxValue": 90_000, "unitText": "YEAR"},
        },
    }
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'


class TestTenantAndUrls:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://{TENANT}.applytojob.com",
            f"https://{TENANT}.applytojob.com/apply",
            LISTING_URL,
            JOB_URL,
            f"{JOB_URL}?ref=public",
        ],
    )
    def test_extracts_tenant_from_canonical_urls(self, url: str):
        assert _tenant_from_url(url) == TENANT

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{TENANT}.applytojob.com/apply",
            "https://applytojob.com/apply",
            f"https://nested.{TENANT}.applytojob.com/apply",
            f"https://user@{TENANT}.applytojob.com/apply",
            f"https://{TENANT}.applytojob.com/admin",
            "https://-bad.applytojob.com/apply",
        ],
    )
    def test_rejects_untrusted_or_non_board_urls(self, url: str):
        assert _tenant_from_url(url) is None


class TestMonitor:
    async def test_discovers_canonical_urls_via_dom_extractor(self):
        page = (
            _listing("FFZLDWvD0h", "lMexJd8yrF")
            .replace(
                "</div>",
                (
                    '<a href="https://other.applytojob.com/apply/jobs/details/foreign">'
                    "Other tenant</a></div>"
                ),
            )
            .replace("</html>", f'<a href="{JOB_URL}/?duplicate=1">Duplicate</a></html>')
        )
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": f"https://{TENANT}.applytojob.com"}, client)

        assert result == {
            JOB_URL,
            f"https://{TENANT}.applytojob.com/apply/jobs/details/lMexJd8yrF",
        }
        assert requests == [LISTING_URL]

    async def test_metadata_tenant_override(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": {"tenant": TENANT}},
                client,
            )

        assert result == set()
        assert seen == [LISTING_URL]

    async def test_conflicting_canonical_and_configured_tenants_fail_closed(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match"):
                await discover(
                    {"board_url": LISTING_URL, "metadata": {"tenant": "other"}},
                    client,
                )

        assert requests == []

    @pytest.mark.parametrize("invalid_tenant", [None, "", 123])
    async def test_explicit_invalid_configured_tenant_fails_closed(self, invalid_tenant: object):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=_listing(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="tenant is invalid"):
                await discover(
                    {"board_url": LISTING_URL, "metadata": {"tenant": invalid_tenant}},
                    client,
                )

        assert requests == []

    async def test_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": LISTING_URL}, client) == set()

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_status_is_board_gone(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError) as exc_info:
                await discover({"board_url": LISTING_URL}, client)
        assert exc_info.value.status_code == status
        assert exc_info.value.url == LISTING_URL

    @pytest.mark.parametrize("status", [204, 302, 400])
    async def test_unexpected_terminal_status_is_not_board_gone(self, status: int):
        headers = {"location": "https://www.jazzhr.com/"} if status == 302 else {}
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, headers=headers, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": LISTING_URL}, client)
        assert exc_info.value.last_status == status
        if status == 302:
            assert exc_info.value.last_location == "https://www.jazzhr.com/"

    async def test_marketing_or_malformed_page_fails_not_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="<html>JazzHR marketing</html>", request=request
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="non-listing"):
                await discover({"board_url": LISTING_URL}, client)

    @pytest.mark.parametrize("first_status", [202, 403, 429, 503])
    async def test_retries_provider_and_transient_statuses(
        self,
        first_status: int,
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
                return httpx.Response(first_status, request=request)
            return httpx.Response(200, text=_listing("FFZLDWvD0h"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": LISTING_URL}, client)
        assert result == {JOB_URL}
        assert calls == 2

    async def test_persistent_waf_failure_propagates(self, monkeypatch: pytest.MonkeyPatch):
        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", no_sleep)
        transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": LISTING_URL}, client)
        assert exc_info.value.last_status == 403

    async def test_empty_200_retries_instead_of_tombstoning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        calls = 0

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            text = "" if calls == 1 else _listing("FFZLDWvD0h")
            return httpx.Response(200, text=text, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover({"board_url": LISTING_URL}, client) == {JOB_URL}
        assert calls == 2

    async def test_html_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        page = _listing("FFZLDWvD0h")
        monkeypatch.setattr(jazzhr, "MAX_HTML_CHARS", len(page))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": LISTING_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {JOB_URL}

    async def test_job_cap_suppresses_tombstoning(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jazzhr, "MAX_JOBS", 1)
        page = _listing("FFZLDWvD0h", "lMexJd8yrF")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=page, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": LISTING_URL}, client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 1


class TestDetection:
    async def test_direct_url_detects_without_client(self):
        assert await can_handle(LISTING_URL) == {"tenant": TENANT}

    async def test_direct_url_verifies_job_count(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing("FFZLDWvD0h", "lMexJd8yrF"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(LISTING_URL, client) == {"tenant": TENANT, "jobs": 2}

    async def test_embedded_link_is_detected_without_guessing(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "example.com":
                return httpx.Response(
                    200,
                    text=f'<a href="https://{TENANT}.applytojob.com/apply">Open roles</a>',
                    request=request,
                )
            return httpx.Response(200, text=_listing("FFZLDWvD0h"), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result == {"tenant": TENANT, "jobs": 1}
        assert requested == ["https://example.com/careers", LISTING_URL]

    async def test_does_not_blind_probe_company_slug(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200, text="<html>No ATS link</html>", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None
        assert requested == ["https://example.com/careers"]


class TestScraper:
    def test_prefers_existing_jsonld_parser(self):
        content = parse_jazzhr_html(_jsonld_detail())
        assert content.title == "Client Success Manager III"
        assert content.description == "<p>Help customers succeed.</p>"
        assert content.locations == ["Dublin, IE"]
        assert content.employment_type == "FULL_TIME"
        assert content.job_location_type == "TELECOMMUTE"
        assert content.date_posted == "2026-07-29"
        assert content.base_salary == {
            "currency": "EUR",
            "min": 70_000,
            "max": 90_000,
            "unit": "year",
        }

    def test_old_theme_reuses_dom_parser_without_second_fetch(self):
        html = """
        <html><h1 class="job_title">Legacy Platform Engineer</h1>
        <div class="job_description"><p>Keep old systems reliable.</p></div>
        <a>Apply Now</a></html>
        """
        content = parse_jazzhr_html(html)
        assert content.title == "Legacy Platform Engineer"
        assert content.description is not None
        assert "Keep old systems reliable" in content.description

    def test_visible_job_meta_fills_missing_location_and_employment(self):
        html = """
        <html><h1 class="job_title">Data Engineer</h1>
        <h3 class="job_meta">Analytics - Remote - Contracted to Full Time</h3>
        <div class="job_description"><p>Build trustworthy data products.</p></div>
        <a>Apply Now</a></html>
        """
        content = parse_jazzhr_html(html)
        assert content.locations == ["Remote"]
        assert content.employment_type == "Contracted to Full Time"
        assert content.job_location_type == "Remote"

    async def test_scrape_fetches_detail_once(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text=_jsonld_detail(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape_jazzhr(JOB_URL, {}, client)
        assert content.title == "Client Success Manager III"
        assert calls == 1

    @pytest.mark.parametrize(
        "url",
        [
            LISTING_URL,
            f"https://{TENANT}.applytojob.com/admin",
            f"https://nested.{TENANT}.applytojob.com/apply/jobs/details/FFZLDWvD0h",
        ],
    )
    async def test_scrape_rejects_non_detail_urls_before_fetch(self, url: str):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, text=_jsonld_detail(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="invalid JazzHR job URL"):
                await scrape_jazzhr(url, {}, client)
        assert calls == 0


def test_workspace_and_runtime_integration():
    assert "jazzhr" in all_monitor_types()
    assert "jazzhr" in scraper_registry
    assert "jazzhr" in all_scraper_types()
    assert "jazzhr" in _KNOWN_ATS_DOMAINS
    assert detect_ats_from_url(LISTING_URL) == "jazzhr"
    assert detect_ats_from_url(f"https://{TENANT}.applytojob.com/admin") is None
    assert auto_scraper_type("jazzhr") == ("jazzhr", None)
    assert "jazzhr" in MONITOR_CARDS
    assert "jazzhr" in SCRAPER_CARDS
    assert "jazzhr" in _MONITOR_CONFIG_HINTS


def test_career_discovery_finds_jazzhr_link():
    html = f'<a href="https://{TENANT}.applytojob.com/apply">Open roles</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [f"https://{TENANT}.applytojob.com/apply"]


def test_career_discovery_finds_detail_only_jazzhr_link():
    html = f'<a href="{JOB_URL}">Open role</a>'
    candidates = _scan_ats_urls_in_html(html)
    assert [candidate.url for candidate in candidates] == [JOB_URL]


def test_career_discovery_rejects_non_board_jazzhr_path():
    html = f'<a href="https://{TENANT}.applytojob.com/admin">Admin</a>'
    assert _scan_ats_urls_in_html(html) == []
