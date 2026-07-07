"""Tests for DOM monitor pagination support."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.monitors.dom import (
    _build_url_matcher,
    _extract_links_static,
    _fetch_via_page,
    _paginate_urls,
    dom_discover,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch target: ``_paginate_urls`` does ``from src.shared.http_retry import
# fetch_with_retry`` (#2722). Earlier patches at ``src.core.monitors.
# fetch_page_text`` no longer apply.
_FETCH_PATCH = "src.shared.http_retry.fetch_with_retry"


def _html_with_links(*urls: str) -> str:
    """Build minimal HTML with anchor tags for the given URLs."""
    links = "".join(f'<a href="{url}">link</a>' for url in urls)
    return f"<html><body>{links}</body></html>"


def _make_fetch(pages: dict[str, str | None]):
    """Return an async function mimicking ``fetch_with_retry`` with
    per-URL canned responses. Signature matches the real function:
    ``(client, url, **kwargs) -> str | None``.
    """

    async def fake_fetch(client, url, **kwargs):
        return pages.get(url)

    return fake_fetch


# ---------------------------------------------------------------------------
# _extract_links_static
# ---------------------------------------------------------------------------


class TestExtractLinksStatic:
    def test_filters_job_keywords(self):
        html = _html_with_links(
            "https://example.com/jobs/123",
            "https://example.com/about",
            "https://example.com/career/456",
        )
        urls = _extract_links_static(html, "https://example.com")
        assert urls == {
            "https://example.com/jobs/123",
            "https://example.com/career/456",
        }

    def test_resolves_relative_urls(self):
        html = _html_with_links("/jobs/42", "/about")
        urls = _extract_links_static(html, "https://example.com/careers/")
        assert "https://example.com/jobs/42" in urls
        assert "https://example.com/about" not in urls

    def test_empty_html(self):
        assert _extract_links_static("", "https://example.com") == set()

    def test_url_matcher_overrides_keywords(self):
        """url_matcher regex replaces the default keyword filter."""
        import re

        html = _html_with_links(
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/about",
            "https://example.com/emploi/lyon/pm/456",
        )
        matcher = re.compile(r"/emploi/")
        urls = _extract_links_static(html, "https://example.com", url_matcher=matcher)
        assert urls == {
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/emploi/lyon/pm/456",
        }

    def test_url_matcher_none_uses_keywords(self):
        """Without url_matcher, default keyword filter applies."""
        html = _html_with_links(
            "https://example.com/emploi/123",
            "https://example.com/jobs/456",
        )
        urls = _extract_links_static(html, "https://example.com", url_matcher=None)
        # /emploi/ doesn't match keywords, /jobs/ does
        assert urls == {"https://example.com/jobs/456"}


class TestBuildUrlMatcher:
    def test_string_filter(self):
        m = _build_url_matcher("/emploi/")
        assert m is not None
        assert m.search("https://example.com/emploi/123")
        assert not m.search("https://example.com/about")

    def test_dict_filter_include(self):
        m = _build_url_matcher({"include": "/jobs/", "exclude": "/blog/"})
        assert m is not None
        assert m.search("https://example.com/jobs/123")

    def test_none_filter(self):
        assert _build_url_matcher(None) is None
        assert _build_url_matcher("") is None


class TestDomDiscoverInitialFetch:
    async def test_initial_403_raises_instead_of_successful_empty(self, monkeypatch):
        """A blocked listing page must fail the monitor cycle.

        Returning an empty set records a healthy empty crawl and masks
        missing jobs, which is the mcdonalds-ph failure mode from #4945.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, text="Forbidden")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await dom_discover(
                    {
                        "board_url": "https://blocked.example/careers",
                        "metadata": {"url_filter": "/career/"},
                    },
                    client,
                )

        assert attempts == 3
        assert exc_info.value.last_status == 403

    async def test_initial_404_remains_empty(self):
        """A missing static page keeps the existing lenient empty-result path."""

        def handler(request):
            return httpx.Response(404, text="Not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": "https://missing.example/careers",
                    "metadata": {"url_filter": "/career/"},
                },
                client,
            )

        assert result == set()


