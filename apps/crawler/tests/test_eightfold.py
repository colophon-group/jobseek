"""Integration tests for the hybrid eightfold monitor.

Mocks sitemap XML + PCSX JSON via httpx.MockTransport and exercises the
full discover_stream orchestration. Unit-level tests for _pcsx and
_watermark helpers live in test_pcsx.py and test_watermark.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors.eightfold import (
    can_handle,
    discover,
    discover_stream,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.kering.com/careers/job/111-gucci-foo?domain=kering</loc></url>
  <url><loc>https://careers.kering.com/careers/job/222-saint-laurent-bar?domain=kering</loc></url>
  <url><loc>https://careers.kering.com/careers/job/333-bottega-baz?domain=kering</loc></url>
</urlset>
"""


def _iso_now_minus(*, days: int = 0, hours: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


def _pcsx_response(positions: list[dict], count: int | None = None) -> dict:
    return {
        "data": {
            "positions": positions,
            "count": count if count is not None else len(positions),
        }
    }


def _make_handler(
    sitemap_xml: str,
    pcsx_pages: list[list[dict]] | None = None,
    pcsx_status: int = 200,
    pcsx_body_override: dict | None = None,
):
    """Build a mock HTTP handler returning sitemap XML and PCSX JSON.

    ``pcsx_pages`` is a list of page responses (each a list of position
    dicts). Fetching offset=N returns pcsx_pages[N // 10].
    """

    call_log: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        call_log.append({"url": url, "params": dict(request.url.params)})
        if "sitemap.xml" in url:
            return httpx.Response(
                200,
                text=sitemap_xml,
                headers={"content-type": "application/xml"},
            )
        if "api/pcsx/search" in url:
            if pcsx_status != 200:
                return httpx.Response(
                    pcsx_status,
                    json=pcsx_body_override or {"message": "error"},
                )
            offset = int(request.url.params.get("start", 0))
            num = int(request.url.params.get("num", 10))
            if num == 1:
                # get_count / probe path: return the first page with count.
                total = sum(len(p) for p in (pcsx_pages or []))
                first = (pcsx_pages[0] if pcsx_pages else [])[:1]
                return httpx.Response(200, json=_pcsx_response(first, count=total))
            page_index = offset // 10
            if pcsx_pages and page_index < len(pcsx_pages):
                return httpx.Response(200, json=_pcsx_response(pcsx_pages[page_index]))
            return httpx.Response(200, json=_pcsx_response([]))
        return httpx.Response(404, text=f"unexpected: {url}")

    return handler, call_log


def _pos(job_id: int, posted_ts: int, **extra) -> dict:
    return {
        "positionUrl": f"/careers/job/{job_id}",
        "name": extra.get("name", f"Job {job_id}"),
        "postedTs": posted_ts,
        "standardizedLocations": extra.get("standardizedLocations", ["Milan, Lombardy, IT"]),
        "workLocationOption": extra.get("workLocationOption", "onsite"),
        "department": extra.get("department", "Sales"),
        "atsJobId": extra.get("atsJobId", f"R{job_id}"),
    }


def _board(metadata: dict | None = None) -> dict:
    return {
        "board_url": "https://careers.kering.com",
        "metadata": metadata or {},
    }


async def _run_discover(handler) -> MonitorResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await discover(_board(), client)


async def _run_discover_stream(handler, metadata: dict | None = None):
    """Run discover_stream and collect all yielded results."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = []
        async for r in discover_stream(_board(metadata), client):
            results.append(r)
        return results


class TestFirstRun:
    async def test_first_run_triggers_full_crawl_with_rich_data(self):
        pages = [
            [
                _pos(111, 1000),
                _pos(222, 900),
                _pos(333, 800),
            ],
        ]
        handler, calls = _make_handler(SITEMAP_XML, pcsx_pages=pages)

        # Spy on both pagination functions to verify the correct code path runs.
        # Without this, the test would pass even if fetch_incremental were
        # silently called (with watermark=0 it'd behave the same as fetch_all).
        with (
            patch(
                "src.core.monitors._pcsx.fetch_all",
                wraps=__import__("src.core.monitors._pcsx", fromlist=["fetch_all"]).fetch_all,
            ) as spy_fetch_all,
            patch(
                "src.core.monitors._pcsx.fetch_incremental",
                wraps=__import__(
                    "src.core.monitors._pcsx", fromlist=["fetch_incremental"]
                ).fetch_incremental,
            ) as spy_fetch_incremental,
        ):
            [result] = await _run_discover_stream(handler)

        # Critical assertion: fetch_all ran, fetch_incremental did NOT.
        assert spy_fetch_all.await_count == 1
        assert spy_fetch_incremental.await_count == 0

        assert len(result.urls) == 3
        assert result.jobs_by_url is not None
        assert len(result.jobs_by_url) == 3
        assert result.hybrid is True

        # Check watermark written
        assert result.metadata_updates is not None
        wm = result.metadata_updates["pcsx_watermark"]
        assert wm["max_ts"] == 1000  # highest postedTs
        assert wm["enabled"] is True
        assert "last_full_at" in wm

        # Check rich data was correctly mapped to sitemap URLs
        gucci_url = "https://careers.kering.com/careers/job/111-gucci-foo?domain=kering"
        assert gucci_url in result.jobs_by_url
        assert result.jobs_by_url[gucci_url].title == "Job 111"
        assert result.jobs_by_url[gucci_url].job_location_type == "onsite"

    async def test_first_run_auto_full_crawl_false_falls_back(self):
        """auto_full_crawl=False + no watermark → sitemap-only, no fetch_all.

        Note: the monitor must still probe (because needs_full triggers probe)
        and fall through to the manual-backfill branch. It returns the
        watermark with the pre-existing state preserved (max_ts stays 0).
        """
        metadata = {
            "pcsx_watermark": {"auto_full_crawl": False, "enabled": True},
        }
        pages = [[_pos(111, 1000)]]
        handler, calls = _make_handler(SITEMAP_XML, pcsx_pages=pages)

        with (
            patch("src.core.monitors._pcsx.fetch_all") as spy_fetch_all,
            patch("src.core.monitors._pcsx.fetch_incremental") as spy_fetch_incremental,
        ):
            [result] = await _run_discover_stream(handler, metadata=metadata)

        # Neither pagination function should run — sitemap-only fallback.
        assert spy_fetch_all.await_count == 0
        assert spy_fetch_incremental.await_count == 0

        # Sitemap URLs present, but NO rich data (fetch_all was skipped).
        assert len(result.urls) == 3
        assert not result.jobs_by_url
        # metadata_updates should carry the watermark but max_ts stays 0.
        assert result.metadata_updates is not None
        wm = result.metadata_updates["pcsx_watermark"]
        assert wm["max_ts"] == 0
        # auto_full_crawl must still be False after the fallback
        assert wm["auto_full_crawl"] is False


class TestIncremental:
    async def test_incremental_stops_at_watermark(self):
        # Watermark is 500. First page all above, second page all below.
        metadata = {
            "pcsx_watermark": {
                "max_ts": 500,
                "enabled": True,
                "last_full_at": _iso_now_minus(days=1),
                "last_incremental_at": _iso_now_minus(hours=12),
            }
        }
        pages = [
            [_pos(111, 1000), _pos(222, 900), _pos(333, 800)],  # all new
            [_pos(444, 400), _pos(555, 300)],  # all old → triggers safety
            [],  # safety 1 ends
        ]
        handler, calls = _make_handler(SITEMAP_XML, pcsx_pages=pages)

        with (
            patch(
                "src.core.monitors._pcsx.fetch_all",
                wraps=__import__("src.core.monitors._pcsx", fromlist=["fetch_all"]).fetch_all,
            ) as spy_fetch_all,
            patch(
                "src.core.monitors._pcsx.fetch_incremental",
                wraps=__import__(
                    "src.core.monitors._pcsx", fromlist=["fetch_incremental"]
                ).fetch_incremental,
            ) as spy_fetch_incremental,
        ):
            [result] = await _run_discover_stream(handler, metadata=metadata)

        # Critical: incremental path was taken, full crawl was NOT.
        assert spy_fetch_incremental.await_count == 1
        assert spy_fetch_all.await_count == 0
        # Verify the watermark was passed correctly.
        call_kwargs = spy_fetch_incremental.await_args.kwargs
        assert call_kwargs["max_posted_ts"] == 500

        assert result.jobs_by_url is not None
        # Only 111, 222, 333 are in the sitemap — 444 and 555 get unmatched.
        assert len(result.jobs_by_url) == 3
        wm = result.metadata_updates["pcsx_watermark"]
        # Watermark advances to max of old + new.
        assert wm["max_ts"] == 1000


class TestPcsxDisabled:
    async def test_probe_403_yields_sitemap_only(self):
        handler, _ = _make_handler(
            SITEMAP_XML,
            pcsx_pages=None,
            pcsx_status=403,
            pcsx_body_override={"message": "PCSX is not enabled for this user."},
        )
        [result] = await _run_discover_stream(handler)

        assert len(result.urls) == 3  # sitemap URLs present
        assert not result.jobs_by_url  # no rich data
        wm = result.metadata_updates["pcsx_watermark"]
        assert wm["enabled"] is False

    async def test_cached_disabled_skips_probe(self):
        metadata = {
            "pcsx_watermark": {
                "enabled": False,
                "last_full_at": _iso_now_minus(days=1),
                "max_ts": 100,
            }
        }
        handler, calls = _make_handler(
            SITEMAP_XML,
            pcsx_pages=None,
            pcsx_status=403,
            pcsx_body_override={"message": "PCSX is not enabled for this user."},
        )
        [result] = await _run_discover_stream(handler, metadata=metadata)
        assert not result.jobs_by_url
        # Even though probe was made, result cached enabled=False
        wm = result.metadata_updates["pcsx_watermark"]
        assert wm["enabled"] is False


class TestPcsxFetchError:
    async def test_405_preserves_watermark(self):
        """Rate-limit block → sitemap-only yield, no metadata_updates."""
        metadata = {
            "pcsx_watermark": {
                "max_ts": 500,
                "enabled": True,
                "last_full_at": _iso_now_minus(days=1),
            }
        }
        handler, _ = _make_handler(SITEMAP_XML, pcsx_pages=None, pcsx_status=405)
        [result] = await _run_discover_stream(handler, metadata=metadata)

        assert len(result.urls) == 3
        assert not result.jobs_by_url
        # metadata_updates should be None on fetch failure so the
        # existing watermark is preserved for next run.
        assert result.metadata_updates is None
        assert result.hybrid is True  # still hybrid flag to skip touched update

    async def test_transient_probe_failure_does_not_cache_disabled(self):
        """5xx/network errors during probe must NOT cache enabled=False.

        Previously, any exception in the probe path set ``wm.enabled =
        False`` and cached it, which meant a single transient 5xx would
        permanently disable the board until the weekly full-crawl cycle
        re-probed. Fixed to distinguish DISABLED (stable 403) from
        TRANSIENT (5xx / timeout / parse error) via ``probe_detail``.
        """
        metadata = {
            "pcsx_watermark": {
                "max_ts": 500,
                "enabled": True,
                "last_full_at": _iso_now_minus(days=1),
                "last_incremental_at": _iso_now_minus(hours=12),
            }
        }
        # Force the needs_probe condition via a full-crawl cycle, then
        # return 500 on every PCSX call (transient failure).
        handler, _ = _make_handler(
            SITEMAP_XML,
            pcsx_pages=None,
            pcsx_status=500,
        )
        # Bypass the cached last_full_at by forcing a full crawl via flag.
        metadata["pcsx_force_full_crawl"] = True

        # Skip the real retry backoff to keep the test fast. The retry
        # loop sleeps 5 * 2^attempt × jitter seconds between attempts —
        # ~35 seconds total for 3 attempts.
        async def _instant(_duration):
            return None

        with patch("src.core.monitors._pcsx.asyncio.sleep", new=_instant):
            [result] = await _run_discover_stream(handler, metadata=metadata)

        # Sitemap-only result — PCSX fetch failed, but watermark is NOT
        # updated (would have poisoned enabled=False).
        assert len(result.urls) == 3
        assert not result.jobs_by_url
        # Critical: metadata_updates is None so the existing enabled=True
        # watermark is preserved for the next run to re-probe.
        assert result.metadata_updates is None
        assert result.hybrid is True


class TestForceFullCrawl:
    async def test_force_full_crawl_overrides_incremental(self):
        """pcsx_force_full_crawl=True → fetch_all regardless of watermark.

        Uses spies to verify that ``fetch_all`` is called even when the
        watermark is recent enough that incremental mode would normally run.
        Without the flag override, this metadata state would trigger
        ``fetch_incremental`` — so the spy assertion is the real test.
        """
        metadata = {
            "pcsx_force_full_crawl": True,
            "pcsx_watermark": {
                "max_ts": 999999,  # would normally trigger incremental
                "enabled": True,
                "last_full_at": _iso_now_minus(days=1),
            },
        }
        pages = [[_pos(111, 1000)]]
        handler, calls = _make_handler(SITEMAP_XML, pcsx_pages=pages)

        with (
            patch(
                "src.core.monitors._pcsx.fetch_all",
                wraps=__import__("src.core.monitors._pcsx", fromlist=["fetch_all"]).fetch_all,
            ) as spy_fetch_all,
            patch(
                "src.core.monitors._pcsx.fetch_incremental",
                wraps=__import__(
                    "src.core.monitors._pcsx", fromlist=["fetch_incremental"]
                ).fetch_incremental,
            ) as spy_fetch_incremental,
        ):
            [result] = await _run_discover_stream(handler, metadata=metadata)

        # Force flag must override incremental mode — full crawl runs instead.
        assert spy_fetch_all.await_count == 1
        assert spy_fetch_incremental.await_count == 0

        assert result.jobs_by_url is not None
        wm = result.metadata_updates["pcsx_watermark"]
        # last_full_at must have been advanced past the old value.
        old_last_full = datetime.fromisoformat(metadata["pcsx_watermark"]["last_full_at"])
        new_last_full = datetime.fromisoformat(wm["last_full_at"])
        assert new_last_full > old_last_full


class TestUnmatched:
    async def test_pcsx_id_not_in_sitemap_is_skipped(self):
        pages = [
            [
                _pos(111, 1000),  # in sitemap
                _pos(999, 900),  # NOT in sitemap (too new)
            ]
        ]
        handler, _ = _make_handler(SITEMAP_XML, pcsx_pages=pages)
        [result] = await _run_discover_stream(handler)

        assert result.jobs_by_url is not None
        assert len(result.jobs_by_url) == 1
        assert 999 not in {int(k.split("/")[-1].split("-")[0]) for k in result.jobs_by_url}

    async def test_sitemap_url_without_pcsx_data_still_in_urls(self):
        """Sitemap URLs without matching PCSX are in result.urls (for gone
        detection) but not in jobs_by_url."""
        pages = [[_pos(111, 1000)]]  # only 111 has rich data; 222 and 333 are stubs
        handler, _ = _make_handler(SITEMAP_XML, pcsx_pages=pages)
        [result] = await _run_discover_stream(handler)

        assert len(result.urls) == 3
        assert result.jobs_by_url is not None
        assert len(result.jobs_by_url) == 1


class TestCanHandle:
    async def test_eightfold_ai_subdomain_detected(self):
        handler, _ = _make_handler(SITEMAP_XML)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://bayer.eightfold.ai/careers", client=client)
        assert result is not None
        assert "sitemap_url" in result
        assert "bayer.eightfold.ai" in result["sitemap_url"]

    async def test_non_eightfold_url_returns_none(self):
        def handler(request):
            return httpx.Response(404, text="not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client=client)
        assert result is None
