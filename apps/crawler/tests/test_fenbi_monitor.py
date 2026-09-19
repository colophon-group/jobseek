from __future__ import annotations

import httpx
import pytest

from src.core.monitors.fenbi import discover

BOARD = "https://www.fenbi.com/page/joinus"
BUNDLE = "https://nodestatic.fbstatic.cn/weblts_spa_online/page/main-ABC123.js"


def _job(
    identifier: int,
    title: str,
    *,
    location: str = "北京、杭州",
    date: str = "2026年09月19日",
) -> str:
    return (
        f'{{id:{identifier},title:"{title}",publicDateShow:"{date}",'
        'publicDate:new Date(2026,8,19).getTime(),salary:"20k-30k/月",'
        f'location:"{location}",experience:"不限",demand:"本科及以上",'
        'description:["负责产品开发","维护服务质量"],'
        'requirements:["具备相关经验","沟通能力良好"],'
        'department:"技术部",number:"1",email:"jobs@fenbi.com"}'
    )


def _bundle(*, fulltime: str | None = None, parttime: str | None = None) -> str:
    fulltime = fulltime or _job(1800, "后端开发工程师")
    parttime = parttime or _job(2001, "兼职助教", location="网络办公")
    return (
        "minifiedPrefix();class Careers{constructor(){this.joinUsArr="
        f'{{email:"fenbihr@fenbi.com",fulltime:[{fulltime}],parttime:[{parttime}]}}'
        ";this.ready=true}}minifiedSuffix();"
    )


def _transport(bundle: str, *, scripts: str | None = None) -> httpx.MockTransport:
    page = scripts or f'<html><script src="{BUNDLE}"></script></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) in {BOARD, f"{BOARD}/parttime"}:
            return httpx.Response(200, text=page)
        if str(request.url) == BUNDLE:
            return httpx.Response(200, text=bundle)
        raise AssertionError(f"unexpected URL: {request.url}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fenbi_monitor_parses_fulltime_bundle_inventory():
    async with httpx.AsyncClient(transport=_transport(_bundle())) as client:
        jobs = await discover(
            {"board_url": BOARD, "metadata": {"kind": "fulltime"}},
            client,
        )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://www.fenbi.com/page/joinusdetail/fulltime/1800"
    assert job.title == "后端开发工程师"
    assert job.locations == ["北京", "杭州"]
    assert job.employment_type == "full_time"
    assert job.job_location_type is None
    assert job.date_posted == "2026-09-19"
    assert job.source_identity == "fenbi:careers:fulltime-1800"
    assert "负责产品开发" in (job.description or "")
    assert "沟通能力良好" in (job.description or "")
    assert job.metadata == {"provider_id": 1800, "department": "技术部"}


@pytest.mark.asyncio
async def test_fenbi_monitor_maps_part_time_network_office_to_remote_china():
    async with httpx.AsyncClient(transport=_transport(_bundle())) as client:
        jobs = await discover(
            {
                "board_url": "https://www.fenbi.com/page/joinus/parttime",
                "metadata": {"kind": "parttime"},
            },
            client,
        )

    assert jobs[0].locations == ["China"]
    assert jobs[0].employment_type == "part_time"
    assert jobs[0].job_location_type == "remote"


@pytest.mark.asyncio
async def test_fenbi_monitor_classifies_internship_from_parttime_inventory():
    internship = _job(2002, "内容运营实习生", location="北京")
    async with httpx.AsyncClient(transport=_transport(_bundle(parttime=internship))) as client:
        jobs = await discover(
            {
                "board_url": "https://www.fenbi.com/page/joinus/parttime",
                "metadata": {"kind": "parttime"},
            },
            client,
        )

    assert jobs[0].employment_type == "internship"


@pytest.mark.asyncio
async def test_fenbi_monitor_rejects_ambiguous_parttime_employment_type():
    ambiguous = _job(2003, "内容运营", location="北京")
    async with httpx.AsyncClient(transport=_transport(_bundle(parttime=ambiguous))) as client:
        with pytest.raises(ValueError, match="does not identify"):
            await discover(
                {
                    "board_url": "https://www.fenbi.com/page/joinus/parttime",
                    "metadata": {"kind": "parttime"},
                },
                client,
            )


@pytest.mark.asyncio
async def test_fenbi_monitor_normalizes_preferred_cities_and_home_option():
    fulltime = _job(
        1801,
        "全国教研员",
        location="北京(优先)、南京(优先)、武汉、居家",
    )
    async with httpx.AsyncClient(transport=_transport(_bundle(fulltime=fulltime))) as client:
        jobs = await discover(
            {"board_url": BOARD, "metadata": {"kind": "fulltime"}},
            client,
        )

    assert jobs[0].locations == ["北京", "南京", "武汉"]
    assert jobs[0].job_location_type == "hybrid"


@pytest.mark.asyncio
async def test_fenbi_monitor_rejects_duplicate_provider_ids():
    duplicate = ",".join((_job(1800, "First"), _job(1800, "Second")))
    async with httpx.AsyncClient(transport=_transport(_bundle(fulltime=duplicate))) as client:
        with pytest.raises(ValueError, match="duplicate job id 1800"):
            await discover(
                {"board_url": BOARD, "metadata": {"kind": "fulltime"}},
                client,
            )


@pytest.mark.asyncio
async def test_fenbi_monitor_rejects_untrusted_or_ambiguous_bundle_urls():
    scripts = (
        f'<script src="{BUNDLE}"></script>'
        '<script src="https://evil.example/weblts_spa_online/page/main-EVIL.js"></script>'
        '<script src="https://nodestatic.fbstatic.cn/weblts_spa_online/page/main-SECOND.js"></script>'
    )
    async with httpx.AsyncClient(transport=_transport(_bundle(), scripts=scripts)) as client:
        with pytest.raises(ValueError, match="exactly one main bundle"):
            await discover(
                {"board_url": BOARD, "metadata": {"kind": "fulltime"}},
                client,
            )


@pytest.mark.asyncio
async def test_fenbi_monitor_rejects_invalid_public_date():
    async with httpx.AsyncClient(
        transport=_transport(_bundle(fulltime=_job(1800, "Role", date="2026/09/19")))
    ) as client:
        with pytest.raises(ValueError, match="invalid publicDateShow"):
            await discover(
                {"board_url": BOARD, "metadata": {"kind": "fulltime"}},
                client,
            )