# ---------------------------------------------------------------------------
# _paginate_urls
# ---------------------------------------------------------------------------


class TestPaginateUrls:
    async def test_accumulates_urls(self):
        """Pages with different job links get merged."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links("https://example.com/jobs/2"),
            "https://example.com/careers?p=3": _html_with_links("https://example.com/jobs/3"),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 3},
                initial,
                MagicMock(),
            )
        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }

    async def test_stops_on_no_new_links(self):
        """Same links on page 2 as initial -> stops."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links(
                "https://example.com/jobs/1"  # duplicate
            ),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 10},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_stops_on_legitimate_end(self):
        """``fetch_with_retry`` returning ``None`` (404/410, empty body)
        stops pagination cleanly — pagination has reached its natural end.
        """
        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=_make_fetch({})):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 5},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_propagates_persistent_fetch_error(self):
        """``fetch_with_retry`` raising ``PaginationFetchError`` after
        retries propagates out of ``_paginate_urls`` instead of being
        treated as silent end-of-pagination — the fix for the 2026-04-26
        NHS spike (#2722). The exception lands in
        ``_process_one_board_streaming``'s generic ``except Exception``
        which records the run as a failure rather than a partial
        success, so ``_MARK_GONE_BY_TIMESTAMP`` does not run.
        """
        from src.shared.http_retry import PaginationFetchError

        async def transient_fail(client, url, **kwargs):
            raise PaginationFetchError(url, attempts=3, last_status=503)

        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=transient_fail):
            try:
                await _paginate_urls(
                    "https://example.com/careers",
                    {"param_name": "p", "max_pages": 5},
                    initial,
                    MagicMock(),
                )
            except PaginationFetchError as exc:
                assert exc.last_status == 503
            else:
                raise AssertionError("expected PaginationFetchError to propagate")

    async def test_browser_path_propagates_persistent_fetch_error(self, monkeypatch):
        """Same contract as the static path, but for ``pagination.browser=true``
        (#2737). A persistent Playwright-side failure must raise rather than
        truncate — the lenovo-careers board's failure mode.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        # First page succeeds with no new links so we get past the
        # ``no_new_urls`` short-circuit only on a real failure path —
        # here every page returns 503 so the very first paginated
        # fetch raises.
        page.evaluate = AsyncMock(return_value={"status": 503, "text": ""})

        initial = {"https://example.com/jobs/1"}
        try:
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 5, "browser": True},
                initial,
                MagicMock(),
                page=page,
            )
        except PaginationFetchError as exc:
            assert exc.last_status == 503
        else:
            raise AssertionError("expected PaginationFetchError to propagate")

    async def test_propagates_persistent_empty_200(self):
        """Empty-200 mid-pagination on the static httpx path now raises
        rather than silently breaking the loop (#2739). Without the
        fix, ``""`` falls through to ``if not html: break`` and the
        un-fetched tail is tombstoned by ``_MARK_GONE_BY_TIMESTAMP``.
        """
        from src.shared.http_retry import PaginationFetchError

        async def empty_200(client, url, **kwargs):
            raise PaginationFetchError(url, attempts=3, last_status=200)

        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=empty_200):
            try:
                await _paginate_urls(
                    "https://example.com/careers",
                    {"param_name": "p", "max_pages": 5},
                    initial,
                    MagicMock(),
                )
            except PaginationFetchError as exc:
                assert exc.last_status == 200
                assert exc.last_error is None
            else:
                raise AssertionError("expected PaginationFetchError to propagate")

    async def test_browser_path_recovers_on_transient(self, monkeypatch):
        """Browser path retries through a single 503 and continues paginating."""
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 503, "text": ""},
                {"status": 200, "text": _html_with_links("https://example.com/jobs/2")},
                # Second pagination loop iteration: page=3 returns 404
                # (legitimate end of pagination).
                {"status": 404, "text": ""},
            ]
        )

        initial = {"https://example.com/jobs/1"}
        result = await _paginate_urls(
            "https://example.com/careers",
            {"param_name": "p", "max_pages": 5, "browser": True},
            initial,
            MagicMock(),
            page=page,
        )
        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
        }

    async def test_respects_max_pages(self):
        """Only fetches up to max_pages."""
        initial = {"https://example.com/jobs/1"}
        call_count = 0
        url_map = {}
        for i in range(2, 20):
            url_map[f"https://example.com/careers?p={i}"] = _html_with_links(
                f"https://example.com/jobs/{i}"
            )

        async def counting_fetch(client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return url_map.get(url)

        with patch(_FETCH_PATCH, new=counting_fetch):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 4},
                initial,
                MagicMock(),
            )
        # max_pages=4 means pages 2, 3, 4 fetched (page 1 is initial)
        assert call_count == 3
        assert len(result) == 4

    async def test_system_cap(self, monkeypatch):
        """max_pages is capped at _MAX_PAGINATION_PAGES."""
        monkeypatch.setattr("src.core.monitors.dom._MAX_PAGINATION_PAGES", 7)
        initial = set()
        call_count = 0

        async def counting_fetch(client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _html_with_links(f"https://example.com/jobs/{call_count}")

        with patch(_FETCH_PATCH, new=counting_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 999},
                initial,
                MagicMock(),
            )
        # Patched cap is 7, so pages 2..7 are fetched.
        assert call_count == 6

    async def test_start_and_increment(self):
        """Custom start and increment produce correct URL params."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "offset", "start": 0, "increment": 20, "max_pages": 3},
                initial,
                MagicMock(),
            )
        # start=0, increment=20 -> first fetch at offset=20, second at offset=40
        assert "offset=20" in fetched_urls[0]
        assert "offset=40" in fetched_urls[1]

    async def test_start_value_alias(self):
        """``start_value`` is accepted as an alias for ``start``."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "offset", "start_value": 0, "increment": 20, "max_pages": 3},
                initial,
                MagicMock(),
            )
        # start_value=0, increment=20 -> first fetch at offset=20, second at offset=40
        assert "offset=20" in fetched_urls[0]
        assert "offset=40" in fetched_urls[1]

    async def test_url_template(self):
        """``url_template`` with ``{page}`` produces path-based pagination URLs."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"url_template": "https://example.com/careers/0-0-2-0-{page}", "max_pages": 4},
                initial,
                MagicMock(),
            )
        assert fetched_urls[0] == "https://example.com/careers/0-0-2-0-2"
        assert fetched_urls[1] == "https://example.com/careers/0-0-2-0-3"
        assert fetched_urls[2] == "https://example.com/careers/0-0-2-0-4"


