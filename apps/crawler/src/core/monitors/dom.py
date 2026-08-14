"""DOM-based job URL discovery monitor.

Extracts job links from a career page's HTML.

By default (``render: false``), fetches via static HTTP and parses ``<a>``
tags.  Set ``render: true`` to render with Playwright for JS-heavy SPAs.

Requires playwright when ``render`` is true:
``uv run playwright install chromium``
"""

from __future__ import annotations

import asyncio
import codecs
import random
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import structlog
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from src.core.monitors import register
from src.core.monitors.raw import save_text_response
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_PAGINATION_PAGES = 10_000

# Browser-pagination fetch budget. Playwright fetches are slower than
# httpx (the JS engine + page context add tens of ms), and the page is
# shared per-board — every retry holds the worker's browser slot. Keep
# this smaller than ``fetch_with_retry``'s default of 3.
_BROWSER_FETCH_RETRIES = 2
_BROWSER_FETCH_BASE_DELAY = 0.5
_BROWSER_FETCH_MAX_CHARS = 500_000

# JS executed inside the Playwright page. Returns ``{status, headers, text}``
# so HTTP-level errors (which ``fetch`` doesn't reject on in JS) are
# observable on the Python side. ``r.text()`` rejects on a body decode
# error; that surfaces as a ``page.evaluate`` exception.
#
# ``headers`` is materialised into a plain object (``Headers`` is iterable
# but not directly serialisable across the page-evaluate bridge) with
# keys lower-cased so the Python TDM-Reservation check (#2842) can do a
# uniform case-insensitive lookup without re-walking the dict.
_BROWSER_FETCH_JS = (
    "async (url) => { "
    "const r = await fetch(url); "
    "const headers = {}; "
    "for (const [k, v] of r.headers.entries()) { headers[k.toLowerCase()] = v; } "
    "return { status: r.status, headers: headers, text: await r.text() }; "
    "}"
)

_JOB_KEYWORDS = frozenset(
    {
        "job",
        "career",
        "position",
        "posting",
        "opening",
        "role",
        "vacancy",
        "stellenangebot",
        "advertisement_display",
    }
)

_LINKEDIN_JOB_FILTER = r"linkedin\.com/jobs/view/"
_LINKEDIN_JOB_TRANSFORM = {
    "find": r".*(?:-|/)(\d+)(?:/?(?:\?.*)?)$",
    "replace": r"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/\1",
}

_KONTACT_MARKER = "kontactintelligence.com"
_KONTACT_URL_FILTER = r"/Physician_Job/Details/"

_TALENTSOFT_MARKERS = ("ts-offer-list-item", "ts-search-engine-form__rss-cta")
_TALENTSOFT_PATH_FILTER = r"/(?:job/job|offre-de-emploi/emploi)-[^/?#]+_\d+\.aspx(?:[?#]|$)"

_JPOSTING_HOST_SUFFIX = ".jposting.net"
_JPOSTING_JOB_FILTER = r"[?&]job_code=[^&#]+"

_VAGAS_HOST = "trabalheconosco.vagas.com.br"
_VAGAS_TENANT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

_REXX_PROVIDER_HOSTS = frozenset({"rexx-systems.com", "www.rexx-systems.com"})
_REXX_JOB_PATH_FILTER = r"/(?:[^/?#]+/)*(?:[^/?#]+-j\d+\.html|job-offer\.html\?yid=\d+)(?:[&#].*)?$"


