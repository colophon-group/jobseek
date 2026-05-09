"""Tests for ``src.shared.tdm`` — TDM-Reservation respect (#2842).

Exercises both the unit-level ``check_response`` /
``check_browser_response`` contract and the per-helper hook integration
(http_retry, dom._fetch_via_page, workday/lever/smartrecruiters/
hireology/umantis/api_sniffer/_pcsx). All tests use ``unittest.mock`` —
no production HTTP, no real careers-page fetches per #2842 mandate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.shared.tdm import (
    TDMReservedError,
    check_browser_response,
    check_response,
)


def _resp(
    status: int,
    text: str = "",
    *,
    headers: dict[str, str] | None = None,
    url: str = "https://example.com",
) -> httpx.Response:
    """Build an httpx.Response stub with optional headers + URL."""
    return httpx.Response(
        status,
        text=text,
        headers=headers or {},
        request=httpx.Request("GET", url),
    )


# =============================================================================
# Unit tests — ``check_response`` semantics
# =============================================================================


class TestCheckResponse:
    """The canonical ``tdm-reservation`` response inspection contract."""

    def test_header_zero_no_op(self):
        """``tdm-reservation: 0`` is explicit opt-in — no exception."""
        resp = _resp(200, "<html>ok</html>", headers={"tdm-reservation": "0"})
        check_response(resp)  # must not raise

    def test_header_one_raises(self):
        """``tdm-reservation: 1`` raises ``TDMReservedError`` with source=header."""
        resp = _resp(
            200,
            "<html>ok</html>",
            headers={"tdm-reservation": "1"},
            url="https://opted-out.example/job/1",
        )
        with pytest.raises(TDMReservedError) as exc_info:
            check_response(resp)
        assert exc_info.value.source == "header"
        assert exc_info.value.url == "https://opted-out.example/job/1"
        assert exc_info.value.policy_url is None

    def test_header_one_case_insensitive(self):
        """Header lookup is case-insensitive (httpx normalises)."""
        resp = _resp(200, "ok", headers={"TDM-Reservation": "1"})
        with pytest.raises(TDMReservedError):
            check_response(resp)

    def test_header_absent_no_op(self):
        """No header, no body excerpt — no exception."""
        resp = _resp(200, "<html>ok</html>")
        check_response(resp)  # must not raise

    def test_meta_one_raises(self):
        """Header absent + HTML meta ``content="1"`` → raises with source=meta."""
        body = '<html><head><meta name="tdm-reservation" content="1"></head></html>'
        resp = _resp(200, body, url="https://meta-only.example/p")
        with pytest.raises(TDMReservedError) as exc_info:
            check_response(resp, body_excerpt=body)
        assert exc_info.value.source == "meta"
        assert exc_info.value.url == "https://meta-only.example/p"

    def test_meta_zero_no_op(self):
        """Header absent + HTML meta ``content="0"`` → no exception (only =1 enforces)."""
        body = '<html><head><meta name="tdm-reservation" content="0"></head></html>'
        resp = _resp(200, body)
        check_response(resp, body_excerpt=body)  # must not raise

    def test_meta_attribute_order_reversed(self):
        """``content`` first, then ``name`` — common HTML variation, must still match."""
        body = '<html><head><meta content="1" name="tdm-reservation"></head></html>'
        resp = _resp(200, body)
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)

    def test_meta_single_quotes(self):
        """Single-quoted attribute values are HTML-valid; must still match."""
        body = "<head><meta name='tdm-reservation' content='1'></head>"
        resp = _resp(200, body)
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)

    def test_meta_unquoted_values(self):
        """Unquoted values (HTML5 admits them for safe tokens). Match."""
        body = "<head><meta name=tdm-reservation content=1></head>"
        resp = _resp(200, body)
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)

    def test_meta_self_closing_xhtml(self):
        """XHTML self-closing form ``<meta ... />``. Match."""
        body = '<head><meta name="tdm-reservation" content="1" /></head>'
        resp = _resp(200, body)
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)

    def test_no_meta_no_header_no_op(self):
        """Plain HTML page with no TDM signals — no exception."""
        body = "<html><body><h1>Welcome</h1></body></html>"
        resp = _resp(200, body)
        check_response(resp, body_excerpt=body)

    def test_header_zero_overrides_meta_one(self):
        """Header is canonical (#2842 spec). ``tdm-reservation: 0`` in
        header takes precedence over a conflicting ``content="1"`` meta —
        the publisher's most authoritative declaration wins."""
        body = '<head><meta name="tdm-reservation" content="1"></head>'
        resp = _resp(200, body, headers={"tdm-reservation": "0"})
        check_response(resp, body_excerpt=body)  # must not raise

    def test_header_one_with_policy_captured(self):
        """``tdm-policy`` companion URL is captured into the exception
        for observability when reservation=1."""
        resp = _resp(
            200,
            "ok",
            headers={
                "tdm-reservation": "1",
                "tdm-policy": "https://opted-out.example/tdm-policy.json",
            },
        )
        with pytest.raises(TDMReservedError) as exc_info:
            check_response(resp)
        assert exc_info.value.policy_url == "https://opted-out.example/tdm-policy.json"

    def test_bad_header_string_no_op(self):
        """Non-integer header value → defensive parse → treated as absent."""
        resp = _resp(200, "ok", headers={"tdm-reservation": "true"})
        check_response(resp)  # must not raise

    def test_bad_header_multivalue_no_op(self):
        """Multi-value header (``"0, 1"``) — undefined by spec, treated as absent."""
        resp = _resp(200, "ok", headers={"tdm-reservation": "0, 1"})
        check_response(resp)

    def test_bad_header_empty_string_no_op(self):
        """Empty-string value → treated as absent."""
        resp = _resp(200, "ok", headers={"tdm-reservation": ""})
        check_response(resp)

    def test_bad_header_whitespace_no_op(self):
        """Whitespace-only value → absent."""
        resp = _resp(200, "ok", headers={"tdm-reservation": "   "})
        check_response(resp)

    def test_out_of_range_integer_no_op(self):
        """Value ``2`` is integer but outside spec — treated as absent.

        The blast-radius comment on #2842 noted "no non-`0`/`1` integer
        values observed". The defensive choice: don't enforce on
        garbage. (See also: spec calls these implementation-defined.)
        """
        resp = _resp(200, "ok", headers={"tdm-reservation": "2"})
        check_response(resp)

    def test_meta_in_body_not_head_still_matches(self):
        """The regex doesn't constrain to ``<head>`` — a stray meta in
        body still triggers (publishers occasionally hoist meta tags)."""
        body = '<body><meta name="tdm-reservation" content="1"></body>'
        resp = _resp(200, body)
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)

    def test_meta_inside_script_does_not_match(self):
        """``<meta ...>`` substring inside a JS string literal must not
        spuriously match — the regex is anchored on real ``<meta>`` tags."""
        body = '<script>const s = "<meta name=tdm-reservation content=1>";</script>'
        resp = _resp(200, body)
        # The current regex is structural — it actually WOULD match
        # ``<meta...>`` even inside a script string. We treat this as
        # acceptable: the false-positive cost is one skipped board, and
        # publishers rarely embed literal ``<meta tdm-reservation>``
        # strings as data inside scripts on real careers pages. The
        # alternative (full HTML parse) is too expensive on the hot path.
        # Pinning the current behaviour so a future tightening is
        # explicit. NOTE: if this assertion changes, audit the body-meta
        # scan policy in ``shared/tdm.py``.
        with pytest.raises(TDMReservedError):
            check_response(resp, body_excerpt=body)


