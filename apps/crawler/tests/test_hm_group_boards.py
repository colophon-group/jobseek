"""Stable provider-identity contracts for H&M Group's career boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors import DiscoveredJob
from src.core.monitors import is_rich_monitor as core_is_rich_monitor
from src.core.monitors import smartrecruiters as smartrecruiters_module
from src.core.monitors.smartrecruiters import (
    _canonical_template,
    _validate_detail,
    _validate_list_page,
    discover,
)
from src.workspace._compat import auto_scraper_type
from src.workspace._compat import is_rich_monitor as compat_is_rich_monitor

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_HM_BOARD = "hm-group-careers-group"
_SELLPY_BOARD = "hm-group-careers-sellpy"
_CANONICAL_TEMPLATE = "https://career.hm.com/job/{job_id}/"
_JOB_A = "11111111-1111-4111-8111-111111111111"
_JOB_B = "22222222-2222-4222-8222-222222222222"
_MISSING = object()


def _board_rows() -> dict[str, dict[str, str]]:
    with _BOARDS_PATH.open(newline="", encoding="utf-8") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "hm-group"
        }


def _config(row: dict[str, str]) -> dict:
    return json.loads(row["monitor_config"] or "{}")


def _detail(
    publication_id: str,
    job_id: str,
    title: str,
    language: str,
    *,
    default: bool = False,
) -> dict:
    return {
        "id": publication_id,
        "jobId": job_id,
        "jobAdId": f"ad-{publication_id}",
        "defaultJobAd": default,
        "active": True,
        "language": {"code": language},
        "refNumber": f"REF-{job_id[:8]}",
        "name": title,
        "releasedDate": "2026-08-25T12:00:00Z",
        "company": {"identifier": "HMGroup", "name": "H&M Group"},
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


async def _discover_details(details: list[dict]) -> list[DiscoveredJob]:
    publications = [{"id": item["id"]} for item in details]
    by_id = {str(item["id"]): item for item in details}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={"content": publications, "totalFound": len(publications)},
                request=request,
            )
        publication_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=by_id[publication_id], request=request)

    board = {
        "board_url": "https://careers.smartrecruiters.com/HMGroup",
        "metadata": {
            "token": "HMGroup",
            "canonical_job_id_url_template": _CANONICAL_TEMPLATE,
            "language_preference": ["en", "de", "fr", "it"],
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)
    assert isinstance(result, list)
    return result


def test_only_hm_group_opts_into_exact_smartrecruiters_job_id_identity():
    rows = _board_rows()
    assert set(rows) == {_HM_BOARD, _SELLPY_BOARD}

    hm_config = _config(rows[_HM_BOARD])
    assert hm_config == {
        "token": "HMGroup",
        "canonical_job_id_url_template": _CANONICAL_TEMPLATE,
        "language_preference": ["en", "de", "fr", "it"],
    }
    assert rows[_HM_BOARD]["scraper_type"] == "skip"
    assert core_is_rich_monitor("smartrecruiters", hm_config) is True
    assert compat_is_rich_monitor("smartrecruiters", hm_config) is True
    assert auto_scraper_type("smartrecruiters", hm_config) == ("skip", None)
    assert core_is_rich_monitor("smartrecruiters", {"token": "ordinary"}) is False
    assert compat_is_rich_monitor("smartrecruiters", {"token": "ordinary"}) is False
    assert auto_scraper_type("smartrecruiters", {"token": "ordinary"}) == (
        "smartrecruiters",
        None,
    )

    with _BOARDS_PATH.open(newline="", encoding="utf-8") as handle:
        opted_in = {
            row["board_slug"]
            for row in csv.DictReader(handle)
            if _config(row).get("canonical_job_id_url_template")
        }
    assert opted_in == {_HM_BOARD}


@pytest.mark.asyncio
async def test_multilingual_publications_and_default_swaps_keep_one_identity():
    first = [
        _detail("104", _JOB_A, "Consulente", "it"),
        _detail("101", _JOB_A, "Advisor", "en", default=True),
        _detail("103", _JOB_A, "Conseiller", "fr"),
        _detail("102", _JOB_A, "Berater", "de"),
    ]
    second = [
        _detail("102", _JOB_A, "Berater", "de", default=True),
        _detail("103", _JOB_A, "Conseiller", "fr"),
        _detail("101", _JOB_A, "Advisor", "en"),
        _detail("104", _JOB_A, "Consulente", "it"),
    ]

    first_result = await _discover_details(first)
    second_result = await _discover_details(second)

    assert [job.url for job in first_result] == [f"https://career.hm.com/job/{_JOB_A}/"]
    assert [job.url for job in second_result] == [f"https://career.hm.com/job/{_JOB_A}/"]
    assert first_result[0].title == second_result[0].title == "Advisor"
    assert first_result[0].language == second_result[0].language == "en"
    assert first_result[0].localizations is not None
    assert set(first_result[0].localizations) == {"de", "en", "fr", "it"}


@pytest.mark.asyncio
async def test_same_language_duplicates_collapse_deterministically():
    jobs = await _discover_details(
        [
            _detail("201", _JOB_A, "Older English title", "en"),
            _detail("202", _JOB_A, "Provider default title", "en", default=True),
        ]
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Provider default title"
    assert jobs[0].localizations == {
        "en": {
            "title": "Provider default title",
            "description": "<h3>Role</h3>\n<p>Provider default title description</p>",
            "locations": ["Zurich, Switzerland"],
        }
    }
    assert jobs[0].metadata is not None
    assert jobs[0].metadata["smartrecruiters_publication_ids"] == ["201", "202"]


@pytest.mark.asyncio
async def test_same_title_and_location_do_not_merge_distinct_provider_job_ids():
    jobs = await _discover_details(
        [
            _detail("301", _JOB_A, "Sales Advisor", "en", default=True),
            _detail("302", _JOB_B, "Sales Advisor", "en", default=True),
        ]
    )

    assert {job.url for job in jobs} == {
        f"https://career.hm.com/job/{_JOB_A}/",
        f"https://career.hm.com/job/{_JOB_B}/",
    }


@pytest.mark.asyncio
async def test_title_change_updates_content_without_changing_identity():
    before = await _discover_details([_detail("401", _JOB_A, "Original title", "en")])
    after = await _discover_details([_detail("402", _JOB_A, "Renamed title", "en")])

    assert before[0].url == after[0].url == f"https://career.hm.com/job/{_JOB_A}/"
    assert before[0].title == "Original title"
    assert after[0].title == "Renamed title"


@pytest.mark.asyncio
async def test_exact_total_is_collected_across_terminal_short_page(monkeypatch):
    monkeypatch.setattr(smartrecruiters_module, "PAGE_SIZE", 2)
    details = [
        _detail("501", _JOB_A, "English", "en"),
        _detail("502", _JOB_A, "German", "de"),
        _detail("503", _JOB_B, "Distinct", "en"),
    ]
    by_id = {item["id"]: item for item in details}
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            offset = int(request.url.params.get("offset", "0"))
            offsets.append(offset)
            page = [{"id": item["id"]} for item in details[offset : offset + 2]]
            return httpx.Response(
                200,
                json={"content": page, "totalFound": 3},
                request=request,
            )
        return httpx.Response(
            200,
            json=by_id[request.url.path.rsplit("/", 1)[-1]],
            request=request,
        )

    board = {
        "board_url": "https://careers.smartrecruiters.com/HMGroup",
        "metadata": {
            "token": "HMGroup",
            "canonical_job_id_url_template": _CANONICAL_TEMPLATE,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(board, client)

    assert offsets == [0, 2]
    assert isinstance(jobs, list)
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_incomplete_page_fails_instead_of_tombstoning_unseen_jobs(monkeypatch):
    monkeypatch.setattr(smartrecruiters_module, "PAGE_SIZE", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"id": "601"}], "totalFound": 3},
            request=request,
        )

    board = {
        "board_url": "https://careers.smartrecruiters.com/HMGroup",
        "metadata": {"token": "HMGroup"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="ended before totalFound"):
            await discover(board, client)


@pytest.mark.parametrize(
    "active",
    [
        pytest.param(_MISSING, id="missing"),
        pytest.param(None, id="null"),
        pytest.param("false", id="string"),
        pytest.param(0, id="numeric-zero"),
        pytest.param(1, id="numeric-one"),
        pytest.param(False, id="false"),
    ],
)
async def test_non_boolean_or_non_live_detail_aborts_entire_localized_cycle(active):
    valid = _detail("701", _JOB_A, "Valid", "en")
    invalid = _detail("702", _JOB_B, "Unproven live state", "en")
    if active is _MISSING:
        invalid.pop("active")
    else:
        invalid["active"] = active

    with pytest.raises(ValueError, match="not affirmatively active"):
        await _discover_details([valid, invalid])


@pytest.mark.parametrize(
    ("payload", "expected_total", "message"),
    [
        ({"content": None, "totalFound": 0}, None, "content must be a list"),
        ({"content": [], "totalFound": None}, None, "non-negative integer"),
        ({"content": [], "totalFound": -1}, None, "non-negative integer"),
        ({"content": [], "totalFound": True}, None, "non-negative integer"),
        ({"content": [], "totalFound": 2}, 1, "changed during pagination"),
        ({"content": [None], "totalFound": 1}, None, "non-object posting"),
    ],
)
def test_list_page_contract_rejects_malformed_or_drifting_totals(payload, expected_total, message):
    with pytest.raises(ValueError, match=message):
        _validate_list_page(payload, expected_total)


@pytest.mark.parametrize(
    ("template", "message"),
    [
        (42, "exactly one"),
        ("https://career.hm.com/job/static/", "exactly one"),
        ("https://career.hm.com/{job_id}/{other}/", "unsupported placeholder"),
        ("http://career.hm.com/job/{job_id}/", "absolute HTTPS"),
        ("https://career.hm.com/job/{job_id}/?source=x", "absolute HTTPS"),
    ],
)
def test_canonical_job_id_template_rejects_ambiguous_or_unsafe_urls(template, message):
    with pytest.raises(ValueError, match=message):
        _canonical_template({"canonical_job_id_url_template": template})


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"id": "wrong"}, "detail id mismatch"),
        ({"company": {"identifier": "OtherTenant"}}, "tenant mismatch"),
        ({"jobId": "not-a-uuid"}, "invalid jobId"),
    ],
)
def test_detail_contract_rejects_wrong_identity_or_tenant(mutation, message):
    detail = _detail("801", _JOB_A, "Role", "en")
    detail.update(mutation)
    with pytest.raises(ValueError, match=message):
        _validate_detail(detail, token="HMGroup", publication_id="801")


def test_sellpy_title_slugs_collapse_to_numeric_teamtailor_identity():
    config = _config(_board_rows()[_SELLPY_BOARD])
    source_urls = {
        "https://career.sellpy.se/jobs/7756274-fullstack-engineer",
        "https://career.sellpy.se/jobs/7756274-renamed-title",
        "https://career.sellpy.se/jobs/8800000-another-role",
    }
    allowed = _apply_url_allowlist(
        MonitorResult(urls=source_urls),
        {"url_allowlist": config["url_allowlist"]},
    )
    transformed = _apply_url_transform(
        allowed,
        {"url_transform": config["url_transform"]},
    )

    assert transformed.urls == {
        "https://career.sellpy.se/jobs/7756274",
        "https://career.sellpy.se/jobs/8800000",
    }
    assert transformed.security_filtered_count == 0


@pytest.mark.parametrize(
    "source_url",
    [
        "https://evil.example/jobs/7756274-fullstack-engineer",
        "http://career.sellpy.se/jobs/7756274-fullstack-engineer",
        "https://career.sellpy.se/jobs/not-numeric",
        "https://career.sellpy.se/jobs/7756274-title?source=bad",
        "https://career.sellpy.se/jobs/7756274-title/extra",
    ],
)
def test_sellpy_identity_contract_rejects_non_provider_or_malformed_urls(source_url):
    config = _config(_board_rows()[_SELLPY_BOARD])
    result = _apply_url_allowlist(
        MonitorResult(urls={source_url}),
        {"url_allowlist": config["url_allowlist"]},
    )

    assert result.urls == set()
    assert result.security_filtered_count == 1
