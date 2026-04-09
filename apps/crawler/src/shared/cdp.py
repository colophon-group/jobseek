"""Per-domain CDP-routed HTTP transport.

Provides an ``httpx.AsyncBaseTransport`` implementation that makes the
request via a remote headless browser (Lightpanda cloud) over the Chrome
DevTools Protocol. Used to bypass datacenter-IP anti-bot blocks (e.g.
AWS WAF on ``apply.starbucks.com``) without changing any scraper or
monitor call sites — the routing is wired into the shared
``httpx.AsyncClient`` via ``mounts``, so ``jsonld.scrape()``,
``_pcsx._fetch_page()``, and ``sitemap._try_fetch_xml()`` all
transparently route through the browser when the hostname matches.

## Configuration

Two layers, merged on every call:

1. **``data/cdp_routes.csv``** (repo-tracked, source of truth) — a
   simple CSV with columns ``hostname,backend,reason``. Adding a
   domain that needs CDP routing is a normal PR — no GitHub secret
   update needed. Override the file path with the
   ``CDP_ROUTES_FILE`` env var.
2. **``CDP_ROUTES`` env var** (JSON, optional) — runtime overrides
   that win over the file. Use for testing a new host before adding
   it to the file, or temporarily disabling a route on production
   without a deploy.

The ``LIGHTPANDA_CDP_URL`` env var holds the ``wss://`` CDP endpoint
(with auth token) — secret, must be set for CDP transport to work.

Only ``lightpanda`` is a supported backend today. Unknown backends are
logged and ignored.

## Session lifecycle

One Lightpanda session per process per CDP URL, lazily opened on the
first request and kept alive until process exit. Playwright's
``APIRequestContext`` (``context.request``) is the request surface —
it issues HTTP requests through the browser's network stack **without
creating a Page**, so we pay neither DOM render nor JS execution cost.
All concurrent requests on the same event loop share the same session
(so the handshake cost is paid once, not per request) — which matches
Lightpanda's billing model (browser-hours of session clock time, not
per request).

Sessions are re-opened **only** on errors that look connection-level
(see ``_CONNECTION_ERROR_MARKERS``), so a flaky CDP websocket heals
on the next retry without propagating. Per-request errors like
upstream timeouts or body decoding errors leave the session alive —
resetting on every error caused a session-churn bug where every
404 from the target site tore down the Lightpanda session, and any
process exit during the reconnect leaked a ~6-minute orphaned session
on the Lightpanda side (idle-timeout reap).

On clean process shutdown (SIGTERM/SIGINT), call
``shutdown_all_sessions`` from the worker shutdown path — the
``cli.py`` ``finally:`` block does this. Without it, the Lightpanda
session ticks toward its 6-minute idle timeout for every process
restart, eating into the monthly browser-hours quota.

## Testing surface

``LightpandaTransport`` takes an injectable ``fetch`` callable so tests
can bypass Playwright entirely and exercise the httpx integration path
with a fake transport. ``_LightpandaSession`` is a module-level cache
keyed by CDP URL — tests can inject a fake session via
``_set_session_for_test(url, fake)``.
"""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


# ── Settings access ────────────────────────────────────────────────────


def _settings() -> Any | None:
    """Return the pydantic settings instance, or None if unavailable."""
    try:
        from src.config import settings

        return settings
    except Exception:  # noqa: BLE001 — defensive, same pattern as proxy.py
        return None


def _cdp_routes_file_path() -> Path:
    """Path to the repo-tracked default routes file.

    Lives next to ``data/boards.csv`` so it ships with the codebase
    and follows the same PR-driven editing flow. Override the path
    with the ``CDP_ROUTES_FILE`` env var if you need a different
    location (tests, dev sandboxes).
    """
    s = _settings()
    if s is not None:
        override = getattr(s, "cdp_routes_file", "") or ""
        if override:
            return Path(override)
    # Default: <repo>/apps/crawler/data/cdp_routes.csv
    return Path(__file__).resolve().parent.parent.parent / "data" / "cdp_routes.csv"


