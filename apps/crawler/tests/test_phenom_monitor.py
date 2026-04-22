"""Tests for the Phenom People monitor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.phenom import (
    _matches_signature,
    _origin_from_url,
    _parse_job,
    _parse_locations,
    _parse_preload_state,
    can_handle,
    discover,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phenom"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# _origin_from_url
# ---------------------------------------------------------------------------


class TestOriginFromUrl:
    def test_vanity_domain(self):
        assert _origin_from_url("https://careers.mcdonalds.ca/jobs") == (
            "https://careers.mcdonalds.ca"
        )

    def test_root_path(self):
        assert _origin_from_url("https://www.werkenbijmcdonalds.nl/") == (
            "https://www.werkenbijmcdonalds.nl"
        )

    def test_port_preserved(self):
        assert _origin_from_url("https://example.com:8443/jobs") == "https://example.com:8443"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot derive origin"):
            _origin_from_url("not-a-url")


# ---------------------------------------------------------------------------
# _parse_locations
# ---------------------------------------------------------------------------


class TestParseLocations:
    def test_prefers_location_text(self):
        raw = {
            "locations": [
                {"locationText": "Toronto ON, Canada", "city": "Toronto"},
            ]
        }
        assert _parse_locations(raw) == ["Toronto ON, Canada"]

    def test_falls_back_to_city_state(self):
        raw = {"locations": [{"cityState": "Berlin, Germany"}]}
        assert _parse_locations(raw) == ["Berlin, Germany"]

    def test_falls_back_to_city_only(self):
        raw = {"locations": [{"city": "Sydney"}]}
        assert _parse_locations(raw) == ["Sydney"]

    def test_deduplicates(self):
        raw = {
            "locations": [
                {"locationText": "HQ"},
                {"locationText": "HQ"},
                {"locationText": "Branch"},
            ]
        }
        assert _parse_locations(raw) == ["HQ", "Branch"]

    def test_skips_non_dict_entries(self):
        raw = {"locations": [None, "ignored", {"locationText": "Valid"}]}
        assert _parse_locations(raw) == ["Valid"]

    def test_empty_list_returns_none(self):
        assert _parse_locations({"locations": []}) is None

    def test_missing_key_returns_none(self):
        assert _parse_locations({}) is None


# ---------------------------------------------------------------------------
# _parse_job
# ---------------------------------------------------------------------------


class TestParseJob:
    def test_builds_absolute_url_from_relative_original(self):
        raw = {
            "originalURL": "crew-member/job/P8-272291-0",
            "title": "Crew Member",
            "description": "<p>Job</p>",
            "lang": "en",
        }
        job = _parse_job(raw, "https://careers.mcdonalds.ca")
        assert job is not None
        assert job.url == "https://careers.mcdonalds.ca/crew-member/job/P8-272291-0"
        assert job.title == "Crew Member"
        assert job.description == "<p>Job</p>"
        assert job.language == "en"

    def test_original_url_with_leading_slash(self):
        raw = {"originalURL": "/some/path", "title": "X"}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.url == "https://example.com/some/path"

    def test_original_url_absolute_used_verbatim(self):
        raw = {"originalURL": "https://other.example.com/job/1", "title": "X"}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.url == "https://other.example.com/job/1"

    def test_falls_back_to_apply_url(self):
        raw = {
            "applyURL": "https://example.com/apply/123",
            "title": "With Apply",
        }
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.url == "https://example.com/apply/123"

    def test_returns_none_when_no_url(self):
        assert _parse_job({"title": "Ghost"}, "https://example.com") is None

    def test_strips_empty_title(self):
        raw = {"originalURL": "a/b/c", "title": "   "}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.title is None

    def test_metadata_fields(self):
        raw = {
            "originalURL": "x/job/1",
            "title": "X",
            "sourceID": "abc",
            "uniqueID": "U1",
            "reference": "P8-1-0",
            "requisitionID": "9999",
            "companyID": "co-1",
        }
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.metadata == {
            "sourceID": "abc",
            "uniqueID": "U1",
            "reference": "P8-1-0",
            "requisitionID": "9999",
            "companyID": "co-1",
        }

    def test_no_metadata_when_all_blank(self):
        raw = {"originalURL": "x/job/1", "title": "X"}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.metadata is None

    def test_employment_type_list_first_string(self):
        raw = {"originalURL": "x/job/1", "title": "X", "employmentType": ["Full-time", "Contract"]}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.employment_type == "Full-time"

    def test_employment_type_empty_list(self):
        raw = {"originalURL": "x/job/1", "title": "X", "employmentType": []}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.employment_type is None

    def test_employment_type_dict_entry(self):
        raw = {
            "originalURL": "x/job/1",
            "title": "X",
            "employmentType": [{"name": "Permanent"}],
        }
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.employment_type == "Permanent"

    def test_remote_bool(self):
        raw = {"originalURL": "x/job/1", "title": "X", "isRemote": True}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.job_location_type == "TELECOMMUTE"

    def test_remote_string_true(self):
        raw = {"originalURL": "x/job/1", "title": "X", "isRemote": "true"}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.job_location_type == "TELECOMMUTE"

    def test_non_remote(self):
        raw = {"originalURL": "x/job/1", "title": "X", "isRemote": False}
        job = _parse_job(raw, "https://example.com")
        assert job is not None
        assert job.job_location_type is None


# ---------------------------------------------------------------------------
# _parse_job against real fixture data
# ---------------------------------------------------------------------------


class TestParseJobFromFixture:
    def test_ca_sample_parses_all_fields(self):
        page = _fixture("mcdonalds_ca_page1.json")
        jobs = [_parse_job(j, "https://careers.mcdonalds.ca") for j in page["jobs"]]
        assert all(j is not None for j in jobs)
        assert len(jobs) == 3
        first = jobs[0]
        assert first.url.startswith("https://careers.mcdonalds.ca/")
        assert first.url.endswith("P8-126884-0")
        assert first.title
        assert first.description and "<p>" in first.description
        assert first.locations and "Edmonton" in first.locations[0]
        assert first.language == "en"
        assert first.metadata and "uniqueID" in first.metadata

    def test_au_sample_parses_hex_unique_id(self):
        page = _fixture("mcdonalds_au_page1.json")
        jobs = [_parse_job(j, "https://careers.mcdonalds.com.au") for j in page["jobs"]]
        assert all(j is not None for j in jobs)
        # AU uses hex uniqueID (no P8- prefix) in originalURL
        assert any("3F0B18E3" in j.url for j in jobs)
        # Requisition ID surfaced as numeric
        assert jobs[0].metadata["requisitionID"] == "3164"


# ---------------------------------------------------------------------------
# Signature detection
# ---------------------------------------------------------------------------


class TestMatchesSignature:
    def test_full_signature(self):
        html = (
            "<html><body><script>window.__PRELOAD_STATE__ = "
            '{"jobSearch":{"totalJob":42,"jobs":[]}};</script></body></html>'
        )
        assert _matches_signature(html) is True

    def test_missing_preload_state(self):
        html = '<html><body>"jobSearch":{"totalJob":42}</body></html>'
        assert _matches_signature(html) is False

    def test_missing_job_search(self):
        html = '<html><body>window.__PRELOAD_STATE__ = {"other":1}</body></html>'
        assert _matches_signature(html) is False

    def test_missing_total_job(self):
        html = (
            "<html><body><script>window.__PRELOAD_STATE__ = "
            '{"jobSearch":{"jobs":[]}};</script></body></html>'
        )
        assert _matches_signature(html) is False

    def test_empty_html(self):
        assert _matches_signature("") is False
        assert _matches_signature(None) is False


class TestParsePreloadState:
    def test_valid(self):
        html = (
            "prefix\nwindow.__PRELOAD_STATE__ = "
            '{"jobSearch":{"totalJob":7,"jobs":[]}};'
            "window.__OTHER__ = 1; suffix"
        )
        state = _parse_preload_state(html)
        assert state is not None
        assert state["jobSearch"]["totalJob"] == 7

    def test_no_match_returns_none(self):
        assert _parse_preload_state("<html></html>") is None

    def test_invalid_json_returns_none(self):
        html = "window.__PRELOAD_STATE__ = {not-json};window.__"
        assert _parse_preload_state(html) is None


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    async def test_detects_signature(self):
        html = (
            "<html><body><script>window.__PRELOAD_STATE__ = "
            '{"jobSearch":{"totalJob":123,"jobs":[]}};'
            "window.__OTHER__ = 1;</script></body></html>"
        )
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, text=html))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://careers.example.com/", client)
            assert result is not None
            assert result["jobs"] == 123
            assert result["api_path"] == "/api/get-jobs"

    async def test_returns_none_when_signature_absent(self):
        html = "<html><body>Just a regular page</body></html>"
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, text=html))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://example.com/", client) is None

    async def test_returns_none_on_network_failure(self):
        transport = httpx.MockTransport(lambda _r: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://example.com/", client) is None

    async def test_without_client_returns_none(self):
        assert await can_handle("https://careers.example.com/", None) is None

    async def test_detects_without_total_job_value(self):
        # Signature matches but we can't parse totalJob — still detected,
        # just no jobs count.
        html = (
            "<html><body><script>window.__PRELOAD_STATE__ = "
            '{"jobSearch":{"totalJob":"not-an-int","jobs":[]}};'
            "window.__OTHER__ = 1;</script></body></html>"
        )
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, text=html))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://careers.example.com/", client)
            assert result is not None
            assert result.get("jobs") is None
            assert result["api_path"] == "/api/get-jobs"


# ---------------------------------------------------------------------------
# discover (fully mocked browser)
# ---------------------------------------------------------------------------


class _FakePage:
    """Stub Playwright Page that returns pre-baked JSON per page_number."""

    def __init__(self, responses: list[dict]):
        # ``responses[i]`` is the response for page_number ``i+1``; absent
        # entries default to {"jobs": []} with status 200.
        self._responses = responses

    async def goto(self, url, wait_until=None, timeout=None):
        return None

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script, page_number):
        idx = page_number - 1
        if idx < 0 or idx >= len(self._responses):
            return {"status": 200, "data": {"jobs": [], "totalJob": 0}}
        entry = self._responses[idx]
        if entry.get("status", 200) != 200:
            return {"status": entry["status"], "data": None}
        return {"status": 200, "data": entry["data"]}


class _FakeOpenPage:
    def __init__(self, page: _FakePage):
        self._page = page

    async def __aenter__(self):
        return self._page

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_browser(page: _FakePage):
    return patch(
        "src.core.monitors.phenom.open_page",
        lambda pw, cfg: _FakeOpenPage(page),
    )


def _make_raw_job(ref: str, origin_path: str) -> dict:
    return {
        "originalURL": origin_path,
        "title": f"Job {ref}",
        "description": f"<p>{ref}</p>",
        "reference": ref,
        "lang": "en",
        "locations": [{"locationText": f"Loc {ref}"}],
    }


class TestDiscover:
    async def test_single_page(self):
        # totalJob=2 → one page of 2 jobs → empty page stops early.
        responses = [
            {
                "status": 200,
                "data": {
                    "jobs": [
                        _make_raw_job("A1", "role-a/job/P8-1-0"),
                        _make_raw_job("A2", "role-b/job/P8-2-0"),
                    ],
                    "totalJob": 2,
                },
            }
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert len(jobs) == 2
        assert all(isinstance(j, DiscoveredJob) for j in jobs)
        assert jobs[0].url == "https://careers.example.com/role-a/job/P8-1-0"
        assert jobs[1].url == "https://careers.example.com/role-b/job/P8-2-0"

    async def test_multi_page_pagination(self):
        # totalJob=25 → 3 pages (10, 10, 5) under the fixed PAGE_SIZE=10.
        responses = [
            {
                "status": 200,
                "data": {
                    "jobs": [_make_raw_job(f"P1-{i}", f"r{i}/job/P8-{i}-0") for i in range(10)],
                    "totalJob": 25,
                },
            },
            {
                "status": 200,
                "data": {
                    "jobs": [
                        _make_raw_job(f"P2-{i}", f"r{10 + i}/job/P8-{10 + i}-0") for i in range(10)
                    ],
                    "totalJob": 25,
                },
            },
            {
                "status": 200,
                "data": {
                    "jobs": [
                        _make_raw_job(f"P3-{i}", f"r{20 + i}/job/P8-{20 + i}-0") for i in range(5)
                    ],
                    "totalJob": 25,
                },
            },
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert len(jobs) == 25
        # Ordering is deterministic by page
        urls = [j.url for j in jobs]
        assert urls[0] == "https://careers.example.com/r0/job/P8-0-0"
        assert urls[-1] == "https://careers.example.com/r24/job/P8-24-0"

    async def test_empty_response(self):
        responses = [{"status": 200, "data": {"jobs": [], "totalJob": 0}}]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert jobs == []

    async def test_first_page_failure_returns_empty(self):
        responses = [{"status": 500, "data": None}]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert jobs == []

    async def test_mid_pagination_failure_halts_gracefully(self):
        # Page 1 OK with 10 items (totalJob=20), page 2 errors out.
        responses = [
            {
                "status": 200,
                "data": {
                    "jobs": [_make_raw_job(f"A{i}", f"r{i}/job/P8-{i}-0") for i in range(10)],
                    "totalJob": 20,
                },
            },
            {"status": 502, "data": None},
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert len(jobs) == 10

    async def test_dedupes_overlapping_pages(self):
        # Page 1 and 2 share two URLs — de-dup should keep the total
        # unique count, not 20.
        shared = [_make_raw_job(f"shared{i}", f"r{i}/job/P8-{i}-0") for i in range(2)]
        page1 = shared + [_make_raw_job(f"u{i}", f"r{10 + i}/job/P8-{10 + i}-0") for i in range(8)]
        page2 = shared + [_make_raw_job(f"v{i}", f"r{20 + i}/job/P8-{20 + i}-0") for i in range(8)]
        responses = [
            {"status": 200, "data": {"jobs": page1, "totalJob": 20}},
            {"status": 200, "data": {"jobs": page2, "totalJob": 20}},
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        # 2 shared + 8 unique per page = 18 total
        assert len(jobs) == 18
        assert len({j.url for j in jobs}) == 18

    async def test_stops_when_whole_page_is_duplicates(self):
        # The API sometimes returns a cursor-stable page once we paginate
        # past the real end; the monitor should detect this and stop.
        page1 = [_make_raw_job(f"a{i}", f"r{i}/job/P8-{i}-0") for i in range(10)]
        responses = [
            {"status": 200, "data": {"jobs": page1, "totalJob": 50}},
            # Same batch again — every URL is a dup.
            {"status": 200, "data": {"jobs": page1, "totalJob": 50}},
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert len(jobs) == 10

    async def test_missing_total_job_paginates_until_empty(self):
        # ``totalJob`` missing → monitor should keep paging until an empty
        # response (safety cap applies but won't fire with 2 pages).
        responses = [
            {
                "status": 200,
                "data": {
                    "jobs": [_make_raw_job(f"a{i}", f"r{i}/job/P8-{i}-0") for i in range(10)],
                    # totalJob deliberately absent
                },
            },
            {"status": 200, "data": {"jobs": []}},
        ]
        page = _FakePage(responses)
        board = {"board_url": "https://careers.example.com/jobs"}
        with _patch_browser(page):
            async with httpx.AsyncClient() as client:
                jobs = await discover(board, client, pw=object())
        assert len(jobs) == 10

    async def test_requires_playwright(self):
        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="requires Playwright"):
                await discover({"board_url": "https://careers.example.com/"}, client, pw=None)