# ---------------------------------------------------------------------------
# _fetch_via_page
# ---------------------------------------------------------------------------


class TestFetchViaPage:
    """``_fetch_via_page`` mirrors ``fetch_with_retry``'s strict semantics
    on the Playwright path: 200 → text, 404/410 → None (legit end), other
    4xx → None (lenient stop), and 5xx / 408 / 425 / 429 / page.evaluate
    exceptions → retry then raise ``PaginationFetchError``. See #2737.
    """

    async def test_returns_html_on_200(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": "<html>ok</html>"})
        result = await _fetch_via_page(page, "https://example.com/page2")
        assert result == "<html>ok</html>"
        page.evaluate.assert_awaited_once()

    async def test_returns_none_on_404(self):
        """404 / 410 are legitimate end-of-pagination — return None, no retry."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 404, "text": "not found"})
        result = await _fetch_via_page(page, "https://example.com/past-end")
        assert result is None
        assert page.evaluate.await_count == 1

    async def test_returns_none_on_410(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 410, "text": ""})
        result = await _fetch_via_page(page, "https://example.com/gone")
        assert result is None

    async def test_returns_none_on_non_retryable_4xx(self):
        """Non-retryable 4xx (403 etc.) is a lenient stop, parity with httpx path."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 403, "text": "forbidden"})
        result = await _fetch_via_page(page, "https://example.com/forbidden")
        assert result is None
        assert page.evaluate.await_count == 1

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Transient 503 retries, then 200 returns text."""
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 503, "text": "down"},
                {"status": 200, "text": "<html>recovered</html>"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "<html>recovered</html>"
        assert page.evaluate.await_count == 2

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 429, "text": ""},
                {"status": 200, "text": "ok"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "ok"
        assert page.evaluate.await_count == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Persistent 5xx exhausts retries -> PaginationFetchError."""
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 503, "text": ""})
        try:
            await _fetch_via_page(
                page,
                "https://example.com/flaky",
                retries=3,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.url == "https://example.com/flaky"
            assert exc.attempts == 3
            assert exc.last_status == 503
            assert page.evaluate.await_count == 3
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_raises_after_persistent_evaluate_exception(self, monkeypatch):
        """Playwright ``page.evaluate`` raising (timeout, navigation,
        page closed) is treated as transient — retry then raise.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=TimeoutError("evaluate timed out"))
        try:
            await _fetch_via_page(
                page,
                "https://example.com/p2",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.last_error == "TimeoutError"
            assert exc.last_status is None
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_recovers_from_evaluate_exception(self, monkeypatch):
        """Single transient evaluate exception then success — recovery
        without raising.
        """
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                Exception("transient crash"),
                {"status": 200, "text": "ok"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "ok"
        assert page.evaluate.await_count == 2

    async def test_truncates_to_max_chars(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": "x" * 1_000_000})
        result = await _fetch_via_page(page, "https://example.com")
        # Default cap is _BROWSER_FETCH_MAX_CHARS = 500_000.
        assert len(result) == 500_000
        assert set(result) == {"x"}

    async def test_recovers_from_empty_200(self, monkeypatch):
        """Single empty-200 on the browser path retries and recovers
        (#2739) — symmetric with the static httpx path. The bug shape
        otherwise: ``""`` is falsy, ``_paginate_urls``'s ``if not html:
        break`` treats it as end-of-pagination, the un-fetched tail is
        tombstoned by ``_MARK_GONE_BY_TIMESTAMP``.
        """
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 200, "text": ""},
                {"status": 200, "text": "<html>recovered</html>"},
            ]
        )

        result = await _fetch_via_page(page, "https://example.com/p2")

        assert result == "<html>recovered</html>"
        assert page.evaluate.await_count == 2

    async def test_raises_after_persistent_empty_200(self, monkeypatch):
        """Persistent empty-200 on the browser path exhausts retries
        and raises ``PaginationFetchError`` with ``last_status=200``
        (#2739) — same operator-facing signal as the static path.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": ""})

        try:
            await _fetch_via_page(
                page,
                "https://example.com/empty",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.url == "https://example.com/empty"
            assert exc.attempts == 2
            assert exc.last_status == 200
            assert exc.last_error is None
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_unexpected_result_shape_retries_then_raises(self, monkeypatch):
        """A malformed ``page.evaluate`` return value (e.g. a string from
        an injected content script substituting our async function)
        falls through to the ``except Exception`` branch via natural
        attribute access — same retry-then-raise contract as a
        ``page.evaluate`` raise. Pinning the contract here so the
        absence of a defensive shape-check is intentional.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value="not a dict")
        try:
            await _fetch_via_page(
                page,
                "https://example.com",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            # ``"not a dict"["status"]`` raises ``TypeError``.
            assert exc.last_error == "TypeError"
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")