def _rexx_url_filter(url: str) -> str | None:
    """Build a same-origin detail filter for a Rexx listing URL."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return rf"^{re.escape(origin)}{_REXX_JOB_PATH_FILTER}"


def _vagas_probe_config(url: str) -> dict | None:
    """Return the proxy-routed preset for Vagas.com employer boards.

    Vagas.com rejects crawler-host geographies with Cloudflare error 1005,
    including before a browser context can be established.  The public
    employer route itself is a stable provider identifier, so recognize it
    before the generic probe fetch and route both listing pages and detail
    pages through the configured production proxy.

    Tenant home pages show only featured openings. When one is supplied as
    the board URL, pagination starts at page 1 of the canonical
    ``/oportunidades`` listing so discovery does not silently miss jobs.
    """

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != _VAGAS_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if not parts or len(parts) > 2:
        return None
    tenant = parts[0].casefold()
    if not _VAGAS_TENANT_RE.fullmatch(tenant):
        return None
    if len(parts) == 2 and parts[1].casefold() != "oportunidades":
        return None

    listing_url = f"https://{_VAGAS_HOST}/{tenant}/oportunidades"
    pagination: dict = {
        "param_name": "pagina",
        "max_pages": 1_000,
    }
    if len(parts) == 1:
        pagination.update(
            {
                "url_template": f"{listing_url}?pagina={{page}}",
                "start": 0,
            }
        )

    return {
        "vagas_tenant": tenant,
        "proxy": True,
        "url_filter": (
            rf"(?i:^https://{re.escape(_VAGAS_HOST)}/{re.escape(tenant)}/"
            r"oportunidade/[^/?#]+/\d+/?(?:[?#].*)?$)"
        ),
        "pagination": pagination,
    }


def _rexx_probe_config(html: str, url: str) -> dict | None:
    """Return a clean DOM preset for Rexx Systems hosted talent portals.

    Rexx boards use human-readable detail URLs ending in ``-j<ID>.html``.
    Their navigation also contains a prominent ``jobalert-<lang>.html`` link,
    which the generic job-keyword heuristic mistakes for a posting. Detecting
    the provider marker and applying its stable detail pattern keeps probes
    from selecting that alert page while preserving localized job URLs.
    """

    parser = _LinkExtractor()
    parser.feed(html)
    if not any(
        (urlparse(urljoin(url, href)).hostname or "").casefold() in _REXX_PROVIDER_HOSTS
        for href in parser.hrefs
    ):
        return None

    url_filter = _rexx_url_filter(url)
    if url_filter is None:
        return None
    matcher = _build_url_matcher(url_filter)
    urls = _extract_links_static(html, url, matcher)
    return {
        "urls": len(urls),
        "url_filter": url_filter,
    }


_TALENTLINK_HOST_SUFFIX = ".tal.net"
_TALENTLINK_BOARD_PATH = re.compile(
    r"/candidate/jobboard/vacancy/\d+(?:/adv)?/?$",
)
_TALENTLINK_EMPTY_MARKER = re.compile(
    r"\bid=[\"']no_results_message[\"']",
    re.IGNORECASE,
)


def _talentlink_url_filter(url: str) -> str | None:
    """Build a same-origin opportunity filter for a TalentLink board."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return rf"^{re.escape(origin)}/[^?#]*/opp/[^?#]+(?:[?#].*)?$"


def _talentlink_probe_config(html: str, url: str) -> dict | None:
    """Return a stable DOM preset for Oleeo/TalentLink vacancy boards.

    TalentLink injects a per-render ``xf-<token>`` path segment and its
    generic link heuristic therefore sees the board switcher, talent bank,
    and the listing page itself as vacancies. Real opportunity links have a
    stable ``/opp/`` segment. Empty boards render the same first-party page
    with ``#no_results_message``, so the provider and route identify a
    healthy zero-job board without relying on noisy link counts.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    is_talentlink_host = host == "tal.net" or host.endswith(_TALENTLINK_HOST_SUFFIX)
    if not is_talentlink_host or not _TALENTLINK_BOARD_PATH.search(parsed.path):
        return None

    # Provider markers prevent an unrelated page on the shared host from
    # being accepted solely because its path resembles a vacancy board.
    if "WCN.global_config" not in html or "candidate/jobboard/vacancy/" not in html:
        return None

    url_filter = _talentlink_url_filter(url)
    if url_filter is None:
        return None
    matcher = _build_url_matcher(url_filter)
    urls = _extract_links_static(html, url, matcher)
    if not urls and not _TALENTLINK_EMPTY_MARKER.search(html):
        # A provider shell without either opportunities or the explicit empty
        # marker may be a partial/error response. Let the generic probe treat
        # it conservatively instead of blessing a destructive empty cycle.
        return None
    return {
        "urls": len(urls),
        "url_filter": url_filter,
    }


def _jposting_probe_config(html: str, url: str) -> dict | None:
    """Return a stable DOM preset for Japan Job Posting listing pages.

    JPosting uses query-string detail links and legacy EUC-JP HTML. Empty
    boards contain only a ``#pagetop`` self-link, which the generic keyword
    heuristic previously misclassified as one live job. The first-party host
    and route are sufficient provider identity, and returning ``urls=0`` is
    intentional: JPosting renders an explicit authoritative empty listing.
    """

    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if not host.endswith(_JPOSTING_HOST_SUFFIX) or not parsed.path.endswith("/u/job.phtml"):
        return None
    if not html.strip():
        return None
    matcher = _build_url_matcher(_JPOSTING_JOB_FILTER)
    urls = _extract_links_static(html, url, matcher)
    return {
        "urls": len(urls),
        "url_filter": _JPOSTING_JOB_FILTER,
        "encoding": "euc_jp",
    }


def _kontact_probe_config(html: str, url: str) -> dict | None:
    """Return the complete DOM config for a KontactIntelligence board.

    These physician boards expose server-rendered links and use a stable
    ``?pg=N`` contract, so the regular HTTP pagination path is sufficient.
    Keeping the provider on that path avoids holding a browser worker while
    walking what can be dozens of otherwise static result pages.
    """

    if _KONTACT_MARKER not in html.casefold():
        return None

    matcher = _build_url_matcher(_KONTACT_URL_FILTER)
    urls = _extract_links_static(html, url, matcher)
    return {
        "urls": len(urls),
        "url_filter": _KONTACT_URL_FILTER,
        "pagination": {
            "param_name": "pg",
            "max_pages": 1_000,
        },
    }


def _talentsoft_probe_config(html: str, url: str) -> dict | None:
    """Return the complete static-listing config for Cegid Talentsoft.

    Talentsoft renders fifty authoritative detail links per page and exposes
    the remaining pages through a regular ``page=N`` query parameter. Its RSS
    endpoint is intentionally capped to the newest twenty vacancies, so it
    cannot be used for gone detection on larger boards.
    """

    if not all(marker in html for marker in _TALENTSOFT_MARKERS):
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    url_filter = rf"^https://{re.escape(parsed.netloc)}{_TALENTSOFT_PATH_FILTER}"
    matcher = _build_url_matcher(url_filter)
    urls = _extract_links_static(html, url, matcher)
    if not urls:
        return None
    return {
        "urls": len(urls),
        "url_filter": url_filter,
        "pagination": {
            "param_name": "page",
            "max_pages": 1_000,
        },
    }


def _is_linkedin_job_url(url: str) -> bool:
    """Return whether *url* is a public LinkedIn job-detail link."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return (host == "linkedin.com" or host.endswith(".linkedin.com")) and parsed.path.startswith(
        "/jobs/view/"
    )


