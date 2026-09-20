from __future__ import annotations

import csv
import json
from unittest.mock import AsyncMock, call, patch

import httpx
import pytest

from src.core.monitors import monitor_needs_browser
from src.core.monitors.dom import BotChallengeError
from src.core.monitors.njoyn import (
    _discover_page,
    _expected_count,
    _is_job_detail_url,
    _ListingPass,
    _pagination_state,
    _raise_if_njoyn_challenge,
    _reconcile_listing_passes,
    can_handle,
    discover,
)
from src.core.scrapers.jsonld import parse_html as parse_jsonld_html
from src.shared.constants import DATA_DIR
from src.shared.proxy import ProxyPoolExhaustedError


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
        page_count_by_page: dict[int, int] | None = None,
        page_count_sequences: dict[int, list[int]] | None = None,
        link_sequences: dict[int, list[list[str]]] | None = None,
        page_number_sequences: dict[int, list[str | None]] | None = None,
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
        self.page_count_by_page = page_count_by_page or {}
        self.page_count_sequences = {
            page_number: list(values)
            for page_number, values in (page_count_sequences or {}).items()
        }
        self.link_sequences = {
            page_number: [list(urls) for urls in values]
            for page_number, values in (link_sequences or {}).items()
        }
        self.page_number_sequences = {
            page_number: list(values)
            for page_number, values in (page_number_sequences or {}).items()
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
        page_count_sequence = self.page_count_sequences.get(page_number)
        page_count = (
            page_count_sequence.pop(0)
            if page_count_sequence
            else self.page_count_by_page.get(page_number, len(self.pages))
        )
        link_sequence = self.link_sequences.get(page_number)
        links = link_sequence.pop(0) if link_sequence else self.pages[self.index]
        page_number_sequence = self.page_number_sequences.get(page_number)
        raw_page_number = (
            page_number_sequence.pop(0) if page_number_sequence else str(self.index + 1)
        )
        result_text = "" if expected is None else f"Search Results ({expected})"
        return {
            "links": links,
            "text": (f"Current opportunities\n{result_text}\nPage {page_number} of {page_count}"),
            "pageNumber": raw_page_number,
        }


class _RecordingPageContext:
    def __init__(self, page: object, exit_types: list[type[BaseException] | None]):
        self.page = page
        self.exit_types = exit_types

    async def __aenter__(self):
        return self.page

    async def __aexit__(self, exc_type, *_args):
        self.exit_types.append(exc_type)
        return False


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


def test_parses_visible_pagination_state() -> None:
    assert _pagination_state("Page 1 of 62  NEXT") == (1, 62)
    assert _pagination_state("Current opportunities") is None


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
        "page_wait_ms": 4_000,
        "transport_attempts": 5,
        "direct_fallback_on_origin_block": True,
        "delist_threshold": 4,
    }
    assert monitor_needs_browser("njoyn", config)


def test_cgi_configs_use_rendered_jobposting_jsonld() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as source:
        row = next(row for row in csv.DictReader(source) if row["board_slug"] == "cgi-global-njoyn")

    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])
    assert row["scraper_type"] == "json-ld"
    assert monitor_config["persistent_context"] is True
    assert scraper_config["persistent_context"] is True
    assert monitor_config["channel"] == "chrome"
    assert scraper_config["channel"] == "chrome"
    assert monitor_config["page_wait_ms"] == 4_000
    assert monitor_config["transport_attempts"] == 5
    assert monitor_config["direct_fallback_on_origin_block"] is True
    assert monitor_config["delist_threshold"] == 4
    assert scraper_config["render"] is True
    assert scraper_config["proxy"] is True
    assert scraper_config["transport_attempts"] == 5
    assert scraper_config["direct_fallback_on_origin_block"] is True
    assert "steps" not in scraper_config


def test_cgi_current_detail_jobposting_contract_extracts_required_fields() -> None:
    html = """
    <html>
      <head>
        <title>Careers | CGI.com</title>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Platform Engineer",
            "description": "<p>Build durable systems.</p>",
            "datePosted": "2026-09-15",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"@type": "Organization", "name": "CGI"},
            "jobLocation": {
              "@type": "Place",
              "address": {
                "@type": "PostalAddress",
                "addressLocality": "Zurich",
                "addressCountry": "Switzerland"
              }
            }
          }
        </script>
      </head>
      <body><h1>Careers</h1><section class="job-desc"><h1>Platform Engineer</h1></section></body>
    </html>
    """

    content = parse_jsonld_html(html)

    assert content.title == "Platform Engineer"
    assert content.description == "<p>Build durable systems.</p>"
    assert content.locations and "Zurich" in content.locations[0]
    assert content.employment_type == "FULL_TIME"
    assert content.date_posted == "2026-09-15"


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


