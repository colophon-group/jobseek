from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, DiscoveredJob
from src.core.monitors.curately import (
    SEARCH_URL,
    _board_config,
    _parse_job,
    _short_name_from_url,
    can_handle,
    discover,
)

BOARD_URL = "https://careers.curately.ai/jobs/bms"


def _raw_job(job_id: int = 1868, **overrides) -> dict:
    raw = {
        "jobTitle": "Project Engineer",
        "jobType": 2,
        "clientId": 6,
        "publicJobDescr": "<p>Build cell-therapy manufacturing systems.</p>",
        "workType": 2,
        "workCity": "Seattle",
        "workState": "WA",
        "workZipcode": "98109",
        "jobHours": 1,
        "estStartDate": "2026-09-17",
        "estEndDate": "2027-09-16",
        "createDate": "2026-08-19 16:30:15.0",
        "payrateMin": 55,
        "payrateMax": 58.43,
        "jobId": job_id,
        "status": 1,
        "clientName": "BMS",
    }
    raw.update(overrides)
    return raw


def _page(items: list[dict], total: int) -> dict:
    return {
        "Success": True,
        "Status": 200,
        "List": items,
        "TotalSize": total,
        "header": "",
        "footer": "",
    }


def _board(**metadata) -> dict:
    config = {
        "short_name": "bms",
        "client_id": 6,
        "days_back": 180,
        "currency": "USD",
        "salary_unit": "hour",
        "language": "en",
    }
    config.update(metadata)
    return {"board_url": BOARD_URL, "metadata": config}


class TestIdentity:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (BOARD_URL, "bms"),
            (BOARD_URL + "/", "bms"),
            (BOARD_URL + "/apply-job/1868/job", "bms"),
            ("https://careers.curately.ai/jobs/acme-inc", "acme-inc"),
            ("http://careers.curately.ai/jobs/bms", None),
            ("https://careers.curately.ai/companies/bms", None),
            ("https://careers.curately.ai.evil.test/jobs/bms", None),
            ("https://user@careers.curately.ai/jobs/bms", None),
            ("https://careers.curately.ai:444/jobs/bms", None),
            ("https://careers.curately.ai/jobs/bms?tenant=other", None),
            ("https://careers.curately.ai/jobs/bms#other", None),
            ("https://example.com/jobs/bms", None),
        ],
    )
    def test_short_name_from_url(self, url: str, expected: str | None):
        assert _short_name_from_url(url) == expected

    def test_configured_short_name_must_match_url_tenant(self):
        with pytest.raises(ValueError, match="does not match"):
            _board_config(_board(short_name="other"))


class TestParseJob:
    def test_maps_full_rich_job(self):
        job = _parse_job(
            _raw_job(),
            short_name="bms",
            currency="USD",
            salary_unit="hour",
            language="en",
        )

        assert job == DiscoveredJob(
            url="https://careers.curately.ai/jobs/bms/apply-job/1868/job",
            title="Project Engineer",
            description="<p>Build cell-therapy manufacturing systems.</p>",
            locations=["Seattle, WA, 98109"],
            employment_type="contract",
            job_location_type="hybrid",
            date_posted="2026-08-19 16:30:15.0",
            base_salary={"currency": "USD", "min": 55, "max": 58.43, "unit": "hour"},
            language="en",
            metadata={
                "id": 1868,
                "client_name": "BMS",
                "estimated_start_date": "2026-09-17",
                "estimated_end_date": "2027-09-16",
                "job_hours_code": 1,
                "job_type_code": 2,
                "work_type_code": 2,
            },
        )

    def test_url_is_stable_when_title_changes(self):
        first = _parse_job(_raw_job(jobTitle="First title"), short_name="bms")
        second = _parse_job(_raw_job(jobTitle="Updated title"), short_name="bms")
        assert first is not None and second is not None
        assert first.url == second.url

    def test_remote_without_address_has_location(self):
        job = _parse_job(
            _raw_job(workType=1, workCity="", workState="", workZipcode=""),
            short_name="bms",
        )
        assert job is not None
        assert job.locations == ["Remote"]
        assert job.job_location_type == "remote"

    def test_permanent_part_time_uses_hours_code(self):
        job = _parse_job(_raw_job(jobType=1, jobHours=2), short_name="bms")
        assert job is not None
        assert job.employment_type == "part_time"

    def test_salary_requires_explicit_currency_and_unit(self):
        job = _parse_job(_raw_job(), short_name="bms", currency="USD")
        assert job is not None
        assert job.base_salary is None

    @pytest.mark.parametrize("status", [0, "0", 2, "2", 3, 4, 5])
    def test_known_inactive_job_is_ignored(self, status: int | str):
        assert _parse_job(_raw_job(status=status), short_name="bms") is None

    def test_unknown_status_fails_closed(self):
        with pytest.raises(ValueError, match="unknown status"):
            _parse_job(_raw_job(status="active"), short_name="bms")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"publicJobDescr": "  "},
            {"workCity": "", "workState": "", "workZipcode": "", "workType": 2},
        ],
    )
    def test_missing_core_rich_fields_fail_closed(self, overrides: dict):
        with pytest.raises(ValueError, match="description|location"):
            _parse_job(_raw_job(**overrides), short_name="bms")

    def test_missing_identity_fails_closed(self):
        with pytest.raises(ValueError, match="jobId or jobTitle"):
            _parse_job(_raw_job(jobId=None), short_name="bms")