def _matches_default_job_url(url: str) -> bool:
    """Match job keywords outside the hostname.

    Career portals commonly live on ``careers.*`` or ``jobs.*`` hosts.  If
    the hostname participates in the fallback keyword check, every link on
    those sites looks job-like, including ``#`` placeholders, login links,
    filters, and application actions.  Restrict the heuristic to the URL
    components controlled by each link while keeping explicit
    ``url_filter`` configurations unchanged.
    """

    parsed = urlparse(url)
    candidate = f"{parsed.path}?{parsed.query}#{parsed.fragment}".casefold()
    return any(keyword in candidate for keyword in _JOB_KEYWORDS)


_SITEGROUND_CHALLENGE_PATHS = (
    "/.well-known/captcha",
    "/.well-known/sgcaptcha",
)

_CLOUDFLARE_CHALLENGE_PATH = "/cdn-cgi/challenge-platform/"
_CLOUDFLARE_CHALLENGE_TEXTS = (
    "enable javascript and cookies",
    "sorry, you have been blocked",
)
_VERIFICATION_CHALLENGE_TEXTS = (
    # Generic interstitial used by vacantescmr.mx. It is served as HTTP 200
    # with no listing links, so treating it as a healthy empty board would
    # tombstone every previously discovered posting.
    "please wait while your request is being verified",
)
_INCAPSULA_INTERSTITIAL_MARKERS = (
    'id="main-iframe"',
    "/_incapsula_resource?cwudnsai=",
)
_RADWARE_CHALLENGE_MARKERS = (
    "validate.perfdrive.com",
    "<title>radware captcha page",
    "botmanager_support@radware.com",
    "captcha.perfdrive.com/captcha-public/",
)


