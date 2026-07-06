"""Unit tests for src.core.monitors._pcsx."""

from __future__ import annotations

import httpx
import pytest

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
    async def test_403_with_unparseable_body_raises_generic_fetch_error(self):
        async def handler(request):
            return httpx.Response(403, text="<html>Forbidden</html>")

        async with _make_client(handler) as http:
            with pytest.raises(PcsxFetchError, match="403 from careers.example.com"):
                await fetch_all("careers.example.com", "example", http)

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

    async def test_persistent_503_raises_pagination_fetch_error(self, monkeypatch):
        """Persistent 503 exhausts retries and raises ``PcsxFetchError``,
        which is now a subclass of :class:`PaginationFetchError` (#2734) so
        callers / monitoring catch the same shape used by dom + sitemap
        monitors (#2722, #2737). ``last_status`` carries the failing code.
        """
        from src.shared.http_retry import PaginationFetchError

        # Skip the real backoff (5 * 2^attempt × jitter ≈ 35s for 3 attempts).
        async def _instant(_duration):
            return None

        monkeypatch.setattr("src.core.monitors._pcsx.asyncio.sleep", _instant)

        async def handler(request):
            return httpx.Response(503, text="upstream down")

        async with _make_client(handler) as http:
            with pytest.raises(PaginationFetchError) as exc_info:
                await fetch_all("careers.kering.com", "kering", http)
        # Both type assertions hold — same class, same instance.
        assert isinstance(exc_info.value, PcsxFetchError)
        assert exc_info.value.last_status == 503

    async def test_cloudflare_5xx_codes_retry(self, monkeypatch):
        """Cloudflare-origin 5xx codes (520-526, 530) are retried (#2734).

        Before this PR, ``_fetch_page`` only retried
        ``(429, 500, 502, 503, 504)`` — Cloudflare 520-526/530 codes
        (common when an Eightfold tenant is behind Cloudflare) fell into
        the "fail fast" branch and produced a single-shot ``PcsxFetchError``.
        Now classification routes through ``is_retryable_status`` which
        covers any 5xx in range. This test pins the contract — first
        call returns the CF status, second returns 200 with positions.
        """

        async def _instant(_duration):
            return None

        monkeypatch.setattr("src.core.monitors._pcsx.asyncio.sleep", _instant)

        for status in (520, 521, 522, 523, 524, 525, 526, 530):
            calls = {"n": 0}

            # Bind both ``status`` and ``calls`` as default args so each
            # iteration's handler closes over its own values (ruff B023).
            async def handler(request, _status=status, _calls=calls):
                _calls["n"] += 1
                if _calls["n"] == 1:
                    return httpx.Response(_status, text="cf error")
                if _calls["n"] == 2:
                    # Retry of the failing page succeeds with one position.
                    return _json_response({"data": {"positions": [{"id": 1, "postedTs": 1000}]}})
                # End of pagination — empty response stops ``fetch_all``.
                return _json_response({"data": {"positions": []}})

            async with _make_client(handler) as http:
                result = await fetch_all("careers.kering.com", "kering", http)
            assert result, f"status {status} should retry then succeed"
            # Retry of failed page (call 2) + one extra page that returns
            # empty (call 3, the end-of-pagination signal). Three calls
            # total = the 5xx was retried once.
            assert calls["n"] == 3, f"status {status} should be retried"

    async def test_pcsx_fetch_error_carries_paginationfetcherror_attrs(self):
        """``PcsxFetchError(message, url=..., last_status=...)`` exposes
        the base-class attributes (``url``, ``attempts``, ``last_status``,
        ``last_error``) so callers that pattern on
        :class:`PaginationFetchError` can introspect uniformly.
        """
        from src.shared.http_retry import PaginationFetchError

        exc = PcsxFetchError("boom", url="https://example.com/api/pcsx/search", last_status=503)
        assert isinstance(exc, PaginationFetchError)
        assert exc.url == "https://example.com/api/pcsx/search"
        assert exc.last_status == 503
        assert exc.last_error is None
        assert str(exc) == "boom"
