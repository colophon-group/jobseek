from __future__ import annotations

import csv
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors import monitor_needs_browser
from src.core.monitors.dom import BotChallengeError
from src.core.monitors.njoyn import (
    _discover_page,
    _expected_count,
    _is_job_detail_url,
    can_handle,
)
from src.shared.constants import DATA_DIR


def _job(job_id: str, brid: int) -> str:
    return (
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?"
        f"CLID=21001&Page=JobDetails&Jobid={job_id}&BRID={brid}&lang=1"
    )


class _FakeNavigation:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakePage:
    def __init__(
        self,
        pages: list[list[str]],
        expected: int | None,
        *,
        repeat: bool = False,
        raise_after_submit: bool = False,
        wrong_pages: dict[int, list[int]] | None = None,
        expected_by_page: dict[int, int] | None = None,
        expected_sequences: dict[int, list[int]] | None = None,
        link_sequences: dict[int, list[list[str]]] | None = None,
    ):
        self.pages = pages
        self.expected = expected
        self.repeat = repeat
        self.raise_after_submit = raise_after_submit
        self.wrong_pages = {target: list(values) for target, values in (wrong_pages or {}).items()}
        self.expected_by_page = expected_by_page or {}
        self.expected_sequences = {
            page_number: list(values) for page_number, values in (expected_sequences or {}).items()
        }
        self.link_sequences = {
            page_number: [list(urls) for urls in values]
            for page_number, values in (link_sequences or {}).items()
        }
        self.submissions: list[int] = []
        self.index = 0
        self.url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"

    def expect_navigation(self, **_kwargs) -> _FakeNavigation:
        return _FakeNavigation()

    async def navigate(self, *_args, **_kwargs) -> None:
        self.index = 0

    async def evaluate(self, _script: str, target_page: int | None = None):
        if target_page is not None:
            self.submissions.append(target_page)
            if self.repeat:
                return True
            alternatives = self.wrong_pages.get(target_page)
            actual_page = alternatives.pop(0) if alternatives else target_page
            self.index = actual_page - 1
            if self.raise_after_submit:
                raise RuntimeError("execution context destroyed by navigation")
            return True

        page_number = self.index + 1
        expected_sequence = self.expected_sequences.get(page_number)
        expected = (
            expected_sequence.pop(0)
            if expected_sequence
            else self.expected_by_page.get(page_number, self.expected)
        )
        link_sequence = self.link_sequences.get(page_number)
        links = link_sequence.pop(0) if link_sequence else self.pages[self.index]
        result_text = "" if expected is None else f"Search Results ({expected})"
        return {
            "links": links,
            "text": f"Current opportunities\n{result_text}",
            "pageNumber": str(self.index + 1),
        }


def test_recognizes_njoyn_detail_urls_case_insensitively() -> None:
    assert _is_job_detail_url(_job("J0826-0527", 1324213))
    assert not _is_job_detail_url(
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    )
    assert not _is_job_detail_url("https://example.com/?Page=JobDetails&Jobid=1&BRID=2")


@pytest.mark.parametrize(
    "url",
    [
        "http://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&Page=JobDetails&Jobid=1&BRID=2",
        "https://user@cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&Page=JobDetails&Jobid=1&BRID=2",
        "https://cgi.njoyn.com:444/corp/xweb/XWeb.asp?CLID=21001&Page=JobDetails&Jobid=1&BRID=2",
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&CLID=22002&Page=JobDetails&Jobid=1&BRID=2",
    ],
)
def test_rejects_unsafe_or_ambiguous_detail_urls(url: str) -> None:
    assert not _is_job_detail_url(url)


def test_rejects_detail_urls_from_a_different_board_tenant() -> None:
    board_url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&Page=JobListing"
    other_tenant = (
        "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=22002&Page=JobDetails&Jobid=J1&BRID=1"
    )

    assert not _is_job_detail_url(other_tenant, board_url=board_url)


def test_parses_advertised_result_count() -> None:
    assert _expected_count("Search Results (3,071)") == 3071
    assert _expected_count("Current opportunities") is None


async def test_can_handle_returns_hardened_browser_defaults() -> None:
    async with httpx.AsyncClient() as client:
        config = await can_handle(
            "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting",
            client,
        )

    assert config == {
        "wait": "domcontentloaded",
        "timeout": 60_000,
        "persistent_context": True,
        "channel": "chrome",
        "headless": False,
        "stealth": True,
        "proxy": True,
    }
    assert monitor_needs_browser("njoyn", config)


def test_cgi_configs_pin_installed_chrome_channel() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as source:
        row = next(row for row in csv.DictReader(source) if row["board_slug"] == "cgi-global-njoyn")

    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])
    assert monitor_config["persistent_context"] is True
    assert scraper_config["persistent_context"] is True
    assert monitor_config["channel"] == "chrome"
    assert scraper_config["channel"] == "chrome"
    assert monitor_config["page_wait_ms"] == 0


