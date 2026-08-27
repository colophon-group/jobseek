"""Durable SmartRecruiters identity contract for Nagarro."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors import DiscoveredJob, is_rich_monitor
from src.core.monitors.smartrecruiters import CANONICAL_IDENTITY_JOB_V1, discover
from src.workspace._compat import auto_scraper_type

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_TOKEN = "Nagarro1"
_JOB_A = "87f580d7-cefb-4de8-b3bf-fe7447a4daae"
_JOB_B = "11111111-1111-4111-8111-111111111111"


def _board_row() -> dict[str, str]:
    with _BOARDS_PATH.open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row["board_slug"] == "nagarro-careers")


def _detail(
    publication_id: str,
    *,
    job_id: str = _JOB_A,
    language: str = "en",
    title: str = "Engineer",
) -> dict:
    return {
        "id": publication_id,
        "jobId": job_id,
        "active": True,
        "defaultJobAd": language == "en",
        "language": {"code": language},
        "refNumber": f"REF-{job_id[:8]}",
        "name": title,
        "releasedDate": "2026-08-25T12:00:00Z",
        "company": {"identifier": _TOKEN, "name": "Nagarro"},
        "location": {"fullLocation": "Zurich, Switzerland", "hybrid": True},
        "jobAd": {
            "sections": {
                "jobDescription": {
                    "title": "Role",
                    "text": f"<p>{title} description</p>",
                }
            }
        },
    }


async def _discover(details: list[dict]) -> list[DiscoveredJob]:
    by_id = {str(detail["id"]): detail for detail in details}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={
                    "content": [{"id": publication_id} for publication_id in by_id],
                    "totalFound": len(by_id),
                },
                request=request,
            )
        publication_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=by_id[publication_id], request=request)

    board = {
        "board_url": "https://careers.smartrecruiters.com/Nagarro1",
        "metadata": {
            "token": _TOKEN,
            "canonical_identity": CANONICAL_IDENTITY_JOB_V1,
            "language_preference": ["en", "de", "fr", "it"],
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)
    assert isinstance(result, list)
    return result


def test_board_uses_rich_exact_job_identity_mode() -> None:
    row = _board_row()
    config = json.loads(row["monitor_config"])

    assert row["board_url"] == "https://careers.smartrecruiters.com/Nagarro1"
    assert row["monitor_type"] == "smartrecruiters"
    assert row["scraper_type"] == "skip"
    assert config == {
        "token": _TOKEN,
        "canonical_identity": "job-v1",
        "language_preference": ["en", "de", "fr", "it"],
    }
    assert is_rich_monitor(row["monitor_type"], config) is True
    assert auto_scraper_type(row["monitor_type"], config) == ("skip", None)


@pytest.mark.asyncio
async def test_locale_publications_collapse_to_one_durable_job_with_real_outbound_url() -> None:
    jobs = await _discover(
        [
            _detail("744000140000001", language="de", title="Softwareentwickler"),
            _detail("744000140000002", language="en", title="Software Engineer"),
        ]
    )

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_identity == f"smartrecruiters:nagarro1:{_JOB_A}"
    assert job.url == "https://jobs.smartrecruiters.com/Nagarro1/744000140000002"
    assert job.title == "Software Engineer"
    assert job.localizations is not None
    assert set(job.localizations) == {"de", "en"}
    assert job.metadata is not None
    assert job.metadata["smartrecruiters_publication_ids"] == [
        "744000140000001",
        "744000140000002",
    ]


@pytest.mark.asyncio
async def test_publication_churn_changes_outbound_url_without_changing_identity() -> None:
    before = await _discover([_detail("744000140000001", language="de")])
    after = await _discover([_detail("744000140000002", language="en")])

    assert before[0].source_identity == after[0].source_identity
    assert before[0].url != after[0].url


@pytest.mark.asyncio
async def test_distinct_provider_job_ids_never_merge_on_title_or_location() -> None:
    jobs = await _discover(
        [
            _detail("744000140000001", job_id=_JOB_A),
            _detail("744000140000002", job_id=_JOB_B),
        ]
    )

    assert {job.source_identity for job in jobs} == {
        f"smartrecruiters:nagarro1:{_JOB_A}",
        f"smartrecruiters:nagarro1:{_JOB_B}",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_mode", ["unknown", [], 7])
async def test_canonical_identity_mode_rejects_unknown_or_non_string_values(identity_mode) -> None:
    board = {
        "board_url": "https://careers.smartrecruiters.com/Nagarro1",
        "metadata": {"token": _TOKEN, "canonical_identity": identity_mode},
    }

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"invalid configuration reached provider request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
        with pytest.raises(ValueError, match="canonical_identity must be one of"):
            await discover(board, client)


@pytest.mark.asyncio
async def test_job_identity_mode_cannot_be_combined_with_outbound_template() -> None:
    board = {
        "board_url": "https://careers.smartrecruiters.com/Nagarro1",
        "metadata": {
            "token": _TOKEN,
            "canonical_identity": CANONICAL_IDENTITY_JOB_V1,
            "canonical_job_id_url_template": "https://example.com/jobs/{job_id}",
        },
    }

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="cannot be combined"):
            await discover(board, client)