class BotChallengeError(RuntimeError):
    """The board returned an anti-bot challenge instead of job listings.

    Returning an empty URL set for a challenge page records a healthy crawl
    and can tombstone every previously known posting.  Raising keeps the
    cycle on the normal failure/retry path until the configured proxy or
    origin recovers.
    """


def _raise_if_bot_challenge(url: str, html: str) -> None:
    haystack = f"{url}\n{html}".lower()
    is_siteground = any(path in haystack for path in _SITEGROUND_CHALLENGE_PATHS)
    is_cloudflare = "<title>just a moment" in haystack or (
        _CLOUDFLARE_CHALLENGE_PATH in haystack
        and any(text in haystack for text in _CLOUDFLARE_CHALLENGE_TEXTS)
    )
    is_verification_interstitial = any(text in haystack for text in _VERIFICATION_CHALLENGE_TEXTS)
    # Imperva/Incapsula can return a full-page HTTP-200 interstitial whose
    # only body content is an iframe pointing at ``/_Incapsula_Resource``.
    # Do not match the ordinary Incapsula sensor script used by legitimate
    # pages (for example PeopleStrong); require both full-page markers.
    is_incapsula_interstitial = all(
        marker in haystack for marker in _INCAPSULA_INTERSTITIAL_MARKERS
    )
    is_radware = any(marker in haystack for marker in _RADWARE_CHALLENGE_MARKERS)
    if (
        is_siteground
        or is_cloudflare
        or is_verification_interstitial
        or is_incapsula_interstitial
        or is_radware
    ):
        raise BotChallengeError(
            f"bot challenge detected for {url}; configure or verify proxy transport"
        )


def _build_url_matcher(url_filter) -> re.Pattern | None:
    """Compile *url_filter* config into a regex, or ``None`` to use keywords."""
    if not url_filter:
        return None
    if isinstance(url_filter, str):
        return re.compile(url_filter)
    include = url_filter.get("include")
    return re.compile(include) if include else None


def _build_url_identity_transform(url_transform) -> tuple[re.Pattern, str] | None:
    """Compile a URL rewrite for transformation-aware pagination dedupe.

    The dispatcher still performs the actual rewrite after discovery. During
    pagination we only use the eventual URL as a stable identity so tracking
    parameters cannot make one posting look new on every page.
    """
    if not isinstance(url_transform, dict):
        return None
    find = url_transform.get("find")
    if not isinstance(find, str) or not find:
        return None
    replace = url_transform.get("replace", "")
    if not isinstance(replace, str):
        return None
    try:
        return re.compile(find), replace
    except re.error as exc:
        log.warning("monitor.url_transform_invalid", error=str(exc))
        return None


def _url_identity(url: str, transform: tuple[re.Pattern, str] | None) -> str:
    if transform is None:
        return url
    pattern, replace = transform
    return pattern.sub(replace, url)


def _dedupe_by_identity(
    urls: set[str],
    transform: tuple[re.Pattern, str] | None,
) -> tuple[set[str], set[str]]:
    """Keep one raw representative for each eventual transformed URL."""
    representatives: set[str] = set()
    identities: set[str] = set()
    for url in sorted(urls):
        identity = _url_identity(url, transform)
        if identity in identities:
            continue
        representatives.add(url)
        identities.add(identity)
    return representatives, identities


# ---------------------------------------------------------------------------
# Static link extraction (no browser)
# ---------------------------------------------------------------------------


