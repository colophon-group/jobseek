"""Registered HTTP/browser adapter for the browser-neutral JSON-LD parser."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from src.core.job_content import JobContent
from src.core.jsonld import _extract_locations as _extract_locations
from src.core.jsonld import _extract_salary as _extract_salary
from src.core.jsonld import _find_job_posting as _find_job_posting
from src.core.jsonld import _JsonLdExtractor as _JsonLdExtractor
from src.core.jsonld import _normalize_meta_locations as _normalize_meta_locations
from src.core.jsonld import _parse_posting as _parse_posting
from src.core.jsonld import _strip_html as _strip_html
from src.core.jsonld import _text_or_list as _text_or_list
from src.core.jsonld import can_handle as can_handle
from src.core.jsonld import contains_job_posting as contains_job_posting
from src.core.jsonld import parse_html as parse_html
from src.core.jsonld import parse_rendered_html as parse_rendered_html
from src.core.scrapers import register
from src.shared.api_sniff import clean_headers
from src.shared.http import (
    is_avature_job_detail_url,
    mark_reachable_response,
    mark_transient_response_failure,
)
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

_MAX_TRANSPORT_ATTEMPTS = 5
_TRANSPORT_RETRY_DELAY = 1.0
_CONTENT_ATTEMPTS = 2
_CONTENT_RETRY_DELAY = 1.0


def _guard_rendered_content(requested_url: str, final_url: str, html: str) -> None:
    """Raise a typed origin-block error while the proxy context is open.

    The shared detector covers generic bot-manager interstitials.  Njoyn also
    returns a tiny provider-specific ``Invalid request XWP...`` document with
    HTTP 200; use its narrow detector only for that hostname.  Running the
    guard inside ``browser.render`` is load-bearing because ``open_page`` can
    then quarantine the selected origin/slot pair before the context closes.
    """
    try:
        hostname = (urlsplit(requested_url).hostname or "").lower()
        if hostname == "njoyn.com" or hostname.endswith(".njoyn.com"):
            from src.core.monitors.njoyn import _raise_if_njoyn_challenge

            _raise_if_njoyn_challenge(final_url, html)
        else:
            from src.core.monitors.dom import _raise_if_bot_challenge

            _raise_if_bot_challenge(final_url, html)
    except Exception as exc:
        if getattr(exc, "proxy_failure_reason", None) == "origin_block":
            # Browser navigation does not pass through the tracked httpx
            # transport. Promote the typed rendered challenge explicitly so
            # the worker's shared host circuit can defer sibling postings.
            # Attribute redirects to the stable requested job origin: WAF
            # challenge URLs can contain opaque or rotating hostnames.
            mark_transient_response_failure(
                requested_url,
                reason="rendered_origin_block",
            )
        raise
    mark_reachable_response(requested_url)


async def _render_with_origin_block_recovery(url: str, config: dict, pw=None) -> str:
    """Render with bounded proxy rotation and guarded direct fallback.

    Direct egress is allowed only when config opts in *and* this call first
    observes a typed origin block from a selected proxy context.  An absent or
    exhausted proxy pool by itself never authorizes a bypass.
    """
    from src.shared.browser import render as browser_render
    from src.shared.proxy import ProxyPoolExhaustedError

    async def _render(*, use_proxy: bool) -> str:
        return await browser_render(
            url,
            config | {"proxy": use_proxy},
            pw=pw,
            content_guard=_guard_rendered_content,
        )

    if not config.get("proxy"):
        return await _render(use_proxy=False)

    attempts = min(
        _MAX_TRANSPORT_ATTEMPTS,
        max(1, int(config.get("transport_attempts", 1))),
    )
    last_origin_block: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await _render(use_proxy=True)
        except ProxyPoolExhaustedError:
            if last_origin_block is None:
                raise
            log.warning(
                "jsonld.render.origin_block_retry",
                attempt=attempt,
                attempts=attempts,
                retrying=False,
                reason="pool_exhausted_after_origin_block",
            )
            break
        except Exception as exc:
            if getattr(exc, "proxy_failure_reason", None) != "origin_block":
                raise
            last_origin_block = exc
            retrying = attempt < attempts
            log.warning(
                "jsonld.render.origin_block_retry",
                attempt=attempt,
                attempts=attempts,
                retrying=retrying,
                reason="origin_block",
            )
            if retrying:
                await asyncio.sleep(_TRANSPORT_RETRY_DELAY * attempt)

    if config.get("direct_fallback_on_origin_block") is True:
        log.warning(
            "jsonld.render.direct_fallback",
            proxy_attempts=attempts,
            last_error_type=type(last_origin_block).__name__,
        )
        return await _render(use_proxy=False)

    assert last_origin_block is not None
    raise last_origin_block


def _selected_description(html: str, selector: object) -> str:
    """Return a required visible description selected from the job page.

    Some providers publish a valid JobPosting object whose description is
    only generic company boilerplate while the full role content is rendered
    elsewhere in the same static document.  An explicit selector lets those
    boards replace only that field while retaining the structured title,
    dates, locations, and salary from JSON-LD.
    """
    if (
        not isinstance(selector, str)
        or not selector.strip()
        or len(selector) > 256
        or "\x00" in selector
    ):
        raise ValueError("JSON-LD description_selector must be a CSS selector up to 256 chars")
    try:
        node = LexborHTMLParser(html).css_first(selector)
    except (SelectolaxError, TypeError, ValueError) as exc:
        raise ValueError("JSON-LD description_selector is not a valid CSS selector") from exc
    if node is None:
        raise ValueError(f"JSON-LD description_selector did not match: {selector!r}")
    selected = (node.inner_html or "").strip()
    if not selected or not _strip_html(selected).strip():
        raise ValueError(f"JSON-LD description_selector was empty: {selector!r}")
    return selected


async def _fetch_html(
    url: str,
    http: httpx.AsyncClient,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    """GET the page with bounded provider/status-aware retries."""

    retry_limits = {403: 1}
    if is_avature_job_detail_url(url):
        retry_limits[406] = 2
    response = await fetch_response_with_status_retries(
        http,
        url,
        retry_limits=retry_limits,
        headers=headers,
        log_event="jsonld.fetch.retry_status",
    )
    response.raise_for_status()
    return response.text


def _icims_iframe_url(requested_url: str, html: str) -> str | None:
    """Return one same-origin iCIMS detail iframe when the outer shell has no data."""
    requested = urlsplit(requested_url)
    if (
        requested.scheme != "https"
        or not (requested.hostname or "").lower().endswith(".icims.com")
        or requested.username is not None
        or requested.password is not None
        or requested.port not in {None, 443}
    ):
        return None
    tree = LexborHTMLParser(html)
    candidates = {
        urljoin(requested_url, node.attributes["src"])
        for node in tree.css("iframe[src]")
        if node.attributes.get("src")
    }
    trusted: list[str] = []
    for candidate in candidates:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme == requested.scheme
            and parsed.hostname == requested.hostname
            and parsed.port == requested.port
            and parsed.path == requested.path
            and parse_qsl(parsed.query, keep_blank_values=True) == [("in_iframe", "1")]
            and not parsed.fragment
            and parsed.username is None
            and parsed.password is None
        ):
            trusted.append(candidate)
    if len(trusted) > 1:
        raise ValueError("JSON-LD iCIMS shell exposed multiple trusted detail iframes")
    return trusted[0] if trusted else None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, **kwargs) -> JobContent:
    """Extract job data from JSON-LD on a page."""
    request_headers = config.get("request_headers") or {}
    headers = clean_headers(request_headers)

    async def load_html() -> str:
        if config.get("render"):
            from src.shared.browser import BROWSER_KEYS

            browser_config = {key: value for key, value in config.items() if key in BROWSER_KEYS}
            return await _render_with_origin_block_recovery(url, browser_config, pw=pw)
        return await _fetch_html(url, http, headers=headers or None)

    html = ""
    content = JobContent()
    for attempt in range(1, _CONTENT_ATTEMPTS + 1):
        html = await load_html()
        content = parse_rendered_html(url, config, html)
        if content.title:
            break
        iframe_url = _icims_iframe_url(url, html)
        if iframe_url is not None:
            iframe_html = await _fetch_html(iframe_url, http, headers=headers or None)
            iframe_content = parse_rendered_html(iframe_url, config, iframe_html)
            if iframe_content.title:
                html = iframe_html
                content = iframe_content
                log.info("jsonld.icims_iframe_fallback", url=url)
                break
        if attempt < _CONTENT_ATTEMPTS:
            log.warning("jsonld.content_retry", url=url, attempt=attempt)
            await asyncio.sleep(_CONTENT_RETRY_DELAY)

    description_selector = config.get("description_selector")
    if description_selector is not None:
        content.description = _selected_description(html, description_selector)
    if content.title:
        log.debug("jsonld.extracted", url=url, title=content.title)
    else:
        log.warning("jsonld.not_found", url=url)
    return content


async def probe(url: str, http: httpx.AsyncClient) -> bool:
    """Check whether a URL has JSON-LD JobPosting data."""

    try:
        response = await http.get(url, follow_redirects=True)
        if response.status_code != 200:
            return False
        return can_handle([response.text]) is not None
    except Exception:
        return False


register("json-ld", scrape, can_handle=can_handle, parse_html=parse_html)


__all__ = [
    "can_handle",
    "contains_job_posting",
    "parse_html",
    "parse_rendered_html",
    "probe",
    "scrape",
]
