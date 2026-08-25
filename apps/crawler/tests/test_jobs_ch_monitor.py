"""Tests for the jobs.ch and jobup.ch employer-profile monitor."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from src.core.monitors.jobs_ch import _ids_from_url, can_handle, discover
from src.workspace._compat import detect_ats_from_url

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EHL_PROFILE_ID = "58f5774f-5f16-4be0-bdf7-68bd52671990"
EHL_DOCUMENT_COMPANY_ID = "852"
EHL_BOARD_URL = (
    f"https://www.jobup.ch/en/enterprises/{EHL_PROFILE_ID}-ehl-hospitality-business-school-sa/"
)


def _client(pages: dict[int, dict], requested: list[httpx.Request] | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if requested is not None:
            requested.append(request)
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page], request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _payload(
    page: int,
    pages: int,
    total: int,
    ids: list[str],
    *,
    company_id: str = "134466",
) -> dict:
    return {
        "rows": 100,
        "numPages": pages,
        "currentPage": page,
        "documents": [{"id": job_id, "company": {"id": company_id}} for job_id in ids],
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
        detect_ats_from_url("https://www.jobup.ch/en/enterprises/6099-leclanche-sa/") == "jobs_ch"
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
        1: _payload(1, 2, 101, [job_a] * 100, company_id="10513"),
        2: _payload(2, 2, 101, [job_c], company_id="10513"),
    }
    pages[1]["documents"] = [
        {"id": str(UUID(int=index + 1)), "company": {"id": "10513"}} for index in range(100)
    ]
    pages[1]["documents"][0] = {"id": job_a, "company": {"id": "10513"}}
    pages[1]["documents"][1] = {"id": job_b, "company": {"id": "10513"}}
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
    async with _client({1: _payload(1, 1, 1, [job_id], company_id="6099")}, requested) as client:
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
async def test_discover_supports_identity_checked_migrated_profile_alias() -> None:
    requested: list[httpx.Request] = []
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    async with _client(
        {1: _payload(1, 1, 1, [job_id], company_id=EHL_DOCUMENT_COMPANY_ID)},
        requested,
    ) as client:
        jobs = await discover(
            {
                "board_url": EHL_BOARD_URL,
                "metadata": {"document_company_id": EHL_DOCUMENT_COMPANY_ID},
            },
            client,
        )

    assert jobs == {f"https://www.jobup.ch/en/jobs/detail/{job_id}/"}
    assert requested[0].url.params["companyIds"] == EHL_PROFILE_ID


@pytest.mark.asyncio
async def test_discover_accepts_authoritative_empty_migrated_profile() -> None:
    requested: list[httpx.Request] = []
    async with _client({1: _payload(1, 0, 0, [])}, requested) as client:
        jobs = await discover(
            {
                "board_url": EHL_BOARD_URL,
                "metadata": {"document_company_id": EHL_DOCUMENT_COMPANY_ID},
            },
            client,
        )

    assert jobs == set()
    assert requested[0].url.params["companyIds"] == EHL_PROFILE_ID


@pytest.mark.asyncio
async def test_discover_migrated_profile_rejects_wrong_document_company() -> None:
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    async with _client({1: _payload(1, 1, 1, [job_id], company_id="999")}) as client:
        with pytest.raises(ValueError, match="outside the configured company"):
            await discover(
                {
                    "board_url": EHL_BOARD_URL,
                    "metadata": {"document_company_id": EHL_DOCUMENT_COMPANY_ID},
                },
                client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("document_company_id", ["", "not-an-id", -1, True])
async def test_discover_rejects_invalid_document_company_alias(
    document_company_id: object,
) -> None:
    async with _client({}) as client:
        with pytest.raises(ValueError, match="document_company_id"):
            await discover(
                {
                    "board_url": EHL_BOARD_URL,
                    "metadata": {"document_company_id": document_company_id},
                },
                client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("company_id", "document_company_id"),
    [
        ("42", "852"),
        (EHL_PROFILE_ID, "0fb7f075-a3f1-40b3-b3dd-6e6304f550f5"),
    ],
)
async def test_discover_rejects_unsupported_company_alias_shapes(
    company_id: str,
    document_company_id: str,
) -> None:
    async with _client({}) as client:
        with pytest.raises(ValueError, match="UUID profile ID and a numeric legacy"):
            await discover(
                {
                    "board_url": EHL_BOARD_URL,
                    "metadata": {
                        "company_id": company_id,
                        "document_company_id": document_company_id,
                    },
                },
                client,
            )


@pytest.mark.asyncio
async def test_discover_fails_closed_on_incomplete_pagination() -> None:
    pages = {
        1: _payload(
            1,
            1,
            2,
            ["644c296f-ee65-4fd5-b90d-77541692be5b"],
            company_id="42",
        )
    }
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
@pytest.mark.parametrize(
    "document_company",
    [None, {}, {"id": "invalid"}, {"id": "999"}],
)
async def test_discover_rejects_unverifiable_company_result(document_company: object) -> None:
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    payload = _payload(1, 1, 1, [job_id], company_id="42")
    payload["documents"][0]["company"] = document_company
    async with _client({1: payload}) as client:
        with pytest.raises(ValueError, match="company"):
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
async def test_can_handle_reports_migrated_profile_document_company_alias() -> None:
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    async with _client(
        {1: _payload(1, 1, 1, [job_id], company_id=EHL_DOCUMENT_COMPANY_ID)}
    ) as client:
        result = await can_handle(EHL_BOARD_URL, client)

    assert result == {
        "company_id": EHL_PROFILE_ID,
        "locale": "en",
        "jobs": 1,
        "portal": "jobup",
        "document_company_id": EHL_DOCUMENT_COMPANY_ID,
    }


@pytest.mark.asyncio
async def test_can_handle_rejects_mixed_document_company_identities() -> None:
    first = "644c296f-ee65-4fd5-b90d-77541692be5b"
    second = "20cf6e2f-366e-452b-9f28-e65a6fefa976"
    payload = _payload(1, 1, 2, [first, second], company_id=EHL_DOCUMENT_COMPANY_ID)
    payload["documents"][1]["company"]["id"] = "999"
    async with _client({1: payload}) as client:
        assert await can_handle(EHL_BOARD_URL, client) is None


@pytest.mark.asyncio
async def test_can_handle_does_not_infer_numeric_to_numeric_alias() -> None:
    job_id = "644c296f-ee65-4fd5-b90d-77541692be5b"
    async with _client({1: _payload(1, 1, 1, [job_id], company_id="852")}) as client:
        assert (
            await can_handle(
                "https://www.jobup.ch/en/enterprises/6099-leclanche-sa/",
                client,
            )
            is None
        )


def test_ehl_jobup_board_commits_the_verified_profile_alias() -> None:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "ehl-group-lausanne-jobup"
        )

    assert row["board_url"] == EHL_BOARD_URL
    assert row["monitor_type"] == "jobs_ch"
    assert json.loads(row["monitor_config"]) == {
        "company_id": EHL_PROFILE_ID,
        "document_company_id": EHL_DOCUMENT_COMPANY_ID,
        "locale": "en",
        "portal": "jobup",
    }
    assert row["scraper_type"] == "json-ld"


@pytest.mark.asyncio
async def test_can_handle_rejects_unrelated_host_without_request() -> None:
    async with _client({}) as client:
        assert await can_handle("https://example.com/jobs", client) is None