class _LinkExtractor(HTMLParser):
    """Extract href values from ``<a>`` tags."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value:
                    self.hrefs.append(value)


def _extract_links_static(
    html: str,
    base_url: str,
    url_matcher: re.Pattern | None = None,
    link_selector: str | None = None,
) -> set[str]:
    """Parse ``<a href>`` links from raw HTML and filter for job URLs.

    When *url_matcher* is provided it is used instead of the default keyword
    filter, allowing non-English career pages to work. When *link_selector* is
    provided, only matching anchors are considered and they are treated as job
    links unless *url_matcher* narrows them further.
    """
    if link_selector is not None:
        tree = LexborHTMLParser(html)
        hrefs = [node.attributes.get("href") for node in tree.css(link_selector)]
    else:
        parser = _LinkExtractor()
        parser.feed(html)
        hrefs = parser.hrefs

    urls: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith("http"):
            continue
        if url_matcher is not None:
            if url_matcher.search(absolute):
                urls.add(absolute)
        elif link_selector is not None or _matches_default_job_url(absolute):
            urls.add(absolute)
    return urls


def _validate_link_selector(value: object) -> str | None:
    """Return a bounded valid CSS selector, or ``None`` when unset."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError("DOM monitor link_selector must be a CSS selector up to 256 chars")
    selector = value.strip()
    try:
        LexborHTMLParser("<a href='/job'>job</a>").css(selector)
    except SelectolaxError as exc:
        raise ValueError(f"DOM monitor link_selector is invalid: {selector!r}") from exc
    return selector


# ---------------------------------------------------------------------------
# Playwright link extraction
# ---------------------------------------------------------------------------


async def _extract_links_rendered(
    page,
    metadata: dict,
    url_matcher: re.Pattern | None = None,
) -> set[str]:
    """Navigate, run actions, and extract job links from a Playwright page."""
    board_url = metadata["_board_url"]
    browser_config = {k: v for k, v in metadata.items() if k in BROWSER_KEYS}
    await navigate(page, board_url, browser_config)
    await run_actions(page, browser_config.get("actions", []))

    # SiteGround returns HTTP 202 followed by a meta-refresh into
    # ``/.well-known/captcha``.  The page contains no job links, so without
    # this guard a WAF block is indistinguishable from a genuinely empty
    # board and the monitor reports a successful empty cycle.
    html = await safe_content(page)
    _raise_if_bot_challenge(page.url, html)

    link_selector = metadata.get("link_selector")
    selector = link_selector or "a[href]"
    links = await page.evaluate(
        """
        (selector) => Array.from(document.querySelectorAll(selector))
            .map(a => a.href)
            .filter(h => h.startsWith('http'))
    """,
        selector,
    )
    urls: set[str] = set()
    for link in links:
        if url_matcher is not None:
            if url_matcher.search(link):
                urls.add(link)
        elif link_selector is not None or _matches_default_job_url(link):
            urls.add(link)
    return urls


# ---------------------------------------------------------------------------
# Pagination — fetch additional pages and merge links
# ---------------------------------------------------------------------------


