"""Monitor registry and shared types.

Monitors discover which jobs exist on a board. They return either:
- list[DiscoveredJob]: full job data (API monitors like greenhouse, lever)
- set[str]: URL set only (page monitors like sitemap, dom)
- tuple[set[str], str | None]: URL set + discovered metadata (sitemap)
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import structlog

if TYPE_CHECKING:
    import httpx

    from src.core.monitor import MonitorResult

    SaveRawClient = httpx.AsyncClient
else:
    MonitorResult = Any
    SaveRawClient = Any

log = structlog.get_logger()


# Explicit identities are deliberately not arbitrary text.  A provider lane
# must namespace the tenant and the provider-owned identifier, for example
# ``smartrecruiters:nagarro:7439990002``.  URL-only monitors never need to
# manufacture this value: the board processor derives their identity from the
# canonical outbound URL exactly as it did before #8034.
SOURCE_IDENTITY_RE = re.compile(
    r"^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:"
    r"[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}$"
)


def validate_explicit_source_identity(value: str) -> str:
    """Validate one bounded, namespaced provider identity.

    Keeping query strings, fragments, whitespace, and Unicode out of this
    internal key makes log/SQL boundaries unambiguous.  The outbound ``url``
    remains the place for a complete user-resolving provider URL.
    """
    if not isinstance(value, str) or SOURCE_IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(
            "source_identity must be provider:tenant:id using bounded ASCII identity tokens"
        )
    return value


@dataclass(slots=True)
class DiscoveredJob:
    """A job discovered by a monitor.

    URL-only monitors (sitemap) set only ``url``.
    Rich monitors (greenhouse, lever) populate all available fields.

    ``description`` is an HTML fragment preserving the original document
    structure (headings, paragraphs, lists).  API monitors return HTML
    natively; scrapers must produce HTML as well.
    """

    url: str
    title: str | None = None
    #: HTML fragment preserving the original page structure.
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    #: ISO 639-1 language code (e.g. "en", "de"). Detected or monitor-provided.
    language: str | None = None
    #: All language versions: {"en": {"title": ..., "description": ..., "locations": [...]}, ...}
    localizations: dict | None = None
    #: Optional structured data (skills, responsibilities, qualifications, etc.)
    extras: dict | None = None
    metadata: dict | None = None
    #: Stable provider identity, separate from the user-facing outbound URL.
    #: Kept last to preserve legacy positional construction of DiscoveredJob.
    #: When omitted, the canonical URL remains the identity for compatibility.
    source_identity: str | None = None

    def __post_init__(self):
        if self.source_identity is not None:
            validate_explicit_source_identity(self.source_identity)
        if isinstance(self.base_salary, str):
            from src.core.salary_extract import parse_salary_text

            self.base_salary = parse_salary_text(self.base_salary)


# Discover functions return either set[str] (URL-only), list[DiscoveredJob]
# (rich), tuple[set[str], str | None] (URL-only + metadata, e.g. sitemap), or
# MonitorResult for hybrid/truncated monitors that need to preserve flags.
type DiscoverResult = set[str] | list[DiscoveredJob] | tuple[set[str], str | None] | MonitorResult
DiscoverFunc = Callable[..., Awaitable[DiscoverResult]]
SaveRawFunc = Callable[[Path, str, dict[str, Any], SaveRawClient], Awaitable[None]]

# can_handle: async (url, client) -> dict | None.
# Returns metadata dict (truthy) when the monitor can handle the URL,
# or None when it cannot.
CanHandleFunc = Callable[..., Awaitable[dict | None]]


@dataclass
class MonitorType:
    name: str
    cost: int  # lower = cheaper = tried first
    discover: DiscoverFunc
    can_handle: CanHandleFunc | None = None
    rich: bool = False  # True for API monitors that return full job data
    stream: Callable | None = None  # async generator yielding batches
    save_raw: SaveRawFunc | None = None


_REGISTRY: list[MonitorType] = []
_ALLOW_SLUG_GUESS = contextvars.ContextVar("allow_slug_guess", default=False)


class BoardGoneError(Exception):
    """The upstream ATS returned an explicit board-retirement signal.

    Raised by API monitors (greenhouse/lever/recruitee/ashby) when the
    per-board API endpoint returns 404. Distinct from a generic
    ``HTTPStatusError`` so the board processor can apply the spaced,
    recoverable provider-gone confirmation policy. See issues #2215 and
    #6156.

    Carries the upstream URL and HTTP status for durable forensics.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


_STREAM_BATCH = 200