async def test_validates_full_pages_and_partial_final_page() -> None:
    expected = {_job(f"J{index}", index) for index in range(1, 6)}
    page = _FakePage(
        [
            [_job("J1", 1), _job("J2", 2)],
            [_job("J3", 3), _job("J4", 4)],
            [_job("J5", 5)],
        ],
        expected=5,
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

    assert urls == expected


async def test_fails_closed_when_final_page_row_count_disagrees_with_total() -> None:
    page = _FakePage(
        [
            [_job("J1", 1), _job("J2", 2)],
            [_job("J3", 3), _job("J4", 4)],
            [_job("J5", 5), _job("J6", 6)],
        ],
        expected=5,
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
        pytest.raises(RuntimeError, match="last_reason=page_row_count_mismatch"),
    ):
        await _discover_page(page, page.url, {})


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
        urls = await _discover_page(page, page.url, {"page_wait_ms": 0})

    assert urls == {_job("J1", 1), _job("J2", 2)}
    assert page.submissions == [2, 2]


async def test_fails_closed_when_max_pages_cannot_reach_total() -> None:
    page = _FakePage(
        [[_job("J1", 1)]],
        expected=2,
        page_count_by_page={1: 2},
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


async def test_reconciles_small_result_total_change_between_pages() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)], [_job("J3", 3)]],
        expected=3,
        expected_sequences={1: [2]},
        page_count_sequences={1: [2]},
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

    assert urls == {_job("J1", 1), _job("J2", 2), _job("J3", 3)}
    assert page.submissions == [2, 3, 2, 3]


async def test_retries_complete_pair_when_page_shape_changes_once() -> None:
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


async def test_retries_complete_pair_when_numeric_page_state_is_temporarily_missing() -> None:
    page = _FakePage(
        [[_job("J1", 1)]],
        expected=1,
        page_number_sequences={1: [None]},
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

    assert urls == {_job("J1", 1)}
    assert navigate.await_count == 3
    sleep.assert_awaited_once_with(2.0)


async def test_reconciles_first_page_change_with_conservative_union() -> None:
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

    assert urls == {_job("J1", 1), _job("J2", 2), _job("J3", 3)}


async def test_fails_closed_when_inventory_fingerprint_drift_exceeds_bound() -> None:
    first = [[_job(f"J-old-{page_number}", page_number)] for page_number in range(1, 6)]
    changed = [[_job(f"J-new-{page_number}", page_number)] for page_number in range(1, 6)]
    page = _FakePage(
        first,
        expected=5,
        link_sequences={
            page_number: [first_urls, changed_urls, first_urls, changed_urls]
            for page_number, (first_urls, changed_urls) in enumerate(
                zip(first, changed, strict=True),
                start=1,
            )
        },
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
        pytest.raises(
            RuntimeError,
            match="last_reason=reconciliation_fingerprint_drift_exceeded",
        ),
    ):
        await _discover_page(page, page.url, {})


async def test_reconciles_last_page_change_with_conservative_union() -> None:
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

    assert urls == {_job("J1", 1), _job("J2", 2), _job("J3", 3)}


async def test_reconciles_middle_page_change_with_conservative_union() -> None:
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

    assert urls == {
        _job("J1", 1),
        _job("J-old-middle", 2),
        _job("J-new-middle", 2),
        _job("J3", 3),
    }


def test_fails_closed_when_reconciled_total_drift_exceeds_bound() -> None:
    urls = frozenset({_job(f"J{index}", index) for index in range(100)})
    first = _ListingPass(urls, (100,), 2)
    second = _ListingPass(urls, (103,), 3)

    with pytest.raises(RuntimeError, match="reconciliation_total_drift_exceeded"):
        _reconcile_listing_passes(first, second)


def test_fails_closed_when_reconciled_union_exceeds_global_job_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.core.monitors.njoyn.MAX_JOBS", 3)
    first = _ListingPass(frozenset({_job("J1", 1), _job("J2", 2), _job("J3", 3)}), (3,), 1)
    second = _ListingPass(frozenset({_job("J1", 1), _job("J2", 2), _job("J4", 4)}), (3,), 1)

    with pytest.raises(RuntimeError, match="reconciliation_job_cap_exceeded"):
        _reconcile_listing_passes(first, second)


async def test_fails_closed_on_radware_challenge_without_logging_its_url() -> None:
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
        pytest.raises(BotChallengeError, match="origin rejected") as raised,
    ):
        await _discover_page(page, page.url, {})

    assert "perfdrive" not in str(raised.value).lower()
    assert raised.value.__cause__ is None


async def test_origin_block_diagnostic_identifies_pagination_progress() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)]],
        expected=2,
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
            side_effect=[
                "<html>jobs</html>",
                "<html><head><title>Radware Captcha Page</title></head></html>",
            ],
        ),
        patch("src.core.monitors.njoyn.log.warning") as warning,
        pytest.raises(BotChallengeError, match="origin rejected"),
    ):
        await _discover_page(page, page.url, {"page_wait_ms": 0})

    warning.assert_any_call(
        "njoyn.transport.origin_block",
        phase="pagination",
        target_page=2,
        collected=1,
        expected=2,
    )