async def _fetch_via_page(
    page,
    url: str,
    *,
    retries: int = _BROWSER_FETCH_RETRIES,
    base_delay: float = _BROWSER_FETCH_BASE_DELAY,
    transient_403: bool = False,
) -> str | None:
    """Fetch ``url`` via Playwright ``page.evaluate(fetch(...))`` with bounded retries.

    Returns:
        - ``str`` (truncated to ``_BROWSER_FETCH_MAX_CHARS``) on HTTP 200
          with a **non-empty** body.
        - ``None`` on HTTP 404 / 410 (legitimate end-of-pagination), or
          any other non-retryable 4xx (lenient stop, mirrors the
          httpx-side ``fetch_with_retry``). When ``transient_403`` is true,
          HTTP 401/403 instead consume the retry budget and fail closed.

    Raises:
        :exc:`BotChallengeError` when the response body is a recognized
        anti-bot interstitial, including non-retryable HTTP 403 pages.
        :exc:`PaginationFetchError` when *retries* attempts have all
        hit a retryable failure (5xx including Cloudflare 520-526/530,
        408, 425, 429, **200-with-empty-body**, or a Playwright
        ``page.evaluate`` exception — timeout, network error, page
        closed). The caller is expected to propagate so
        ``_process_one_board_streaming`` records the run as a failure
        rather than a partial success — the fix for the silent-
        truncation bug from #2737, extended in #2739 to cover empty-200.

    Empty-200 handling (#2739). Symmetric with the static httpx path:
    a 200 with an empty body is transient (anti-bot challenge dropping
    the body, partial Cloudflare response, origin glitch) — retry,
    then raise. Returning ``""`` would cascade through
    ``_paginate_urls``'s ``if not html: break`` and tombstone the
    un-fetched tail.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` between
    retries. Fewer retries than the static path (Playwright fetches
    are slower and share the per-board browser context).
    """
    from src.shared.http_retry import (
        END_OF_PAGINATION_STATUSES,
        PaginationFetchError,
        is_retryable_status,
    )
    from src.shared.tdm import (
        TDMReservedError,
        check_browser_response,
    )

    last_exc: BaseException | None = None
    last_status: int | None = None

    for attempt in range(retries):
        try:
            result = await page.evaluate(_BROWSER_FETCH_JS, url)
            # ``result`` is the JS object literal we constructed above —
            # ``{status, headers, text}``. If something upstream malformed
            # it (anti-bot script substituting a Promise rejection, page
            # navigation completing the evaluate with a non-dict value),
            # ``result["status"]`` raises ``AttributeError`` /
            # ``TypeError`` and falls through to the ``except Exception``
            # branch below — retried, then surfaced as
            # ``PaginationFetchError``. No defensive shape-check needed.
            status = result["status"]
            text = result.get("text") or ""
            resp_headers = result.get("headers") or {}
            last_status = status
            if text:
                _raise_if_bot_challenge(url, text)
            if status == 200:
                if text:
                    # TDM-Reservation respect (#2842). Symmetric with the
                    # static httpx path: a publisher emitting the W3C
                    # opt-out signal is honored even when the page is
                    # reached via a Playwright fetch (``pagination.browser=true``).
                    check_browser_response(resp_headers, text, url=url)
                    return text[:_BROWSER_FETCH_MAX_CHARS]
                # Empty-200 (#2739): transient, fall through to backoff.
                last_exc = None
                log.info(
                    "dom.pagination.browser_fetch_empty_200",
                    url=url,
                    attempt=attempt + 1,
                )
            elif status in END_OF_PAGINATION_STATUSES:
                return None
            elif is_retryable_status(status) or (transient_403 and status in {401, 403}):
                last_exc = None  # status-only, no exception
            else:
                # Other 4xx (auth, forbidden, bad-request) — not
                # transient, not "end of pagination" canonically.
                # Mirror the httpx path: lenient stop, logged so
                # anomalies are observable.
                log.warning(
                    "dom.pagination.browser_fetch_non_retryable_status",
                    url=url,
                    status=status,
                )
                return None
        except (BotChallengeError, TDMReservedError):
            # Anti-bot interstitials and publisher policy declarations are
            # deterministic responses, not transport glitches. Propagate
            # them so the monitor run fails/skips instead of truncating.
            raise
        except Exception as exc:  # page.evaluate raised — timeout, navigation, page closed
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "dom.pagination.browser_fetch_backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )


