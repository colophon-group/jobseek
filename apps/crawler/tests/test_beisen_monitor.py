from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, all_monitor_types, api_monitor_types
from src.core.monitors.beisen import can_handle, discover
from src.core.scrapers.dom import parse_html
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.beisen import (
    BeisenBoard,
    beisen_board_from_metadata,
    beisen_board_from_url,
    beisen_tenant_from_url,
    extract_beisen_bootstrap,
)
from src.shared.http_retry import PaginationFetchError
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

TENANT = "acme-jobs"
ROOT_URL = f"https://{TENANT}.zhiye.com/"
MODERN_URL = f"https://{TENANT}.zhiye.com/jobs"
LEGACY_URL = f"https://{TENANT}.zhiye.com/Social"
INLINE_URL = f"https://{TENANT}.zhiye.com/index"
API_URL = f"https://{TENANT}.zhiye.com/api/Jobad/GetJobAdPageList"
PORTAL_ID = "22222222-2222-4222-8222-222222222222"
PUBLIC_ID = "11111111-1111-4111-8111-111111111111"


def _bootstrap(*, status: int = 1) -> str:
    payload = {
        "PortalId": PORTAL_ID,
        "tenantInfo": {"Id": 123, "Name": TENANT, "Status": status, "SystemVersion": 2},
    }
    return f"<html><script>var BSGlobal = {json.dumps(payload)};</script></html>"


def _modern_job(
    *,
    public_id: str = PUBLIC_ID,
    title: str = "Platform Engineer",
    category_id: str = "1",
) -> dict:
    return {
        "Id": public_id,
        "JobAdId": 987,
        "JobAdName": title,
        "Category": "社会招聘",
        "CategoryId": category_id,
        "LocNames": ["Shanghai", "Shanghai"],
        "Duty": "Build systems.\nKeep them healthy.",
        "Require": "Python experience.",
        "Kind": "Full-time",
        "PostDate": "2026-07-31T00:00:00",
        "Status": 1,
    }


def _payload(*jobs: dict, count: int | None = None) -> dict:
    return {
        "Code": 200,
        "Message": "operation success",
        "Count": len(jobs) if count is None else count,
        "Data": list(jobs),
        "Total": 0,
    }


def _legacy_page(
    *job_ids: int,
    page_max: int = 1,
    inline: bool = False,
) -> str:
    marker = "<script>_splash('zhiye_contentpage',0,123,'new_zhiye_com')</script>"
    pager = f'<a href="/Social/?PageIndex={page_max}">尾页</a>' if page_max > 1 else ""
    if inline:
        rows = "".join(
            f"""<li><h2>
              <span>Role {job_id}</span><span>HQ</span><span>Engineering</span>
              <span>四川省,成都市</span><span>2026-07-30</span></h2>
              <a jobadid="{job_id}" href="javascript:void(0)">立即申请</a></li>"""
            for job_id in job_ids
        )
    else:
        rows = "".join(
            f"""<tr><td><a title="Role {job_id}" href="/zpdetail/{job_id}">
              Role {job_id}</a></td><td></td><td>1</td>
              <td title="Shanghai">Shanghai</td><td>2026-07-30</td></tr>"""
            for job_id in job_ids
        )
    return f"<html>{marker}<table>{rows}</table>{pager}</html>"


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "company_slug": "acme",
        "board_slug": "acme-beisen",
        "board_url": MODERN_URL,
        "monitor_type": "beisen",
        "monitor_config": json.dumps(
            {
                "tenant": TENANT,
                "variant": "modern",
                "portal_id": PORTAL_ID,
                "tenant_id": 123,
            }
        ),
        "scraper_type": "skip",
        "scraper_config": "",
    }
    row.update(overrides)
    return row