def _make_chunked_stream(discover_fn: DiscoverFunc):
    """Create a generic streaming wrapper that chunks discover() results.

    A monitor that returned a ``MonitorResult`` directly (e.g. with
    ``truncated=True`` per #3216) is yielded whole rather than reshaped:
    the per-batch flags it carries (``truncated``, ``new_sitemap_url``,
    ``metadata_updates``, ``hybrid``) need to reach the pipeline intact.
    A plain ``list[DiscoveredJob]`` keeps the original 200-item chunking
    so heartbeats fire on large boards.
    """

    async def _chunked_stream(board, client, pw=None):
        result = await discover_fn(board, client, pw=pw)
        # Local import to avoid the circular dep with src.core.monitor.
        from src.core.monitor import MonitorResult as _MR
        from src.core.monitor import _collapse_source_identity_publications

        if isinstance(result, _MR):
            yield result
            return
        if isinstance(result, list):
            # Provider-localized publications must be considered as one
            # complete result before the generic heartbeat chunks are cut.
            # Otherwise matching publications at positions 200/201 become
            # separate jobs and make identity handling depend on API order.
            # URL-only and legacy rich results keep their existing streaming
            # path unchanged.
            if any(job.source_identity is not None for job in result):
                result = _collapse_source_identity_publications(result)
            for i in range(0, len(result), _STREAM_BATCH):
                yield result[i : i + _STREAM_BATCH]
        else:
            yield result

    return _chunked_stream


def register(
    name: str,
    discover: DiscoverFunc,
    cost: int,
    can_handle: CanHandleFunc | None = None,
    *,
    rich: bool = False,
    stream: Callable | None = None,
    save_raw: SaveRawFunc | None = None,
) -> None:
    """Register a monitor type. Registry stays sorted by cost (cheapest first).

    Rich monitors without an explicit stream function automatically get a
    chunked-stream wrapper that yields batches of 200 from the discover()
    result.  This prevents worker-pool timeouts during R2 uploads.
    """
    if stream is None:
        stream = _make_chunked_stream(discover)
    _REGISTRY.append(
        MonitorType(
            name=name,
            cost=cost,
            discover=discover,
            can_handle=can_handle,
            rich=rich,
            stream=stream,
            save_raw=save_raw,
        )
    )
    _REGISTRY.sort(key=lambda m: m.cost)


@contextlib.contextmanager
def slug_guess_mode(enabled: bool):
    """Temporarily control whether monitor can_handle may use slug guessing."""
    token = _ALLOW_SLUG_GUESS.set(enabled)
    try:
        yield
    finally:
        _ALLOW_SLUG_GUESS.reset(token)


def slug_guess_allowed() -> bool:
    """Return whether monitor probes may use slug-based fallback guessing."""
    return bool(_ALLOW_SLUG_GUESS.get())


def api_monitor_types() -> frozenset[str]:
    """Return the set of monitor type names that return rich (full) job data."""
    return frozenset(m.name for m in _REGISTRY if m.rich)


def is_rich_monitor(monitor_type: str, config: dict | None = None) -> bool:
    """Check if a monitor type returns rich data (scraper not needed).

    Statically-rich monitors (greenhouse, lever, etc.) always return True.
    api_sniffer/nextdata are rich when ``fields`` is present; SmartRecruiters
    is rich when exact ``jobId`` locale collapse is configured; dom is partial
    rich when strict static ``rich_rows`` extraction is configured.
    """
    return (
        monitor_type in api_monitor_types()
        or (monitor_type in ("api_sniffer", "nextdata") and bool((config or {}).get("fields")))
        or (
            monitor_type == "smartrecruiters"
            and bool((config or {}).get("canonical_job_id_url_template"))
        )
        or (
            monitor_type == "smartrecruiters"
            and (config or {}).get("canonical_identity") in {"job-v1", "job-location-v1"}
        )
        or (monitor_type == "dom" and bool((config or {}).get("rich_rows")))
    )


def all_monitor_types() -> frozenset[str]:
    """Return the set of all registered monitor type names."""
    return frozenset(m.name for m in _REGISTRY)


def monitor_needs_browser(name: str, config: dict | None = None) -> bool:
    """Return True if the monitor requires a Playwright browser.

    accenture, brassring, darwinbox, and dayforce always need a browser
    (public session replay via Playwright).
    api_sniffer needs a browser when ``browser`` is set in config or when
    no ``api_url`` is configured (auto-discover mode).  dom always benefits
    from a browser but falls back to static HTML.
    """
    if name in {"accenture", "brassring", "candidatus", "darwinbox", "dayforce", "njoyn"}:
        return True
    if name == "api_sniffer":
        cfg = config or {}
        if not cfg.get("api_url"):
            return True
        return bool(cfg.get("browser"))
    if name in ("dom", "inline"):
        return bool((config or {}).get("render"))
    if name == "nextdata":
        cfg = config or {}
        return bool(
            cfg.get("render")
            or cfg.get("actions")
            or cfg.get("source") == "browser"
            or cfg.get("browser_expression")
        )
    return False


