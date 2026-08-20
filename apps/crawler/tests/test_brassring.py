from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors import monitor_needs_browser
from src.core.monitors.brassring import (
    _board_ids,
    _parse_job,
    _parse_page,
    _SnapshotChanged,
    discover,
)

BOARD_URL = "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"


def test_monitor_always_routes_to_browser_worker():
    assert monitor_needs_browser("brassring") is True


def _row(**question_values):
    values = {
        "reqid": "3383626",
        "jobtitle": "Customer Support Representative - Evansville, IN",
        "jobdescription": "<strong>Responsibilities</strong><ul><li>Help customers</li></ul>",
        "formtext8": "Evansville ",
        "formtext9": "IN - Indiana",
        "lastupdated": "12-Aug-2026",
        "department": "Sales",
        **question_values,
    }
    return {
        "Questions": [{"QuestionName": key, "Value": value} for key, value in values.items()],
        "Link": (
            "https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad"
            "?partnerid=25416&siteid=5998&PageType=JobDetails&jobid=3383626"
        ),
    }


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (BOARD_URL, ("25416", "5998")),
        (
            "https://jobs.example.com/TGnewUI/Search/home/HomeWithPreLoad"
            "?partnerId=1&siteId=2&PageType=JobDetails&jobid=3",
            ("1", "2"),
        ),
        ("https://example.com/jobs?partnerid=1&siteid=2", None),
        ("https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=x&siteid=2", None),
    ],
)
def test_board_ids(url, expected):
    assert _board_ids(url) == expected


def test_parse_job_maps_rich_fields():
    job = _parse_job(_row(), "25416", "5998")

    assert job is not None
    assert job.title == "Customer Support Representative - Evansville, IN"
    assert job.locations == ["Evansville, IN - Indiana"]
    assert job.date_posted == "2026-08-12"
    assert "<strong>Responsibilities</strong>" in job.description
    assert job.metadata == {
        "provider": "brassring",
        "requisition_id": "3383626",
        "department": "Sales",
    }


def test_parse_job_accepts_global_description_and_country():
    job = _parse_job(
        _row(
            jobdescription="",
            formtext3="<p>Global description</p>",
            formtext9="",
            formtext10="Poland",
        ),
        "25416",
        "5998",
    )

    assert job is not None
    assert job.description == "<p>Global description</p>"
    assert job.locations == ["Evansville, Poland"]


def test_parse_job_accepts_missing_optional_description():
    job = _parse_job(
        _row(jobdescription="", formtext3=""),
        "25416",
        "5998",
    )

    assert job is not None
    assert job.description is None


@pytest.mark.parametrize(
    "changes",
    [
        {"jobtitle": ""},
        {"reqid": "not-numeric"},
    ],
)
def test_parse_job_rejects_incomplete_required_fields(changes):
    assert _parse_job(_row(**changes), "25416", "5998") is None


def test_parse_job_rejects_cross_board_link():
    assert _parse_job(_row(), "25416", "5429") is None


def test_parse_page_accepts_authoritative_empty_board():
    assert _parse_page({"JobsCount": 0, "Jobs": None}) == (0, [])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"JobsCount": "1", "Jobs": {"Job": []}},
        {"JobsCount": 1, "Jobs": {}},
    ],
)
def test_parse_page_rejects_malformed_payload(payload):
    with pytest.raises(ValueError):
        _parse_page(payload)


async def test_discover_retries_snapshot_churn_once():
    expected = [_parse_job(_row(), "25416", "5998")]
    collect = AsyncMock(side_effect=[_SnapshotChanged("count changed"), expected])

    with (
        patch("src.core.monitors.brassring._discover_page", collect),
        patch("src.core.monitors.brassring.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
        result = await discover({"board_url": BOARD_URL, "metadata": {}}, pw=object())

    assert result == expected
    assert collect.await_count == 2
    sleep.assert_awaited_once()