class TestIdentity:
    @pytest.mark.parametrize(
        "url",
        [
            ROOT_URL,
            MODERN_URL,
            LEGACY_URL,
            INLINE_URL,
            f"{ROOT_URL}social/detail?jobAdId={PUBLIC_ID}",
            f"{ROOT_URL}zpdetail/12345",
        ],
    )
    def test_accepts_public_urls(self, url: str):
        assert beisen_tenant_from_url(url) == TENANT
        assert beisen_board_from_url(url) == BeisenBoard(TENANT)

    @pytest.mark.parametrize(
        "url",
        [
            f"http://{TENANT}.zhiye.com/",
            f"https://user@{TENANT}.zhiye.com/",
            f"https://{TENANT}.zhiye.com:444/",
            "https://zhiye.com/",
            "https://www.zhiye.com/",
            f"https://{TENANT}.zhiye.com.evil.test/",
        ],
    )
    def test_rejects_untrusted_urls(self, url: str):
        assert beisen_board_from_url(url) is None

    def test_modern_metadata_and_urls(self):
        board = beisen_board_from_metadata(
            {
                "tenant": TENANT.upper(),
                "variant": "modern",
                "portal_id": PORTAL_ID.upper(),
                "tenant_id": 123,
            }
        )
        assert board == BeisenBoard(
            TENANT,
            variant="modern",
            portal_id=PORTAL_ID,
            tenant_id=123,
        )
        assert board is not None
        assert board.api_url() == API_URL
        assert board.modern_job_url(PUBLIC_ID, "1") == (
            f"{ROOT_URL}social/detail?jobAdId={PUBLIC_ID}"
        )
        assert board.modern_job_url(PUBLIC_ID, "2") == (
            f"{ROOT_URL}campus/detail?jobAdId={PUBLIC_ID}"
        )

    def test_legacy_metadata_is_template_bound(self):
        standard = beisen_board_from_metadata(
            {
                "tenant": TENANT,
                "variant": "legacy",
                "listing_path": "/social",
                "legacy_template": "standard",
            }
        )
        inline = beisen_board_from_metadata(
            {
                "tenant": TENANT,
                "variant": "legacy",
                "listing_path": "/index",
                "legacy_template": "inline",
            }
        )
        assert standard is not None and standard.listing_url() == LEGACY_URL
        assert inline is not None and inline.legacy_job_url(123) == f"{ROOT_URL}zwxq?jobId=123"
        assert (
            beisen_board_from_metadata(
                {
                    "tenant": TENANT,
                    "variant": "legacy",
                    "listing_path": "/index",
                    "legacy_template": "standard",
                }
            )
            is None
        )

    def test_bootstrap_exposes_disabled_state(self):
        active = extract_beisen_bootstrap(_bootstrap(), TENANT)
        disabled = extract_beisen_bootstrap(_bootstrap(status=0), TENANT)
        assert active is not None and active[1] is True
        assert disabled is not None and disabled[1] is False