def get_discoverer(name: str) -> DiscoverFunc:
    """Look up a discover function by monitor type name."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            return monitor.discover
    available = [m.name for m in _REGISTRY]
    raise ValueError(f"Unknown monitor type: {name!r}. Available: {available}")


def get_stream_fn(name: str) -> Callable | None:
    """Look up a stream function by monitor type name. Returns None if not streaming."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            return monitor.stream
    return None


def get_save_raw(name: str) -> SaveRawFunc | None:
    """Look up the raw artifact saver for a monitor type, if one exists."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            return monitor.save_raw
    return None


def get_can_handle(name: str) -> CanHandleFunc:
    """Look up a can_handle function by monitor type name."""
    for monitor in _REGISTRY:
        if monitor.name == name:
            if monitor.can_handle is None:
                raise ValueError(f"Monitor {name!r} has no can_handle probe")
            return monitor.can_handle
    available = [m.name for m in _REGISTRY]
    raise ValueError(f"Unknown monitor type: {name!r}. Available: {available}")


async def detect_monitor_type(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
) -> tuple[str, dict] | None:
    """Determine the best monitor type for a URL, trying cheapest first.

    Returns (monitor_name, metadata) or None if no monitor can handle the URL.
    """
    for monitor in _REGISTRY:
        if monitor.can_handle is None:
            continue
        result = await monitor.can_handle(url, client, pw=pw)
        if result is not None:
            return monitor.name, result
    return None


def slugs_from_url(url: str) -> list[str]:
    """Derive candidate ATS board slugs from a URL.

    Extracts the second-level domain label, e.g.
    "https://www.isomorphiclabs.com/job-openings" -> ["isomorphiclabs"]
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    # LinkedIn is the platform host, not the hiring company's slug. Guessing
    # ``linkedin`` here hits LinkedIn's own Greenhouse/Lever test boards.
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return []
    parts = host.split(".")
    if len(parts) >= 2:
        return [parts[-2]]
    return [parts[0]] if parts else []


async def fetch_page_text(
    url: str,
    client: httpx.AsyncClient,
    max_chars: int = 500_000,
    *,
    board_gone_statuses: frozenset[int] = frozenset(),
) -> str | None:
    """Fetch a page and return its text content (capped), or None on error.

    ``board_gone_statuses`` lets a provider wrapper turn an explicit first-page
    retirement response into the crawler's recoverable board-gone workflow.

    TDM-Reservation respect (#2842). Lenient wrapper but still honors the
    W3C opt-out signal — :class:`TDMReservedError` is **not** swallowed by
    the broad ``except Exception``; it propagates so the caller (typically
    a discovery probe path) can surface the publisher policy decision
    rather than silently returning None and treating the board as
    fetch-failed.
    """
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code in board_gone_statuses:
            raise BoardGoneError(
                f"Board page returned HTTP {resp.status_code}",
                url=str(resp.url),
                status_code=resp.status_code,
            )
        if resp.status_code != 200:
            return None
        text = resp.text[:max_chars]
        _tdm_check(resp, body_excerpt=text)
        return text
    except TDMReservedError:
        raise
    except BoardGoneError:
        raise
    except Exception:
        log.debug("monitors.fetch_page_text_failed", url=url, exc_info=True)
        return None


