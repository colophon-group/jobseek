"""Tests for the NSC KIPT PDF vacancy bulletin monitor."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from src.core.monitors import kipt

SAMPLE_BULLETIN = """\
Національний науковий центр
«Харківський фізико-технічний інститут» НАНУ
оголошує конкурс
на заміщення вакантних посад:

- заступника начальника відділу - 1 вакансія, підрозділ 24-00;
- наукового співробітника за спеціальністю «фізика твердого
тіла» (01.04.07) - 1 вакансія, підрозділ 15-30.

Вимоги до кандидатів:
- науковий ступінь – доктор наук або доктор філософії;
- досвід роботи у відповідній галузі науки.

Документи подавати за адресою: 61108, м. Харків, вул. Академічна, 1.
"""


def test_active_bulletins_filters_expired_and_non_vacancy_links():
    page = """
    <a href="../news/2026/vacancy_23_06_2026.pdf">current</a>
    <a href="../news/2026/vacancy_15_05_2026.pdf">expired</a>
    <a href="../news/2026/admission_01_07_2026.pdf">not a vacancy bulletin</a>
    """

    result = kipt._active_bulletins(
        "https://www.kipt.kharkov.ua/ua/vacancy.html",
        page,
        today=date(2026, 7, 22),
        max_age_days=30,
    )

    assert result == [
        ("https://www.kipt.kharkov.ua/news/2026/vacancy_23_06_2026.pdf", date(2026, 6, 23))
    ]


def test_parse_bulletin_splits_positions_and_preserves_common_details():
    pdf_url = "https://www.kipt.kharkov.ua/news/2026/vacancy_23_06_2026.pdf"

    jobs = kipt._parse_bulletin(
        pdf_url,
        SAMPLE_BULLETIN,
        date(2026, 6, 23),
        "Kharkiv, Ukraine",
    )

    assert [job.title for job in jobs] == [
        "заступника начальника відділу",
        "наукового співробітника за спеціальністю «фізика твердого тіла» (01.04.07)",
    ]
    assert len({job.url for job in jobs}) == 2
    assert all(job.url.startswith(f"{pdf_url}?_jid=") for job in jobs)
    assert all(job.locations == ["Kharkiv, Ukraine"] for job in jobs)
    assert all(job.date_posted == "2026-06-23" for job in jobs)
    assert all(job.language == "uk" for job in jobs)
    assert all("Вимоги до кандидатів" in job.description for job in jobs)
    assert "підрозділ 24-00" in jobs[0].description
    assert "підрозділ 15-30" in jobs[1].description


@pytest.mark.asyncio
async def test_can_handle_kipt_vacancy_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<a href="../news/2026/vacancy_23_06_2026.pdf">vacancies</a>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        config = await kipt.can_handle(
            "https://www.kipt.kharkov.ua/ua/vacancy.html",
            client,
        )
        assert config == {
            "max_age_days": 30,
            "default_location": "Kharkiv, Ukraine",
        }
        assert await kipt.can_handle("https://example.com/vacancy.html", client) is None


@pytest.mark.asyncio
async def test_discover_returns_rich_jobs(monkeypatch):
    today = date.today()
    pdf_name = f"vacancy_{today.day:02d}_{today.month:02d}_{today.year}.pdf"
    board_url = "https://www.kipt.kharkov.ua/ua/vacancy.html"
    pdf_url = f"https://www.kipt.kharkov.ua/news/{today.year}/{pdf_name}"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == board_url:
            return httpx.Response(
                200, text=f'<a href="../../news/{today.year}/{pdf_name}">today</a>'
            )
        if str(request.url) == pdf_url:
            return httpx.Response(200, content=b"fake-pdf")
        return httpx.Response(404)

    async def fake_pdf_text(url: str, client: httpx.AsyncClient) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return SAMPLE_BULLETIN

    monkeypatch.setattr(kipt, "_pdf_text", fake_pdf_text)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await kipt.discover(
            {"board_url": board_url, "metadata": {}},
            client,
        )

    assert len(jobs) == 2
    assert all(job.description and job.locations and job.date_posted for job in jobs)
