"""Tests for the jobs.ch employer-profile monitor."""

from __future__ import annotations

import httpx
import pytest

from src.core.monitors.jobs_ch import _ids_from_url, can_handle, discover


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
        ("https://example.com/fr/entreprises/134466-cite-gestion-sa/", (None, None)),
        ("https://www.jobs.ch/fr/offres-emplois/", (None, None)),
    ],
)
def test_ids_from_url(url: str, expected: tuple[str | None, str | None]) -> None:
    assert _ids_from_url(url) == expected


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
    pages = {
        1: _payload(1, 2, 3, ["job-a", "job-b"]),
        2: _payload(2, 2, 3, ["job-c"]),
    }
    async with _client(pages, requested) as client:
        jobs = await discover(
            {
                "board_url": "https://www.jobs.ch/en/companies/10513-example/jobs/",
                "metadata": {},
            },
            client,
        )

    assert jobs == {
        "https://www.jobs.ch/en/vacancies/detail/job-a/",
        "https://www.jobs.ch/en/vacancies/detail/job-b/",
        "https://www.jobs.ch/en/vacancies/detail/job-c/",
    }
    assert [request.url.params["page"] for request in requested] == ["1", "2"]
    assert requested[0].url.params.get_list("publishedOn") == [
        "SEARCH",
        "SEARCH_COMPANY_PROFILE",
    ]


@pytest.mark.asyncio
async def test_discover_fails_closed_on_incomplete_pagination() -> None:
    pages = {1: _payload(1, 1, 2, ["job-a"])}
    async with _client(pages) as client:
        with pytest.raises(ValueError, match="expected 2"):
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
async def test_can_handle_rejects_unrelated_host_without_request() -> None:
    async with _client({}) as client:
        assert await can_handle("https://example.com/jobs", client) is None