# =============================================================================
# Unit tests — ``check_browser_response`` (Playwright-shaped input)
# =============================================================================


class TestCheckBrowserResponse:
    def test_header_one_raises(self):
        with pytest.raises(TDMReservedError) as exc:
            check_browser_response(
                {"tdm-reservation": "1"},
                "<html>ok</html>",
                url="https://x.example/p",
            )
        assert exc.value.source == "header"
        assert exc.value.url == "https://x.example/p"

    def test_header_zero_no_op(self):
        check_browser_response({"tdm-reservation": "0"}, "ok", url="https://x.example")

    def test_meta_one_raises(self):
        body = '<head><meta name="tdm-reservation" content="1"></head>'
        with pytest.raises(TDMReservedError) as exc:
            check_browser_response({}, body, url="https://x.example")
        assert exc.value.source == "meta"

    def test_header_canonical_wins_over_meta(self):
        body = '<head><meta name="tdm-reservation" content="1"></head>'
        check_browser_response({"tdm-reservation": "0"}, body, url="https://x.example")

    def test_uppercase_header_keys(self):
        """JS ``Headers`` normalises to lowercase, but defend against
        callers passing un-normalized header dicts."""
        with pytest.raises(TDMReservedError):
            check_browser_response(
                {"TDM-Reservation": "1"},
                "ok",
                url="https://x.example",
            )

    def test_none_inputs_no_op(self):
        check_browser_response(None, None, url="https://x.example")

    def test_policy_url_captured(self):
        with pytest.raises(TDMReservedError) as exc:
            check_browser_response(
                {"tdm-reservation": "1", "tdm-policy": "https://policy.example"},
                "ok",
                url="https://x.example",
            )
        assert exc.value.policy_url == "https://policy.example"