def _build_comment(name: str, metadata: dict) -> str:
    """Build a human-readable comment from probe metadata."""
    if name == "amazon":
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Amazon Jobs API \u2014 {jobs} jobs"
        return "Amazon Jobs API"
    if name == "bite":
        key = metadata.get("key", "?")
        customer = metadata.get("customer")
        jobs = metadata.get("jobs")
        label = f"customer: {customer}" if customer else f"key: {key[:12]}..."
        if jobs is not None:
            return f"BITE API \u2014 {label}, {jobs} jobs"
        return f"BITE API \u2014 {label}"
    if name == "brassring":
        partner_id = metadata.get("partner_id", "?")
        site_id = metadata.get("site_id", "?")
        return f"BrassRing TGnewUI \u2014 partner: {partner_id}, site: {site_id}"
    if name == "breezy":
        portal_url = metadata.get("portal_url", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Breezy \u2014 {portal_url}, {jobs} jobs"
        return f"Breezy \u2014 {portal_url}"
    if name == "beehire":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Beehire public campaigns \u2014 slug: {slug}, {jobs} jobs"
        return f"Beehire public campaigns \u2014 slug: {slug}"
    if name == "bamboohr":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"BambooHR API \u2014 tenant: {tenant}, {jobs} jobs"
        return f"BambooHR API \u2014 tenant: {tenant}"
    if name == "adp":
        cid = metadata.get("cid", "?")
        cc_id = metadata.get("cc_id", "?")
        jobs = metadata.get("jobs")
        label = f"cid: {cid}, career center: {cc_id}"
        if jobs is not None:
            return f"ADP Workforce Now API \u2014 {label}, {jobs} jobs"
        return f"ADP Workforce Now API \u2014 {label}"
    if name == "paycom":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Paycom API \u2014 portal: {token}, {jobs} jobs"
        return f"Paycom API \u2014 portal: {token}"
    if name == "jazzhr":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"JazzHR static listing \u2014 tenant: {tenant}, {jobs} jobs"
        return f"JazzHR static listing \u2014 tenant: {tenant}"
    if name == "jobbank104":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"104 Job Bank company listing \u2014 token: {token}, {jobs} jobs"
        return f"104 Job Bank company listing \u2014 token: {token}"
    if name == "computrabajo":
        company_id = metadata.get("company_id", "?")
        jobs = metadata.get("jobs")
        label = f"Computrabajo employer profile \u2014 company: {company_id}"
        return f"{label}, {jobs} jobs" if jobs is not None else label
    if name == "papa_johns":
        jobs = metadata.get("jobs")
        pages = metadata.get("pages")
        if jobs is not None and pages is not None:
            return f"Papa Johns careers — {jobs} jobs across {pages} pages (proxy required)"
        return "Papa Johns careers — proxy required"
    if name == "cvwarehouse":
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"CVWarehouse hosted board \u2014 {jobs} jobs"
        return "CVWarehouse hosted board"
    if name == "jobstreet":
        company_id = metadata.get("company_id", "?")
        jobs = metadata.get("jobs")
        label = f"JobStreet employer profile \u2014 company: {company_id}"
        return f"{label}, {jobs} jobs" if jobs is not None else label
    if name == "infoniqa":
        employer = metadata.get("employer_name", "?")
        jobs = metadata.get("jobs")
        label = f"Infoniqa jobexchange \u2014 employer: {employer}"
        return f"{label}, {jobs} jobs" if jobs is not None else label
    if name == "jobvite":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Jobvite static listing \u2014 tenant: {tenant}, {jobs} jobs"
        return f"Jobvite static listing \u2014 tenant: {tenant}"
    if name == "icims":
        host = metadata.get("host", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"iCIMS static listing \u2014 host: {host}, {jobs} jobs"
        return f"iCIMS static listing \u2014 host: {host}"
    if name == "herp":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"HERP static listing \u2014 slug: {slug}, {jobs} jobs"
        return f"HERP static listing \u2014 slug: {slug}"
    if name == "hrmos":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"HRMOS static listing \u2014 tenant: {tenant}, {jobs} jobs"
        return f"HRMOS static listing \u2014 tenant: {tenant}"
    if name == "gupy":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Gupy NextData listing \u2014 tenant: {tenant}, {jobs} jobs"
        return f"Gupy NextData listing \u2014 tenant: {tenant}"
    if name == "earcu":
        feed_url = metadata.get("feed_url", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"eArcu live-vacancy feed \u2014 {jobs} jobs at {feed_url}"
        return f"eArcu live-vacancy feed \u2014 {feed_url}"
    if name == "cornerstone":
        tenant = metadata.get("tenant", "?")
        site_id = metadata.get("site_id", "?")
        jobs = metadata.get("jobs")
        label = f"tenant: {tenant}, site: {site_id}"
        if jobs is not None:
            return f"Cornerstone API \u2014 {label}, {jobs} jobs"
        return f"Cornerstone API \u2014 {label}"
    if name == "dayforce":
        tenant = metadata.get("tenant", "?")
        portal = metadata.get("portal", "?")
        return f"Dayforce API \u2014 tenant: {tenant}, portal: {portal}"
    if name == "darwinbox":
        host = metadata.get("host", "?")
        company_id = metadata.get("company_id", "main")
        return f"Darwinbox API \u2014 host: {host}, company: {company_id}"
    if name == "avature":
        listing_url = metadata.get("listing_url", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Avature static listing \u2014 {jobs} jobs at {listing_url}"
        return f"Avature static listing \u2014 {listing_url}"
    if name == "pageup":
        listing_url = metadata.get("listing_url", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"PageUp static listing \u2014 {jobs} jobs at {listing_url}"
        return f"PageUp static listing \u2014 {listing_url}"
    if name == "ukg":
        tenant = metadata.get("tenant", "?")
        board_id = metadata.get("board_id", "?")
        jobs = metadata.get("jobs")
        label = f"tenant: {tenant}, board: {board_id}"
        if jobs is not None:
            return f"UKG Pro API \u2014 {label}, {jobs} jobs"
        return f"UKG Pro API \u2014 {label}"
    if name == "comeet":
        jobs = metadata.get("jobs")
        company_id = metadata.get("company_id")
        if company_id:
            label = f"API company: {company_id}"
        else:
            company = metadata.get("company", "?")
            board_id = metadata.get("board_id", "?")
            label = f"embedded data: {company}/{board_id}"
        if jobs is not None:
            return f"Comeet \u2014 {label}, {jobs} jobs"
        return f"Comeet \u2014 {label}"
    if name == "eightfold":
        sitemap_url = metadata.get("sitemap_url", "?")
        urls = metadata.get("urls")
        if urls is not None:
            return f"Eightfold AI \u2014 {urls} jobs at {sitemap_url}"
        return f"Eightfold AI \u2014 {sitemap_url}"
    if name == "ashby":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Ashby API \u2014 token: {token}, {jobs} jobs"
        return f"Ashby API \u2014 token: {token}"
    if name == "gem":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Gem API \u2014 slug: {token}, {jobs} jobs"
        return f"Gem API \u2014 slug: {token}"
    if name == "manatal":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Manatal API \u2014 slug: {slug}, {jobs} jobs"
        return f"Manatal API \u2014 slug: {slug}"
    if name == "seamlesshiring":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"SeamlessHiring API \u2014 tenant: {tenant}, {jobs} jobs"
        return f"SeamlessHiring API \u2014 tenant: {tenant}"
    if name == "inploi":
        segment = metadata.get("segment_id", "?")
        jobs = metadata.get("jobs")
        label = f"Inploi API \u2014 segment: {segment}"
        return f"{label}, {jobs} jobs" if jobs is not None else label
    if name == "intervieweb":
        jobs = metadata.get("jobs")
        pages = metadata.get("pages")
        label = "Intervieweb POST-paginated listing"
        if jobs is not None and pages is not None:
            return f"{label} \u2014 {jobs} jobs across {pages} pages"
        return f"{label} \u2014 {jobs} jobs" if jobs is not None else label
    if name == "typify":
        jobs = metadata.get("jobs")
        label = "Typify partitioned vacancy API"
        return f"{label} \u2014 {jobs} jobs" if jobs is not None else label
    if name == "greenhouse":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Greenhouse API \u2014 token: {token}, {jobs} jobs"
        return f"Greenhouse API \u2014 token: {token}"
    if name == "lever":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Lever API \u2014 token: {token}, {jobs} jobs"
        return f"Lever API \u2014 token: {token}"
    if name == "join":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"JOIN \u2014 slug: {slug}, {jobs} jobs"
        return f"JOIN \u2014 slug: {slug}"
    if name == "linkedin":
        company_id = metadata.get("company_id", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"LinkedIn guest jobs \u2014 company: {company_id}, {jobs} jobs"
        return f"LinkedIn guest jobs \u2014 company: {company_id}"
    if name == "headhunter":
        employer_id = metadata.get("employer_id", "?")
        jobs = metadata.get("jobs")
        label = f"HeadHunter API \u2014 employer: {employer_id}"
        return f"{label}, {jobs} jobs" if jobs is not None else f"{label} (proxy required)"
    if name == "nextdata":
        path = metadata.get("path", "?")
        count = metadata.get("count")
        render = " (render)" if metadata.get("render") else ""
        if count is not None:
            return f"__NEXT_DATA__ \u2014 {count} items at {path}{render}"
        return f"__NEXT_DATA__ \u2014 {path}{render}"
    if name == "sitemap":
        sitemap_url = metadata.get("sitemap_url", "?")
        urls = metadata.get("urls")
        if urls is not None:
            return f"Sitemap \u2014 {urls} URLs at {sitemap_url}"
        return f"Sitemap \u2014 {sitemap_url}"
    if name == "talentbrew":
        jobs = metadata.get("jobs")
        pages = metadata.get("pages")
        if jobs is not None and pages is not None:
            return f"TalentBrew/Radancy \u2014 {jobs} jobs across {pages} pages"
        if jobs is not None:
            return f"TalentBrew/Radancy \u2014 {jobs} jobs"
        urls = metadata.get("urls")
        if urls is not None:
            return f"TalentBrew/Radancy \u2014 {urls} job links found"
        return "TalentBrew/Radancy"
    if name == "talemetry":
        jobs = metadata.get("jobs")
        pages = metadata.get("pages")
        if jobs is not None and pages is not None:
            return f"Talemetry/Jobvite Career Sites \u2014 {jobs} jobs across {pages} pages"
        urls = metadata.get("urls")
        if urls is not None:
            return f"Talemetry/Jobvite Career Sites \u2014 {urls} job links found"
        return "Talemetry/Jobvite Career Sites"
    if name == "practicematch":
        return "PracticeMatch employer board (proxy-routed form pagination)"
    if name == "dom":
        urls = metadata.get("urls")
        if urls is not None:
            return f"DOM \u2014 {urls} job links found (static)"
        return "DOM \u2014 link extraction"
    if name == "dvinci":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"d.vinci API \u2014 slug: {slug}, {jobs} jobs"
        return f"d.vinci API \u2014 slug: {slug}"
    if name == "smartrecruiters":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"SmartRecruiters API \u2014 token: {token}, {jobs} jobs"
        return f"SmartRecruiters API \u2014 token: {token}"
    if name == "softgarden":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Softgarden \u2014 slug: {slug}, {jobs} jobs"
        return f"Softgarden \u2014 slug: {slug}"
    if name == "umantis":
        cname = metadata.get("cname")
        cid = metadata.get("customer_id", "?")
        region = metadata.get("region", "")
        jobs = metadata.get("jobs")
        label = f"CNAME: {cname}" if cname else f"ID: {cid}" + (f" ({region})" if region else "")
        if jobs is not None:
            return f"Umantis \u2014 {label}, {jobs} jobs"
        return f"Umantis \u2014 {label}"
    if name == "traffit":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"TRAFFIT API \u2014 slug: {slug}, {jobs} jobs"
        return f"TRAFFIT API \u2014 slug: {slug}"
    if name == "almacareer":
        slug = metadata.get("slug", "?")
        country = (metadata.get("country") or "?").upper()
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"AlmaCareer (Capybara) \u2014 {slug} [{country}], {jobs} jobs"
        return f"AlmaCareer (Capybara) \u2014 {slug} [{country}]"
    if name == "recruitee":
        slug = metadata.get("slug", "?")
        api_base = metadata.get("api_base", "")
        jobs = metadata.get("jobs")
        label = slug if slug != "?" else api_base
        if jobs is not None:
            return f"Recruitee API \u2014 {label}, {jobs} jobs"
        return f"Recruitee API \u2014 {label}"
    if name == "recruiterbox":
        tenant = metadata.get("tenant", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Recruiterbox / Trakstar Hire \u2014 tenant: {tenant}, {jobs} jobs"
        return f"Recruiterbox / Trakstar Hire \u2014 tenant: {tenant}"
    if name == "keka":
        tenant = metadata.get("tenant", "?")
        portal = metadata.get("portal", "default")
        jobs = metadata.get("jobs")
        label = f"tenant: {tenant}, portal: {portal}"
        if jobs is not None:
            return f"Keka API \u2014 {label}, {jobs} jobs"
        return f"Keka API \u2014 {label}"
    if name == "taleo":
        org = metadata.get("org", "?")
        cws = metadata.get("cws", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Taleo Business Edition \u2014 {org}/cws-{cws}, {jobs} jobs"
        return f"Taleo Business Edition \u2014 {org}/cws-{cws}"
    if name == "recruiter_co_kr":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Recruiter.co.kr \u2014 slug: {slug}, {jobs} jobs"
        return f"Recruiter.co.kr \u2014 slug: {slug}"
    if name == "hirehive":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"HireHive API \u2014 slug: {slug}, {jobs} jobs"
        return f"HireHive API \u2014 slug: {slug}"
    if name == "hireology":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Hireology API \u2014 slug: {slug}, {jobs} jobs"
        return f"Hireology API \u2014 slug: {slug}"
    if name == "turbohire":
        org_id = metadata.get("org_id", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"TurboHire API \u2014 organization: {org_id}, {jobs} jobs"
        return f"TurboHire API \u2014 organization: {org_id}"
    if name == "rippling":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Rippling API \u2014 slug: {slug}, {jobs} jobs"
        return f"Rippling API \u2014 slug: {slug}"
    if name == "workable":
        token = metadata.get("token", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Workable API \u2014 token: {token}, {jobs} jobs"
        return f"Workable API \u2014 token: {token}"
    if name == "workday":
        company = metadata.get("company", "?")
        site = metadata.get("site", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Workday API \u2014 {company}/{site}, {jobs} jobs"
        return f"Workday API \u2014 {company}/{site}"
    if name == "pinpoint":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Pinpoint API \u2014 slug: {slug}, {jobs} jobs"
        return f"Pinpoint API \u2014 slug: {slug}"
    if name == "personio":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Personio XML \u2014 slug: {slug}, {jobs} jobs"
        return f"Personio XML \u2014 slug: {slug}"
    if name == "paylocity":
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Paylocity embedded data \u2014 {jobs} jobs"
        return "Paylocity embedded data"
    if name == "phenom":
        sitemap_url = metadata.get("sitemap_url", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Phenom \u2014 {jobs} jobs at {sitemap_url}"
        return f"Phenom \u2014 {sitemap_url}"
    if name == "jobylon":
        group = metadata.get("company_group_id")
        company = metadata.get("company_id")
        label = f"company-group: {group}" if group else f"company: {company}" if company else "?"
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Jobylon embed \u2014 {label}, {jobs} jobs"
        return f"Jobylon embed \u2014 {label}"
    if name == "jarvi":
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Jarvi public API \u2014 {jobs} jobs"
        return "Jarvi public API"
    if name == "rss":
        preset = metadata.get("preset", "generic")
        variant = metadata.get("variant")
        if preset == "successfactors" and variant == "legacy":
            company = metadata.get("company", "?")
            host = metadata.get("host", "?")
            jobs = metadata.get("jobs")
            label = f"SuccessFactors legacy DWR \u2014 company: {company} @ {host}"
            return f"{label}, {jobs} jobs" if jobs is not None else label
        feed_url = metadata.get("feed_url", "?")
        jobs = metadata.get("jobs")
        label = {
            "successfactors": "SuccessFactors RSS",
            "teamtailor": "Teamtailor RSS",
            "wp_job_manager": "WP Job Manager RSS",
        }.get(preset, f"RSS ({preset})")
        count_str = f"{jobs}" if jobs is not None else ""
        # For paginated presets, first-page count may be approximate
        if preset in {"teamtailor", "wp_job_manager"} and jobs is not None:
            from src.core.monitors.rss import _PRESETS

            selected = _PRESETS.get(preset)
            if selected and jobs >= selected.page_size:
                count_str = f"{jobs}+"
        if count_str:
            return f"{label} \u2014 {feed_url}, {count_str} jobs"
        return f"{label} \u2014 {feed_url}"
    if name == "ycombinator":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        warn = " (last resort — prefer a dedicated ATS if available)"
        if jobs is not None:
            return f"YCombinator — slug: {slug}, {jobs} jobs{warn}"
        return f"YCombinator — slug: {slug}{warn}"
    if name == "api_sniffer":
        items = metadata.get("items")
        total = metadata.get("total")
        score = metadata.get("score")
        api_url = metadata.get("api_url", "?")
        # Truncate API URL for display
        if len(api_url) > 80:
            api_url = api_url[:77] + "..."
        parts = []
        if items is not None:
            parts.append(f"{items} items")
        if total is not None:
            parts.append(f"total: {total}")
        if score is not None:
            parts.append(f"score: {score}")
        detail = ", ".join(parts) if parts else ""
        if detail:
            return f"API sniffer \u2014 {detail} at {api_url}"
        return f"API sniffer \u2014 {api_url}"
    if name == "welcometothejungle":
        slug = metadata.get("slug", "?")
        jobs = metadata.get("jobs")
        if jobs is not None:
            return f"Welcome to the Jungle \u2014 {slug}, {jobs} jobs"
        return f"Welcome to the Jungle \u2014 {slug}"
    if name == "johdi":
        jobs = metadata.get("jobs")
        locale = metadata.get("locale", "?")
        if jobs is not None:
            return f"Johdi Suite \u2014 {jobs} jobs, locale: {locale}"
        return f"Johdi Suite \u2014 locale: {locale}"
    return str(metadata)


async def probe_all_monitors(
    url: str,
    client: httpx.AsyncClient,
    timeout: float = 60.0,
    pw=None,
) -> list[tuple[str, dict | None, str]]:
    """Run can_handle for every monitor type in parallel.

    Returns [(name, metadata_or_none, comment), ...] sorted by registry order.

    When *pw* is provided, it is forwarded to monitors that use Playwright.
    """

    async def _probe_one(monitor: MonitorType) -> tuple[str, dict | None, str]:
        if monitor.can_handle is None:
            return monitor.name, None, "No probe available"
        try:
            result = await asyncio.wait_for(
                monitor.can_handle(url, client, pw=pw),
                timeout=timeout,
            )
            if result is not None:
                return monitor.name, result, _build_comment(monitor.name, result)
            return monitor.name, None, "Not detected"
        except TimeoutError:
            return monitor.name, None, f"Timeout ({timeout:.0f}s)"
        except Exception as exc:
            return monitor.name, None, f"Error: {exc}"

    tasks = [_probe_one(m) for m in _REGISTRY if m.name not in _PROBE_SKIP]
    return list(await asyncio.gather(*tasks))


# Company-specific monitors excluded from generic probing.
_PROBE_SKIP: frozenset[str] = frozenset({"amazon", "accenture", "unifr", "unisante"})


# Import modules to trigger registration
from src.core.monitors import (  # noqa: E402
    accenture,  # noqa: F401
    adp,  # noqa: F401
    almacareer,  # noqa: F401
    amazon,  # noqa: F401
    api_sniffer,  # noqa: F401
    ashby,  # noqa: F401
    avature,  # noqa: F401
    bamboohr,  # noqa: F401
    beehire,  # noqa: F401
    beisen,  # noqa: F401
    bite,  # noqa: F401
    brassring,  # noqa: F401
    breezy,  # noqa: F401
    candidatus,  # noqa: F401
    cnstaff,  # noqa: F401
    comeet,  # noqa: F401
    computrabajo,  # noqa: F401
    cornerstone,  # noqa: F401
    curately,  # noqa: F401
    cvwarehouse,  # noqa: F401
    darwinbox,  # noqa: F401
    dayforce,  # noqa: F401
    deel,  # noqa: F401
    dom,  # noqa: F401
    dvinci,  # noqa: F401
    earcu,  # noqa: F401
    eightfold,  # noqa: F401
    gem,  # noqa: F401
    greenhouse,  # noqa: F401
    gupy,  # noqa: F401
    headhunter,  # noqa: F401
    herp,  # noqa: F401
    hibob,  # noqa: F401
    hirehive,  # noqa: F401
    hireology,  # noqa: F401
    hrmos,  # noqa: F401
    icims,  # noqa: F401
    infoniqa,  # noqa: F401
    infor,  # noqa: F401
    inline,  # noqa: F401
    inploi,  # noqa: F401
    intervieweb,  # noqa: F401
    jarvi,  # noqa: F401
    jazzhr,  # noqa: F401
    jobbank104,  # noqa: F401
    jobs_ch,  # noqa: F401
    jobstreet,  # noqa: F401
    jobvite,  # noqa: F401
    jobylon,  # noqa: F401
    johdi,  # noqa: F401
    join,  # noqa: F401
    keka,  # noqa: F401
    kipt,  # noqa: F401
    lever,  # noqa: F401
    linkedin,  # noqa: F401
    manatal,  # noqa: F401
    mokahr,  # noqa: F401
    nextdata,  # noqa: F401
    njoyn,  # noqa: F401
    notion,  # noqa: F401
    oracle_hcm,  # noqa: F401
    pageup,  # noqa: F401
    papa_johns,  # noqa: F401
    paycom,  # noqa: F401
    paylocity,  # noqa: F401
    personio,  # noqa: F401
    phenom,  # noqa: F401
    pinpoint,  # noqa: F401
    practicematch,  # noqa: F401
    prospective,  # noqa: F401
    recruitee,  # noqa: F401
    recruiter_co_kr,  # noqa: F401
    recruiterbox,  # noqa: F401
    rippling,  # noqa: F401
    rss,  # noqa: F401
    seamlesshiring,  # noqa: F401
    sitemap,  # noqa: F401
    smartrecruiters,  # noqa: F401
    softgarden,  # noqa: F401
    talemetry,  # noqa: F401
    talentbrew,  # noqa: F401
    taleo,  # noqa: F401
    traffit,  # noqa: F401
    turbohire,  # noqa: F401
    typify,  # noqa: F401
    ukg,  # noqa: F401
    umantis,  # noqa: F401
    unifr,  # noqa: F401
    unisante,  # noqa: F401
    welcometothejungle,  # noqa: F401
    workable,  # noqa: F401
    workday,  # noqa: F401
    ycombinator,  # noqa: F401
)
