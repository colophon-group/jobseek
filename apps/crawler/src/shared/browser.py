"""Configuration-driven browser executor.

Centralizes Playwright browser lifecycle, navigation, and an action pipeline
so that monitors, scrapers, and scripts share one implementation.  All
behaviour is controlled via plain config dicts that flow from the JSON columns
in boards.csv — no schema change needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

try:
    from src import metrics
except ImportError:
    # The slim ``jobseek-crawler-setup`` (ws CLI) wheel does not ship
    # ``src/metrics.py`` — it would pull in prometheus_client, which is
    # unnecessary for workspace/config-time commands. Fall back to a
    # no-op stub so this module stays importable from the ws install.
    class _NoopMetric:
        def labels(self, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            pass

    class _NoopMetricsModule:
        def __getattr__(self, _name):
            return _NoopMetric()

    metrics = _NoopMetricsModule()  # type: ignore[assignment]

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
DEFAULT_WAIT = "networkidle"
# Default fallback for ``navigate()``: when the primary ``page.goto`` times out
# (typically because an SPA never reaches ``networkidle`` due to persistent
# analytics/telemetry chatter), retry once with ``domcontentloaded``. Set
# ``wait_fallback=None`` in config to explicitly disable for a given board.
# This is strictly safer than the previous behaviour: the fallback only fires
# on paths that were already failing, so there is no extra CPU cost vs the
# status quo, and sites that do settle under ``networkidle`` are untouched.
DEFAULT_WAIT_FALLBACK = "domcontentloaded"
DEFAULT_TIMEOUT = 30_000
FALLBACK_WAIT_TIMEOUT = 5_000
CONTEXT_TIMEOUT = 120_000  # hard cap: no single Playwright operation exceeds 2 minutes
BROWSER_CLOSE_TIMEOUT_SECONDS = 15.0
NAVIGATION_NETWORK_RETRY_DELAY_SECONDS = 0.5
VALID_WAIT_STRATEGIES = frozenset({"load", "domcontentloaded", "networkidle", "commit"})
VALID_RESOURCE_POLICIES = frozenset({"auto", "none", "lean", "aggressive"})
VALID_BLOCK_RESOURCE_TYPES = frozenset(
    {
        "document",
        "stylesheet",
        "image",
        "media",
        "font",
        "script",
        "texttrack",
        "xhr",
        "fetch",
        "eventsource",
        "websocket",
        "manifest",
        "other",
    }
)
# These resource classes consume bandwidth but are not needed for ordinary
# DOM/job-data extraction. They are blocked only when board recon explicitly
# opts in after a board-specific canary.
LEAN_BLOCK_RESOURCE_TYPES = frozenset({"font", "media"})
AGGRESSIVE_BLOCK_RESOURCE_TYPES = LEAN_BLOCK_RESOURCE_TYPES | {"image"}
# The explicit aggressive policy also aborts video and telemetry hosts that
# repeatedly dominated provider activity.
# Match by hostname suffix so regional/CDN subdomains are covered without
# blocking broad providers such as google.com or jsdelivr.net, which can host
# application code required to render a job page.
AGGRESSIVE_BLOCK_HOSTS = frozenset(
    {
        "youtube.com",
        "youtube-nocookie.com",
        "ytimg.com",
        "google-analytics.com",
        "googletagmanager.com",
        "doubleclick.net",
        "googlesyndication.com",
        "connect.facebook.net",
        "newrelic.com",
        "nr-data.net",
        "bat.bing.com",
    }
)
_RETRYABLE_NAVIGATION_NETWORK_ERRORS = {
    "ERR_CONNECTION_RESET": "connection_reset",
    "ERR_NETWORK_CHANGED": "network_changed",
    "ERR_SOCKET_NOT_CONNECTED": "socket_not_connected",
}
OVERLAY_SELECTORS = (
    '[class*="cookie-banner"]',
    '[class*="cookie-consent"]',
    '[class*="cookie-notice"]',
    '[id*="cookie"]',
    '[class*="consent-banner"]',
    '[class*="consent-modal"]',
    '[role="dialog"][class*="cookie"]',
    '[role="dialog"][class*="consent"]',
)

# Browser config keys recognised by open_page / navigate / run_actions.
# Used by scrapers and monitors to separate browser keys from other config.
BROWSER_KEYS = frozenset(
    {
        "wait",
        "wait_fallback",
        "timeout",
        "user_agent",
        "headless",
        "stealth",
        "actions",
        "warmup_url",
        "cookies",
        "disable_http2",
        "persistent_context",
        "channel",
        "viewport",
        "locale",
        "skip_ssl",
        "resource_policy",
        "bot_protection",
        "block_resource_types",
        "block_hosts",
        # Scrapers project their config through BROWSER_KEYS before calling
        # render(). Without this entry a board-level proxy opt-in is silently
        # discarded and the browser launches from direct egress.
        "proxy",
    }
)

# Sites that fingerprint the browser (Akamai Bot Manager, PerimeterX,
# DataDome) reject vanilla ``pw.chromium.launch() + browser.new_context()``
# because that pair produces a cold Chromium profile with no plugins, no
# history, no extensions — a shape indistinguishable from automation.
# ``launch_persistent_context`` with a user-data-dir + ``channel="chrome"``
# produces a real-Chrome profile that passes most bot-manager challenges.
# Boards opt in via ``"persistent_context": true`` (and usually
# ``"channel": "chrome"``) in monitor_config / scraper_config.
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
DEFAULT_LOCALE = "en-US"

# Narrow subset that affects only ``navigate()`` and the action pipeline — not
# browser launch (``open_page``).  Use this in call sites that historically
# only forwarded ``wait``/``timeout``/``actions`` so we can add ``wait_fallback``
# without silently activating previously-dropped launch-time keys (``stealth``,
# ``user_agent``, ``cookies``, etc.) on boards that set them.
NAVIGATE_KEYS = frozenset({"wait", "wait_fallback", "timeout", "actions"})

# Playwright raises ``TargetClosedError`` (a public ``Error`` subclass) when
# Chromium loses the page, context, or browser while an operation is in
# flight.  The concrete subclass is not exported from ``playwright.async_api``,
# but the message is stable and intentionally identifies all three resources.
# Keep the classification here so callers do not import Playwright's private
# ``_impl`` package.
_TARGET_CLOSED_MARKER = "Target page, context or browser has been closed"


class BrowserNavigationHTTPStatusError(RuntimeError):
    """A browser navigation completed with an HTTP error document."""

    def __init__(
        self,
        *,
        requested_url: str,
        response_url: str,
        status: int,
        phase: str,
    ) -> None:
        self.requested_url = requested_url
        self.response_url = response_url
        self.status = status
        self.phase = phase
        super().__init__(
            f"Browser navigation returned HTTP {status} during {phase} navigation "
            f"(requested_url={requested_url!r}, response_url={response_url!r})"
        )


class BrowserActionNoMatchError(RuntimeError):
    """A required click action had no matching element."""


class BrowserActionUnknownError(RuntimeError):
    """A configured browser action type is not supported."""


class BrowserBackend:
    """Python-only lifecycle and page-allocation boundary.

    This seam keeps local browser allocation out of worker orchestration while
    the Python crawler still exists. It deliberately exposes Playwright
    ``Page`` behavior and is therefore not the Go/Lightpanda wire boundary.
    Go uses the typed browser plan/result contract tracked by #7937 and #7961.
    """

    implementation = "unknown"

    async def start(self) -> BrowserBackend:
        raise NotImplementedError

    @asynccontextmanager
    async def open_page(
        self,
        config: dict | None = None,
        *,
        use_proxy: bool = False,
        target_url: str | None = None,
    ) -> AsyncIterator[Any]:
        raise NotImplementedError
        yield  # pragma: no cover - makes this an async context manager

    async def stop(self) -> None:
        raise NotImplementedError


class ChromiumBrowserBackend(BrowserBackend):
    """Existing local Playwright/Chromium implementation of BrowserBackend."""

    implementation = "chromium"

    def __init__(self) -> None:
        self._owner: object | None = None
        self._playwright: Any | None = None

    async def start(self) -> ChromiumBrowserBackend:
        from playwright.async_api import async_playwright

        if self._playwright is not None:
            return self
        owner = async_playwright()
        try:
            playwright = await owner.start()
        except BaseException:
            metrics.browser_backend_lifecycle_total.labels(
                backend=self.implementation,
                event="start",
                outcome="error",
            ).inc()
            raise
        self._owner = owner
        self._playwright = playwright
        metrics.browser_backend_lifecycle_total.labels(
            backend=self.implementation,
            event="start",
            outcome="success",
        ).inc()
        return self

    @asynccontextmanager
    async def open_page(
        self,
        config: dict | None = None,
        *,
        use_proxy: bool = False,
        target_url: str | None = None,
    ) -> AsyncIterator[Any]:
        if self._playwright is None:
            raise RuntimeError("browser backend has not been started")
        async with _open_page_playwright(
            self._playwright,
            config,
            use_proxy=use_proxy,
            target_url=target_url,
        ) as page:
            yield page

    async def stop(self) -> None:
        playwright = self._playwright
        if playwright is None:
            return
        try:
            await playwright.stop()
        except BaseException:
            metrics.browser_backend_lifecycle_total.labels(
                backend=self.implementation,
                event="stop",
                outcome="error",
            ).inc()
            raise
        finally:
            self._playwright = None
            self._owner = None
        metrics.browser_backend_lifecycle_total.labels(
            backend=self.implementation,
            event="stop",
            outcome="success",
        ).inc()


def is_target_closed_error(exc: BaseException) -> bool:
    """Return whether *exc* is Playwright's lost-target failure class."""
    return isinstance(exc, PlaywrightError) and _TARGET_CLOSED_MARKER in str(exc)