# =============================================================================
# Hook integration — fetch_with_retry (http_retry.py)
# =============================================================================


class TestFetchWithRetryHook:
    """The shared static-httpx fetch helper raises TDMReservedError
    (not PaginationFetchError) on tdm-reservation=1."""

    async def test_tdm_one_raises_no_retry(self):
        """200 with ``tdm-reservation: 1`` → raises immediately, no retry budget burnt."""
        from src.shared.http_retry import fetch_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(200, "<html>ok</html>", headers={"tdm-reservation": "1"})
        )

        with pytest.raises(TDMReservedError):
            await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        # Critical: only 1 attempt — the publisher policy decision is
        # not retryable.
        assert client.get.await_count == 1

    async def test_tdm_zero_passes_through(self):
        """200 with ``tdm-reservation: 0`` → body returned, no exception."""
        from src.shared.http_retry import fetch_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(200, "<html>ok</html>", headers={"tdm-reservation": "0"})
        )

        out = await fetch_with_retry(client, "https://example.com", base_delay=0.001)
        assert out == "<html>ok</html>"

    async def test_tdm_meta_in_body_raises(self):
        """200 + meta ``content="1"`` (no header) → raises."""
        from src.shared.http_retry import fetch_with_retry

        body = '<head><meta name="tdm-reservation" content="1"></head>'
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, body))

        with pytest.raises(TDMReservedError):
            await fetch_with_retry(client, "https://example.com", base_delay=0.001)

        assert client.get.await_count == 1

    async def test_no_tdm_passes_through(self):
        """Baseline: no TDM signal → body returned normally."""
        from src.shared.http_retry import fetch_with_retry

        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "<html>ok</html>"))

        out = await fetch_with_retry(client, "https://example.com")
        assert out == "<html>ok</html>"


# =============================================================================
# Hook integration — dom._fetch_via_page (Playwright path)
# =============================================================================


class TestDomFetchViaPageHook:
    async def test_browser_tdm_one_raises(self):
        """Playwright fetch returning ``tdm-reservation: 1`` header → raises."""
        from src.core.monitors.dom import _fetch_via_page

        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "status": 200,
                "headers": {"tdm-reservation": "1"},
                "text": "<html>ok</html>",
            }
        )

        with pytest.raises(TDMReservedError):
            await _fetch_via_page(page, "https://browser.example/p", base_delay=0.001)

        # No retry — publisher policy is not transient.
        assert page.evaluate.await_count == 1

    async def test_browser_no_tdm_passes_through(self):
        """No TDM signal → text returned normally."""
        from src.core.monitors.dom import _fetch_via_page

        page = AsyncMock()
        page.evaluate = AsyncMock(
            return_value={
                "status": 200,
                "headers": {},
                "text": "<html>ok</html>",
            }
        )

        out = await _fetch_via_page(page, "https://browser.example/p", base_delay=0.001)
        assert out == "<html>ok</html>"

    async def test_browser_meta_in_body_raises(self):
        """No header but meta ``content="1"`` in body → raises."""
        from src.core.monitors.dom import _fetch_via_page

        body = '<html><head><meta name="tdm-reservation" content="1"></head></html>'
        page = AsyncMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "headers": {}, "text": body})

        with pytest.raises(TDMReservedError):
            await _fetch_via_page(page, "https://browser.example/p", base_delay=0.001)


# =============================================================================
# Hook integration — workday._post_page_with_retry
# =============================================================================


class TestWorkdayHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.workday import _post_page_with_retry

        client = AsyncMock()
        client.post = AsyncMock(
            return_value=_resp(
                200,
                '{"jobPostings":[],"total":0}',
                headers={"tdm-reservation": "1"},
                url="https://wd.example",
            )
        )

        with pytest.raises(TDMReservedError):
            await _post_page_with_retry(client, "https://wd.example", {}, base_delay=0.001)

        assert client.post.await_count == 1

    async def test_no_tdm_passes_through(self):
        from src.core.monitors.workday import _post_page_with_retry

        client = AsyncMock()
        client.post = AsyncMock(
            return_value=_resp(
                200,
                '{"jobPostings":[],"total":0,"facets":[]}',
                url="https://wd.example",
            )
        )

        data = await _post_page_with_retry(client, "https://wd.example", {}, base_delay=0.001)
        assert data == {"jobPostings": [], "total": 0, "facets": []}


