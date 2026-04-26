"""Tests for the dedicated Accenture monitor."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.accenture import (
    FINDJOBS,
    JOBSEARCH,
    _build_body,
    _discover_values,
    _fetch_page_with_retry,
    _make_filter,
    _paginate,
    _parse_findjobs_job,
    _parse_items,
    _parse_jobsearch_job,
    _url_key_for,
)

# ---------------------------------------------------------------------------
# _build_body
# ---------------------------------------------------------------------------


class TestBuildBody:
    def test_basic_fields(self):
        body = _build_body(0, "USA", "en", "us-en")
        assert 'name="startIndex"\r\n\r\n0' in body
        assert 'name="maxResultSize"\r\n\r\n500' in body
        assert 'name="jobCountry"\r\n\r\nUSA' in body
        assert 'name="jobLanguage"\r\n\r\nen' in body
        assert 'name="countrySite"\r\n\r\nus-en' in body
        assert 'name="sortBy"\r\n\r\n2' in body
        assert 'name="totalHits"\r\n\r\ntrue' in body

    def test_offset(self):
        body = _build_body(500, "India", "en", "in-en")
        assert 'name="startIndex"\r\n\r\n500' in body

    def test_no_filters_by_default(self):
        body = _build_body(0, "USA", "en", "us-en")
        assert "jobFilters" not in body

    def test_with_filters(self):
        filters = [{"fieldName": "businessArea.keyword", "items": ["Technology"]}]
        body = _build_body(0, "USA", "en", "us-en", filters=filters)
        assert "jobFilters" in body
        # Parse the jobFilters value from multipart
        assert "Technology" in body

    def test_multipart_format(self):
        body = _build_body(0, "USA", "en", "us-en")
        # Should start with delimiter and end with closing delimiter
        assert body.startswith("------FormBoundary\r\n")
        assert body.endswith("------FormBoundary--")

    def test_unicode_country(self):
        body = _build_body(0, "日本", "ja", "jp-ja")
        assert 'name="jobCountry"\r\n\r\n日本' in body


# ---------------------------------------------------------------------------
# _parse_findjobs_job
# ---------------------------------------------------------------------------


class TestParseFindjobsJob:
    def test_basic(self):
        raw = {
            "guid": "abc-123",
            "title": "Software Engineer",
            "jobDescription": "<p>Great job</p>",
            "location": "New York, NY",
            "remoteType": "Remote",
            "postedDate": "2025-01-15",
            "businessArea": "Technology",
            "careerLevel": "Senior",
        }
        job = _parse_findjobs_job(raw, "us-en")
        assert job is not None
        assert job.url == "https://www.accenture.com/us-en/careers/jobdetails?id=abc-123"
        assert job.title == "Software Engineer"
        assert job.description == "<p>Great job</p>"
        assert job.locations == ["New York, NY"]
        assert job.job_location_type == "Remote"
        assert job.date_posted == "2025-01-15"
        assert job.metadata == {
            "businessArea": "Technology",
            "careerLevel": "Senior",
            "guid": "abc-123",
        }

    def test_missing_guid_returns_none(self):
        assert _parse_findjobs_job({}, "us-en") is None
        assert _parse_findjobs_job({"title": "No GUID"}, "us-en") is None

    def test_location_as_list(self):
        raw = {"guid": "x", "location": ["Berlin", "Munich"]}
        job = _parse_findjobs_job(raw, "de-de")
        assert job.locations == ["Berlin", "Munich"]

    def test_no_optional_fields(self):
        raw = {"guid": "x"}
        job = _parse_findjobs_job(raw, "us-en")
        assert job is not None
        assert job.title is None
        assert job.description is None
        assert job.locations is None
        assert job.metadata == {"guid": "x"}

    def test_site_in_url(self):
        raw = {"guid": "test-id"}
        job = _parse_findjobs_job(raw, "in-en")
        assert "in-en" in job.url


# ---------------------------------------------------------------------------
# _parse_jobsearch_job
# ---------------------------------------------------------------------------


class TestParseJobsearchJob:
    def test_basic(self):
        raw = {
            "jobDetailUrl": "https://www.accenture.com/br-pt/careers/jobdetails?id=xyz",
            "title": "Analista",
            "jobCityState": "São Paulo, SP",
            "postedDate": "2025-03-01",
        }
        job = _parse_jobsearch_job(raw)
        assert job is not None
        assert job.url == "https://www.accenture.com/br-pt/careers/jobdetails?id=xyz"
        assert job.title == "Analista"
        assert job.locations == ["São Paulo, SP"]
        assert job.date_posted == "2025-03-01"

    def test_missing_url_returns_none(self):
        assert _parse_jobsearch_job({}) is None

    def test_relative_url_made_absolute(self):
        raw = {"jobDetailUrl": "/fr-fr/careers/jobdetails?id=123"}
        job = _parse_jobsearch_job(raw)
        assert job.url == "https://www.accenture.com/fr-fr/careers/jobdetails?id=123"

    def test_no_description(self):
        """jobsearch/result items don't include descriptions."""
        raw = {"jobDetailUrl": "https://example.com/job"}
        job = _parse_jobsearch_job(raw)
        assert job.description is None