def _retryable_navigation_network_error(exc: BaseException) -> str | None:
    """Classify transient Chromium transport failures safe for one retry."""
    if not isinstance(exc, PlaywrightError):
        return None
    message = str(exc)
    for marker, reason in _RETRYABLE_NAVIGATION_NETWORK_ERRORS.items():
        if marker in message:
            return reason
    return None


async def _close_browser_resource(resource, resource_name: str) -> None:
    """Close a Playwright resource without replacing the completed page task."""
    try:
        await asyncio.wait_for(resource.close(), timeout=BROWSER_CLOSE_TIMEOUT_SECONDS)
    except TimeoutError:
        metrics.browser_cleanup_failures_total.labels(
            resource=resource_name,
            outcome="timeout",
        ).inc()
        log.warning(
            "browser.cleanup.timeout",
            resource=resource_name,
            timeout_seconds=BROWSER_CLOSE_TIMEOUT_SECONDS,
        )
    except Exception:
        metrics.browser_cleanup_failures_total.labels(
            resource=resource_name,
            outcome="error",
        ).inc()
        log.warning("browser.cleanup.error", resource=resource_name, exc_info=True)


# ---------------------------------------------------------------------------
# Config placeholders
# ---------------------------------------------------------------------------


def _resolve_placeholders(cookies: list[dict]) -> list[dict]:
    """Replace ``{uuid}`` in cookie values with a fresh random UUID."""
    resolved = []
    for cookie in cookies:
        value = cookie.get("value")
        if isinstance(value, str) and "{uuid}" in value:
            cookie = {**cookie, "value": value.replace("{uuid}", uuid.uuid4().hex)}
        resolved.append(cookie)
    return resolved