class TestModernMonitor:
    async def test_returns_complete_rich_records_without_detail_fetches(self):
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            assert str(request.url) == API_URL
            body = json.loads(request.content)
            assert body["PageSize"] == 1_000
            assert body["PortalId"] == PORTAL_ID
            return httpx.Response(200, json=_payload(_modern_job()), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": MODERN_URL}, client)

        assert isinstance(result, MonitorResult)
        assert result.truncated is False
        assert result.hybrid is False
        assert result.urls == {f"{ROOT_URL}social/detail?jobAdId={PUBLIC_ID}"}
        job = result.jobs_by_url[next(iter(result.urls))]  # type: ignore[index]
        assert job.title == "Platform Engineer"
        assert job.locations == ["Shanghai"]
        assert job.date_posted == "2026-07-31"
        assert "<h3>Responsibilities</h3>" in (job.description or "")
        assert job.metadata == {
            "job_ad_id": 987,
            "category": "社会招聘",
            "category_id": "1",
        }
        assert requested == [ROOT_URL, API_URL]

    async def test_invalid_date_is_omitted(self):
        job = _modern_job()
        job["PostDate"] = "0001-01-01T00:00:00"

        def handler(request: httpx.Request) -> httpx.Response:
            response = _bootstrap() if str(request.url) == ROOT_URL else _payload(job)
            return httpx.Response(
                200,
                text=response if isinstance(response, str) else None,
                json=response if isinstance(response, dict) else None,
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": ROOT_URL}, client)
        assert result.jobs_by_url is not None
        assert next(iter(result.jobs_by_url.values())).date_posted is None

    async def test_malformed_record_marks_result_truncated(self):
        bad = _modern_job(public_id="not-a-uuid")

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(200, json=_payload(_modern_job(), bad), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": ROOT_URL}, client)
        assert result.truncated is True
        assert len(result.urls) == 1

    async def test_disabled_portal_is_board_gone(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(status=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError, match="disabled"):
                await discover({"board_url": ROOT_URL}, client)

    async def test_api_redirect_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            return httpx.Response(302, headers={"location": "https://evil.test"}, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover({"board_url": ROOT_URL}, client)
        assert exc_info.value.last_status == 302

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("portal_id", "33333333-3333-4333-8333-333333333333"),
            ("tenant_id", 456),
        ],
    )
    async def test_configured_identity_must_match_live_portal(self, field: str, value: object):
        metadata = {
            "tenant": TENANT,
            "variant": "modern",
            "portal_id": PORTAL_ID,
            "tenant_id": 123,
        }
        metadata[field] = value
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="live portal identity changed"):
                await discover({"board_url": MODERN_URL, "metadata": metadata}, client)

    async def test_configured_tenant_must_match_board_url(self):
        metadata = {
            "tenant": TENANT,
            "variant": "modern",
            "portal_id": PORTAL_ID,
            "tenant_id": 123,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request to {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="does not match board URL tenant"):
                await discover(
                    {"board_url": "https://other-tenant.zhiye.com/jobs", "metadata": metadata},
                    client,
                )


class TestLegacyMonitor:
    @pytest.fixture
    def standard_config(self) -> dict:
        return {
            "tenant": TENANT,
            "variant": "legacy",
            "listing_path": "/Social",
            "legacy_template": "standard",
        }

    async def test_paginates_and_returns_partial_rich_records(self, standard_config: dict):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_legacy_page(), request=request)
            page = request.url.params.get("PageIndex")
            body = (
                _legacy_page(101, page_max=2)
                if page is None
                else _legacy_page(102, page_max=2)
                .replace("/zpdetail/102", "/zpdetail/102?PageIndex=2")
                .replace('<a href="/Social/?PageIndex=2">尾页</a>', "当前第2/2页")
            )
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": LEGACY_URL, "metadata": standard_config},
                client,
            )

        assert result.hybrid is True
        assert result.truncated is False
        assert result.urls == {f"{ROOT_URL}zpdetail/101", f"{ROOT_URL}zpdetail/102"}
        assert result.jobs_by_url is not None
        assert result.jobs_by_url[f"{ROOT_URL}zpdetail/101"].locations == ["Shanghai"]
        assert result.jobs_by_url[f"{ROOT_URL}zpdetail/101"].date_posted == "2026-07-30"
        assert requests == [ROOT_URL, LEGACY_URL, f"{LEGACY_URL}?PageIndex=2"]

    async def test_inline_template_uses_stable_detail_route(self):
        config = {
            "tenant": TENANT,
            "variant": "legacy",
            "listing_path": "/index",
            "legacy_template": "inline",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            body = (
                _legacy_page() if str(request.url) == ROOT_URL else _legacy_page(151, inline=True)
            )
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": INLINE_URL, "metadata": config}, client)
        assert result.urls == {f"{ROOT_URL}zwxq?jobId=151"}
        assert result.jobs_by_url is not None
        job = result.jobs_by_url[f"{ROOT_URL}zwxq?jobId=151"]
        assert job.title == "Role 151"
        assert job.locations == ["四川省,成都市"]

    async def test_duplicate_page_suppresses_removals(self, standard_config: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            body = _legacy_page() if str(request.url) == ROOT_URL else _legacy_page(101, page_max=2)
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": LEGACY_URL, "metadata": standard_config},
                client,
            )
        assert result.truncated is True
        assert result.urls == {f"{ROOT_URL}zpdetail/101"}

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_first_listing_is_board_gone(
        self,
        standard_config: dict,
        status: int,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_legacy_page(), request=request)
            return httpx.Response(status, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BoardGoneError):
                await discover(
                    {"board_url": LEGACY_URL, "metadata": standard_config},
                    client,
                )

    @pytest.mark.parametrize("status", [404, 410])
    async def test_terminal_child_page_is_not_board_gone(
        self,
        standard_config: dict,
        status: int,
    ):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_legacy_page(), request=request)
            if request.url.params.get("PageIndex") is None:
                return httpx.Response(200, text=_legacy_page(101, page_max=2), request=request)
            return httpx.Response(status, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await discover(
                    {"board_url": LEGACY_URL, "metadata": standard_config},
                    client,
                )
        assert exc_info.value.last_status == status


class TestWsAndOps:
    async def test_probe_requires_http_verification(self):
        assert await can_handle(MODERN_URL) is None

    async def test_direct_modern_probe_fetches_only_one_record(self):
        sizes: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_bootstrap(), request=request)
            body = json.loads(request.content)
            sizes.append(body["PageSize"])
            return httpx.Response(200, json=_payload(_modern_job(), count=37), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(MODERN_URL, client)
        assert result == {
            "tenant": TENANT,
            "variant": "modern",
            "portal_id": PORTAL_ID,
            "tenant_id": 123,
            "jobs": 37,
        }
        assert sizes == [1]

    async def test_linked_legacy_probe_preserves_listing_variant(self):
        career_url = "https://example.com/careers"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == career_url:
                return httpx.Response(200, text=f'<a href="{LEGACY_URL}">jobs</a>', request=request)
            if str(request.url) == ROOT_URL:
                return httpx.Response(200, text=_legacy_page(), request=request)
            return httpx.Response(200, text=_legacy_page(101), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(career_url, client)
        assert result == {
            "tenant": TENANT,
            "variant": "legacy",
            "listing_path": "/Social",
            "legacy_template": "standard",
            "jobs": 1,
        }

    def test_ws_auto_scraper_variants(self):
        assert auto_scraper_type("beisen", {"variant": "modern"}) == ("skip", None)
        standard = auto_scraper_type("beisen", {"variant": "legacy", "legacy_template": "standard"})
        inline = auto_scraper_type("beisen", {"variant": "legacy", "legacy_template": "inline"})
        assert standard is not None and standard[0] == "dom"
        assert inline is not None and inline[0] == "dom"
        assert standard[1]["enrich"] == ["description"]  # type: ignore[index]
        assert inline[1]["enrich"] == ["description"]  # type: ignore[index]
        assert auto_scraper_type("beisen", {}) is None

    def test_dom_presets_extract_both_legacy_detail_templates(self):
        standard = auto_scraper_type("beisen", {"variant": "legacy", "legacy_template": "standard"})
        inline = auto_scraper_type("beisen", {"variant": "legacy", "legacy_template": "inline"})
        assert standard is not None and standard[1] is not None
        assert inline is not None and inline[1] is not None
        standard_content = parse_html(
            """<div class="boxSupertitle"><span>Engineer</span></div>
            <span>工作地点：<b>上海市</b></span><h3>工作职责：</h3>
            <p>Build things.</p><h3>任职资格：</h3><p>Be kind.</p><a>现在申请</a>""",
            standard[1],
        )
        inline_content = parse_html(
            """<h2>Engineer</h2><span>工作地点：<b>成都市</b></span>
            <h3>【岗位职责】</h3><p>Build things.</p>
            <h3>【任职要求】</h3><p>Be kind.</p><a>立即申请</a>""",
            inline[1],
        )
        assert standard_content.title is None
        assert standard_content.locations is None
        assert "Build things" in (standard_content.description or "")
        assert inline_content.title is None
        assert inline_content.locations is None
        assert "Build things" in (inline_content.description or "")

    async def test_probe_boards_native_handler(self):
        user_agents: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            user_agents.append(request.headers["User-Agent"])
            return httpx.Response(200, text=_bootstrap(), request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            headers={"User-Agent": "jobseek-probe/1.0"},
        ) as client:
            result = await probe_row(_row(), client)
        assert result.status == "ok"
        assert result.monitor_type == "beisen"
        assert user_agents and user_agents[0].startswith("Mozilla/5.0")

    async def test_probe_boards_reports_disabled_portal(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_bootstrap(status=0), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(_row(), client)
        assert result.status == "fail"
        assert result.message == "portal disabled"

    def test_registration_detection_discovery_and_throttling(self):
        assert "beisen" in all_monitor_types()
        assert "beisen" in api_monitor_types()
        assert detect_ats_from_url(MODERN_URL) == "beisen"
        found = _scan_ats_urls_in_html(f'<a href="{LEGACY_URL}">Careers</a>')
        assert any(link.url == LEGACY_URL for link in found)
        assert delay_for_domain(f"{TENANT}.zhiye.com") == delay_for_domain("greenhouse")
        assert "beisen" in _MONITOR_CONFIG_HINTS
        assert "beisen" in MONITOR_CARDS