def _load_cdp_routes_file() -> dict[str, str]:
    """Read ``data/cdp_routes.csv`` (if present) into ``{hostname: backend}``.

    The file is the source of truth for which hostnames go through
    a CDP-routed transport. Adding a domain is a normal PR — no
    GitHub secret update needed, no manual env-var dance.

    File format (CSV with header)::

        hostname,backend,reason
        apply.starbucks.com,lightpanda,AWS WAF blocks Hetzner
        ...

    Parses leniently: missing file → empty dict (logged at info, not
    fatal); malformed rows → skipped with warning; the ``reason``
    column is documentation only and is not consumed.

    Cached at module load? **No** — read on every call so a sync
    operation that rewrites the file picks up changes without a
    process restart. Reads are negligible (a few hundred bytes).
    """
    path = _cdp_routes_file_path()
    if not path.exists():
        return {}
    routes: dict[str, str] = {}
    try:
        with path.open() as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "hostname" not in reader.fieldnames:
                log.warning("cdp.routes_file.no_hostname_column", path=str(path))
                return {}
            for row in reader:
                host = (row.get("hostname") or "").strip()
                backend = (row.get("backend") or "lightpanda").strip()
                if not host or host.startswith("#"):
                    continue
                routes[host] = backend
    except Exception as exc:  # noqa: BLE001 — file is non-critical
        log.warning("cdp.routes_file.parse_failed", path=str(path), error=str(exc))
        return {}
    return routes


def _cdp_routes() -> dict[str, str]:
    """``{hostname: backend_name}`` for hosts that should route via CDP.

    Source order (later overrides earlier):

    1. ``data/cdp_routes.csv`` — repo-tracked default list, edited via
       normal PRs. Single source of truth for "which hostnames are
       behind anti-bot blocks we need to bypass".
    2. ``CDP_ROUTES`` env var (JSON) — runtime overrides for adhoc
       cases (e.g. testing a new host before adding it to the file,
       or temporarily disabling a route on production without a
       deploy).

    Both layers are merged on every call (~microseconds). The settings
    field for the env var is typed as ``str`` so pydantic-settings
    doesn't try to JSON-decode an empty string at startup (which used
    to crash worker startup before PR #2137).
    """
    routes = _load_cdp_routes_file()
    s = _settings()
    if s is not None:
        raw = getattr(s, "cdp_routes", "")
        if isinstance(raw, dict):
            routes.update(raw)
        else:
            routes.update(parse_cdp_routes(raw))
    return routes


def _lightpanda_cdp_url() -> str | None:
    s = _settings()
    if s is None:
        return None
    return getattr(s, "lightpanda_cdp_url", None) or None


# ── Session (Playwright + Lightpanda) ─────────────────────────────────

# Module-level session cache keyed by CDP URL. A session is lazy — it
# opens on the first real request. All transports mounted for hosts that
# share the same CDP URL share the same session, so handshake cost is
# amortized.
_sessions: dict[str, _LightpandaSession] = {}


class CdpRequestError(Exception):
    """Raised when the Lightpanda session fails a request.

    Unwrapped into httpx-flavored errors by the transport so existing
    retry/timeout handling at call sites keeps working.
    """


# Substrings that indicate the underlying CDP/websocket session is dead
# and the next request would also fail. Matched case-insensitively
# against ``str(exc)``. Conservative — anything not on this list keeps
# the session alive.
_CONNECTION_ERROR_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "browser closed",
    "target closed",
    "context closed",
    "session closed",
    "websocket",
    "connection closed",
    "disconnected",
    "connect_over_cdp",
    "page, context or browser has been closed",
)


def _is_connection_error(exc: BaseException) -> bool:
    """True if ``exc`` indicates a broken CDP/websocket connection.

    Used to decide whether the session needs a reset+reconnect on the
    next request, vs. whether the error is local to a single request
    (HTTP 4xx/5xx via fetch — playwright doesn't raise on those, but
    body decoding errors and per-request timeouts do — those don't
    invalidate the session).
    """
    msg = str(exc).lower()
    return any(marker in msg for marker in _CONNECTION_ERROR_MARKERS)


