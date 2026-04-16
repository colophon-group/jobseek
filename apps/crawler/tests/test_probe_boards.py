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
