from __future__ import annotations

import httpx
import pytest

from src.core.monitors.jobconvo import _listing_identity, can_handle, discover
from src.core.scrapers.jobconvo import _detail_url, _parse_detail, _parse_job_url, scrape
from src.workspace._compat import auto_scraper_type

CAREER_PAGE = "ddf2b2f5-cc30-4503-8ec8-458f9869e2ba"
BOARD_URL = f"https://jobs.jobconvo.com/pt-br/careers/Deloitte/{CAREER_PAGE}/"
JOB_ONE = "7f2464bf-1bf6-4f8d-bc87-62e0205d536d"
JOB_TWO = "76dcf0e5-ca85-4acb-a1b0-2dc603c245fe"


def _listing(rows: list[tuple[str, str]], pages: list[int], *, active_page: int = 1) -> str:
    job_rows = "".join(
        f"""
        <tr class="joblist"><td>
          <a href="https://app.jobconvo.com/job/{slug}/{job_id}/"
             >{slug}</a>
        </td></tr>
        """
        for slug, job_id in rows
    )
    page_links = "".join(f'<li><a href="?page={page}">{page}</a></li>' for page in pages)
    return f"""
    <html><body>
      <table id="tbl"><tbody>{job_rows}</tbody></table>
      <ul class="pagination">
        <li class="active"><a href="#">{active_page}</a></li>
        {page_links}
      </ul>
    </body></html>
    """


@pytest.mark.asyncio
async def test_jobconvo_monitor_follows_only_advertised_pages():
    page_one = _listing([("one-role", JOB_ONE)], [2])
    page_two = _listing([("two-role", JOB_TWO)], [1], active_page=2)

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        if page is None:
            return httpx.Response(200, text=page_one)
        if page == "2":
            return httpx.Response(200, text=page_two)
        if page == "1":
            return httpx.Response(200, text=page_one)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert jobs == {
        f"https://app.jobconvo.com/job/one-role/{JOB_ONE}/",
        f"https://app.jobconvo.com/job/two-role/{JOB_TWO}/",
    }


@pytest.mark.asyncio
async def test_jobconvo_monitor_accepts_authoritative_empty_table():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_listing([], [])))
    ) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)
    assert jobs == set()