async def _paginate_urls(
    board_url: str,
    pagination: dict,
    initial_urls: set[str],
    client: httpx.AsyncClient,
    page=None,
    url_matcher: re.Pattern | None = None,
    url_transform: dict | None = None,
    encoding: str | None = None,
    link_selector: str | None = None,
) -> set[str]:
    """Fetch paginated pages and merge discovered links with *initial_urls*.

    Supports two URL modes:
    - ``param_name``: appends ``?param=value`` query parameter (default).
    - ``url_template``: formats a URL template containing ``{page}`` with the
      current page value — for path-based pagination.

    Failure semantics (#2722, #2737, #2739). Both fetch paths use
    bounded retries with exponential backoff and full jitter. Empty-200
    classification is symmetric across the two paths and treated as
    transient (retry, then raise) rather than end-of-pagination — the
    fix from #2739 closing the silent-truncation hole on empty bodies
    served as 200 (anti-bot challenge dropping body, partial CDN
    response, origin glitch).

    - Static httpx (``pagination.browser=false``) — :func:`fetch_with_retry`.
    - Browser (``pagination.browser=true``) — :func:`_fetch_via_page`, which
      runs ``fetch`` inside the Playwright page and inspects the response
      status. Smaller retry budget than the httpx path because Playwright
      fetches are slower and share the per-board browser context.

    Both fetchers:

    - Return ``None`` on 404/410 (legitimate end-of-pagination — break).
    - Return the body on 200 (continue).
    - Return ``None`` on other 4xx (e.g. 403) by default — lenient stop so
      misconfigured paginators don't poison the run as a failure. Boards with
      ``pagination.transient_403=true`` instead retry HTTP 401/403 and raise
      on exhaustion so a WAF-blocked tail cannot be accepted as complete.
    - **Raise** :exc:`PaginationFetchError` on persistent 5xx, 429,
      timeout, network error, or Playwright ``page.evaluate`` exception
      after the retry budget. The exception propagates out of
      ``dom_discover`` and lands in
      ``_process_one_board_streaming``'s generic ``except Exception``,
      which records the run as a failure (``_RECORD_FAILURE`` →
      consecutive_failures++ with exponential backoff). Critically,
      ``_MARK_GONE_BY_TIMESTAMP`` is **not** called, so a transient
      origin failure mid-pagination cannot tombstone the URLs that
      live on the unfetched pages — the fix for the 2026-04-26 NHS
      spike (#2722) and the matching ``pagination.browser=true``
      hole (#2737, ``lenovo-careers``).
    """
    from src.shared.api_sniff import set_url_param
    from src.shared.http_retry import fetch_with_retry

    url_template = pagination.get("url_template")
    param_name = pagination.get("param_name")
    start = pagination.get("start", pagination.get("start_value", 1))
    increment = pagination.get("increment", 1)
    max_pages = min(pagination.get("max_pages", _MAX_PAGINATION_PAGES), _MAX_PAGINATION_PAGES)
    use_browser = pagination.get("browser", False) and page is not None
    transient_403 = pagination.get("transient_403", False)
    if not isinstance(transient_403, bool):
        raise ValueError("DOM pagination transient_403 must be a boolean")
    if not url_template and not isinstance(param_name, str):
        raise ValueError("DOM pagination requires param_name or url_template")

    identity_transform = _build_url_identity_transform(url_transform)
    all_urls, seen_identities = _dedupe_by_identity(initial_urls, identity_transform)
    value = start + increment

    for page_num in range(2, max_pages + 1):
        if url_template:
            page_url = url_template.format(page=value)
        else:
            assert isinstance(param_name, str)
            page_url = set_url_param(board_url, param_name, value)

        if use_browser:
            html = await _fetch_via_page(
                page,
                page_url,
                transient_403=transient_403,
            )
        else:
            html = await fetch_with_retry(
                client,
                page_url,
                encoding=encoding,
                transient_403=transient_403,
            )

        if not html:
            # Legitimate end-of-pagination (404/410, empty body, or
            # browser fetch returned None). Caller's contract: a
            # successful run with the URLs accumulated so far.
            log.info("dom.pagination.end", page=page_num, url=page_url)
            break

        _raise_if_bot_challenge(page_url, html)
        new_urls = _extract_links_static(html, page_url, url_matcher, link_selector)
        added: set[str] = set()
        for url in sorted(new_urls):
            identity = _url_identity(url, identity_transform)
            if identity in seen_identities:
                continue
            added.add(url)
            seen_identities.add(identity)
        if not added:
            log.info("dom.pagination.no_new_urls", page=page_num)
            break

        # Only retain the representatives that introduced a new transformed
        # identity.  Unioning every raw URL here reintroduced tracking/apply
        # variants that ``seen_identities`` had correctly classified as
        # duplicates.
        all_urls |= added
        log.debug("dom.pagination.page", page=page_num, new=len(added), total=len(all_urls))
        value += increment

    return all_urls


