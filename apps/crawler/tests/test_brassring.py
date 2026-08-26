from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors import monitor_needs_browser
from src.core.monitors.brassring import (
    _board_ids,
    _bounded_inventory_rows,
    _discover_page,
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
    job_id = values["reqid"]
    return {
        "Questions": [{"QuestionName": key, "Value": value} for key, value in values.items()],
        "Link": (
            "https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad"
            f"?partnerid=25416&siteid=5998&PageType=JobDetails&jobid={job_id}"
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


def test_bounded_inventory_accepts_and_slices_non_aligned_final_page():
    rows = list(range(6))

    assert _bounded_inventory_rows(rows, 5, truncated=True) == list(range(5))


def test_uncapped_inventory_rejects_extra_rows():
    with pytest.raises(_SnapshotChanged, match="6 rows for 5 expected"):
        _bounded_inventory_rows(list(range(6)), 5, truncated=False)


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


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def count(self):
        return 1

    async def click(self):
        self.page.clicked.append(self.selector)

    async def evaluate_all(self, _script):
        if self.selector == "#sortBy option":
            return self.page.sort_options
        raise AssertionError(f"unexpected evaluate_all selector: {self.selector}")


class _FakePage:
    def __init__(self, *, sort_options=None):
        self.clicked = []
        self.waited_pages = []
        self.sort_options = ["0", "1"] if sort_options is None else sort_options

    def locator(self, selector):
        return _FakeLocator(self, selector)

    async def wait_for_function(self, _script, *, arg, timeout):
        assert timeout == 60_000
        self.waited_pages.append(arg)


def _payload(total, *rows):
    return {"JobsCount": total, "Jobs": {"Job": list(rows)}}


async def test_discover_uses_sorted_page_one_as_authoritative_snapshot():
    page = _FakePage()

    @asynccontextmanager
    async def fake_open_page(*_args, **_kwargs):
        yield page

    default_only = _row(reqid="9", jobtitle="Default-order row")
    sorted_first = _row(reqid="1", jobtitle="Alpha")
    sorted_second = _row(reqid="2", jobtitle="Beta")
    click_for_json = AsyncMock(
        side_effect=[
            _payload(3, default_only),
            _payload(2, sorted_first),
            _payload(2, sorted_second),
        ]
    )

    with (
        patch("src.core.monitors.brassring.open_page", fake_open_page),
        patch("src.core.monitors.brassring.navigate", new=AsyncMock()),
        patch("src.core.monitors.brassring._click_for_json", click_for_json),
    ):
        jobs = await _discover_page(BOARD_URL, {}, "25416", "5998", object())

    assert [job.metadata["requisition_id"] for job in jobs] == ["1", "2"]
    assert page.clicked == ["#sortBy-button"]
    assert page.waited_pages == [1, 1, 2]
    assert [call.args[1] for call in click_for_json.await_args_list] == [
        "#clearResumeJobsBtn",
        "#sortBy-menu li:nth-child(2)",
        'button[title="Next Page"]:not(.disabled-link)',
    ]


async def test_discover_fails_closed_when_alphabetical_sort_is_unavailable():
    page = _FakePage(sort_options=["0"])

    @asynccontextmanager
    async def fake_open_page(*_args, **_kwargs):
        yield page

    with (
        patch("src.core.monitors.brassring.open_page", fake_open_page),
        patch("src.core.monitors.brassring.navigate", new=AsyncMock()),
        patch(
            "src.core.monitors.brassring._click_for_json",
            new=AsyncMock(return_value=_payload(2, _row(reqid="1"))),
        ),
        pytest.raises(_SnapshotChanged, match="alphabetical sort option"),
    ):
        await _discover_page(BOARD_URL, {}, "25416", "5998", object())


async def test_discover_fails_closed_when_sorted_inventory_disappears():
    page = _FakePage()

    @asynccontextmanager
    async def fake_open_page(*_args, **_kwargs):
        yield page

    click_for_json = AsyncMock(
        side_effect=[
            _payload(2, _row(reqid="1")),
            _payload(0),
        ]
    )

    with (
        patch("src.core.monitors.brassring.open_page", fake_open_page),
        patch("src.core.monitors.brassring.navigate", new=AsyncMock()),
        patch("src.core.monitors.brassring._click_for_json", click_for_json),
        pytest.raises(_SnapshotChanged, match="non-zero to zero"),
    ):
        await _discover_page(BOARD_URL, {}, "25416", "5998", object())


async def test_discover_leaves_single_page_inventory_unsorted():
    page = _FakePage(sort_options=[])

    @asynccontextmanager
    async def fake_open_page(*_args, **_kwargs):
        yield page

    only_row = _row(reqid="1")
    click_for_json = AsyncMock(return_value=_payload(1, only_row))

    with (
        patch("src.core.monitors.brassring.open_page", fake_open_page),
        patch("src.core.monitors.brassring.navigate", new=AsyncMock()),
        patch("src.core.monitors.brassring._click_for_json", click_for_json),
    ):
        jobs = await _discover_page(BOARD_URL, {}, "25416", "5998", object())

    assert [job.metadata["requisition_id"] for job in jobs] == ["1"]
    assert page.clicked == []
    assert page.waited_pages == []
    click_for_json.assert_awaited_once()