def _resolve_resource_blocking(
    config: dict,
    *,
    use_proxy: bool = False,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return resource types and host suffixes to abort for this context.

    ``none`` is the default, so an unconfigured board keeps native browser
    networking. ``auto`` is an opt-in recon result: it uses ``lean`` only when
    recon explicitly recorded ``bot_protection: false`` and the browser config
    has no anti-bot-shaped transport/profile settings. Unknown or protected
    boards remain ``none``. Boards can also select a fixed policy and extend
    it with board-specific types/hosts after a same-egress canary.
    """

    raw_policy = config.get("resource_policy")
    if raw_policy is None:
        policy = "none"
    elif not isinstance(raw_policy, str):
        raise ValueError("resource_policy must be a string when provided")
    else:
        policy = raw_policy
    if policy not in VALID_RESOURCE_POLICIES:
        raise ValueError(
            f"Invalid resource_policy {policy!r}, must be one of {sorted(VALID_RESOURCE_POLICIES)}"
        )

    if "bot_protection" in config and not isinstance(config["bot_protection"], bool):
        raise ValueError("bot_protection must be a boolean when provided")

    # Validate additive syntax even when the off switch ignores valid lists,
    # so malformed agent output is still rejected by ``ws validate``.
    raw_resource_types = config.get("block_resource_types", [])
    if not isinstance(raw_resource_types, list) or not all(
        isinstance(value, str) and value in VALID_BLOCK_RESOURCE_TYPES
        for value in raw_resource_types
    ):
        raise ValueError(
            "block_resource_types must be a list containing only "
            f"{sorted(VALID_BLOCK_RESOURCE_TYPES)}"
        )

    raw_hosts = config.get("block_hosts", [])
    if not isinstance(raw_hosts, list):
        raise ValueError("block_hosts must be a list of hostname suffixes")
    additive_hosts: set[str] = set()
    for value in raw_hosts:
        if not isinstance(value, str):
            raise ValueError("block_hosts must be a list of hostname suffixes")
        host = value.strip().lower()
        if host.startswith("*."):
            host = host[2:]
        host = host.lstrip(".")
        if not host or "://" in host or "/" in host or ":" in host:
            raise ValueError(f"Invalid block_hosts hostname suffix: {value!r}")
        additive_hosts.add(host)

    # ``none`` is the absolute fail-safe and rollback switch. Ignore stale
    # additive lists rather than leaving a route installed after an operator
    # changes only the policy (or omits it to return to the default).
    if policy == "none":
        return frozenset(), frozenset()

    if policy == "auto":
        if raw_resource_types or raw_hosts:
            raise ValueError("resource_policy 'auto' cannot be combined with additive block lists")
        anti_bot_shaped = bool(
            use_proxy
            or config.get("proxy")
            or config.get("persistent_context")
            or config.get("stealth")
            or config.get("headless") is False
            or config.get("channel")
            or config.get("warmup_url")
            or config.get("cookies")
            or config.get("user_agent")
            or config.get("disable_http2")
        )
        recon_cleared = config.get("bot_protection") is False
        policy = "lean" if recon_cleared and not anti_bot_shaped else "none"

    if policy == "aggressive":
        resource_types = set(AGGRESSIVE_BLOCK_RESOURCE_TYPES)
        hosts = set(AGGRESSIVE_BLOCK_HOSTS)
    elif policy == "lean":
        resource_types = set(LEAN_BLOCK_RESOURCE_TYPES)
        hosts = set()
    else:
        resource_types = set()
        hosts = set()

    resource_types.update(raw_resource_types)
    hosts.update(additive_hosts)

    return frozenset(resource_types), frozenset(hosts)


def _host_matches_suffix(hostname: str | None, suffixes: frozenset[str]) -> bool:
    """Return whether *hostname* is exactly or below a blocked suffix."""

    if not hostname:
        return False
    hostname = hostname.lower().rstrip(".")
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


async def _install_resource_blocking(
    context,
    resource_types: frozenset[str],
    host_suffixes: frozenset[str],
) -> None:
    """Install one context-wide route before any warmup/navigation occurs."""

    if not resource_types and not host_suffixes:
        return

    async def _route(route, request) -> None:
        resource_type = request.resource_type
        try:
            hostname = urlsplit(request.url).hostname
        except (TypeError, ValueError):
            hostname = None

        reason: str | None = None
        if resource_type in resource_types:
            reason = "resource_type"
        elif _host_matches_suffix(hostname, host_suffixes):
            reason = "host"

        if reason is None:
            await route.continue_()
            return

        metrics.browser_resource_blocked_total.labels(
            reason=reason,
            resource_type=resource_type,
        ).inc()
        await route.abort()

    await context.route("**/*", _route)


def _x_server_alive(display: str) -> bool:
    """Probe the X server by running ``xdpyinfo``.

    Returns ``False`` on timeout, missing binary, or non-zero exit — i.e.
    any state that would cause Playwright's headful launch to crash with
    "XServer running" (#2431). A ``True`` result means an X server is
    actually responding to protocol requests on *display*, not merely that
    ``DISPLAY`` is set in the environment.

    ``xdpyinfo`` ships in the ``x11-utils`` Debian package (installed in
    ``apps/crawler/Dockerfile`` — see the full stage apt line). On dev
    machines without the binary, ``FileNotFoundError`` falls through to
    ``False`` and the caller coerces to headless just like in prod.

    The 2s timeout caps worst-case latency: a healthy ``xdpyinfo`` returns
    in ~20ms; the timeout fires only when the X server is hung. Called
    once per browser launch — if that becomes hot, cache the result.
    """
    try:
        result = subprocess.run(
            ["xdpyinfo", "-display", display],
            capture_output=True,
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _resolve_headless(requested_headless: bool) -> tuple[bool, bool]:
    """Decide the effective headless mode given the runtime display state.

    Boards that need to pass Akamai / PerimeterX / DataDome bot managers set
    ``"headless": false`` in their monitor/scraper config. The crawler's
    ``browser-1`` container ships an xvfb entrypoint (``/usr/local/bin/with-xvfb``
    in ``apps/crawler/Dockerfile``) that starts ``Xvfb :99``, waits for the
    server to respond to ``xdpyinfo``, and exports ``DISPLAY=:99`` before
    launching — so headful Chromium has an X server to draw into.

    If that entrypoint is missing, bypassed, or Xvfb dies *after* the
    entrypoint handed off to the crawler (e.g. OOM-killed, segfault mid-run),
    Playwright crashes with:

        "launched a headed browser without having a XServer running. Set
         either headless: true or use xvfb-run <your-playwright-app>"

    Historically this produced hourly crashes per affected board (#2431).
    Instead of hard-failing, fall back to headless mode and log loudly — the
    Akamai bypass is best-effort and a degraded run (possibly blocked by the
    bot manager) is strictly better than a crash that blocks the worker slot
    every cycle.

    Probing the X server via ``xdpyinfo`` (not just ``$DISPLAY``-is-set) is
    what distinguishes this from the original #2431 fix: a dead Xvfb still
    leaves ``DISPLAY`` set in the child environment, so a bare env check
    would wave the launch through to the same crash it was meant to prevent.

    Returns ``(effective_headless, coerced)`` where ``coerced`` is True only
    when we flipped the caller's explicit ``headless=False`` to True.
    """
    if requested_headless:
        return True, False
    display = os.environ.get("DISPLAY")
    if not display:
        log.warning(
            "browser.headless_coerced",
            reason="no_display",
            detail=(
                "headless=False requested but DISPLAY is unset — falling "
                "back to headless=True with --headless=new. Expected in "
                "dev; in prod this means the xvfb entrypoint (with-xvfb) "
                "did not run. Rebuild crawler-full and ensure docker run "
                "does not override ENTRYPOINT."
            ),
        )
        metrics.browser_headless_coerced_total.labels(reason="no_display").inc()
        return True, True
    if not _x_server_alive(display):
        log.warning(
            "browser.headless_coerced",
            reason="display_unresponsive",
            display=display,
            detail=(
                "headless=False requested and DISPLAY is set, but "
                "xdpyinfo could not talk to the X server (timed out, "
                "non-zero exit, or xdpyinfo missing). Falling back to "
                "headless=True with --headless=new rather than letting "
                "Playwright crash on launch. In prod this usually means "
                "Xvfb died after with-xvfb handed off — check the "
                "browser-1 container logs for Xvfb exit traces."
            ),
        )
        metrics.browser_headless_coerced_total.labels(reason="display_unresponsive").inc()
        return True, True
    return False, False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_page(
    pw,  # BrowserBackend or legacy AsyncPlaywright
    config: dict | None = None,
    *,
    use_proxy: bool = False,
    target_url: str | None = None,
) -> AsyncIterator:
    """Allocate a page through the configured browser backend.

    The legacy ``AsyncPlaywright`` input remains accepted for workspace and
    focused test callers. Production Python workers pass a
    :class:`BrowserBackend`; Go/Lightpanda uses the separate typed
    BrowserExecutor contract.
    """

    if isinstance(pw, BrowserBackend):
        async with pw.open_page(
            config,
            use_proxy=use_proxy,
            target_url=target_url,
        ) as page:
            yield page
        return

    async with _open_page_playwright(
        pw,
        config,
        use_proxy=use_proxy,
        target_url=target_url,
    ) as page:
        yield page


@asynccontextmanager
async def _open_page_playwright(
    pw,
    config: dict | None = None,
    *,
    use_proxy: bool = False,
    target_url: str | None = None,
) -> AsyncIterator:
    """Select one affine Webshare endpoint and account for its outcome."""

    from src.shared.proxy import (
        abandon_proxy_selection,
        playwright_proxy_selection_for,
        report_proxy_failure,
        report_proxy_success,
    )

    config = config or {}
    selection_origin = None
    selection_url = target_url or config.get("warmup_url")
    if isinstance(selection_url, str):
        with contextlib.suppress(ValueError):
            selection_origin = (urlsplit(selection_url).hostname or "").lower() or None
    pw_proxy, selection = playwright_proxy_selection_for(
        use_proxy=use_proxy,
        origin=selection_origin,
    )

    try:
        async with _open_page_playwright_session(
            pw,
            config,
            use_proxy=use_proxy,
            pw_proxy=pw_proxy,
        ) as page:
            yield page
    except BaseException as exc:
        if selection is not None:
            if isinstance(exc, BrowserNavigationHTTPStatusError):
                failure_origin = selection_origin
                with contextlib.suppress(ValueError):
                    failure_origin = (
                        urlsplit(exc.response_url).hostname
                        or urlsplit(exc.requested_url).hostname
                        or selection_origin
                    )
                failure_origin = failure_origin.lower() if failure_origin else None
                if exc.status in {403, 429}:
                    report_proxy_failure(
                        selection,
                        origin=failure_origin,
                        reason="origin_block",
                    )
                else:
                    # A concrete target response proves the proxy tunnel is
                    # usable even though navigation correctly propagates the
                    # target's HTTP error to the caller.
                    report_proxy_success(selection, origin=selection_origin)
            elif isinstance(exc, PlaywrightError):
                message = str(exc).upper()
                if "407" in message and ("PROXY" in message or "TUNNEL" in message):
                    report_proxy_failure(selection, reason="proxy_auth")
                elif (
                    "ERR_PROXY_" in message
                    or "ERR_TUNNEL_" in message
                    or "ERR_NO_SUPPORTED_PROXIES" in message
                ):
                    report_proxy_failure(selection, reason="proxy_transport")
                elif any(marker in message for marker in _RETRYABLE_NAVIGATION_NETWORK_ERRORS):
                    report_proxy_failure(
                        selection,
                        origin=selection_origin,
                        reason="origin_transport",
                    )
                else:
                    abandon_proxy_selection(selection, origin=selection_origin)
            else:
                abandon_proxy_selection(selection, origin=selection_origin)
        raise
    else:
        if selection is not None:
            report_proxy_success(selection, origin=selection_origin)


@asynccontextmanager
async def _open_page_playwright_session(
    pw,
    config: dict | None = None,
    *,
    use_proxy: bool = False,
    pw_proxy: dict[str, str] | None = None,
) -> AsyncIterator:
    """Create browser → context → page.  Yields a Playwright *Page*.

    The caller manages the outer ``async_playwright()`` context so they can
    attach hooks (e.g. response interception) between page creation and
    navigation.

    Config keys consumed: ``user_agent``, ``headless`` (default ``True``),
    ``persistent_context``, ``channel``, ``viewport``, ``locale``.

    ``headless: false`` requires an X server at runtime (DISPLAY env var
    set). In production the browser worker's Docker entrypoint
    (``/usr/local/bin/with-xvfb``) starts Xvfb :99 and exports DISPLAY
    before launching. If DISPLAY is unset at runtime we coerce back to
    ``headless=True`` with ``--headless=new`` and log a warning — see
    :func:`_resolve_headless` for rationale (#2431).

    When ``use_proxy`` is True, the browser launches through the active
    proxy provider (see :mod:`src.shared.proxy`).

    When ``persistent_context`` is True, uses
    ``pw.chromium.launch_persistent_context`` with an ephemeral
    user-data-dir — needed for Akamai / PerimeterX / DataDome sites that
    reject vanilla ``launch + new_context`` profiles. Usually combined
    with ``"channel": "chrome"`` to use the system Chrome binary (which
    is on the trusted-vendor list for most bot managers, unlike
    Playwright's bundled Chromium).
    """
    config = config or {}
    blocked_resource_types, blocked_host_suffixes = _resolve_resource_blocking(
        config,
        use_proxy=use_proxy,
    )
    requested_headless = bool(config.get("headless", True))
    # Boards that need Akamai/PerimeterX bypass set ``headless: false`` and
    # rely on the ``browser-1`` container's xvfb entrypoint to provide an
    # X server. If DISPLAY is missing at runtime (entrypoint bypassed,
    # image predates the entrypoint), a headful launch crashes with
    # "launched a headed browser without having a XServer running".
    # Coerce to headless + ``--headless=new`` so the run degrades to
    # bot-manager-blocked rather than blocking the worker slot every
    # cycle. See _resolve_headless for the full rationale (#2431).
    headless, headless_coerced = _resolve_headless(requested_headless)
    warmup_url = config.get("warmup_url")
    cookies = config.get("cookies")
    persistent = bool(config.get("persistent_context"))
    channel = config.get("channel")
    viewport = config.get("viewport", DEFAULT_VIEWPORT)
    locale = config.get("locale", DEFAULT_LOCALE)
    # When a real-browser channel is used, the binary's own UA string
    # (e.g. ``Chrome/146.0.0.0``) matches its JS fingerprint. Overriding
    # with ``DEFAULT_USER_AGENT`` (fixed ``Chrome/133``) creates a client
    # hint mismatch that Akamai's sensor detects. Keep the default UA
    # for bundled-Chromium launches (where Playwright's pinned version
    # doesn't match any real release anyway), but opt-out when channel
    # pins a shipping Chrome.
    if "user_agent" in config:
        user_agent = config["user_agent"]
    elif channel:
        user_agent = None
    else:
        user_agent = DEFAULT_USER_AGENT

    extra_args: list[str] = []
    if headless and (config.get("stealth") or headless_coerced):
        # Chromium's new headless mode (--headless=new) is less detectable
        # by anti-bot systems (Cloudflare Turnstile etc.). Enable via
        # stealth: true, or automatically when we coerced a headful
        # request into headless (#2431 — Akamai-gated boards fall back
        # here when xvfb is missing, and --headless=new gives them the
        # best chance of not being blocked outright).
        extra_args.append("--headless=new")
    if config.get("disable_http2"):
        extra_args.append("--disable-http2")
    if persistent:
        # Real-Chrome-profile shape: mask the ``navigator.webdriver``
        # blink feature that Akamai's sensor bundle reads before the
        # stealth init-script has a chance to mask the JS property.
        extra_args.append("--disable-blink-features=AutomationControlled")

    if persistent:
        async with _open_persistent_page(
            pw,
            headless=headless,
            channel=channel,
            extra_args=extra_args,
            pw_proxy=pw_proxy,
            user_agent=user_agent,
            viewport=viewport,
            locale=locale,
            cookies=cookies,
            warmup_url=warmup_url,
            skip_ssl=bool(config.get("skip_ssl")),
            blocked_resource_types=blocked_resource_types,
            blocked_host_suffixes=blocked_host_suffixes,
        ) as page:
            yield page
        return

    launch_kwargs: dict = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    if extra_args:
        launch_kwargs["args"] = extra_args
    if pw_proxy:
        launch_kwargs["proxy"] = pw_proxy

    browser = await pw.chromium.launch(**launch_kwargs)
    context = None
    try:
        ctx_kwargs: dict = {}
        if user_agent:
            ctx_kwargs["user_agent"] = user_agent
        if config.get("skip_ssl"):
            ctx_kwargs["ignore_https_errors"] = True
        context = await browser.new_context(**ctx_kwargs)
        context.set_default_timeout(CONTEXT_TIMEOUT)
        await _install_resource_blocking(
            context,
            blocked_resource_types,
            blocked_host_suffixes,
        )
        if cookies:
            await context.add_cookies(_resolve_placeholders(cookies))
        page = await context.new_page()
        if warmup_url:
            log.debug("browser.warmup", url=warmup_url)
            await page.goto(warmup_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        yield page
    finally:
        # A context close can fail when Chromium was killed or its transport
        # disappeared. The nested finally is deliberate: the old straight-line
        # cleanup skipped browser.close() in exactly that case, leaving the
        # outer Playwright driver to retain a surviving child until container
        # restart (#5488).
        try:
            if context:
                await _close_browser_resource(context, "context")
        finally:
            await _close_browser_resource(browser, "browser")


@asynccontextmanager
async def _open_persistent_page(
    pw,
    *,
    headless: bool,
    channel: str | None,
    extra_args: list[str],
    pw_proxy: dict | None,
    user_agent: str | None,
    viewport: dict | None,
    locale: str | None,
    cookies: list[dict] | None,
    warmup_url: str | None,
    skip_ssl: bool = False,
    blocked_resource_types: frozenset[str] = frozenset(),
    blocked_host_suffixes: frozenset[str] = frozenset(),
) -> AsyncIterator:
    """``launch_persistent_context`` variant of :func:`open_page`.

    Kept in a separate helper so the vanilla-launch path above stays a
    straight line. The user-data-dir is an ephemeral tmpdir, cleaned up
    after the context closes — we don't persist Akamai cookies between
    runs because (a) ``_abck`` tokens are short-lived and (b) leaking a
    profile between concurrent board jobs would cause cross-board
    interference under the browser worker pool.
    """
    user_data_dir = tempfile.mkdtemp(prefix="pw_persist_")
    launch_kwargs: dict = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    if extra_args:
        launch_kwargs["args"] = extra_args
    if pw_proxy:
        launch_kwargs["proxy"] = pw_proxy
    # persistent_context takes the context-level knobs directly; there's
    # no separate ``new_context`` call.
    if user_agent:
        launch_kwargs["user_agent"] = user_agent
    if viewport:
        launch_kwargs["viewport"] = viewport
    if locale:
        launch_kwargs["locale"] = locale
    if skip_ssl:
        launch_kwargs["ignore_https_errors"] = True
    context = await pw.chromium.launch_persistent_context(user_data_dir, **launch_kwargs)
    try:
        context.set_default_timeout(CONTEXT_TIMEOUT)
        await _install_resource_blocking(
            context,
            blocked_resource_types,
            blocked_host_suffixes,
        )
        if cookies:
            await context.add_cookies(_resolve_placeholders(cookies))
        # launch_persistent_context always opens one blank page; reuse
        # it rather than open a second (which would look more like a
        # user typing into a new tab, but also doubles the startup cost).
        page = context.pages[0] if context.pages else await context.new_page()
        if warmup_url:
            log.debug("browser.warmup", url=warmup_url, persistent=True)
            await page.goto(warmup_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)
        yield page
    finally:
        with contextlib.suppress(Exception):
            await _close_browser_resource(context, "persistent_context")
        shutil.rmtree(user_data_dir, ignore_errors=True)


def _raise_for_navigation_http_status(response, requested_url: str, phase: str) -> None:
    """Reject a concrete HTTP(S) main-document error response."""
    if response is None:
        return

    response_url = getattr(response, "url", "")
    if not isinstance(response_url, str):
        # Keep compatibility with custom Page implementations and loose test
        # doubles that return a response-like object without a concrete URL.
        return
    try:
        scheme = urlsplit(response_url).scheme.lower()
    except (TypeError, ValueError):
        return
    if scheme not in {"http", "https"}:
        return

    status = getattr(response, "status", None)
    if isinstance(status, int) and 400 <= status <= 599:
        raise BrowserNavigationHTTPStatusError(
            requested_url=requested_url,
            response_url=response_url,
            status=status,
            phase=phase,
        )


async def navigate(
    page,  # playwright Page
    url: str,
    config: dict | None = None,
) -> None:
    """Navigate *page* to *url* respecting wait strategy and timeout.

    Config keys:
        ``wait``           Primary wait strategy (default ``"networkidle"``).
        ``timeout``        Navigation timeout in ms (default ``30000``).
        ``wait_fallback``  Fallback load state checked on the current document
                           when the primary ``page.goto`` raises Playwright's
                           ``TimeoutError``. Transient Chromium transport
                           failures are retried once before they propagate.
                           Defaults to ``DEFAULT_WAIT_FALLBACK``
                           ("domcontentloaded") so SPA sites that never reach
                           ``networkidle`` still produce usable HTML. Set to
                           ``None`` in config to opt out; set to the same value
                           as ``wait`` for an effective no-op. The fallback is
                           capped at ``FALLBACK_WAIT_TIMEOUT`` and never starts
                           a duplicate navigation.
    """
    config = config or {}
    wait_strategy = config.get("wait", DEFAULT_WAIT)
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    # Distinguish "not set" (use default) from "explicitly None" (disable).
    if "wait_fallback" in config:
        fallback_strategy = config["wait_fallback"]
    else:
        fallback_strategy = DEFAULT_WAIT_FALLBACK

    if wait_strategy not in VALID_WAIT_STRATEGIES:
        raise ValueError(
            f"Invalid wait strategy {wait_strategy!r}, "
            f"must be one of {sorted(VALID_WAIT_STRATEGIES)}"
        )
    if fallback_strategy is not None and fallback_strategy not in VALID_WAIT_STRATEGIES:
        raise ValueError(
            f"Invalid wait_fallback strategy {fallback_strategy!r}, "
            f"must be one of {sorted(VALID_WAIT_STRATEGIES)}"
        )

    fallback_response = None

    def _capture_main_navigation_response(response) -> None:
        nonlocal fallback_response
        try:
            is_main_navigation = (
                response.frame == page.main_frame and response.request.is_navigation_request()
            )
        except (AttributeError, TypeError):
            return
        if is_main_navigation:
            # Redirects emit one response per hop. Retaining the latest one
            # gives the final committed document when goto times out while
            # waiting for a stricter state such as networkidle.
            fallback_response = response

    page.on("response", _capture_main_navigation_response)
    try:
        network_retry_reason: str | None = None
        network_retry_finished = False

        def _finish_network_retry(outcome: str) -> None:
            """Record exactly one terminal outcome for an initiated retry."""
            nonlocal network_retry_finished
            if network_retry_reason is None or network_retry_finished:
                return
            metrics.browser_navigation_network_retry_total.labels(
                reason=network_retry_reason,
                outcome=outcome,
            ).inc()
            network_retry_finished = True

        for attempt in range(2):
            try:
                response = await page.goto(url, wait_until=wait_strategy, timeout=timeout)
            except PlaywrightTimeoutError:
                if not fallback_strategy:
                    # Board opted out via wait_fallback=None. Record separately from
                    # the match-primary case so operators can tell why the retry was
                    # skipped.
                    metrics.browser_navigate_fallback_total.labels(
                        primary=wait_strategy, fallback="none", outcome="disabled"
                    ).inc()
                    _finish_network_retry("exhausted")
                    raise
                if fallback_strategy == wait_strategy:
                    # Fallback equals primary — nothing to gain from another attempt.
                    metrics.browser_navigate_fallback_total.labels(
                        primary=wait_strategy, fallback=fallback_strategy, outcome="match"
                    ).inc()
                    _finish_network_retry("exhausted")
                    raise
                break
            except PlaywrightError as exc:
                reason = _retryable_navigation_network_error(exc)
                if reason is None:
                    _finish_network_retry("exhausted")
                    raise
                if attempt == 1:
                    _finish_network_retry("exhausted")
                    raise
                network_retry_reason = reason
                metrics.browser_navigation_network_retry_total.labels(
                    reason=reason,
                    outcome="retry",
                ).inc()
                log.info(
                    "browser.navigate.network_retry",
                    url=url,
                    reason=reason,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(NAVIGATION_NETWORK_RETRY_DELAY_SECONDS)
            else:
                try:
                    _raise_for_navigation_http_status(response, url, "primary")
                except BrowserNavigationHTTPStatusError:
                    _finish_network_retry("exhausted")
                    raise
                _finish_network_retry("recovered")
                return

        fallback_timeout = min(timeout, FALLBACK_WAIT_TIMEOUT)
        log.info(
            "browser.navigate.fallback_wait",
            url=url,
            primary=wait_strategy,
            fallback=fallback_strategy,
            timeout_ms=fallback_timeout,
        )
        try:
            # A wait-strategy timeout does not imply that navigation failed. In
            # the common networkidle case the document is already committed and
            # DOMContentLoaded has fired; reissuing goto discards that usable page,
            # doubles origin traffic, and can turn a recoverable wait into another
            # timeout. Check the current document's state instead (#5708).
            await page.wait_for_load_state(fallback_strategy, timeout=fallback_timeout)
        except Exception:
            metrics.browser_navigate_fallback_total.labels(
                primary=wait_strategy, fallback=fallback_strategy, outcome="failed"
            ).inc()
            _finish_network_retry("exhausted")
            raise

        try:
            _raise_for_navigation_http_status(fallback_response, url, "fallback")
        except BrowserNavigationHTTPStatusError:
            metrics.browser_navigate_fallback_total.labels(
                primary=wait_strategy, fallback=fallback_strategy, outcome="http_error"
            ).inc()
            _finish_network_retry("exhausted")
            raise
        _finish_network_retry("recovered")
        metrics.browser_navigate_fallback_total.labels(
            primary=wait_strategy, fallback=fallback_strategy, outcome="success"
        ).inc()
    finally:
        with contextlib.suppress(Exception):
            page.remove_listener("response", _capture_main_navigation_response)


ACTION_TIMEOUT = 10.0  # seconds
_REPEAT_TIMEOUT = 300.0  # seconds — repeat actions get a longer default


async def run_actions(page, actions: list[dict]) -> None:
    """Execute an action pipeline sequentially on *page*.

    Each action is wrapped in a timeout (default 10s, configurable per-action
    via a ``"timeout"`` key).  On failure or timeout an individual action logs
    a warning and execution continues with the next action unless it opts into
    fail-closed behavior with ``"required": true``. ``paginate_collect`` is
    always required because accepting its partial URL set is unsafe.
    """
    for action in actions:
        kind = action.get("action")
        required = bool(action.get("required")) or kind == "paginate_collect"
        default_timeout = (
            _REPEAT_TIMEOUT if kind in ("repeat", "paginate_collect") else ACTION_TIMEOUT
        )
        timeout = action.get("timeout", default_timeout)
        try:
            await asyncio.wait_for(_execute_action(page, action, kind), timeout=timeout)
        except BrowserActionNoMatchError:
            log.warning(
                "browser.action.click_no_match",
                selector=action.get("selector"),
                required=required,
            )
            if required:
                raise
        except BrowserActionUnknownError:
            log.warning("browser.action.unknown", action=kind, required=required)
            if required:
                raise
        except TimeoutError:
            log.warning(
                "browser.action.timeout",
                action=kind,
                timeout=timeout,
                required=required,
            )
            if required:
                raise
        except Exception:
            log.warning(
                "browser.action.failed",
                action=kind,
                required=required,
                exc_info=True,
            )
            if required:
                raise


async def _execute_action(page, action: dict, kind: str | None) -> None:
    """Dispatch a single action on *page*."""
    if kind == "remove":
        selector = action["selector"]
        await page.evaluate(
            "(sel) => document.querySelectorAll(sel).forEach(el => el.remove())",
            selector,
        )
    elif kind == "click":
        selector = action["selector"]
        loc = page.locator(selector).first
        if await loc.count() > 0:
            await loc.click()
        else:
            raise BrowserActionNoMatchError(
                f"browser click action matched no elements: {selector!r}"
            )
    elif kind == "wait_for":
        selector = action["selector"]
        state = action.get("state", "visible")
        # The outer asyncio timeout is the action pipeline's single source of
        # truth. Disable Playwright's independent 30-second default so a
        # configured longer action is not cut short inside the locator call.
        await page.locator(selector).first.wait_for(state=state, timeout=0)
    elif kind == "wait":
        ms = action.get("ms", 1000)
        await asyncio.sleep(ms / 1000)
    elif kind == "evaluate":
        script = action["script"]
        await page.evaluate(script)
    elif kind == "dismiss_overlays":
        await dismiss_overlays(page)
    elif kind == "repeat":
        await _execute_repeat(page, action)
    elif kind == "paginate_collect":
        await _execute_paginate_collect(page, action)
    else:
        raise BrowserActionUnknownError(f"unknown browser action: {kind!r}")


def _resolve_frame(page, frame_selector: str | None):
    """Return the target frame (or the page itself) for actions.

    *frame_selector* is a CSS selector matching an ``<iframe>`` in the
    main page.  When provided, Playwright's ``frame_locator`` is used to
    pierce the cross-origin boundary.
    """
    if not frame_selector:
        return page
    return page.frame_locator(frame_selector)


async def _execute_repeat(page, action: dict) -> None:
    """Click an element repeatedly until no new links appear or selector is gone.

    When ``frame`` is set (CSS selector matching an ``<iframe>``), clicks
    target elements inside that frame.  After all clicks, links from the
    frame are injected as hidden ``<a>`` tags into the main page so the
    DOM monitor's link extractor can see them.
    """
    selector = action["selector"]
    max_iter = action.get("max", 50)
    wait_ms = action.get("wait_ms", 2000)
    frame_selector = action.get("frame")
    force = action.get("force", False)

    target = page.frame_locator(frame_selector) if frame_selector else page

    # For frame targets, measure link counts inside the frame.
    count_ctx = page
    if frame_selector:
        for f in page.frames:
            if f != page.main_frame and f.url and f.url != "about:blank":
                count_ctx = f
                break

    for i in range(max_iter):
        before = await count_ctx.evaluate("() => document.querySelectorAll('a[href]').length")
        if frame_selector and count_ctx != page:
            # Use JS click inside cross-origin frame to bypass overlays.
            clicked = await count_ctx.evaluate(
                "(sel) => { const el = document.querySelector(sel);"
                " if (el) { el.click(); return true; } return false; }",
                selector,
            )
            if not clicked:
                log.info("browser.repeat.selector_gone", iteration=i)
                break
        else:
            loc = target.locator(selector).first
            if await loc.count() == 0:
                log.info("browser.repeat.selector_gone", iteration=i)
                break
            await loc.click(force=force)
        await asyncio.sleep(wait_ms / 1000)
        after = await count_ctx.evaluate("() => document.querySelectorAll('a[href]').length")
        if after <= before:
            log.info("browser.repeat.no_new_links", iteration=i + 1, links=after)
            break
        log.debug("browser.repeat.click", iteration=i + 1, new=after - before, total=after)

    # Inject cross-origin iframe links into the main page.
    if frame_selector:
        frame = None
        for f in page.frames:
            if f != page.main_frame and f.url and f.url != "about:blank":
                frame = f
                break
        if frame:
            links = await frame.evaluate(
                "() => [...document.querySelectorAll('a[href]')].map(a => a.href)"
            )
            if links:
                await page.evaluate(
                    "(urls) => urls.forEach(u => {"
                    "  const a = document.createElement('a');"
                    "  a.href = u; a.style.display = 'none';"
                    "  document.body.appendChild(a);"
                    "})",
                    links,
                )
                log.info("browser.repeat.frame_links_injected", count=len(links))


async def _execute_paginate_collect(page, action: dict) -> None:
    """Click through paginated content, collecting links from every page.

    For portals that *replace* page content on navigation (rather than
    appending), the standard ``repeat`` action only sees the last page.
    This action visits every page, accumulates all ``<a href>`` URLs, and
    injects them as hidden elements so the dom monitor's link extractor
    finds the full set.

    Config keys:
        next_selector (str): CSS selector for the clickable "next page"
            element.  Pagination stops when the selector matches nothing.
        page_size_selector (str): Optional CSS selector for a ``<select>``
            dropdown that controls items-per-page.
        page_size (int|str): Value to set on the page-size dropdown.
        wait_ms (int): Delay in ms after each navigation (default 5000).
        max_pages (int): Safety cap on pagination clicks (default 50).
        force (bool): Use Playwright's force-click behavior when consent
            overlays or other non-semantic elements intercept the control.
        stop_when_hidden (bool): Treat a matching but hidden next-page control
            as the terminal state. Some portals keep the final control in the
            DOM instead of removing or disabling it.
    """
    next_sel = action.get("next_selector", "li.next:not(.next_disabled) a")
    ps_selector = action.get("page_size_selector", "")
    page_size = action.get("page_size", "")
    wait_ms = action.get("wait_ms", 5000)
    max_pages = action.get("max_pages", 50)
    force = action.get("force", False)
    stop_when_hidden = action.get("stop_when_hidden", False)

    if ps_selector and page_size:
        await page.evaluate(
            """([selector, value]) => {
                const sel = document.querySelector(selector);
                if (!sel) return;
                sel.value = value;
                sel.dispatchEvent(new Event('change'));
                // SuccessFactors uses the juic event bus.
                if (typeof juic !== 'undefined' && sel.id)
                    juic.fire(sel.id, '_onChange', new Event('change'));
            }""",
            [ps_selector, str(page_size)],
        )
        await asyncio.sleep(wait_ms / 1000)

    collect_js = """() => Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.href)
        .filter(href => href.startsWith('http'))"""
    all_links = set(await page.evaluate(collect_js))

    # Keep the controller outside the document. A normal anchor click replaces
    # the page's JavaScript execution context, so a loop inside one evaluate()
    # call dies on the first navigation. Locator clicks return only after the
    # initiated navigation settles, letting each iteration resume against the
    # newly loaded document; SPA pagination continues to work the same way.
    for _ in range(max_pages):
        next_el = page.locator(next_sel).first
        if await next_el.count() == 0:
            break
        if stop_when_hidden and not await next_el.is_visible():
            log.info("browser.paginate_collect.next_hidden")
            break
        await next_el.click(force=force)
        await asyncio.sleep(wait_ms / 1000)
        page_links = set(await page.evaluate(collect_js))
        if not page_links - all_links:
            raise RuntimeError("paginate_collect made no progress after clicking next page")
        all_links.update(page_links)
    else:
        if await page.locator(next_sel).first.count() > 0:
            raise RuntimeError(f"paginate_collect reached max_pages={max_pages} before the end")

    await page.evaluate(
        """urls => {
            const container = document.createElement('div');
            container.style.display = 'none';
            urls.forEach(href => {
                const a = document.createElement('a');
                a.href = href;
                container.appendChild(a);
            });
            document.body.appendChild(container);
        }""",
        sorted(all_links),
    )
    log.info("browser.paginate_collect.done", total=len(all_links))


async def dismiss_overlays(page) -> None:
    """Remove common cookie / consent / dialog overlays from *page*."""
    selector = ", ".join(OVERLAY_SELECTORS)
    await page.evaluate(
        "(sel) => document.querySelectorAll(sel).forEach(el => el.remove())",
        selector,
    )


# Playwright raises a plain ``Error`` when ``page.content()`` is called while
# the page is in the middle of a navigation (SPA route change, client redirect,
# delayed meta-refresh, late-firing analytics that triggers a reload). The
# error message is stable across versions — substring match is sufficient.
# Observed on post.ch (issue #2188); same race can occur on any board whose
# final actions trigger navigation or whose SPA settles after the configured
# wait strategy fires.
_CONTENT_NAVIGATING_MARKER = "page is navigating and changing the content"
_SAFE_CONTENT_RETRIES = 2
_SAFE_CONTENT_SETTLE_MS = 500


async def safe_content(page) -> str:
    """Return ``page.content()`` with retry on the navigation-race error.

    Playwright refuses to serialize the DOM when the page is mid-navigation
    and raises ``Error("... page is navigating and changing the content")``.
    The race is almost always transient: waiting for ``domcontentloaded``
    after the error lets the new document settle, and a retry succeeds.
    Non-matching errors propagate so real failures are not swallowed.
    """
    last_exc: Exception | None = None
    for attempt in range(_SAFE_CONTENT_RETRIES + 1):
        try:
            html = await page.content()
        except Exception as exc:  # noqa: BLE001 — Playwright raises plain Error
            if _CONTENT_NAVIGATING_MARKER not in str(exc):
                raise
            last_exc = exc
            if attempt == _SAFE_CONTENT_RETRIES:
                break
            metrics.browser_content_retry_total.labels(outcome="retry").inc()
            log.info("browser.content.navigating_retry", attempt=attempt + 1)
            # Tolerate wait failure — we retry page.content() either way.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=CONTEXT_TIMEOUT)
            await asyncio.sleep(_SAFE_CONTENT_SETTLE_MS / 1000)
            continue
        else:
            if attempt > 0:
                metrics.browser_content_retry_total.labels(outcome="recovered").inc()
            return html
    metrics.browser_content_retry_total.labels(outcome="failed").inc()
    assert last_exc is not None
    raise last_exc


async def render(url: str, config: dict | None = None, pw=None) -> str:
    """All-in-one: launch browser → navigate → run actions → return HTML.

    Convenience wrapper for consumers that just need rendered page content.

    When *pw* (an ``AsyncPlaywright`` instance) is provided, it is reused
    instead of launching a new ``async_playwright()`` context.
    """
    config = config or {}

    if pw is not None:
        async with open_page(
            pw,
            config,
            use_proxy=bool(config.get("proxy")),
            target_url=url,
        ) as page:
            await navigate(page, url, config)
            await run_actions(page, config.get("actions", []))
            return await safe_content(page)

    from playwright.async_api import async_playwright

    async with (
        async_playwright() as _pw,
        open_page(
            _pw,
            config,
            use_proxy=bool(config.get("proxy")),
            target_url=url,
        ) as page,
    ):
        await navigate(page, url, config)
        await run_actions(page, config.get("actions", []))
        return await safe_content(page)