class TestDiscover:
    async def test_paginates_with_tenant_scoped_post_body(self):
        offsets: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert str(request.url) == SEARCH_URL
            body = json.loads(request.content)
            assert body["clientids"] == "6"
            assert body["daysback"] == "180"
            assert body["query"] == body["city"] == body["state"] == ""
            offsets.append(body["next"])
            if body["next"] == 0:
                return httpx.Response(200, json=_page([_raw_job(1), _raw_job(2)], 3))
            return httpx.Response(200, json=_page([_raw_job(3)], 3))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(_board(), client)

        assert offsets == [0, 2]
        assert [job.metadata["id"] for job in jobs] == [1, 2, 3]
        assert all(job.description and job.locations for job in jobs)

    async def test_empty_board_is_authoritative(self):
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=_page([], 0)))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover(_board(), client) == []

    async def test_premature_empty_page_fails_closed(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json=_page([_raw_job(1)], 2) if calls == 1 else _page([], 2),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="before advertised total"):
                await discover(_board(), client)

    async def test_changing_total_fails_closed(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json=_page([_raw_job(calls)], 3 if calls % 2 else 2),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="TotalSize changed"):
                await discover(_board(), client)

    async def test_changing_total_retries_complete_snapshot(self, monkeypatch):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            responses = [
                _page([_raw_job(1)], 2),
                _page([_raw_job(2)], 1),
                _page([_raw_job(1), _raw_job(2)], 2),
            ]
            return httpx.Response(200, json=responses[calls - 1])

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr("src.core.monitors.curately.asyncio.sleep", no_sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(_board(), client)

        assert calls == 3
        assert [job.metadata["id"] for job in jobs] == [1, 2]

    async def test_duplicate_job_id_fails_closed(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=_page([_raw_job(1)], 2))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="repeated jobId 1"):
                await discover(_board(), client)

    async def test_cross_tenant_job_fails_closed(self):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_page([_raw_job(clientId=7)], 1),
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="configured client_id 6"):
                await discover(_board(), client)

    async def test_page_cannot_overshoot_advertised_total(self):
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_page([_raw_job(1), _raw_job(2)], 1),
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="2 rows for advertised total 1"):
                await discover(_board(), client)

    async def test_resolves_client_id_when_config_omits_it(self):
        methods: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "Success": True,
                        "Status": 200,
                        "shortName": "bms",
                        "clientId": 6,
                    },
                )
            return httpx.Response(200, json=_page([], 0))

        board = _board()
        del board["metadata"]["client_id"]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover(board, client) == []
        assert methods == ["GET", "POST"]

    async def test_retired_tenant_raises_board_gone(self):
        board = _board()
        del board["metadata"]["client_id"]
        transport = httpx.MockTransport(lambda _request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError, match="no longer exists"):
                await discover(board, client)

    async def test_large_total_marks_result_truncated(self, monkeypatch):
        from src.core.monitors import curately

        monkeypatch.setattr(curately, "MAX_JOBS", 2)
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json=_page([_raw_job(1), _raw_job(2)], 3))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(_board(), client)
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.jobs_by_url is not None
        assert len(result.jobs_by_url) == 2


class TestCanHandle:
    async def test_detects_and_counts_direct_board(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "Success": True,
                        "Status": 200,
                        "shortName": "bms",
                        "clientId": 6,
                    },
                )
            return httpx.Response(200, json=_page([_raw_job()], 216))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "short_name": "bms",
                "client_id": 6,
                "days_back": 180,
                "jobs": 216,
            }

    async def test_rejects_unrelated_url_without_request(self):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: pytest.fail("unrelated URL must not be fetched")
            )
        )
        async with client:
            assert await can_handle("https://example.com/jobs/bms", client) is None
