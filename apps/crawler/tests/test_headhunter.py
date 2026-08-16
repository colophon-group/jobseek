from __future__ import annotations

import httpx
import pytest

from src.core.monitors.headhunter import (
    _employer_id_from_url,
    _parse_job,
    can_handle,
    discover,
)
from src.core.scrapers.headhunter import _vacancy_id_from_url, parse_payload, scrape
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

EMPLOYER_ID = "4556149"
BOARD_URL = f"https://hh.ru/employer/{EMPLOYER_ID}"


def _detail(vacancy_id: str = "12345") -> dict:
    return {
        "id": vacancy_id,
        "name": "Главный агроном",
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "description": "<p>Руководство агрономической службой.</p>",
        "published_at": "2026-08-14T09:00:00+0300",
        "area": {"id": "113", "name": "Россия"},
        "address": {"city": "Пенза", "street": "Советская улица", "building": "1"},
        "salary": {"from": 150000, "to": 200000, "currency": "RUR", "gross": True},
        "employment": {"id": "full", "name": "Полная занятость"},
        "schedule": {"id": "fullDay", "name": "Полный день"},
        "work_format": [{"id": "ON_SITE", "name": "На месте работодателя"}],
        "employer": {"id": EMPLOYER_ID, "name": "Sucden"},
        "department": {"id": "sucden-1", "name": "Производство"},
        "experience": {"id": "between3And6", "name": "Опыт 3–6 лет"},
        "key_skills": [{"name": "Агрономия"}],
        "professional_roles": [{"id": "1", "name": "Агроном"}],
        "languages": [{"id": "rus", "name": "Русский", "level": {"id": "native"}}],
    }


def test_employer_id_from_supported_urls():
    assert _employer_id_from_url(BOARD_URL) == EMPLOYER_ID
    assert _employer_id_from_url(f"https://www.hh.ru/employer/{EMPLOYER_ID}/") == EMPLOYER_ID
    assert (
        _employer_id_from_url(f"https://api.hh.ru/vacancies?employer_id={EMPLOYER_ID}&per_page=100")
        == EMPLOYER_ID
    )
    assert _employer_id_from_url("https://example.com/employer/4556149") is None
    assert _employer_id_from_url(f"http://hh.ru/employer/{EMPLOYER_ID}") is None
    assert _employer_id_from_url(f"https://user@hh.ru/employer/{EMPLOYER_ID}") is None
    assert _employer_id_from_url(f"https://hh.ru:444/employer/{EMPLOYER_ID}") is None
    assert _employer_id_from_url(f"https://hh.kz/employer/{EMPLOYER_ID}") == EMPLOYER_ID
    assert _employer_id_from_url(f"https://api.hh.ru/employer/{EMPLOYER_ID}") is None
    assert (
        _employer_id_from_url(
            f"https://api.hh.ru/vacancies?employer_id={EMPLOYER_ID}&host=rabota.by"
        )
        == EMPLOYER_ID
    )


def test_parse_job_maps_full_detail():
    job = _parse_job(_detail(), employer_id=EMPLOYER_ID)

    assert job is not None
    assert job.url == "https://hh.ru/vacancy/12345"
    assert job.title == "Главный агроном"
    assert job.description == "<p>Руководство агрономической службой.</p>"
    assert job.locations == ["Пенза, Советская улица, 1"]
    assert job.employment_type == "full"
    assert job.job_location_type == "onsite"
    assert job.date_posted == "2026-08-14T09:00:00+0300"
    assert job.base_salary == {
        "currency": "RUR",
        "min": 150000,
        "max": 200000,
        "unit": "month",
    }
    assert job.extras == {
        "skills": ["Агрономия"],
        "professional_roles": ["Агроном"],
        "languages": ["Русский"],
    }
    assert job.metadata == {
        "vacancy_id": "12345",
        "headhunter_employer_id": EMPLOYER_ID,
        "employer": "Sucden",
        "department": "Производство",
        "experience": "Опыт 3–6 лет",
        "schedule": "Полный день",
    }


def test_parse_job_rejects_cross_employer_result():
    payload = _detail()
    payload["employer"] = {"id": "999", "name": "Other"}
    assert _parse_job(payload, employer_id=EMPLOYER_ID) is None


def test_parse_job_uses_current_salary_and_employment_fields():
    payload = _detail()
    payload["alternate_url"] = "https://attacker.example/vacancy/12345"
    payload["salary"] = {"from": 1, "to": 2, "currency": "USD"}
    payload["salary_range"] = {
        "from": 900,
        "to": 1200,
        "currency": "RUR",
        "mode": {"id": "HOUR", "name": "За час"},
        "frequency": {"id": "WEEKLY", "name": "Раз в неделю"},
        "gross": True,
    }
    payload["employment_form"] = {"id": "PART", "name": "Частичная занятость"}

    job = _parse_job(payload, employer_id=EMPLOYER_ID, site_host="rabota.by")

    assert job is not None
    assert job.url == "https://rabota.by/vacancy/12345"
    assert job.base_salary == {
        "currency": "RUR",
        "min": 900,
        "max": 1200,
        "unit": "hour",
    }
    assert job.employment_type == "part"


def test_parse_job_drops_salary_with_no_canonical_granularity():
    payload = _detail()
    payload["salary_range"] = {
        "from": 5000,
        "to": None,
        "currency": "RUR",
        "mode": {"id": "SHIFT", "name": "За смену"},
        "gross": True,
    }

    job = _parse_job(payload, employer_id=EMPLOYER_ID)

    assert job is not None
    assert job.base_salary is None


