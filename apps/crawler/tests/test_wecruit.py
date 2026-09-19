from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors.wecruit import _fetch_listings, _Tenant, can_handle, discover

ORIGIN = "https://joinus.example.cn"
SUITE = "670ca36b1c240e54e1ee0556"


def _row(post_id: str, *, recruit_type: int = 2, title: str = "采购工程师") -> dict:
    return {
        "postId": post_id,
        "recruitType": recruit_type,
        "currentSuiteKey": SUITE,
        "postName": title,
        "publishDate": "2026-09-11 14:01:02",
    }


def _list_payload(
    rows: list[dict],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    count = len(rows) if total is None else total
    return {
        "state": "200",
        "data": {
            "pageForm": {
                "totalPage": (count + page_size - 1) // page_size,
                "pageSize": page_size,
                "pageData": rows,
                "currentPage": page,
                "dataCount": count,
            },
            "positonNum": count,
        },
    }


def _detail(post_id: str, *, title: str = "采购工程师") -> dict:
    return {
        "state": "200",
        "data": {
            "postId": post_id,
            "recruitType": 2,
            "postName": title,
            "workContent": "负责采购与供应商管理\n推动成本优化",
            "serviceCondition": "本科及以上\n五年相关经验",
            "workPlaceList": [{"code": "0/4/7", "name": "北京"}],
            "publishDate": "2026-09-11 14:01:02",
            "endDate": "2027-09-11 23:59:59",
            "postCode": "BESTSELLER027677",
            "externalPostId": "269609",
            "company": "D&A集团",
            "department": "间接采购团队",
            "postTypeName": "采购",
            "jobLevel": "专员",
            "education": "本科及以上",
            "recruitNumStr": "1",
            "projectName": "非门店需求职位",
        },
    }


def _form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


@pytest.mark.asyncio
async def test_can_handle_resolves_branded_iframe_launcher():
    post_id = "6aa3d91b5e0f494d82a9809c"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/wecruit/common/getSLD":
            assert request.method == "POST"
            assert _form(request) == {"sld": "joinus.example.cn"}
            return httpx.Response(
                200,
                json={
                    "state": "200",
                    "data": {"linkData": {"link": f"{ORIGIN}/SU{SUITE}/pb/index.html#/"}},
                },
            )
        if request.url.path == f"/wecruit/suite/config/SU{SUITE}":
            return httpx.Response(
                200,
                json={
                    "state": "200",
                    "data": {"suiteKey": SUITE, "companyId": "company", "config": {}},
                },
            )
        if request.url.path == f"/wecruit/positionInfo/listPosition/SU{SUITE}":
            recruit_type = int(_form(request)["recruitType"])
            rows = [_row(post_id)] if recruit_type == 2 else []
            return httpx.Response(200, json=_list_payload(rows))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(f"{ORIGIN}/bestseller/position/index", client)

    assert result == {
        "api_origin": ORIGIN,
        "suite_key": SUITE,
        "recruit_types": [1, 2, 12, 13],
        "jobs": 1,
    }


@pytest.mark.asyncio
async def test_discover_paginates_and_joins_rich_details():
    post_ids = ["6aa3d91b5e0f494d82a9809c", "6aa3973bded00b8cb69c0f49"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/wecruit/positionInfo/listPosition/SU{SUITE}":
            page = int(_form(request)["currentPage"])
            return httpx.Response(
                200,
                json=_list_payload(
                    [_row(post_ids[page - 1], title=f"职位 {page}")],
                    total=2,
                    page=page,
                    page_size=1,
                ),
            )
        if request.url.path == f"/wecruit/positionInfo/listPositionDetail/SU{SUITE}":
            post_id = _form(request)["postId"]
            return httpx.Response(
                200,
                json=_detail(post_id, title=f"职位 {post_ids.index(post_id) + 1}"),
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    board = {
        "board_url": f"{ORIGIN}/SU{SUITE}/pb/social.html",
        "metadata": {
            "api_origin": ORIGIN,
            "suite_key": SUITE,
            "recruit_types": [2],
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(board, client)

    assert len(jobs) == 2
    assert [job.title for job in jobs] == ["职位 1", "职位 2"]
    assert jobs[0].locations == ["北京"]
    assert jobs[0].date_posted == "2026-09-11"
    assert jobs[0].language == "zh"
    assert jobs[0].source_identity == f"wecruit:{SUITE}:{post_ids[0]}"
    assert jobs[0].url == (f"{ORIGIN}/SU{SUITE}/pb/posDetail.html?postId={post_ids[0]}&postType=2")
    assert "<h3>工作职责</h3>" in jobs[0].description
    assert "负责采购与供应商管理<br>推动成本优化" in jobs[0].description
    assert jobs[0].extras == {
        "qualifications": "本科及以上\n五年相关经验",
        "responsibilities": "负责采购与供应商管理\n推动成本优化",
        "valid_through": "2027-09-11",
    }
    assert jobs[0].metadata["department"] == "间接采购团队"


@pytest.mark.asyncio
async def test_discover_rejects_changed_listing_total():
    post_ids = ["6aa3d91b5e0f494d82a9809c", "6aa3973bded00b8cb69c0f49"]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(_form(request)["currentPage"])
        total = 2 if page == 1 else 3
        return httpx.Response(
            200,
            json=_list_payload(
                [_row(post_ids[page - 1])],
                total=total,
                page=page,
                page_size=1,
            ),
        )

    board = {
        "board_url": f"{ORIGIN}/SU{SUITE}/pb/social.html",
        "metadata": {"api_origin": ORIGIN, "suite_key": SUITE, "recruit_types": [2]},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="changed during pagination"):
            await discover(board, client)


@pytest.mark.asyncio
async def test_listing_snapshot_restarts_after_lane_drift(monkeypatch: pytest.MonkeyPatch):
    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("src.core.monitors.wecruit.asyncio.sleep", no_sleep)
    post_ids = ["6aa3d91b5e0f494d82a9809c", "6aa3973bded00b8cb69c0f49"]
    first_page_calls = 0
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_page_calls
        page = int(_form(request)["currentPage"])
        pages.append(page)
        if page == 1:
            first_page_calls += 1
        total = 3 if page == 2 and first_page_calls == 1 else 2
        return httpx.Response(
            200,
            json=_list_payload(
                [_row(post_ids[page - 1])],
                total=total,
                page=page,
                page_size=1,
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows, truncated = await _fetch_listings(_Tenant(ORIGIN, SUITE), (2,), client)

    assert [row["postId"] for row in rows] == post_ids
    assert truncated is False
    assert pages == [1, 2, 1, 2]


@pytest.mark.asyncio
async def test_can_handle_rejects_cross_origin_non_provider_link():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "state": "200",
                "data": {
                    "linkData": {"link": f"https://attacker.example/SU{SUITE}/pb/index.html#/"}
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await can_handle(f"{ORIGIN}/bestseller/position/index", client) is None