async def test_applies_safe_default_pacing_to_every_page_transition() -> None:
    page = _FakePage(
        [[_job("J1", 1)], [_job("J2", 2)]],
        expected=2,
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
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        urls = await _discover_page(page, page.url, {})

    assert urls == {_job("J1", 1), _job("J2", 2)}
    assert sleep.await_args_list == [call(4.0), call(4.0)]


def test_classifies_tiny_njoyn_xwp_response_as_origin_block() -> None:
    html = "<html><body>Invalid request XWP10022</body></html>"

    with pytest.raises(BotChallengeError, match="origin rejected") as raised:
        _raise_if_njoyn_challenge("https://cgi.njoyn.com/corp/xweb/XWeb.asp", html)

    assert raised.value.proxy_failure_reason == "origin_block"


def test_does_not_classify_xwp_text_inside_a_normal_sized_page() -> None:
    html = "<html><body>Invalid request XWP10022" + (" job listing" * 100) + "</body></html>"

    _raise_if_njoyn_challenge("https://cgi.njoyn.com/corp/xweb/XWeb.asp", html)


async def test_rotates_blocked_proxy_contexts_then_uses_explicit_direct_fallback() -> None:
    board_url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    board = {
        "board_url": board_url,
        "metadata": {
            "proxy": True,
            "transport_attempts": 2,
            "direct_fallback_on_origin_block": True,
        },
    }
    exit_types: list[type[BaseException] | None] = []
    transports: list[bool] = []

    def fake_open_page(_pw, _config, *, use_proxy: bool, target_url: str):
        assert target_url == board_url
        transports.append(use_proxy)
        return _RecordingPageContext(object(), exit_types)

    expected = {_job("J1", 1)}
    with (
        patch("src.core.monitors.njoyn.open_page", side_effect=fake_open_page),
        patch(
            "src.core.monitors.njoyn._discover_page",
            new_callable=AsyncMock,
            side_effect=[
                BotChallengeError("blocked proxy one"),
                BotChallengeError("blocked proxy two"),
                expected,
            ],
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await discover(board, AsyncMock(), pw=object())

    assert result == expected
    assert transports == [True, True, False]
    assert exit_types == [BotChallengeError, BotChallengeError, None]
    assert sleep.await_args_list == [call(1.0)]


async def test_does_not_use_direct_fallback_without_explicit_opt_in() -> None:
    board_url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    board = {
        "board_url": board_url,
        "metadata": {"proxy": True, "transport_attempts": 2},
    }
    exit_types: list[type[BaseException] | None] = []
    transports: list[bool] = []

    def fake_open_page(_pw, _config, *, use_proxy: bool, target_url: str):
        assert target_url == board_url
        transports.append(use_proxy)
        return _RecordingPageContext(object(), exit_types)

    with (
        patch("src.core.monitors.njoyn.open_page", side_effect=fake_open_page),
        patch(
            "src.core.monitors.njoyn._discover_page",
            new_callable=AsyncMock,
            side_effect=[
                BotChallengeError("blocked proxy one"),
                BotChallengeError("blocked proxy two"),
            ],
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(BotChallengeError, match="blocked proxy two"),
    ):
        await discover(board, AsyncMock(), pw=object())

    assert transports == [True, True]
    assert exit_types == [BotChallengeError, BotChallengeError]


async def test_direct_fallback_survives_pool_exhaustion_after_observed_block() -> None:
    board_url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    board = {
        "board_url": board_url,
        "metadata": {
            "proxy": True,
            "transport_attempts": 5,
            "direct_fallback_on_origin_block": True,
        },
    }
    exit_types: list[type[BaseException] | None] = []
    transports: list[bool] = []

    def fake_open_page(_pw, _config, *, use_proxy: bool, target_url: str):
        assert target_url == board_url
        transports.append(use_proxy)
        if transports == [True, True]:
            raise ProxyPoolExhaustedError("all origin slots cooling down")
        return _RecordingPageContext(object(), exit_types)

    expected = {_job("J1", 1)}
    with (
        patch("src.core.monitors.njoyn.open_page", side_effect=fake_open_page),
        patch(
            "src.core.monitors.njoyn._discover_page",
            new_callable=AsyncMock,
            side_effect=[BotChallengeError("blocked proxy"), expected],
        ),
        patch("src.core.monitors.njoyn.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await discover(board, AsyncMock(), pw=object())

    assert result == expected
    assert transports == [True, True, False]
    assert exit_types == [BotChallengeError, None]
    assert sleep.await_args_list == [call(1.0)]


async def test_never_bypasses_an_unavailable_proxy_without_observed_origin_block() -> None:
    board_url = "https://cgi.njoyn.com/corp/xweb/XWeb.asp?CLID=21001&page=joblisting"
    board = {
        "board_url": board_url,
        "metadata": {
            "proxy": True,
            "direct_fallback_on_origin_block": True,
        },
    }
    transports: list[bool] = []

    def fake_open_page(_pw, _config, *, use_proxy: bool, target_url: str):
        assert target_url == board_url
        transports.append(use_proxy)
        raise ProxyPoolExhaustedError("proxy unavailable")

    with (
        patch("src.core.monitors.njoyn.open_page", side_effect=fake_open_page),
        pytest.raises(ProxyPoolExhaustedError, match="proxy unavailable"),
    ):
        await discover(board, AsyncMock(), pw=object())

    assert transports == [True]