@pytest.mark.asyncio
async def test_jobconvo_monitor_rejects_cross_tenant_job_link():
    other_page = "11111111-2222-3333-4444-555555555555"
    html = f"""
    <table id="tbl"><tbody><tr class="joblist"><td>
      <a href="https://app.jobconvo.com/job/role/{JOB_ONE}/?career_page={other_page}">
        role
      </a>
    </td></tr></tbody></table>
    <ul class="pagination"><li class="active"><a href="#">1</a></li></ul>
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    ) as client:
        with pytest.raises(ValueError, match="different career page"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)


@pytest.mark.asyncio
async def test_jobconvo_monitor_rejects_missing_authoritative_paginator():
    html = f"""
    <table id="tbl"><tbody><tr class="joblist"><td>
      <a href="https://app.jobconvo.com/job/role/{JOB_ONE}/">role</a>
    </td></tr></tbody></table>
    """
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    ) as client:
        with pytest.raises(ValueError, match="authoritative paginator"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)


@pytest.mark.asyncio
async def test_jobconvo_monitor_rejects_ignored_page_parameter():
    page_one = _listing([("one-role", JOB_ONE)], [2])
    ignored_page_two = _listing([("one-role", JOB_ONE)], [2])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ignored_page_two if request.url.params else page_one)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="does not match the requested page"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)


@pytest.mark.asyncio
async def test_jobconvo_can_handle_returns_exact_config_and_count():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=_listing([("one-role", JOB_ONE)], []))
        )
    ) as client:
        result = await can_handle(BOARD_URL, client)

    assert result == {
        "listing_url": BOARD_URL,
        "locale": "pt-br",
        "career_page": CAREER_PAGE,
        "jobs": 1,
    }


def test_jobconvo_scraper_url_and_auto_config():
    url = f"https://app.jobconvo.com/job/MTgxOTU5NQ-recepcionista/{JOB_ONE}/"
    assert _parse_job_url(url) == ("MTgxOTU5NQ-recepcionista", JOB_ONE)
    assert _detail_url("pt-br", "MTgxOTU5NQ-recepcionista", JOB_ONE) == (
        f"https://app.jobconvo.com/pt-br/api/job/{JOB_ONE}/MTgxOTU5NQ-recepcionista/"
    )
    assert auto_scraper_type("jobconvo", {"locale": "pt-br", "career_page": CAREER_PAGE}) == (
        "jobconvo",
        {"locale": "pt-br"},
    )


@pytest.mark.parametrize(
    "url",
    [
        f"https://user@app.jobconvo.com/job/one-role/{JOB_ONE}/",
        f"https://app.jobconvo.com:444/job/one-role/{JOB_ONE}/",
        f"https://app.jobconvo.com:invalid/job/one-role/{JOB_ONE}/",
    ],
)
def test_jobconvo_scraper_rejects_credentialed_or_nonstandard_port_urls(url):
    assert _parse_job_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        BOARD_URL.replace("jobs.jobconvo.com", "user@jobs.jobconvo.com"),
        BOARD_URL.replace("jobs.jobconvo.com", "jobs.jobconvo.com:444"),
        BOARD_URL.replace("jobs.jobconvo.com", "jobs.jobconvo.com:invalid"),
    ],
)
def test_jobconvo_monitor_rejects_credentialed_or_nonstandard_port_urls(url):
    assert _listing_identity(url) is None


def test_jobconvo_parse_detail_maps_all_available_fields():
    content = _parse_detail(
        {
            "id": JOB_ONE,
            "company": "TALENTOS EXPERIENTES",
            "title": "Recepcionista bilingue",
            "description": "<p>Atendimento ao publico.</p>",
            "requirements": "<ul><li>Boa comunicacao</li></ul>",
            "benefits": "<p>Vale refeicao</p>",
            "city": "Sao Paulo",
            "state": "SP",
            "country": "Brazil",
            "employment": "CLT - Tempo Integral",
            "type_work_location": 0,
            "pub_date": "2026-08-28",
            "deadline": "2026-09-27",
            "level": "Junior",
            "status": "1",
            "company_language": "pt-br",
            "salary": None,
        }
    )

    assert content.title == "Recepcionista bilingue"
    assert content.locations == ["Sao Paulo, SP, Brazil"]
    assert content.employment_type == "CLT - Tempo Integral"
    assert content.job_location_type == "onsite"
    assert content.date_posted == "2026-08-28"
    assert content.language == "pt"
    assert content.description == (
        "<p>Atendimento ao publico.</p>\n<h3>Benefits</h3>\n<p>Vale refeicao</p>"
    )
    assert content.extras == {"qualifications": "<ul><li>Boa comunicacao</li></ul>"}
    assert content.metadata == {
        "id": JOB_ONE,
        "company": "TALENTOS EXPERIENTES",
        "deadline": "2026-09-27",
        "level": "Junior",
        "status": "1",
    }


@pytest.mark.asyncio
async def test_jobconvo_scrape_uses_public_detail_api():
    detail = {
        "id": JOB_ONE,
        "title": "Recepcionista bilingue",
        "description": "<p>Descricao completa.</p>",
        "city": "Sao Paulo",
        "country": "Brazil",
        "company_language": "pt-br",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/json"
        assert request.url == httpx.URL(
            f"https://app.jobconvo.com/pt-br/api/job/{JOB_ONE}/one-role/"
        )
        return httpx.Response(200, json=detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            f"https://app.jobconvo.com/job/one-role/{JOB_ONE}/",
            {"locale": "pt-br"},
            client,
        )

    assert content.title == "Recepcionista bilingue"
    assert content.description == "<p>Descricao completa.</p>"
    assert content.locations == ["Sao Paulo, Brazil"]