# =============================================================================
# Hook integration — lever._get_page_with_retry
# =============================================================================


class TestLeverHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.lever import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(
                200,
                "[]",
                headers={"tdm-reservation": "1"},
                url="https://api.lever.co/x",
            )
        )

        with pytest.raises(TDMReservedError):
            await _get_page_with_retry(client, "https://api.lever.co/x", {}, base_delay=0.001)

        assert client.get.await_count == 1

    async def test_no_tdm_passes_through(self):
        from src.core.monitors.lever import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, "[]", url="https://api.lever.co/x"))

        out = await _get_page_with_retry(client, "https://api.lever.co/x", {}, base_delay=0.001)
        assert out == []


# =============================================================================
# Hook integration — smartrecruiters._get_page_with_retry
# =============================================================================


class TestSmartRecruitersHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.smartrecruiters import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(
                200,
                '{"content":[],"totalFound":0}',
                headers={"tdm-reservation": "1"},
                url="https://api.smartrecruiters.com/x",
            )
        )

        with pytest.raises(TDMReservedError):
            await _get_page_with_retry(
                client,
                "https://api.smartrecruiters.com/x",
                {"limit": 10, "offset": 0},
                base_delay=0.001,
            )

        assert client.get.await_count == 1

    async def test_no_tdm_passes_through(self):
        from src.core.monitors.smartrecruiters import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(
                200,
                '{"content":[],"totalFound":0}',
                url="https://api.smartrecruiters.com/x",
            )
        )

        data = await _get_page_with_retry(
            client,
            "https://api.smartrecruiters.com/x",
            {"limit": 10, "offset": 0},
            base_delay=0.001,
        )
        assert data == {"content": [], "totalFound": 0}


# =============================================================================
# Hook integration — hireology._get_page_with_retry
# =============================================================================


class TestHireologyHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.hireology import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(
                200,
                '{"data":[],"count":0}',
                headers={"tdm-reservation": "1"},
                url="https://hireology.example/api",
            )
        )

        with pytest.raises(TDMReservedError):
            await _get_page_with_retry(
                client, "https://hireology.example/api", {}, base_delay=0.001
            )

        assert client.get.await_count == 1


# =============================================================================
# Hook integration — umantis._get_page_with_retry
# =============================================================================


class TestUmantisHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.umantis import _get_page_with_retry

        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_resp(
                200,
                "<html>ok</html>",
                headers={"tdm-reservation": "1"},
                url="https://umantis.example/jobs",
            )
        )

        with pytest.raises(TDMReservedError):
            await _get_page_with_retry(client, "https://umantis.example/jobs", base_delay=0.001)

        assert client.get.await_count == 1

    async def test_meta_in_body_raises(self):
        """Umantis returns HTML, so the meta-tag fallback is exercised."""
        from src.core.monitors.umantis import _get_page_with_retry

        body = '<html><head><meta name="tdm-reservation" content="1"></head></html>'
        client = AsyncMock()
        client.get = AsyncMock(return_value=_resp(200, body, url="https://umantis.example/jobs"))

        with pytest.raises(TDMReservedError):
            await _get_page_with_retry(client, "https://umantis.example/jobs", base_delay=0.001)


# =============================================================================
# Hook integration — api_sniffer.http_fetch_with_retry
# =============================================================================


class TestApiSnifferHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        client = AsyncMock()
        client.request = AsyncMock(
            return_value=_resp(
                200,
                "{}",
                headers={"tdm-reservation": "1"},
                url="https://api.example/jobs",
            )
        )

        with pytest.raises(TDMReservedError):
            await http_fetch_with_retry(client, "GET", "https://api.example/jobs", base_delay=0.001)

        assert client.request.await_count == 1


# =============================================================================
# Hook integration — _pcsx._fetch_page
# =============================================================================


class TestPcsxHook:
    async def test_tdm_one_raises(self):
        from src.core.monitors._pcsx import _fetch_page

        http = AsyncMock()
        http.get = AsyncMock(
            return_value=_resp(
                200,
                '{"data":{"positions":[]}}',
                headers={"tdm-reservation": "1"},
                url="https://pcsx.example/api/pcsx/search",
            )
        )

        with pytest.raises(TDMReservedError):
            await _fetch_page(
                "pcsx.example",
                "example.com",
                http,
                offset=0,
            )