async def test_can_handle_rejects_job_detail_url() -> None:
    async with httpx.AsyncClient() as client:
        assert await can_handle(_job("J0826-0527", 1324213), client) is None


async def test_collects_form_paginated_listing_and_checks_total() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)], [_job("J3", 3)]],
        expected=3,
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ) as navigate,
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
    ):
        urls = await _discover_page(page, page.url, {"page_wait_ms": 1})

    assert urls == {_job("J1", 1), _job("J2", 2), _job("J3", 3)}
    assert page.index == 2
    assert page.submissions == [2, 3, 2, 3]
    assert navigate.await_count == 2


async def test_retries_exact_page_without_mixing_partial_snapshots() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)]],
        expected=2,
        wrong_pages={2: [1, 2]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ) as navigate,
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        urls = await _discover_page(page, page.url, {"page_wait_ms": 1})

    assert urls == {_job("J1", 1), _job("J2", 2)}
    assert page.submissions == [2, 2, 2]
    assert navigate.await_count == 2
    sleep.assert_any_await(1.0)


async def test_accepts_verified_page_when_evaluate_is_interrupted_by_navigation() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)]],
        expected=2,
        raise_after_submit=True,
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J2", 2)}
    assert page.submissions == [2, 2]


async def test_fails_closed_when_max_pages_cannot_reach_total() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=2)
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        pytest.raises(RuntimeError, match="hit max_pages=1"),
    ):
        await _discover_page(page, page.url, {"max_pages": 1})


async def test_fails_closed_without_advertised_total() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=None)
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        pytest.raises(RuntimeError, match="missing its Search Results total"),
    ):
        await _discover_page(page, page.url, {})


async def test_fails_closed_when_next_repeats_same_page() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=2, repeat=True)
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="did not stabilize after 2 complete attempts"),
    ):
        await _discover_page(
            page,
            page.url,
            {"page_wait_ms": 1, "page_change_timeout_ms": 500},
        )


async def test_fails_closed_when_result_total_changes_between_pages() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)]],
        expected=2,
        expected_by_page={2: 3},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="last_reason=result_total_changed"),
    ):
        await _discover_page(page, page.url, {})


async def test_restarts_complete_snapshot_when_result_total_changes_once() -> None:
    discarded = [_job("J-old", 1)]
    stable_first = [_job("J1", 1)]
    page = _FakePage(
        [stable_first, [_job("J2", 2)]],
        expected=2,
        expected_sequences={2: [3]},
        link_sequences={1: [discarded, stable_first, stable_first]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ) as navigate,
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J2", 2)}
    assert _job("J-old", 1) not in urls
    assert navigate.await_count == 3
    assert page.submissions == [2, 2, 2]
    sleep.assert_any_await(2.0)


async def test_restarts_snapshot_when_first_page_fingerprint_changes() -> None:
    first = [_job("J1", 1)]
    changed = [_job("J3", 3)]
    page = _FakePage(
        [first, [_job("J2", 2)]],
        expected=2,
        link_sequences={1: [first, changed]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J2", 2)}


async def test_fails_closed_when_boundary_fingerprint_never_stabilizes() -> None:
    first = [_job("J1", 1)]
    changed = [_job("J3", 3)]
    page = _FakePage(
        [first, [_job("J2", 2)]],
        expected=2,
        link_sequences={1: [first, changed, first, changed]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="last_reason=first_page_fingerprint_changed"),
    ):
        await _discover_page(page, page.url, {})


async def test_restarts_snapshot_when_last_page_fingerprint_changes() -> None:
    original_last = [_job("J2", 2)]
    changed_last = [_job("J3", 3)]
    page = _FakePage(
        [[_job("J1", 1)], original_last],
        expected=2,
        link_sequences={2: [original_last, changed_last]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J2", 2)}


async def test_restarts_snapshot_when_middle_page_changes_with_same_total() -> None:
    old_middle = [_job("J-old-middle", 2)]
    new_middle = [_job("J-new-middle", 2)]
    page = _FakePage(
        [[_job("J1", 1)], new_middle, [_job("J3", 3)]],
        expected=3,
        link_sequences={2: [old_middle, new_middle, new_middle, new_middle]},
    )
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html>jobs</html>",
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J-new-middle", 2), _job("J3", 3)}
    assert _job("J-old-middle", 2) not in urls


async def test_fails_closed_on_radware_challenge() -> None:
    page = _FakePage([[_job("J1", 1)]], expected=1)
    page.url = "https://validate.perfdrive.com/?ssk=botmanager_support@radware.com"
    with (
        patch(
            "src.core.monitors.njoyn.navigate",
            new_callable=AsyncMock,
            side_effect=page.navigate,
        ),
        patch(
            "src.core.monitors.njoyn.safe_content",
            new_callable=AsyncMock,
            return_value="<html><head><title>Radware Captcha Page</title></head></html>",
        ),
        pytest.raises(BotChallengeError, match="proxy transport"),
    ):
        await _discover_page(page, page.url, {})
