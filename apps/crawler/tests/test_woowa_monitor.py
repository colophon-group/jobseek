from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import BoardGoneError, all_monitor_types, is_rich_monitor, woowa
from src.core.monitors.woowa import (
    _VARIANTS,
    _parse_job,
    _standard_locations,
    _variant_from_url,
    can_handle,
    discover,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _listing(number: str, title: str = "Backend Engineer") -> dict:
    return {
        "recruitNumber": number,
        "recruitName": title,
        "description": "서울특별시 송파구",
        "desiredBranches": [],
    }


def _detail(number: str, **overrides) -> dict:
    detail = {
        "recruitNumber": number,
        "recruitName": "Backend Engineer",
        "recruitCorporationNumber": "WOOWA_BROTHERS",
        "recruitContents": "<p>구분: 경력 근무지역 : 서울 역할 : API 개발</p>",
        "recruitOpenDate": "2026-09-01 09:00:00",
        "recruitEndDate": "2026-10-01 23:59:59",
        "employmentType": {"recruitItemCode": "BA002001"},
        "careerType": {"recruitItemCode": "BA003002"},
        "jobGroup": {"recruitItemCode": "BA005001"},
    }
    detail.update(overrides)
    return detail


def test_exact_hosts_select_variants_and_reject_spoofs():
    assert _variant_from_url("https://career.woowahan.com/recruitment/").name == "brothers"
    assert _variant_from_url("https://career.woowayouths.com/").name == "youths"
    assert _variant_from_url("https://bmart-career.woowayouths.com/").name == "bmart"
    assert _variant_from_url("https://career.woowahan.com.evil.test/") is None
    assert _variant_from_url("http://career.woowahan.com/") is None
    assert _variant_from_url("https://career.woowahan.com:8443/") is None


def test_standard_mapping_extracts_locations_and_provider_identity():
    variant = _VARIANTS["career.woowahan.com"]
    job = _parse_job(
        variant,
        _listing("R2609012"),
        _detail(
            "R2609012",
            recruitName="Regional Sales Lead",
            recruitContents="<p>구분: 경력 근무지역 : 대전&광주 역할 : 영업 관리</p>",
        ),
    )

    assert job.url == "https://career.woowahan.com/recruitment/R2609012/detail"
    assert job.title == "Regional Sales Lead"
    assert job.description == "<p>구분: 경력 근무지역 : 대전&광주 역할 : 영업 관리</p>"
    assert job.locations == ["Daejeon, South Korea", "Gwangju, South Korea"]
    assert job.employment_type == "full_time"
    assert job.date_posted == "2026-09-01"
    assert job.extras == {"valid_through": "2026-10-01"}
    assert job.language == "ko"
    assert job.source_identity == "woowa:brothers:R2609012"


def test_standard_location_falls_back_to_country_and_handles_nationwide():
    assert _standard_locations("<p>No location label</p>", "South Korea") == ["South Korea"]
    assert _standard_locations(
        "<p>모집지역: 전국(서울, 부산) 고용형태: 기간제</p>", "South Korea"
    ) == ["South Korea"]


def test_bmart_mapping_uses_branch_address_and_accessible_description():
    variant = _VARIANTS["bmart-career.woowayouths.com"]
    detail = _detail(
        "B2609000",
        recruitName="B-mart Gimpo Crew",
        recruitCorporationNumber=None,
        recruitContents='<p><img src="https://cdn.example/job.png"></p>',
        employmentType={"recruitItemCode": "BA002002"},
        desiredBranches=[
            {
                "recruitItemName": "김포점",
                "recruitItemRemark": "lat=37.64&lng=126.68&addr=경기도 김포시 전원로 11",
            }
        ],
        applicantCheckList=[
            {
                "recruitItemName": "PDA를 사용할 수 있어요.",
                "recruitItemRemark": "바코드 스캔에 사용합니다.",
            }
        ],
    )
    job = _parse_job(variant, _listing("B2609000"), detail)

    assert job.url == "https://bmart-career.woowayouths.com/recruitment/detail/B2609000"
    assert job.locations == ["경기도 김포시 전원로 11, South Korea"]
    assert job.employment_type == "temporary"
    assert "B-mart Gimpo Crew" in job.description
    assert "PDA를 사용할 수 있어요." in job.description
    assert "official job page" not in job.description
    assert job.source_identity == "woowa:bmart:B2609000"


def test_invalid_provider_dates_fail_closed():
    variant = _VARIANTS["career.woowahan.com"]
    with pytest.raises(ValueError, match="invalid date"):
        _parse_job(
            variant,
            _listing("R2609012"),
            _detail("R2609012", recruitOpenDate="2026-02-30 09:00:00"),
        )


async def test_discover_paginates_and_enriches_every_row(monkeypatch):
    monkeypatch.setattr(woowa, "PAGE_SIZE", 2)
    rows = [_listing("R1", "One"), _listing("R2", "Two"), _listing("R3", "Three")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/w1/recruits":
            params = parse_qs(request.url.query.decode())
            page = int(params["page"][0])
            page_rows = rows[page * 2 : (page + 1) * 2]
            return httpx.Response(
                200,
                json={
                    "code": "2000",
                    "message": "OK",
                    "data": {"totalSize": 3, "list": page_rows},
                },
            )
        number = request.url.path.rsplit("/", 1)[-1]
        title = next(row["recruitName"] for row in rows if row["recruitNumber"] == number)
        return httpx.Response(
            200,
            json={"code": "2000", "data": _detail(number, recruitName=title)},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(
            {"board_url": "https://career.woowahan.com/recruitment/", "metadata": {}},
            client,
        )

    assert [job.title for job in jobs] == ["One", "Two", "Three"]
    assert all(job.description and job.locations for job in jobs)


async def test_discover_fails_closed_on_count_drift(monkeypatch):
    monkeypatch.setattr(woowa, "PAGE_SIZE", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(parse_qs(request.url.query.decode())["page"][0])
        total = 2 if page == 0 else 3
        return httpx.Response(
            200, json={"code": "2000", "data": {"totalSize": total, "list": [_listing(f"R{page}")]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="total changed"):
            await discover(
                {"board_url": "https://career.woowahan.com/recruitment/", "metadata": {}},
                client,
            )


async def test_discover_rejects_unsafe_recruit_number_before_detail_request():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": "2000",
                "data": {"totalSize": 1, "list": [_listing("../secret")]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="invalid recruitNumber"):
            await discover(
                {"board_url": "https://career.woowahan.com/recruitment/", "metadata": {}},
                client,
            )

    assert requested_paths == ["/w1/recruits"]


async def test_discover_converts_first_page_404_to_board_gone():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        with pytest.raises(BoardGoneError, match="no longer exists"):
            await discover(
                {"board_url": "https://career.woowahan.com/recruitment/", "metadata": {}},
                client,
            )


async def test_can_handle_verifies_exact_host_and_total():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "2000", "data": {"totalSize": 53, "list": [_listing("R1")]}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await can_handle("https://career.woowahan.com/recruitment/", client) == {
            "variant": "brothers",
            "jobs": 53,
        }
        assert await can_handle("https://example.com/careers", client) is None


def test_registered_as_rich_auto_skip_monitor():
    assert "woowa" in all_monitor_types()
    assert is_rich_monitor("woowa") is True
    assert auto_scraper_type("woowa") == ("skip", None)
    assert detect_ats_from_url("https://career.woowahan.com/recruitment/") == "woowa"
    assert "woowa" in MONITOR_CARDS
    assert "woowa" in _MONITOR_CONFIG_HINTS


def test_delivery_hero_uses_complete_distinct_board_set_and_uploaded_images():
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "delivery-hero"]

    assert {row["board_slug"] for row in rows} == {
        "delivery-hero-careers-bmart-crew",
        "delivery-hero-careers-woowa",
        "delivery-hero-careers-woowa-youths",
        "delivery-hero-inventory-careers",
    }
    woowa_rows = [row for row in rows if row["monitor_type"] == "woowa"]
    assert len(woowa_rows) == 3
    assert all(row["scraper_type"] == "skip" for row in woowa_rows)

    with (DATA_DIR / "companies.csv").open(newline="", encoding="utf-8") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "delivery-hero")
    assert company["logo_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/delivery-hero/"
    )
    assert company["icon_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/delivery-hero/"
    )
    assert company["extras"] and '"tickerSymbol": "DHER"' in company["extras"]