class _LightpandaSession:
    """Lazy, reusable Playwright+Lightpanda session bound to one event loop.

    Public methods:
      - ``request(method, url, headers, data, timeout)`` — issues a raw
        HTTP request via the browser's network stack (no page created).
      - ``close()`` — best-effort teardown.

    Safe to call ``request()`` concurrently from multiple coroutines.
    The lock only guards connection open/reset, not the request itself
    (Playwright's ``APIRequestContext`` is concurrency-safe).
    """

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self._context = None

    async def _ensure_open(self) -> None:
        if self._context is not None:
            return
        async with self._lock:
            if self._context is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover — prod has playwright
                raise CdpRequestError(
                    "playwright not installed but CDP routing is configured"
                ) from exc
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(self._cdp_url)
            context = await browser.new_context()
            self._pw = pw
            self._browser = browser
            self._context = context
            log.info("cdp.session.opened", backend="lightpanda")

    async def _reset_locked(self) -> None:
        """Tear down the session. Caller must hold the lock (or accept race)."""
        ctx, browser, pw = self._context, self._browser, self._pw
        self._context = self._browser = self._pw = None
        for name, obj, closer in (
            ("context", ctx, "close"),
            ("browser", browser, "close"),
            ("playwright", pw, "stop"),
        ):
            if obj is None:
                continue
            try:
                await getattr(obj, closer)()
            except Exception as exc:  # noqa: BLE001
                log.debug("cdp.session.close_failed", component=name, error=str(exc))

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Issue an HTTP request via Lightpanda. Returns (status, body, headers).

        Raises :class:`CdpRequestError` on any failure. The session is
        reset **only** when the failure looks like a broken CDP/websocket
        connection (per :func:`_is_connection_error`). Per-request errors
        like upstream timeouts or target-server quirks leave the session
        intact, since the websocket is still good and the next request
        can reuse it. Resetting on every error caused a session-churn
        bug where every 404 from the target tore down the Lightpanda
        session, and any process exit during the reconnect leaked a
        ~6-minute orphaned session on the Lightpanda side.
        """
        await self._ensure_open()
        assert self._context is not None  # for type checkers
        try:
            resp = await self._context.request.fetch(
                url,
                method=method,
                headers=headers or {},
                data=data,
                timeout=int(timeout * 1000),
                max_redirects=10,
            )
            body = await resp.body()
            status = resp.status
            resp_headers = dict(resp.headers)
            return status, body, resp_headers
        except Exception as exc:  # noqa: BLE001 — translated to CdpRequestError
            if _is_connection_error(exc):
                async with self._lock:
                    await self._reset_locked()
            raise CdpRequestError(f"lightpanda request failed: {exc}") from exc

    async def close(self) -> None:
        async with self._lock:
            await self._reset_locked()


def _get_session(cdp_url: str) -> _LightpandaSession:
    sess = _sessions.get(cdp_url)
    if sess is None:
        sess = _LightpandaSession(cdp_url)
        _sessions[cdp_url] = sess
    return sess


def _set_session_for_test(cdp_url: str, sess: _LightpandaSession | None) -> None:
    """Test hook: inject or clear the session for a given CDP URL."""
    if sess is None:
        _sessions.pop(cdp_url, None)
    else:
        _sessions[cdp_url] = sess


# Per-session close timeout for graceful shutdown. The Lightpanda CDP
# websocket can hang on close (network glitch, server-side reap in
# progress) — without a timeout the cli ``finally:`` block would block
# forever, eventually getting SIGKILL'd by docker stop's grace period
# and leaking the session anyway. 5 seconds is generous: a healthy
# close completes in ~50ms.
_SHUTDOWN_CLOSE_TIMEOUT_SEC = 5.0


async def shutdown_all_sessions() -> None:
    """Close every cached CDP session. Call from the worker shutdown path.

    Each session close is bounded by ``_SHUTDOWN_CLOSE_TIMEOUT_SEC`` and
    runs concurrently — a hung session must not block the others, and
    the shutdown must complete inside docker's stop-grace window so the
    process exits cleanly (otherwise SIGKILL leaks the very sessions we
    were trying to close).

    Idempotent — safe to call multiple times. The cache is cleared at
    the end so a follow-up request opens a fresh session.
    """
    sessions = list(_sessions.values())
    if not sessions:
        return

    async def _close_one(sess: _LightpandaSession) -> None:
        try:
            await asyncio.wait_for(sess.close(), timeout=_SHUTDOWN_CLOSE_TIMEOUT_SEC)
        except TimeoutError:
            log.warning(
                "cdp.shutdown.session_close_timeout",
                timeout_sec=_SHUTDOWN_CLOSE_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("cdp.shutdown.session_close_failed", error=str(exc))

    await asyncio.gather(*(_close_one(s) for s in sessions), return_exceptions=True)
    _sessions.clear()


# ── httpx transport ────────────────────────────────────────────────────


# Fetch callable signature: (method, url, headers, data, timeout) -> (status, body, headers)
FetchFn = Callable[
    [str, str, dict[str, str], bytes | None, float],
    Awaitable[tuple[int, bytes, dict[str, str]]],
]


class LightpandaTransport(httpx.AsyncBaseTransport):
    """httpx transport that routes requests through a Lightpanda CDP session.

    Use via ``httpx.AsyncClient(mounts={"all://apply.starbucks.com": <transport>})``.
    Any call to ``client.get("https://apply.starbucks.com/...")`` is then
    transparently routed through Lightpanda — the scraper/monitor code
    doesn't need to know the transport swapped underneath.

    The ``fetch`` parameter is injectable so tests can supply a fake
    instead of connecting to real Playwright.
    """

    def __init__(self, cdp_url: str, *, fetch: FetchFn | None = None):
        self._cdp_url = cdp_url
        self._fetch = fetch  # None → use real Lightpanda session

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        method = request.method
        # Copy headers but strip hop-by-hop / noisy ones Playwright re-adds.
        headers: dict[str, str] = {}
        for k, v in request.headers.items():
            kl = k.lower()
            if kl in ("host", "content-length", "accept-encoding", "connection"):
                continue
            headers[k] = v
        content = request.content or None
        timeout_ctx = request.extensions.get("timeout") or {}
        # httpx provides timeouts as a dict of phase→seconds; use the
        # "read" phase if set, else a sane default.
        timeout = float(timeout_ctx.get("read") or timeout_ctx.get("pool") or 30.0)
        try:
            if self._fetch is not None:
                status, body, resp_headers = await self._fetch(
                    method, url, headers, content, timeout
                )
            else:
                sess = _get_session(self._cdp_url)
                status, body, resp_headers = await sess.request(
                    method, url, headers=headers, data=content, timeout=timeout
                )
        except CdpRequestError as exc:
            # Surface as httpx ConnectError so existing retry logic treats
            # it the same as any other transport failure.
            raise httpx.ConnectError(str(exc), request=request) from exc
        except httpx.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise httpx.ConnectError(f"cdp transport failed: {exc}", request=request) from exc
        # Strip headers that no longer match the body. Lightpanda's
        # APIRequestContext returns the body already decompressed, so
        # passing through ``content-encoding: gzip`` would make httpx try
        # to inflate the body a second time and crash with
        # ``DecodingError: incorrect header check``. Same for
        # ``content-length`` and ``transfer-encoding`` — the bytes we hand
        # back are the final post-transfer-decoding payload.
        cleaned_headers = {
            k: v
            for k, v in resp_headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(
            status_code=status,
            headers=cleaned_headers,
            content=body,
            request=request,
        )

    async def aclose(self) -> None:
        # Session lifecycle is deliberately decoupled from httpx client
        # lifecycle — sessions are long-lived singletons and closing the
        # client shouldn't tear them down (other clients in the same
        # process may still be using them). Use ``shutdown_all_sessions``
        # from the worker shutdown path.
        return None


# ── Mount builder (consumed by src/shared/http.py) ────────────────────


_SUPPORTED_BACKENDS = frozenset({"lightpanda"})


def build_cdp_mounts(*, fetch: FetchFn | None = None) -> dict[str, httpx.AsyncBaseTransport] | None:
    """Build httpx ``mounts`` dict for every hostname in ``CDP_ROUTES``.

    Returns ``None`` when no routes are configured or the backend URL
    isn't set. Hostnames with unknown backend names are logged and
    skipped (graceful degradation — a typo shouldn't break the whole
    client).

    The ``fetch`` parameter is passed through to every transport for
    test injection — production code calls this with no argument.
    """
    routes = _cdp_routes()
    if not routes:
        return None

    cdp_url = _lightpanda_cdp_url()
    if not cdp_url:
        log.warning(
            "cdp.routes_set_but_no_url",
            hosts=sorted(routes),
            hint="set LIGHTPANDA_CDP_URL or remove CDP_ROUTES",
        )
        return None

    mounts: dict[str, httpx.AsyncBaseTransport] = {}
    for host, backend in routes.items():
        if backend not in _SUPPORTED_BACKENDS:
            log.warning(
                "cdp.route_unknown_backend",
                host=host,
                backend=backend,
                supported=sorted(_SUPPORTED_BACKENDS),
            )
            continue
        key = f"all://{host}"
        mounts[key] = LightpandaTransport(cdp_url, fetch=fetch)
        log.info("cdp.mount", host=host, backend=backend)
    return mounts or None


def parse_cdp_routes(raw: str | dict[str, str] | None) -> dict[str, str]:
    """Parse ``CDP_ROUTES`` env var into ``{host: backend}``.

    Accepts either a JSON string or an already-parsed dict. Invalid JSON
    logs a warning and returns an empty dict so the crawler keeps
    running without CDP routing.
    """
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("cdp.routes_parse_failed", error=str(exc), raw=raw[:200])
        return {}
    if not isinstance(parsed, dict):
        log.warning("cdp.routes_not_object", raw=raw[:200])
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def should_route_via_cdp(url: str) -> bool:
    """Convenience check for call sites that want to branch explicitly.

    The primary integration point is ``build_cdp_mounts`` via the
    shared httpx client — code using the shared client doesn't need to
    call this. But some code paths (e.g. Playwright-based browser
    scrapers that don't go through httpx) may want to know whether
    their target host should be routed differently.
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host:
        return False
    return host in _cdp_routes()
