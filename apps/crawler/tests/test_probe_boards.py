from __future__ import annotations

import json

import httpx
import pytest

from src.probe_boards import (
    PROBES,
    ProbeResult,
    probe_row,
    rows_added_or_changed,
)


def _row(**overrides) -> dict:
    base = {
        "company_slug": "acme",
        "board_slug": "acme-greenhouse",
        "board_url": "https://job-boards.greenhouse.io/acme",
        "monitor_type": "greenhouse",
        "monitor_config": json.dumps({"token": "acme"}),
        "scraper_type": "",
        "scraper_config": "",
    }
    base.update(overrides)
    return base


class TestRowsAddedOrChanged:
    def test_new_row_is_included(self):
        base = [_row()]
        head = base + [_row(board_slug="acme-ashby", board_url="https://jobs.ashbyhq.com/acme")]
        diff = rows_added_or_changed(base, head)
        assert len(diff) == 1
        assert diff[0]["board_slug"] == "acme-ashby"

    def test_changed_url_is_included(self):
        base = [_row()]
        head = [_row(board_url="https://job-boards.greenhouse.io/acme-new")]
        diff = rows_added_or_changed(base, head)
        assert len(diff) == 1

    def test_changed_monitor_config_is_included(self):
        base = [_row()]
        head = [_row(monitor_config=json.dumps({"token": "acme-new"}))]
        diff = rows_added_or_changed(base, head)
        assert len(diff) == 1

    def test_unchanged_probe_fields_ignored(self):
        base = [_row()]
        # Only scraper_config changed — probe doesn't care.
        head = [_row(scraper_config='{"enrich":["description"]}')]
        diff = rows_added_or_changed(base, head)
        assert diff == []

    def test_identical_returns_empty(self):
        base = [_row()]
        head = [_row()]
        assert rows_added_or_changed(base, head) == []