# ---------------------------------------------------------------------------
# can_handle — static probe for link discovery
# ---------------------------------------------------------------------------


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Probe whether *url* has discoverable job links via static fetch.

    Returns metadata dict when job links are found, None otherwise.
    """
    vagas = _vagas_probe_config(url)
    if vagas is not None:
        return vagas

    from src.core.monitors import fetch_page_text

    html = await fetch_page_text(url, client)
    if not html:
        return None

    kontact = _kontact_probe_config(html, url)
    if kontact is not None:
        return kontact

    talentsoft = _talentsoft_probe_config(html, url)
    if talentsoft is not None:
        return talentsoft

    jposting = _jposting_probe_config(html, url)
    if jposting is not None:
        return jposting

    rexx = _rexx_probe_config(html, url)
    if rexx is not None:
        return rexx

    talentlink = _talentlink_probe_config(html, url)
    if talentlink is not None:
        return talentlink

    urls = _extract_links_static(html, url)
    linkedin_urls = {candidate for candidate in urls if _is_linkedin_job_url(candidate)}
    if linkedin_urls and len(linkedin_urls) * 2 >= len(urls):
        # Normal LinkedIn detail pages commonly return HTTP 999 or an authwall
        # to crawler traffic.  Their public guest endpoint serves the same job
        # content without authentication, so make that stable rewrite part of
        # the probe-generated DOM config.
        return {
            "urls": len(linkedin_urls),
            "url_filter": _LINKEDIN_JOB_FILTER,
            "url_transform": dict(_LINKEDIN_JOB_TRANSFORM),
        }
    if urls:
        return {"urls": len(urls)}
    return None


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def dom_discover(
    board: dict,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> set[str]:
    """Discover job URLs from a career page.

    ``include_board_url`` is an explicit escape hatch for boards whose URL
    is itself a job-detail document (for example, a directly linked PDF).
    The normal fetch still runs first, so a removed document produces an
    empty result and follows the regular gone-detection path.
    """
    if client is None:
        raise ValueError("DOM monitor requires an HTTP client")
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    render = metadata.get("render", False)
    actions = metadata.get("actions")
    pagination = metadata.get("pagination")
    url_matcher = _build_url_matcher(metadata.get("url_filter"))
    url_transform = metadata.get("url_transform")
    link_selector = _validate_link_selector(metadata.get("link_selector"))
    encoding = metadata.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str) or not encoding:
            raise ValueError("DOM monitor encoding must be a non-empty codec name")
        codecs.lookup(encoding)

    if not render and actions:
        log.warning(
            "dom.misconfiguration",
            board_url=board_url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    if render:
        combined = {**metadata, "_board_url": board_url}

        if pw is not None:
            async with open_page(pw, combined, use_proxy=bool(metadata.get("proxy"))) as page:
                urls = await _extract_links_rendered(page, combined, url_matcher)
                if pagination:
                    browser_page = page if pagination.get("browser") else None
                    urls = await _paginate_urls(
                        board_url,
                        pagination,
                        urls,
                        client,
                        browser_page,
                        url_matcher,
                        url_transform,
                        encoding,
                        link_selector,
                    )
        else:
            try:
                from playwright.async_api import async_playwright
            except ImportError as err:
                raise RuntimeError(
                    "playwright is required for the dom monitor with render=true. "
                    "Install with: uv sync --group dev && uv run playwright install chromium"
                ) from err

            async with (
                async_playwright() as p,
                open_page(p, combined, use_proxy=bool(metadata.get("proxy"))) as page,
            ):
                urls = await _extract_links_rendered(page, combined, url_matcher)
                if pagination:
                    browser_page = page if pagination.get("browser") else None
                    urls = await _paginate_urls(
                        board_url,
                        pagination,
                        urls,
                        client,
                        browser_page,
                        url_matcher,
                        url_transform,
                        encoding,
                        link_selector,
                    )
    else:
        from src.shared.http_retry import fetch_with_retry

        html = await fetch_with_retry(
            client,
            board_url,
            transient_403=True,
            retryable_statuses={202},
            encoding=encoding,
        )
        if not html:
            log.warning("dom.fetch_failed", board_url=board_url)
            return set()
        _raise_if_bot_challenge(board_url, html)
        urls = _extract_links_static(html, board_url, url_matcher, link_selector)
        if pagination:
            urls = await _paginate_urls(
                board_url,
                pagination,
                urls,
                client,
                url_matcher=url_matcher,
                url_transform=url_transform,
                encoding=encoding,
                link_selector=link_selector,
            )

    # Exclude the board URL itself by default — it is normally the listing
    # page, not a job. Direct document boards opt in after the successful
    # fetch above so the source URL is emitted as their one job URL.
    normalized_board = board_url.rstrip("/")

    def _without_fragment(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")).rstrip("/")

    urls = {u for u in urls if _without_fragment(u) != normalized_board}
    if metadata.get("include_board_url"):
        urls.add(board_url)

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    log.info("dom.complete", board_url=board_url, urls_found=len(urls), render=render)
    return urls


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    await save_text_response(
        artifact_dir,
        client,
        board_url,
        filename="page.html",
        follow_redirects=True,
    )


register("dom", dom_discover, cost=100, can_handle=can_handle, save_raw=save_raw)