# ---------------------------------------------------------------------------
# _discover_values
# ---------------------------------------------------------------------------


class TestDiscoverValues:
    def test_extracts_unique_values(self):
        items = [
            {"businessArea": "Technology"},
            {"businessArea": "Operations"},
            {"businessArea": "Technology"},
            {"businessArea": "Song"},
        ]
        result = _discover_values(items, "businessArea")
        assert result == {"Technology", "Operations", "Song"}

    def test_skips_missing_field(self):
        items = [
            {"businessArea": "Tech"},
            {"other": "field"},
            {},
        ]
        result = _discover_values(items, "businessArea")
        assert result == {"Tech"}

    def test_empty_items(self):
        assert _discover_values([], "businessArea") == set()


# ---------------------------------------------------------------------------
# _make_filter
# ---------------------------------------------------------------------------


class TestMakeFilter:
    def test_format(self):
        f = _make_filter("businessArea", "Technology")
        assert f == {
            "fieldName": "businessArea.keyword",
            "items": ["Technology"],
            "multiSelect": False,
        }


# ---------------------------------------------------------------------------
# _url_key_for
# ---------------------------------------------------------------------------


class TestUrlKeyFor:
    def test_findjobs(self):
        assert _url_key_for(FINDJOBS) == "guid"

    def test_jobsearch(self):
        assert _url_key_for(JOBSEARCH) == "jobDetailUrl"


# ---------------------------------------------------------------------------
# _parse_items
# ---------------------------------------------------------------------------


class TestParseItems:
    def test_findjobs_endpoint(self):
        raw_items = [
            {"guid": "a", "title": "Job A"},
            {"guid": "b", "title": "Job B"},
            {"no_guid": True},  # should be skipped
        ]
        jobs = _parse_items(raw_items, FINDJOBS, "us-en")
        assert len(jobs) == 2
        assert jobs[0].title == "Job A"
        assert jobs[1].title == "Job B"

    def test_jobsearch_endpoint(self):
        raw_items = [
            {"jobDetailUrl": "https://example.com/a", "title": "Job A"},
            {"no_url": True},
        ]
        jobs = _parse_items(raw_items, JOBSEARCH, "fr-fr")
        assert len(jobs) == 1
        assert jobs[0].title == "Job A"


# ---------------------------------------------------------------------------
# Dedup logic (integration-style)
# ---------------------------------------------------------------------------


class TestDedup:
    def test_guid_dedup(self):
        """Items with the same guid should be deduplicated."""
        items = [
            {"guid": "a", "title": "First"},
            {"guid": "b", "title": "Second"},
            {"guid": "a", "title": "Duplicate"},
        ]
        seen: set[str] = set()
        deduped: list[dict] = []
        for item in items:
            k = item.get("guid")
            if k and k not in seen:
                seen.add(k)
                deduped.append(item)
        assert len(deduped) == 2
        assert deduped[0]["title"] == "First"
        assert deduped[1]["title"] == "Second"


