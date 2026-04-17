"""Unit tests for src.core.monitors._pcsx."""

from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob  # noqa: F401 — used via mapping return type
from src.core.monitors._pcsx import (
    PcsxDisabled,
    PcsxFetchError,
    PcsxStableBlock,
    build_sitemap_id_map,
    extract_host_and_domain,
    fetch_all,
    fetch_incremental,
    get_count,
    parse_job_id,
    pcsx_to_discovered,
    probe,
)


def _make_client(handler):
    """Construct an httpx.AsyncClient with a MockTransport handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _json_response(data: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


# ── probe ───────────────────────────────────────────────────────────────


class TestProbe:
    async def test_success_returns_true(self):
        async def handler(request):
            return _json_response({"data": {"positions": [{"id": 1}], "count": 1}})

        async with _make_client(handler) as http:
            assert await probe("careers.kering.com", "kering", http) is True

    async def test_403_pcsx_not_enabled_returns_false(self):
        async def handler(request):
            return _json_response({"message": "PCSX is not enabled for this user."}, status=403)

        async with _make_client(handler) as http:
            assert await probe("talent.bayer.com", "bayer.com", http) is False

    async def test_405_returns_false(self):
        """Starbucks-style stable block — probe surfaces it as False."""

        async def handler(request):
            return httpx.Response(405, text="blocked")

        async with _make_client(handler) as http:
            assert await probe("apply.starbucks.com", "starbucks.com", http) is False

    async def test_empty_positions_still_counts_as_enabled(self):
        async def handler(request):
            return _json_response({"data": {"positions": [], "count": 0}})

        async with _make_client(handler) as http:
            assert await probe("careers.example.com", "example", http) is True


# ── get_count ───────────────────────────────────────────────────────────


class TestGetCount:
    async def test_reads_data_count(self):
        async def handler(request):
            return _json_response({"data": {"positions": [{"id": 1}], "count": 909}})

        async with _make_client(handler) as http:
            assert await get_count("careers.kering.com", "kering", http) == 909

    async def test_missing_count_returns_zero(self):
        async def handler(request):
            return _json_response({"data": {"positions": []}})

        async with _make_client(handler) as http:
            assert await get_count("careers.kering.com", "kering", http) == 0

    async def test_non_200_returns_zero(self):
        async def handler(request):
            return httpx.Response(500, text="boom")

        async with _make_client(handler) as http:
            assert await get_count("careers.kering.com", "kering", http) == 0


# ── fetch_all / fetch_incremental ──────────────────────────────────────


class TestFetchAll:
    async def test_paginates_until_empty(self):
        pages = [
            [{"id": i, "postedTs": 1000 - i} for i in range(10)],
            [{"id": i + 10, "postedTs": 990 - i} for i in range(10)],
            [{"id": 20, "postedTs": 980}],
            [],
        ]
        calls: list[int] = []

        async def handler(request):
            offset = int(request.url.params.get("start", 0))
            calls.append(offset)
            page_index = offset // 10
            if page_index < len(pages):
                return _json_response({"data": {"positions": pages[page_index]}})
            return _json_response({"data": {"positions": []}})

        async with _make_client(handler) as http:
            result = await fetch_all("careers.kering.com", "kering", http)
        assert len(result) == 21
        assert calls == [0, 10, 20, 30]


class TestFetchIncremental:
    async def test_stops_at_watermark_plus_safety(self):
        # Jobs descend in postedTs. Watermark=500 means pages with postedTs<=500
        # count as "all old" and trigger safety pages.
        pages = [
            [{"id": i, "postedTs": 1000 - i * 10} for i in range(10)],  # 1000..910
            [{"id": 10 + i, "postedTs": 900 - i * 10} for i in range(10)],  # 900..810
            [{"id": 20 + i, "postedTs": 800 - i * 10} for i in range(10)],  # 800..710
            [{"id": 30 + i, "postedTs": 700 - i * 10} for i in range(10)],  # 700..610
            [{"id": 40 + i, "postedTs": 600 - i * 10} for i in range(10)],  # 600..510
            [{"id": 50 + i, "postedTs": 500 - i * 10} for i in range(10)],  # 500..410 all-old
            [{"id": 60 + i, "postedTs": 400 - i * 10} for i in range(10)],  # safety 1
            [{"id": 70 + i, "postedTs": 300 - i * 10} for i in range(10)],  # safety 2
            [{"id": 80 + i, "postedTs": 200 - i * 10} for i in range(10)],  # safety 3 -> stop
            [{"id": 90 + i, "postedTs": 100 - i * 10} for i in range(10)],  # never reached
        ]
        calls: list[int] = []

        async def handler(request):
            offset = int(request.url.params.get("start", 0))
            calls.append(offset)
            page_index = offset // 10
            if page_index < len(pages):
                return _json_response({"data": {"positions": pages[page_index]}})
            return _json_response({"data": {"positions": []}})

        async with _make_client(handler) as http:
            result = await fetch_incremental(
                "careers.kering.com", "kering", http, max_posted_ts=500, safety_pages=3
            )
        # Pages 0..5 are before/at the boundary (6 pages) + 3 safety pages = 9 pages.
        # Page index 5 is the first all-old page; safety pages follow.
        assert len(calls) == 9
        assert len(result) == 90


# ── parse_job_id ────────────────────────────────────────────────────────


class TestParseJobId:
    def test_short_form(self):
        assert parse_job_id("/careers/job/563705876642261") == "563705876642261"

    def test_full_sitemap_url(self):
        url = (
            "https://careers.kering.com/careers/job/"
            "563705876642261-client-advisor-shandong-china?domain=kering"
        )
        assert parse_job_id(url) == "563705876642261"

    def test_no_match(self):
        assert parse_job_id("/careers/apply/foo") is None

    def test_none(self):
        assert parse_job_id(None) is None

    def test_empty(self):
        assert parse_job_id("") is None


# ── build_sitemap_id_map ───────────────────────────────────────────────


class TestBuildSitemapIdMap:
    def test_builds_map(self):
        urls = [
            "https://careers.kering.com/careers/job/111-foo?domain=kering",
            "https://careers.kering.com/careers/job/222-bar?domain=kering",
        ]
        result = build_sitemap_id_map(urls)
        assert result == {
            "111": "https://careers.kering.com/careers/job/111-foo?domain=kering",
            "222": "https://careers.kering.com/careers/job/222-bar?domain=kering",
        }

    def test_skips_non_job_urls(self):
        urls = [
            "https://careers.kering.com/careers/job/111-foo",
            "https://careers.kering.com/careers/contact",
        ]
        result = build_sitemap_id_map(urls)
        assert result == {"111": "https://careers.kering.com/careers/job/111-foo"}

    def test_duplicate_id_keeps_first(self):
        first = "https://careers.kering.com/careers/job/111-first"
        second = "https://careers.kering.com/careers/job/111-second"
        result = build_sitemap_id_map([first, second])
        # First wins — verify the specific URL, not just count. Without this
        # assertion, a buggy "last wins" implementation would still pass.
        assert result == {"111": first}


# ── extract_host_and_domain ────────────────────────────────────────────


class TestExtractHostAndDomain:
    def test_finds_domain_in_query(self):
        urls = [
            "https://careers.kering.com/careers/job/111-foo?domain=kering",
            "https://careers.kering.com/careers/job/222-bar?domain=kering",
        ]
        assert extract_host_and_domain(urls) == ("careers.kering.com", "kering")

    def test_falls_through_to_first_with_domain(self):
        urls = [
            "https://careers.kering.com/",  # no domain query
            "https://careers.kering.com/careers/job/111-foo?domain=kering",
        ]
        assert extract_host_and_domain(urls) == ("careers.kering.com", "kering")

    def test_returns_none_when_no_domain_query(self):
        urls = ["https://careers.kering.com/", "https://careers.kering.com/job/111"]
        assert extract_host_and_domain(urls) is None

    def test_empty_iterable(self):
        assert extract_host_and_domain([]) is None

    def test_case_insensitive_domain_param(self):
        """Malformed sitemaps in the wild sometimes use ``Domain=`` or
        ``DOMAIN=`` instead of the canonical ``domain=``. The extraction
        must tolerate both."""
        for variant in ("Domain", "DOMAIN", "DoMaIn"):
            urls = [
                f"https://careers.example.com/careers/job/111-foo?{variant}=example",
            ]
            assert extract_host_and_domain(urls) == ("careers.example.com", "example")


# ── pcsx_to_discovered ─────────────────────────────────────────────────


class TestPcsxToDiscovered:
    def test_complete_mapping(self):
        raw = {
            "name": "GUCCI Embroidery Designer",
            "standardizedLocations": ["Milan, Lombardy, IT"],
            "workLocationOption": "onsite",
            "postedTs": 1775606400,  # 2026-04-08 UTC
            "department": "Creative Design",
            "atsJobId": "R163871",
            "positionUrl": "/careers/job/563705891066897",
        }
        sitemap_url = (
            "https://careers.kering.com/careers/job/563705891066897-gucci-embroidery?domain=kering"
        )
        job = pcsx_to_discovered(raw, sitemap_url)
        assert job.url == sitemap_url
        assert job.title == "GUCCI Embroidery Designer"
        assert job.description is None  # scraper fills
        assert job.locations == ["Milan, Lombardy, IT"]
        assert job.job_location_type == "onsite"
        assert job.date_posted == "2026-04-08"
        assert job.metadata == {
            "department": "Creative Design",
            "ats_job_id": "R163871",
        }

    def test_missing_locations_becomes_none(self):
        raw = {"name": "Job", "postedTs": 1775606400}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.locations is None

    def test_empty_locations_becomes_none(self):
        raw = {"name": "Job", "standardizedLocations": [], "postedTs": 1775606400}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.locations is None

    def test_zero_posted_ts_becomes_none(self):
        raw = {"name": "Job", "postedTs": 0}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.date_posted is None

    def test_missing_posted_ts_becomes_none(self):
        raw = {"name": "Job"}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.date_posted is None

    def test_unknown_work_location_passes_through_lowercased(self):
        raw = {"name": "Job", "workLocationOption": "HYBRID", "postedTs": 1775606400}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.job_location_type == "hybrid"

    def test_empty_metadata_becomes_none(self):
        raw = {"name": "Job", "postedTs": 1775606400}
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.metadata is None

    def test_json_encoded_locations_string(self):
        """Some PCSX tenants return standardizedLocations as a JSON-encoded
        string rather than a list. The defensive decoder must unwrap it."""
        raw = {
            "name": "Job",
            "standardizedLocations": '["Milan, Lombardy, IT", "Rome, Lazio, IT"]',
            "postedTs": 1775606400,
        }
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.locations == ["Milan, Lombardy, IT", "Rome, Lazio, IT"]

    def test_json_encoded_non_list_wraps(self):
        """A JSON string that isn't a list falls back to wrapping the original
        string as a one-element list. This is defensive against tenants that
        quote a single location."""
        raw = {
            "name": "Job",
            "standardizedLocations": '"Milan, Lombardy, IT"',
            "postedTs": 1775606400,
        }
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        # parsed to "Milan, Lombardy, IT" (str), then wrapped as [original_str].
        assert job.locations == ['"Milan, Lombardy, IT"']

    def test_invalid_json_string_wraps_as_single_element(self):
        """Malformed JSON → keep the original string as a one-element list
        so we never lose the value."""
        raw = {
            "name": "Job",
            "standardizedLocations": "[broken json",
            "postedTs": 1775606400,
        }
        job = pcsx_to_discovered(raw, "https://example.com/careers/job/1")
        assert job.locations == ["[broken json"]


# ── Error handling for _fetch_page via public surface ──────────────────


class TestFetchErrors:
    async def test_403_pcsx_disabled_raises(self):
        """_fetch_page surfaces PcsxDisabled via fetch_all."""

        async def handler(request):
            return _json_response({"message": "PCSX is not enabled for this user."}, status=403)

        async with _make_client(handler) as http:
            with pytest.raises(PcsxDisabled):
                await fetch_all("talent.bayer.com", "bayer.com", http)

    async def test_405_raises_stable_block(self):
        """Stable block (Starbucks pattern) → PcsxStableBlock.

        The exception subclasses ``PcsxFetchError`` so old ``except
        PcsxFetchError`` catches still work, but callers that want to
        distinguish the WAF block from a transient 429/5xx can match on the
        subclass first (see ``eightfold.discover_stream``).
        """

        async def handler(request):
            return httpx.Response(405, text="blocked")

        async with _make_client(handler) as http:
            with pytest.raises(PcsxStableBlock):
                await fetch_all("apply.starbucks.com", "starbucks.com", http)
            # And backward-compatible — still a PcsxFetchError subclass.
            assert issubclass(PcsxStableBlock, PcsxFetchError)
