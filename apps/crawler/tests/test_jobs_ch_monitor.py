"""Tests for the jobs.ch and jobup.ch employer-profile monitor."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from src.core.monitors.jobs_ch import _ids_from_url, can_handle, discover
from src.workspace._compat import detect_ats_from_url


def _client(pages: dict[int, dict], requested: list[httpx.Request] | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page], request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _payload(page: int, pages: int, total: int, ids: list[str]) -> dict:
    return {
        "rows": 100,
        "numPages": pages,
        "currentPage": page,
        "documents": [{"id": job_id} for job_id in ids],
        "start": (page - 1) * 100,
        "totalHits": total,
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.jobs.ch/fr/entreprises/134466-cite-gestion-sa/",
            ("134466", "fr"),
        ),
        ("https://jobs.ch/de/firmen/42-example/offene-stellen/", ("42", "de")),
        ("https://www.jobs.ch/en/companies/9-example/", ("9", "en")),
        ("https://www.jobup.ch/en/enterprises/6099-leclanche-sa/", ("6099", "en")),
        ("https://jobup.ch/fr/societes/42-example/emplois/", ("42", "fr")),
        (
            "https://www.jobs.ch/de/firmen/0fb7f075-a3f1-40b3-b3dd-6e6304f550f5-tertianum-ag/",
            ("0fb7f075-a3f1-40b3-b3dd-6e6304f550f5", "de"),
        ),
        ("http://www.jobs.ch/fr/entreprises/134466-cite-gestion-sa/", (None, None)),
        (
            "https://user@www.jobs.ch/fr/entreprises/134466-cite-gestion-sa/",
            (None, None),
        ),
        (
            "https://www.jobs.ch.evil.example/fr/entreprises/134466-cite-gestion-sa/",
            (None, None),
        ),
        ("https://example.com/fr/entreprises/134466-cite-gestion-sa/", (None, None)),
        ("https://www.jobup.ch/de/firmen/42-example/", (None, None)),
        ("https://www.jobs.ch/fr/offres-emplois/", (None, None)),
    ],
)
def test_ids_from_url(url: str, expected: tuple[str | None, str | None]) -> None:
    assert _ids_from_url(url) == expected


def test_workspace_detects_jobup_employer_profile() -> None:
    assert (
        detect_ats_from_url("https://www.jobup.ch/en/enterprises/6099-leclanche-sa/")
        == "jobs_ch"
    )


@pytest.mark.asyncio
async def test_discover_accepts_explicit_empty_board() -> None:
    async with _client({1: _payload(1, 0, 0, [])}) as client:
        jobs = await discover(
            {
                "board_url": "https://www.jobs.ch/fr/entreprises/134466-cite-gestion-sa/",
                "metadata": {},
            },
            client,
        )
    assert jobs == set()


@pytest.mark.asyncio
async def test_discover_paginates_and_builds_localized_urls() -> None:
    requested: list[httpx.Request] = []
    job_a = "644c296f-ee65-4fd5-b90d-77541692be5b"
    job_b = "20cf6e2f-366e-452b-9f28-e65a6fefa976"
    job_c = "3e0fe269-1742-4ac4-aad4-f84c34306c57"
    pages = {
        1: _payload(1, 2, 101, [job_a] * 100),
        2: _payload(2, 2, 101, [job_c]),
    }
    pages[1]["documents"] = [{"id": str(UUID(int=index + 1))} for index in range(100)]
    pages[1]["documents"][0] = {"id": job_a}
    pages[1]["documents"][1] = {"id": job_b}
    async with _client(pages, requested) as client:
        jobs = await discover(
            {
                "board_url": "https://www.jobs.ch/en/companies/10513-example/jobs/",
                "metadata": {},
            },
            client,
        )

    assert len(jobs) == 101
    assert f"https://www.jobs.ch/en/vacancies/detail/{job_a}/" in jobs
    assert f"https://www.jobs.ch/en/vacancies/detail/{job_b}/" in jobs
    assert f"https://www.jobs.ch/en/vacancies/detail/{job_c}/" in jobs
    assert [request.url.params["page"] for request in requested] == ["1", "2"]
    assert requested[0].url.params.get_list("publishedOn") == [
        "SEARCH",
        "SEARCH_COMPANY_PROFILE",
    ]


@pytest.mark.asyncio
async def test_discover_uses_jobup_api_and_localized_detail_urls() -> None:
    requested: list[httpx.Request] = []
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    async with _client({1: _payload(1, 1, 1, [job_id])}, requested) as client:
        jobs = await discover(
            {
                "board_url": "https://www.jobup.ch/en/enterprises/6099-leclanche-sa/",
                "metadata": {},
            },
            client,
        )

    assert jobs == {f"https://www.jobup.ch/en/jobs/detail/{job_id}/"}
    assert requested[0].url.host == "job-search-api.jobup.ch"


@pytest.mark.asyncio
async def test_discover_fails_closed_on_incomplete_pagination() -> None:
    pages = {1: _payload(1, 1, 2, ["644c296f-ee65-4fd5-b90d-77541692be5b"])}
    async with _client(pages) as client:
        with pytest.raises(ValueError, match="returned 1 documents; expected 2"):
            await discover(
                {
                    "board_url": "https://www.jobs.ch/de/firmen/42-example/",
                    "metadata": {},
                },
                client,
            )


@pytest.mark.asyncio
async def test_can_handle_returns_explicit_job_count() -> None:
    async with _client({1: _payload(1, 0, 0, [])}) as client:
        result = await can_handle(
            "https://www.jobs.ch/fr/entreprises/134466-cite-gestion-sa/",
            client,
        )
    assert result == {"company_id": "134466", "locale": "fr", "jobs": 0}


@pytest.mark.asyncio
async def test_can_handle_returns_jobup_portal() -> None:
    async with _client({1: _payload(1, 0, 0, [])}) as client:
        result = await can_handle(
            "https://www.jobup.ch/en/enterprises/6099-leclanche-sa/",
            client,
        )
    assert result == {
        "company_id": "6099",
        "locale": "en",
        "jobs": 0,
        "portal": "jobup",
    }


@pytest.mark.asyncio
async def test_can_handle_rejects_unrelated_host_without_request() -> None:
    async with _client({}) as client:
        assert await can_handle("https://example.com/jobs", client) is None