# ---------------------------------------------------------------------------
# Pagination failure semantics (#2735)
# ---------------------------------------------------------------------------


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError as ``raise_for_status`` would emit."""
    request = httpx.Request("POST", "https://www.accenture.com/api/x")
    response = httpx.Response(status, text="error", request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


_START_INDEX_RE = re.compile(r'name="startIndex"\r\n\r\n(\d+)')


def _start_index(body: str) -> int | None:
    """Parse the ``startIndex`` value out of a ``_build_body`` multipart body.

    Robust against changes to surrounding multipart whitespace — used by
    body-dispatching test handlers below to route per-offset responses.
    """
    match = _START_INDEX_RE.search(body)
    return int(match.group(1)) if match else None


class TestFetchPageWithRetry:
    """``_fetch_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    Accenture's POST endpoint: 5xx / 408 / 425 / 429 / network errors are
    retried, non-retryable 4xx fail fast, and persistent failures raise
    :class:`PaginationFetchError` so a single broken partition doesn't
    silently truncate the run (#2735).
    """

    async def test_returns_on_success(self):
        fetch_fn = AsyncMock(return_value={"data": [{"guid": "1"}], "totalHits": 1})
        items, total = await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS)
        assert items == [{"guid": "1"}]
        assert total == 1
        assert fetch_fn.await_count == 1

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        from src.core.monitors import accenture as acc_module

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock(
            side_effect=[
                _http_status_error(503),
                _http_status_error(503),
                {"data": [{"guid": "1"}], "totalHits": 1},
            ]
        )
        items, total = await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS, base_delay=0.001)
        assert items == [{"guid": "1"}]
        assert total == 1
        assert fetch_fn.await_count == 3

    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        """Cloudflare origin codes 520-526/530 are retried (parity with
        dom + sitemap + PCSX). Pinned for one representative code; the
        full set is exercised by ``test_http_retry`` in PR #2736."""
        from src.core.monitors import accenture as acc_module

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock()
        fetch_fn.side_effect = [
            _http_status_error(520),
            {"data": [{"guid": "1"}], "totalHits": 1},
        ]
        items, _ = await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS, base_delay=0.001)
        assert items == [{"guid": "1"}]
        assert fetch_fn.await_count == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        from src.core.monitors import accenture as acc_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock(side_effect=_http_status_error(503))
        with pytest.raises(PaginationFetchError) as exc_info:
            await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS, retries=3, base_delay=0.001)
        assert exc_info.value.last_status == 503
        assert exc_info.value.attempts == 3
        assert fetch_fn.await_count == 3

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 / 400 indicates a hard error (auth expired,
        misconfigured request) — no point retrying. Raise
        ``PaginationFetchError`` on the first attempt so the run is
        recorded as a failure."""
        from src.core.monitors import accenture as acc_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock(side_effect=_http_status_error(401))
        with pytest.raises(PaginationFetchError) as exc_info:
            await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS, retries=3, base_delay=0.001)
        assert exc_info.value.last_status == 401
        # Exactly one attempt — no retry on non-retryable 4xx.
        assert fetch_fn.await_count == 1

    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import accenture as acc_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
        with pytest.raises(PaginationFetchError) as exc_info:
            await _fetch_page_with_retry(fetch_fn, "body", FINDJOBS, retries=2, base_delay=0.001)
        assert exc_info.value.last_status is None
        assert exc_info.value.last_error == "ConnectError"
        assert fetch_fn.await_count == 2


class TestPaginatePartitionFailure:
    """Issue #2735 acceptance: a 503 on one partition raises
    :class:`PaginationFetchError` (the conservative path).

    Previously, ``_paginate`` used ``asyncio.gather(return_exceptions=True)``
    and silently dropped failed pages with a warning log — a partition
    failing at offset=2000 would shrink the discovered set and downstream
    gone-detection would tombstone real jobs. Now the run is recorded
    as a failure end-to-end.
    """

    async def test_seed_failure_propagates(self, monkeypatch):
        from src.core.monitors import accenture as acc_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())
        fetch_fn = AsyncMock(side_effect=_http_status_error(503))
        with pytest.raises(PaginationFetchError):
            await _paginate(fetch_fn, "USA", "en", "us-en", FINDJOBS)

    async def test_partition_503_raises_not_silent_truncation(self, monkeypatch):
        """Seed page returns enough items to trigger pagination
        (totalHits=1500 → offsets 500, 1000). Offset 500 returns 503
        persistently; offset 1000 returns success. The run must raise
        ``PaginationFetchError`` rather than returning the 1000 items
        from the seed + the second partition (silent truncation).
        """
        from src.core.monitors import accenture as acc_module
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())

        async def fake_fetch(method, url, headers, body):
            offset = _start_index(body)
            if offset == 0:
                return {
                    "data": [{"guid": f"seed-{i}"} for i in range(500)],
                    "totalHits": 1500,
                }
            if offset == 500:
                # Persistent 503 across all retries on this offset.
                raise _http_status_error(503)
            if offset == 1000:
                return {"data": [{"guid": f"p2-{i}"} for i in range(500)], "totalHits": 1500}
            raise AssertionError(f"unexpected offset in body: {offset!r}")

        fetch_fn = AsyncMock(side_effect=fake_fetch)
        with pytest.raises(PaginationFetchError) as exc_info:
            await _paginate(fetch_fn, "USA", "en", "us-en", FINDJOBS)
        assert exc_info.value.last_status == 503

    async def test_all_partitions_succeed(self, monkeypatch):
        """Sanity: when no partition fails, ``_paginate`` returns the
        aggregated set without raising — confirms the gather contract
        change didn't break the happy path.
        """
        from src.core.monitors import accenture as acc_module

        monkeypatch.setattr(acc_module.asyncio, "sleep", AsyncMock())

        async def fake_fetch(method, url, headers, body):
            offset = _start_index(body)
            if offset in (0, 500, 1000):
                return {
                    "data": [{"guid": f"o{offset}-{i}"} for i in range(500)],
                    "totalHits": 1500,
                }
            raise AssertionError(f"unexpected offset in body: {offset!r}")

        fetch_fn = AsyncMock(side_effect=fake_fetch)
        items, hit_ceiling = await _paginate(fetch_fn, "USA", "en", "us-en", FINDJOBS)
        assert len(items) == 1500
        assert hit_ceiling is False
