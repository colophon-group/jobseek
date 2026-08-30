from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.job51 import (
    _board_origin,
    _parse_job,
    _parse_jsonp,
    can_handle,
    discover,
)


def _jsonp(body: dict, *, status: str = "1") -> str:
    payload = {"status": status, "message": "ok", "resultbody": body}
    return f"jsoncallback({json.dumps(payload, ensure_ascii=False)})"


def _list_row(job_id: int) -> dict:
    return {"ctmid": "258121", "jobid": str(job_id), "jobname": f"Role {job_id}"}


def _detail(job_id: int, **overrides) -> dict:
    body = {
        "ctmid": "258121",
        "jobid": str(job_id),
        "jobname": f"Role {job_id}",
        "coname": "PVH China",
        "divname": "零售-深圳",
        "jobareaname": "深圳-福田区",
        "workareaname": "深圳-福田区",
        "issuedate": "2026-08-20 09:30:00",
        "address": "福田区 Example Mall",
        "term": "全职",
        "funtype": "店员",
        "workyearname": "1年",
        "degreefrom": "高中",
        "providesalarname": "7千-1万·13薪",
        "jobwelf": "五险一金 带薪年假",
        "jkeyword": "零售 顾客服务 团队合作",
        "jobinfo": (
            "工作职责：<br>维护品牌形象<br>服务顾客<br><br>"
            "岗位要求：<br>良好的沟通能力<br>团队合作精神"
        ),
    }
    body.update(overrides)
    return body


class TestBoardOrigin:
    def test_accepts_exact_employer_board(self):
        assert _board_origin("https://pvh.51job.com/C01job_list.html") == "https://pvh.51job.com"

    @pytest.mark.parametrize(
        "url",
        [
            "http://pvh.51job.com/C01job_list.html",
            "https://www.51job.com/C01job_list.html",
            "https://jobs.51job.com/all/123.html",
            "https://pvh.51job.com/index.html",
            "https://pvh.51job.com/C01job_list.html?jobarea=020000",
            "https://example.com/C01job_list.html",
        ],
    )
    def test_rejects_noncanonical_urls(self, url):
        assert _board_origin(url) is None


class TestParseJsonp:
    def test_extracts_success_body(self):
        assert _parse_jsonp(_jsonp({"totalnum": "0", "joblist": []})) == {
            "totalnum": "0",
            "joblist": [],
        }

    @pytest.mark.parametrize("text", ["{}", "callback({})", "jsoncallback(not-json)"])
    def test_rejects_malformed_payload(self, text):
        with pytest.raises(ValueError, match="51job CoAPI"):
            _parse_jsonp(text)

    def test_rejects_provider_error(self):
        with pytest.raises(ValueError, match="unsuccessful"):
            _parse_jsonp(_jsonp({}, status="0"))


class TestParseJob:
    def test_maps_complete_rich_detail(self):
        job = _parse_job(_detail(170836097), ctmid=258121, expected_job_id="170836097")

        assert isinstance(job, DiscoveredJob)
        assert job.url == "https://jobs.51job.com/all/170836097.html"
        assert job.title == "Role 170836097"
        assert job.locations == ["深圳-福田区"]
        assert job.employment_type == "全职"
        assert job.date_posted == "2026-08-20"
        assert job.language == "zh"
        assert "维护品牌形象" in job.description
        assert job.extras == {
            "skills": ["零售", "顾客服务", "团队合作"],
            "responsibilities": "工作职责：<br>维护品牌形象<br>服务顾客",
            "qualifications": "良好的沟通能力<br>团队合作精神",
        }
        assert job.metadata["salary_label"] == "7千-1万·13薪"
        assert job.source_identity == "job51:258121:170836097"

    def test_rejects_mismatched_identity(self):
        with pytest.raises(ValueError, match="invalid identity"):
            _parse_job(_detail(2), ctmid=258121, expected_job_id="1")

    def test_rejects_missing_description(self):
        with pytest.raises(ValueError, match="without a description"):
            _parse_job(_detail(1, jobinfo=""), ctmid=258121, expected_job_id="1")


class TestDiscover:
    async def test_paginates_and_fetches_complete_details(self):
        rows = [_list_row(job_id) for job_id in range(1, 22)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "pvh.51job.com":
                return httpx.Response(200, text="<script>var JOBPARAMS={ctmid: 258121}</script>")
            assert request.url.host == "coapi.51job.com"
            params = json.loads(request.url.params["params"])
            assert request.url.params["sign"]
            if request.url.path.endswith("job_list.php"):
                page = params["pagenum"]
                start = (page - 1) * 20
                body = {"totalnum": "21", "joblist": rows[start : start + 20]}
            else:
                body = _detail(int(params["jobid"]))
            return httpx.Response(200, text=_jsonp(body))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {"board_url": "https://pvh.51job.com/C01job_list.html"},
                client,
            )

        assert len(jobs) == 21
        assert jobs[0].title == "Role 1"
        assert jobs[-1].metadata["id"] == "21"
        assert all(job.description and job.locations for job in jobs)

    async def test_uses_configured_ctmid_without_fetching_board(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "coapi.51job.com"
            params = json.loads(request.url.params["params"])
            if request.url.path.endswith("job_list.php"):
                return httpx.Response(
                    200,
                    text=_jsonp({"totalnum": "1", "joblist": [_list_row(1)]}),
                )
            return httpx.Response(200, text=_jsonp(_detail(int(params["jobid"]))))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://pvh.51job.com/C01job_list.html",
                    "metadata": {"ctmid": 258121},
                },
                client,
            )

        assert [job.metadata["id"] for job in jobs] == ["1"]


class TestCanHandle:
    async def test_verifies_public_tenant_and_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "pvh.51job.com":
                return httpx.Response(200, text="var JOBPARAMS = { ctmid: 258121 }")
            return httpx.Response(
                200,
                text=_jsonp({"totalnum": "1", "joblist": [_list_row(1)]}),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://pvh.51job.com/C01job_list.html", client)

        assert result == {
            "origin": "https://pvh.51job.com",
            "ctmid": 258121,
            "jobs": 1,
        }

    async def test_rejects_unrelated_url_without_request(self):
        assert await can_handle("https://example.com/jobs") is None