@pytest.mark.asyncio
class TestProbeRow:
    async def _run(self, row, handler):
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await probe_row(row, client)

    async def test_greenhouse_200_is_ok(self):
        def handler(request):
            assert "boards-api.greenhouse.io/v1/boards/acme/jobs" in str(request.url)
            return httpx.Response(200, json={"jobs": []})

        result = await self._run(_row(), handler)
        assert result.status == "ok"
        assert result.monitor_type == "greenhouse"

    async def test_greenhouse_404_is_fail(self):
        def handler(request):
            return httpx.Response(404, json={"error": "not found"})

        result = await self._run(_row(), handler)
        assert result.status == "fail"
        assert "404" in result.message

    async def test_greenhouse_500_is_warn(self):
        def handler(request):
            return httpx.Response(500)

        result = await self._run(_row(), handler)
        assert result.status == "warn"

    async def test_lever_404_is_fail(self):
        row = _row(
            board_slug="acme-lever",
            board_url="https://jobs.lever.co/acme",
            monitor_type="lever",
            monitor_config=json.dumps({"token": "acme"}),
        )

        def handler(request):
            assert "api.lever.co/v0/postings/acme" in str(request.url)
            return httpx.Response(404)

        result = await self._run(row, handler)
        assert result.status == "fail"

    async def test_lever_eu_url_uses_eu_api(self):
        row = _row(
            board_slug="acme-lever",
            board_url="https://jobs.eu.lever.co/acme",
            monitor_type="lever",
            monitor_config="",
        )

        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json=[])

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://api.eu.lever.co/v0/postings/acme?limit=1&mode=json"

    async def test_lever_region_config_uses_eu_api(self):
        row = _row(
            board_slug="acme-lever",
            board_url="https://jobs.lever.co/acme",
            monitor_type="lever",
            monitor_config=json.dumps({"token": "acme", "region": "eu"}),
        )

        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json=[])

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://api.eu.lever.co/v0/postings/acme?limit=1&mode=json"

    async def test_ashby_uses_config_token(self):
        row = _row(
            board_slug="acme-ashby",
            board_url="https://jobs.ashbyhq.com/acme-old",
            monitor_type="ashby",
            monitor_config=json.dumps({"token": "acme-new"}),
        )

        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"jobs": []})

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert "job-board/acme-new" in captured["url"]

    async def test_bamboohr_uses_config_tenant(self):
        row = _row(
            board_slug="acme-bamboohr",
            board_url="https://legacy.example/jobs",
            monitor_type="bamboohr",
            monitor_config=json.dumps({"tenant": "acme"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"meta": {"totalCount": 0}, "result": []})

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://acme.bamboohr.com/careers/list"

    async def test_bamboohr_parses_tenant_url_and_fails_404(self):
        row = _row(
            board_slug="acme-bamboohr",
            board_url="https://acme.bamboohr.com/careers",
            monitor_type="bamboohr",
            monitor_config="",
        )

        def handler(request):
            assert str(request.url) == "https://acme.bamboohr.com/careers/list"
            return httpx.Response(404)

        result = await self._run(row, handler)
        assert result.status == "fail"
        assert "404" in result.message

    async def test_bamboohr_retirement_redirect_is_not_followed(self):
        row = _row(
            board_slug="acme-bamboohr",
            board_url="https://acme.bamboohr.com/careers",
            monitor_type="bamboohr",
            monitor_config="",
        )
        requests: list[str] = []

        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(
                302,
                headers={"location": "https://www.bamboohr.com/careers/"},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            result = await probe_row(row, client)

        assert result.status == "fail"
        assert "marketing site" in result.message
        assert requests == ["https://acme.bamboohr.com/careers/list"]

    async def test_paycom_uses_config_token(self):
        token = "0123456789abcdef0123456789abcdef"
        row = _row(
            board_slug="acme-paycom",
            board_url="https://legacy.example/jobs",
            monitor_type="paycom",
            monitor_config=json.dumps({"token": token}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text="<script>var configsFromHost = {};</script>",
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"].endswith(f"/portal/{token}/career-page")

    async def test_paycom_unavailable_200_is_failed(self):
        token = "0123456789abcdef0123456789abcdef"
        row = _row(
            board_slug="acme-paycom",
            board_url=(f"https://www.paycomonline.net/v4/ats/web.php/portal/{token}/career-page"),
            monitor_type="paycom",
            monitor_config="",
        )

        def handler(request):
            return httpx.Response(
                200,
                text="Job board does not exist or is unavailable at this time.",
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "fail"
        assert "unavailable" in result.message

    async def test_jazzhr_uses_config_tenant(self):
        row = _row(
            board_slug="acme-jazzhr",
            board_url="https://legacy.example/jobs",
            monitor_type="jazzhr",
            monitor_config=json.dumps({"tenant": "acme"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text='<div id="job_listings_wrapper"></div>',
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://acme.applytojob.com/apply/jobs"

    async def test_jazzhr_marketing_redirect_is_failed(self):
        row = _row(
            board_slug="acme-jazzhr",
            board_url="https://acme.applytojob.com/apply",
            monitor_type="jazzhr",
            monitor_config="",
        )
        requests: list[str] = []

        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(
                302,
                headers={"location": "https://www.jazzhr.com/"},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            result = await probe_row(row, client)

        assert result.status == "fail"
        assert "marketing site" in result.message
        assert requests == ["https://acme.applytojob.com/apply/jobs"]

    async def test_icims_uses_config_host(self):
        row = _row(
            board_slug="acme-icims",
            board_url="https://legacy.example/jobs",
            monitor_type="icims",
            monitor_config=json.dumps({"host": "careers-acme.icims.com"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text='<body class="iCIMS_ListingsPage"></body>',
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == ("https://careers-acme.icims.com/jobs/search?ss=1&in_iframe=1")

    async def test_icims_custom_site_redirect_page_is_warned(self):
        row = _row(
            board_slug="acme-icims",
            board_url="https://careers-acme.icims.com",
            monitor_type="icims",
            monitor_config="",
        )

        def handler(request):
            return httpx.Response(
                200,
                text="<script>window.top.location.href = 'https://careers.example/jobs'</script>",
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "warn"
        assert "migrated" in result.message

    async def test_icims_filtered_url_is_not_widened(self):
        row = _row(
            board_slug="acme-icims",
            board_url=("https://careers-acme.icims.com/jobs/search?searchLocation=12781--EMEA"),
            monitor_type="icims",
            monitor_config="",
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)

        assert result.status == "warn"
        assert "no valid host" in result.message

    async def test_herp_uses_config_slug(self):
        row = _row(
            board_slug="acme-herp",
            board_url="https://legacy.example/jobs",
            monitor_type="herp",
            monitor_config=json.dumps({"slug": "a244"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text='<div class="requisition-list"></div>',
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://herp.careers/v1/a244"

    async def test_herp_404_is_failed(self):
        row = _row(
            board_slug="acme-herp",
            board_url="https://herp.careers/v1/a244",
            monitor_type="herp",
            monitor_config="",
        )

        result = await self._run(row, lambda request: httpx.Response(404, request=request))
        assert result.status == "fail"
        assert "not found" in result.message

    async def test_herp_query_scoped_url_is_not_widened(self):
        row = _row(
            board_slug="acme-herp",
            board_url="https://herp.careers/v1/a244?group=engineering",
            monitor_type="herp",
            monitor_config="",
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)

        assert result.status == "warn"
        assert "no valid slug" in result.message

    async def test_gupy_uses_config_tenant(self):
        row = _row(
            board_slug="acme-gupy",
            board_url="https://legacy.example/jobs",
            monitor_type="gupy",
            monitor_config=json.dumps({"tenant": "afya"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text='<script id="__NEXT_DATA__" type="application/json">{}</script>',
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://afya.gupy.io/"

    async def test_gupy_404_is_failed(self):
        row = _row(
            board_slug="acme-gupy",
            board_url="https://afya.gupy.io/",
            monitor_type="gupy",
            monitor_config="",
        )

        result = await self._run(row, lambda request: httpx.Response(404, request=request))
        assert result.status == "fail"
        assert "not found" in result.message

    async def test_gupy_query_scoped_url_is_not_widened(self):
        row = _row(
            board_slug="acme-gupy",
            board_url="https://afya.gupy.io/?page=2",
            monitor_type="gupy",
            monitor_config="",
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)

        assert result.status == "warn"
        assert "no valid tenant" in result.message

    async def test_gupy_reserved_config_tenant_is_rejected(self):
        row = _row(
            board_slug="acme-gupy",
            board_url="https://legacy.example/jobs",
            monitor_type="gupy",
            monitor_config=json.dumps({"tenant": "portal"}),
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)

        assert result.status == "warn"
        assert "no valid tenant" in result.message

    async def test_cornerstone_uses_configured_board_identity(self):
        row = _row(
            board_slug="acme-cornerstone",
            board_url="https://legacy.example/jobs",
            monitor_type="cornerstone",
            monitor_config=json.dumps(
                {"tenant": "aswatsoneurope", "site_id": 16, "corp": "aswatsoneurope"}
            ),
        )
        token = f"{'a' * 24}.{'b' * 24}.{'c' * 24}"
        context = {
            "corp": "aswatsoneurope",
            "token": token,
            "cultureID": 1,
            "cultureName": "en-US",
            "endpoints": {"cloud": "https://eu-fra.api.csod.com/"},
        }
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text=f"<script>csod.context={json.dumps(context)};</script>",
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == (
            "https://aswatsoneurope.csod.com/ux/ats/careersite/16/home?c=aswatsoneurope"
        )

    async def test_cornerstone_404_is_failed(self):
        row = _row(
            board_slug="acme-cornerstone",
            board_url=(
                "https://aswatsoneurope.csod.com/ux/ats/careersite/16/home?c=aswatsoneurope"
            ),
            monitor_type="cornerstone",
            monitor_config="",
        )
        result = await self._run(row, lambda request: httpx.Response(404, request=request))
        assert result.status == "fail"
        assert "not found" in result.message

    async def test_cornerstone_scoped_url_is_not_widened(self):
        row = _row(
            board_slug="acme-cornerstone",
            board_url=(
                "https://aswatsoneurope.csod.com/ux/ats/careersite/16/home?c=aswatsoneurope&page=2"
            ),
            monitor_type="cornerstone",
            monitor_config="",
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)
        assert result.status == "warn"
        assert "no valid tenant/site_id/corp" in result.message

    async def test_cornerstone_reserved_config_tenant_is_rejected(self):
        row = _row(
            board_slug="acme-cornerstone",
            board_url="https://legacy.example/jobs",
            monitor_type="cornerstone",
            monitor_config=json.dumps({"tenant": "portal", "site_id": 1, "corp": "portal"}),
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)
        assert result.status == "warn"
        assert "no valid tenant/site_id/corp" in result.message

    async def test_hrmos_uses_config_tenant(self):
        row = _row(
            board_slug="acme-hrmos",
            board_url="https://legacy.example/jobs",
            monitor_type="hrmos",
            monitor_config=json.dumps({"tenant": "a-tech"}),
        )
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                text='<section id="jsi-joblist"></section>',
                request=request,
            )

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://hrmos.co/pages/a-tech/jobs"

    async def test_hrmos_404_is_failed(self):
        row = _row(
            board_slug="acme-hrmos",
            board_url="https://hrmos.co/pages/a-tech/jobs",
            monitor_type="hrmos",
            monitor_config="",
        )

        result = await self._run(row, lambda request: httpx.Response(404, request=request))
        assert result.status == "fail"
        assert "not found" in result.message

    async def test_hrmos_query_scoped_url_is_not_widened(self):
        row = _row(
            board_slug="acme-hrmos",
            board_url="https://hrmos.co/pages/a-tech/jobs?category=engineering",
            monitor_type="hrmos",
            monitor_config="",
        )
        transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("should not fetch"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)

        assert result.status == "warn"
        assert "no valid tenant" in result.message

    async def test_recruitee_uses_host_from_url(self):
        row = _row(
            board_slug="acme-recruitee",
            board_url="https://acme.recruitee.com",
            monitor_type="recruitee",
            monitor_config="",
        )

        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"offers": []})

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == "https://acme.recruitee.com/api/offers/"

    async def test_workday_parses_url_components(self):
        row = _row(
            board_slug="acme-workday",
            board_url="https://acme.wd5.myworkdayjobs.com/External",
            monitor_type="workday",
            monitor_config="",
        )

        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            assert request.method == "POST"
            return httpx.Response(200, json={"total": 0, "jobPostings": []})

        result = await self._run(row, handler)
        assert result.status == "ok"
        assert captured["url"] == ("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs")

    async def test_unsupported_monitor_is_skipped(self):
        row = _row(monitor_type="dom", monitor_config="")
        # No HTTP call should be made, so use a handler that raises.
        transport = httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(AssertionError("should not be called"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_row(row, client)
        assert result.status == "skipped"
        assert "dom" in result.message

    async def test_network_error_is_warn(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        result = await self._run(_row(), handler)
        # _retry falls back on HTTPError, still bubbles up the exception
        assert result.status == "warn"
        assert "network error" in result.message


def test_probe_registry_covers_expected_types():
    expected = {
        "greenhouse",
        "lever",
        "ashby",
        "bamboohr",
        "paycom",
        "jazzhr",
        "icims",
        "gupy",
        "cornerstone",
        "herp",
        "hrmos",
        "recruitee",
        "rippling",
        "smartrecruiters",
        "workday",
    }
    assert expected.issubset(PROBES.keys())


def test_probe_result_is_dataclass():
    r = ProbeResult("s", "greenhouse", "https://x", "ok", "200")
    assert r.board_slug == "s"
    assert r.status == "ok"