async def test_discover_paginates_rich_summaries_without_detail_requests():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("Jobseek/1.0")
        if request.url.path == "/vacancies":
            assert request.url.params["employer_id"] == EMPLOYER_ID
            page = int(request.url.params["page"])
            assert request.url.params["host"] == "hh.ru"
            if page == 0:
                payload = {
                    "items": [_detail(str(10000 + index)) for index in range(100)],
                    "found": 101,
                    "pages": 2,
                    "page": 0,
                    "per_page": 100,
                }
            else:
                payload = {
                    "items": [_detail("67890")],
                    "found": 101,
                    "pages": 2,
                    "page": 1,
                    "per_page": 100,
                }
            return httpx.Response(200, json=payload, request=request)
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
            client,
        )

    assert len(result) == 101
    assert result[0].url == "https://hh.ru/vacancy/10000"
    assert result[-1].url == "https://hh.ru/vacancy/67890"
    assert all(job.title for job in result)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"page": 1}, "pagination does not match"),
        ({"pages": 2}, "reports 2 pages"),
        ({"per_page": 50}, "pagination does not match"),
        ({"items": []}, "0 unique vacancies, expected 1"),
    ],
)
async def test_discover_fails_closed_on_inconsistent_inventory(mutation, message):
    payload = {
        "items": [_detail()],
        "found": 1,
        "pages": 1,
        "page": 0,
        "per_page": 100,
    }
    payload.update(mutation)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match=message):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
                client,
            )


async def test_discover_fails_closed_on_repeated_vacancy():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        items = [_detail(str(10000 + index)) for index in range(100)]
        if page == 1:
            items = [_detail("10000")]
        return httpx.Response(
            200,
            json={"items": items, "found": 101, "pages": 2, "page": page, "per_page": 100},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="repeated across pages"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
                client,
            )


async def test_discover_fails_closed_when_totals_drift_between_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        found = 101 if page == 0 else 102
        items = [_detail(str(10000 + index)) for index in range(100)]
        if page == 1:
            items = [_detail("99999")]
        return httpx.Response(
            200,
            json={"items": items, "found": found, "pages": 2, "page": page, "per_page": 100},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="totals changed"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
                client,
            )


async def test_discover_marks_upstream_deep_pagination_cap_as_truncated():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        items = [_detail(str(page * 100 + index + 1)) for index in range(100)]
        return httpx.Response(
            200,
            json={"items": items, "found": 2001, "pages": 20, "page": page, "per_page": 100},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
            client,
        )

    assert result.truncated is True
    assert len(result.jobs_by_url) == 2000


async def test_discover_fails_closed_on_cross_employer_vacancy():
    payload = _detail()
    payload["employer"] = {"id": "999", "name": "Other"}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"items": [payload], "found": 1, "pages": 1, "page": 0, "per_page": 100},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="outside the configured employer"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"employer_id": EMPLOYER_ID}},
                client,
            )


async def test_discover_rejects_metadata_employer_mismatch():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
        with pytest.raises(ValueError, match="does not match"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"employer_id": "999"}},
                client,
            )


def test_scraper_payload_maps_full_detail():
    content = parse_payload(_detail())

    assert content.title == "Главный агроном"
    assert content.description == "<p>Руководство агрономической службой.</p>"
    assert content.locations == ["Пенза, Советская улица, 1"]
    assert content.extras == {
        "skills": ["Агрономия"],
        "professional_roles": ["Агроном"],
        "languages": ["Русский"],
    }


async def test_scraper_fetches_detail_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/vacancies/12345"
        assert request.url.params["host"] == "hh.ru"
        assert request.headers["user-agent"].startswith("Jobseek/1.0")
        return httpx.Response(200, json=_detail(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape("https://hh.ru/vacancy/12345", {}, client)

    assert content.description


async def test_scraper_rejects_mismatched_detail_id():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_detail("99999"), request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="does not match"):
            await scrape("https://hh.ru/vacancy/12345", {}, client)


async def test_scraper_treats_closed_vacancy_as_empty():
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape("https://hh.ru/vacancy/12345", {}, client)

    assert content.description is None


def test_scraper_extracts_only_headhunter_vacancy_ids():
    assert _vacancy_id_from_url("https://hh.ru/vacancy/12345") == "12345"
    assert _vacancy_id_from_url("https://hh.kz/vacancy/12345") == "12345"
    assert _vacancy_id_from_url("https://api.hh.ru/vacancy/12345") is None
    assert _vacancy_id_from_url("http://hh.ru/vacancy/12345") is None
    assert _vacancy_id_from_url("https://user@hh.ru/vacancy/12345") is None
    assert _vacancy_id_from_url("https://hh.ru:444/vacancy/12345") is None
    assert _vacancy_id_from_url("https://example.com/vacancy/12345") is None


async def test_probe_keeps_strong_detection_when_direct_egress_is_blocked():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            403,
            json={"errors": [{"type": "forbidden"}]},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await can_handle(BOARD_URL, client)

    assert result == {"employer_id": EMPLOYER_ID, "proxy": True}


async def test_probe_reports_job_count_when_reachable():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"items": [], "found": 4, "pages": 0},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await can_handle(BOARD_URL, client)

    assert result == {"employer_id": EMPLOYER_ID, "proxy": True, "jobs": 4}


def test_workspace_detection_and_auto_scraper():
    assert detect_ats_from_url(BOARD_URL) == "headhunter"
    assert auto_scraper_type("headhunter") == (
        "headhunter",
        {
            "proxy": True,
            "enrich": [
                "description",
                "locations",
                "employment_type",
                "job_location_type",
                "date_posted",
                "base_salary",
            ],
        },
    )
