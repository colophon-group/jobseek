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
import io
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_text_response
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content
from src.shared.public_request_headers import (
    same_origin,
    validated_public_request_headers,
)
from src.shared.response_fingerprint import (
    build_response_fingerprint_url,
    response_fingerprint_validators,
    same_origin_response,
)
from src.shared.tdm import check_response as check_tdm_response
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_PAGINATION_PAGES = 10_000
_JSONLD_VERIFICATION_CONCURRENCY = 8
_MAX_JSONLD_VERIFICATION_URLS = 500
_PDF_EXPIRATION_VERIFICATION_CONCURRENCY = 4
_MAX_PDF_EXPIRATION_VERIFICATION_URLS = 100
_MAX_PDF_EXPIRATION_BYTES = 20 * 1024 * 1024
_MAX_PDF_EXPIRATION_PAGES = 200
_MAX_PDF_EXPIRATION_TEXT_CHARS = 2_000_000
_MAX_EXPLICIT_EMPTY_BODY_BYTES = 2 * 1024 * 1024
_RESPONSE_FINGERPRINT_CONCURRENCY = 4
_MAX_RESPONSE_FINGERPRINT_URLS = 100
_MAX_RICH_ROWS_LIFECYCLE_URLS = 500

_DEADLINE_MONTH_ALIASES = {
    "januar": "January",
    "februar": "February",
    "märz": "March",
    "mai": "May",
    "juni": "June",
    "juli": "July",
    "oktober": "October",
    "dezember": "December",
}

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
_TALENTSOFT_PARTITION_SELECTOR = "ul.facette-titre-niv1 a[href*='facet_Contract=']"
_TALENTSOFT_PARTITION_FALLBACK_SELECTOR = "ul.facette-titre-niv1 a[href*='facet_JobFamily=']"
_TALENTSOFT_PARTITION_COUNT_REGEX = r"\((\d+)\s+(?:vacancies|offres)"
_MAX_PAGINATION_PARTITIONS = 500
_PAGINATION_PARTITION_CONCURRENCY = 4

_JPOSTING_HOST_SUFFIX = ".jposting.net"
_JPOSTING_JOB_FILTER = r"[?&]job_code=[^&#]+"


async def _filter_jsonld_job_urls(urls: set[str], client: httpx.AsyncClient) -> set[str]:
    """Retain URLs whose current detail page contains JobPosting JSON-LD.

    Detail fetch failures propagate so a transient provider outage cannot turn
    a partial inventory into a successful monitor cycle and tombstone jobs.
    Legitimate 404/410 responses and pages without JobPosting data are omitted.
    """
    if len(urls) > _MAX_JSONLD_VERIFICATION_URLS:
        raise ValueError(
            "DOM monitor require_jsonld_jobposting supports at most "
            f"{_MAX_JSONLD_VERIFICATION_URLS} discovered URLs"
        )

    from src.core.scrapers.jsonld import contains_job_posting
    from src.shared.http_retry import fetch_with_retry

    semaphore = asyncio.Semaphore(_JSONLD_VERIFICATION_CONCURRENCY)

    async def verify(url: str) -> tuple[str, bool]:
        async with semaphore:
            html = await fetch_with_retry(
                client,
                url,
                transient_403=True,
                # Verification is authoritative for gone detection. A bounded
                # prefix can omit valid JSON-LD near the end of a large page
                # and make a live posting look stale.
                max_chars=None,
            )
        if html is None:
            return url, False
        _raise_if_bot_challenge(url, html)
        return url, contains_job_posting(html)

    tasks = [asyncio.create_task(verify(url)) for url in sorted(urls)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        # ``gather`` propagates the first failure without cancelling sibling
        # tasks. Stop and drain them so a failed monitor cycle cannot leave
        # hundreds of provider requests running in the worker.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return {url for url, is_job in results if is_job}


def _validated_response_fingerprint_config(value: object) -> str | None:
    """Validate opt-in response-validator URL fingerprinting.

    The expected media type is mandatory so a listing cannot silently start
    pointing at an HTML error page while still producing a plausible ETag.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"content_type"}:
        raise ValueError("DOM monitor fingerprint_response must contain only content_type")
    content_type = value.get("content_type")
    if (
        not isinstance(content_type, str)
        or not content_type.strip()
        or len(content_type) > 128
        or "\x00" in content_type
        or "/" not in content_type
    ):
        raise ValueError("DOM monitor fingerprint_response.content_type must be a media type")
    return content_type.strip().casefold()


async def _fingerprint_response_urls(
    urls: set[str],
    client: httpx.AsyncClient,
    expected_content_type: str,
    *,
    request_headers: dict[str, str] | None = None,
    allowed_origin_url: str | None = None,
) -> set[str]:
    """Turn mutable URLs into stable identities from strong HTTP validators.

    All validators are required. Any request or validation failure propagates,
    so a provider outage or header regression fails the whole monitor cycle
    instead of returning a partial inventory and marking live jobs gone.
    """
    if len(urls) > _MAX_RESPONSE_FINGERPRINT_URLS:
        raise ValueError(
            "DOM monitor fingerprint_response supports at most "
            f"{_MAX_RESPONSE_FINGERPRINT_URLS} discovered URLs"
        )

    semaphore = asyncio.Semaphore(_RESPONSE_FINGERPRINT_CONCURRENCY)

    async def fingerprint(url: str) -> str:
        if request_headers and allowed_origin_url and not same_origin(url, allowed_origin_url):
            raise ValueError(f"DOM fingerprint_response refused a cross-origin URL: {url}")
        async with semaphore:
            response = await same_origin_response(
                client,
                "HEAD",
                url,
                stream=True,
                headers=request_headers,
            )
        try:
            validators = response_fingerprint_validators(
                response,
                expected_content_type=expected_content_type,
                source_url=url,
            )
        finally:
            await response.aclose()
        return build_response_fingerprint_url(url, validators)

    tasks = [asyncio.create_task(fingerprint(url)) for url in sorted(urls)]
    try:
        return set(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


@dataclass(frozen=True)
class _PdfDateRule:
    pattern: re.Pattern[str]
    date_format: str


@dataclass(frozen=True)
class _PdfDeadlineConfig:
    deadlines: tuple[_PdfDateRule, ...]
    active_window: _PdfDateRule | None = None


def _validated_pdf_date_rule(value: object, *, path: str) -> _PdfDateRule:
    if not isinstance(value, dict) or set(value) != {"pattern", "date_format"}:
        raise ValueError(f"{path} must contain only pattern and date_format")
    pattern = value.get("pattern")
    date_format = value.get("date_format")
    if not isinstance(pattern, str) or not pattern or len(pattern) > 1_024 or "\x00" in pattern:
        raise ValueError(f"{path}.pattern must be 1-1024 characters")
    if (
        not isinstance(date_format, str)
        or not date_format
        or len(date_format) > 128
        or "\x00" in date_format
    ):
        raise ValueError(f"{path}.date_format must be 1-128 characters")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{path}.pattern is invalid") from exc
    if compiled.groups < 1:
        raise ValueError(f"{path}.pattern requires a capture group")
    return _PdfDateRule(compiled, date_format)


def _validated_unexpired_pdf_config(value: object) -> _PdfDeadlineConfig | None:
    """Validate one deadline rule or a bounded heterogeneous PDF rule set."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("DOM monitor require_unexpired_pdf must be an object")
    if set(value) <= {"pattern", "date_format"}:
        rule = _validated_pdf_date_rule(value, path="DOM monitor require_unexpired_pdf")
        return _PdfDeadlineConfig((rule,))
    if set(value) - {"rules", "active_window"}:
        raise ValueError(
            "DOM monitor require_unexpired_pdf must contain pattern/date_format or "
            "rules/active_window"
        )

    raw_rules = value.get("rules", [])
    if not isinstance(raw_rules, list) or len(raw_rules) > 16:
        raise ValueError("DOM monitor require_unexpired_pdf.rules must contain at most 16 rules")
    rules = tuple(
        _validated_pdf_date_rule(
            rule,
            path=f"DOM monitor require_unexpired_pdf.rules[{index}]",
        )
        for index, rule in enumerate(raw_rules)
    )

    active_window = None
    if (raw_window := value.get("active_window")) is not None:
        active_window = _validated_pdf_date_rule(
            raw_window,
            path="DOM monitor require_unexpired_pdf.active_window",
        )
        required_groups = {"month", "opens", "closes", "year"}
        if not required_groups <= set(active_window.pattern.groupindex):
            raise ValueError(
                "DOM monitor require_unexpired_pdf.active_window.pattern requires named "
                "month, opens, closes, and year groups"
            )
    if not rules and active_window is None:
        raise ValueError("DOM monitor require_unexpired_pdf requires rules or active_window")
    return _PdfDeadlineConfig(rules, active_window)


def _validated_pdf_text_config(
    value: object,
) -> tuple[re.Pattern[str], re.Pattern[str]] | None:
    """Validate the opt-in, exhaustive PDF ownership classifier."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"include", "exclude"}:
        raise ValueError("DOM monitor require_pdf_text requires include and exclude regexes")

    compiled: list[re.Pattern[str]] = []
    for name in ("include", "exclude"):
        pattern = value.get(name)
        if not isinstance(pattern, str) or not pattern or len(pattern) > 1_024 or "\x00" in pattern:
            raise ValueError(
                f"DOM monitor require_pdf_text.{name} must be a regex up to 1024 characters"
            )
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(f"DOM monitor require_pdf_text.{name} is invalid") from exc
    return compiled[0], compiled[1]


def _extract_bounded_pdf_text(content: bytes) -> str:
    """Extract PDF text without allowing unbounded page or text expansion."""
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(content))
    if len(reader.pages) > _MAX_PDF_EXPIRATION_PAGES:
        raise ValueError(f"DOM PDF verification document exceeds {_MAX_PDF_EXPIRATION_PAGES} pages")

    parts: list[str] = []
    text_chars = 0
    for page in reader.pages:
        page_text = page.extract_text() or ""
        text_chars += len(page_text)
        if text_chars > _MAX_PDF_EXPIRATION_TEXT_CHARS:
            raise ValueError(
                "DOM PDF verification extracted text exceeds "
                f"{_MAX_PDF_EXPIRATION_TEXT_CHARS} characters"
            )
        parts.append(page_text)
    return "\n\n".join(parts).strip()


async def _fetch_bounded_pdf_text(
    url: str,
    client: httpx.AsyncClient,
    *,
    config_name: str,
    request_headers: dict[str, str] | None = None,
    allowed_origin_url: str | None = None,
) -> str | None:
    """Fetch one bounded PDF and return text, or ``None`` when it was removed."""
    source_is_pdf = urlsplit(url).path.lower().endswith(".pdf")
    if request_headers and allowed_origin_url and not same_origin(url, allowed_origin_url):
        raise ValueError(f"DOM {config_name} refused a cross-origin PDF URL: {url}")
    try:
        response = await same_origin_response(
            client,
            "GET",
            url,
            stream=True,
            headers=request_headers,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {404, 410}:
            return None
        raise

    try:
        check_tdm_response(response)

        # Extensionless first-party document routes are accepted only after a
        # same-origin redirect resolves to a real PDF response.
        if not source_is_pdf:
            final_is_pdf = urlsplit(str(response.url)).path.lower().endswith(".pdf")
            content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if not final_is_pdf or content_type.strip().casefold() != "application/pdf":
                raise ValueError(
                    f"DOM {config_name} extensionless URL did not resolve to a PDF: {url}"
                )

        raw_content_length = response.headers.get("content-length")
        if raw_content_length is not None:
            try:
                content_length = int(raw_content_length)
            except ValueError as exc:
                raise ValueError(
                    f"DOM {config_name} response has invalid Content-Length: {url}"
                ) from exc
            if content_length < 0:
                raise ValueError(f"DOM {config_name} response has invalid Content-Length: {url}")
            if content_length > _MAX_PDF_EXPIRATION_BYTES:
                raise ValueError(
                    f"DOM {config_name} document exceeds {_MAX_PDF_EXPIRATION_BYTES} bytes: {url}"
                )

        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > _MAX_PDF_EXPIRATION_BYTES:
                raise ValueError(
                    f"DOM {config_name} document exceeds {_MAX_PDF_EXPIRATION_BYTES} bytes: {url}"
                )
            content.extend(chunk)
    finally:
        await response.aclose()

    pdf_content = bytes(content)
    if not pdf_content.lstrip().startswith(b"%PDF"):
        raise ValueError(f"DOM {config_name} response is not a PDF: {url}")
    return await asyncio.to_thread(_extract_bounded_pdf_text, pdf_content)


def _normalize_deadline_text(value: str) -> str:
    """Normalize common ordinal and German date spellings for ``strptime``."""
    value = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(?<=\d)\.(?=\s)", "", value)
    for source, target in _DEADLINE_MONTH_ALIASES.items():
        value = re.sub(rf"\b{re.escape(source)}\b", target, value, flags=re.IGNORECASE)
    return value


async def _filter_unexpired_pdf_urls(
    urls: set[str],
    client: httpx.AsyncClient,
    config: _PdfDeadlineConfig,
    *,
    required_text_pattern: re.Pattern[str] | None = None,
    raise_on_required_text_mismatch: bool = False,
    return_deadlines: bool = False,
    return_classified_currentness: bool = False,
    request_headers: dict[str, str] | None = None,
    allowed_origin_url: str | None = None,
) -> set[str] | tuple[set[str], dict[str, str]]:
    """Keep only linked PDFs whose captured application deadline has not passed.

    Every linked document must remain parseable and expose the configured date.
    That fail-closed contract prevents a PDF layout change from turning an
    expired, still-linked advert into a live posting. When
    ``required_text_pattern`` is supplied, documents that do not match the
    ownership/content marker are omitted before deadline parsing, or raise when
    ``raise_on_required_text_mismatch`` requests operator classification.
    ``return_deadlines`` additionally returns normalized ISO dates for active
    rich monitor metadata. ``return_classified_currentness`` instead returns
    dates for every document whose active/expired/window state was parsed. A
    removed 404/410 PDF is omitted because the listing can briefly retain stale
    document links.
    """
    if len(urls) > _MAX_PDF_EXPIRATION_VERIFICATION_URLS:
        raise ValueError(
            "DOM monitor require_unexpired_pdf supports at most "
            f"{_MAX_PDF_EXPIRATION_VERIFICATION_URLS} discovered URLs"
        )
    if return_deadlines and return_classified_currentness:
        raise ValueError("PDF currentness filter return modes are mutually exclusive")

    semaphore = asyncio.Semaphore(_PDF_EXPIRATION_VERIFICATION_CONCURRENCY)

    def parse_date(raw: str, date_format: str, url: str) -> date:
        raw = re.sub(r"\s+", " ", raw).strip()
        raw = _normalize_deadline_text(raw)
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError as exc:
            raise ValueError(
                f"DOM require_unexpired_pdf deadline {raw!r} did not match "
                f"date_format {date_format!r}: {url}"
            ) from exc

    async def verify(url: str) -> tuple[str, bool, str | None]:
        async with semaphore:
            text = await _fetch_bounded_pdf_text(
                url,
                client,
                config_name="require_unexpired_pdf",
                request_headers=request_headers,
                allowed_origin_url=allowed_origin_url,
            )
        if text is None:
            return url, False, None
        if required_text_pattern is not None and required_text_pattern.search(text) is None:
            if raise_on_required_text_mismatch:
                raise ValueError(f"linked PDF did not match the required ownership markers: {url}")
            return url, False, None
        today = datetime.now(UTC).date()
        if config.active_window is not None:
            windows = list(config.active_window.pattern.finditer(text))
            if windows:
                active_deadlines: list[date] = []
                classified_deadlines: list[date] = []
                for match in windows:
                    month = match.group("month")
                    year = match.group("year")
                    opens = parse_date(
                        f"{month} {match.group('opens')} {year}",
                        config.active_window.date_format,
                        url,
                    )
                    closes = parse_date(
                        f"{month} {match.group('closes')} {year}",
                        config.active_window.date_format,
                        url,
                    )
                    if closes < opens:
                        raise ValueError(
                            f"DOM require_unexpired_pdf found an invalid window: {url}"
                        )
                    classified_deadlines.append(closes)
                    if opens <= today <= closes:
                        active_deadlines.append(closes)
                deadline = max(active_deadlines) if active_deadlines else None
                classified = deadline or max(classified_deadlines)
                return url, deadline is not None, classified.isoformat()

        matches: list[tuple[_PdfDateRule, re.Match[str]]] = []
        for rule in config.deadlines:
            matches.extend((rule, match) for match in rule.pattern.finditer(text))
        if not matches:
            raise ValueError(f"DOM require_unexpired_pdf deadline was not found: {url}")
        if len(matches) != 1:
            raise ValueError(f"DOM require_unexpired_pdf deadline was ambiguous: {url}")
        rule, match = matches[0]
        raw_deadline = match.group(1)
        if raw_deadline is None:
            raise ValueError(f"DOM require_unexpired_pdf deadline was not found: {url}")
        deadline = parse_date(raw_deadline, rule.date_format, url)
        return url, deadline >= today, deadline.isoformat()

    tasks = [asyncio.create_task(verify(url)) for url in sorted(urls)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    active_urls = {url for url, is_active, _deadline in results if is_active}
    if return_deadlines:
        deadlines = {
            url: deadline
            for url, is_active, deadline in results
            if is_active and deadline is not None
        }
        return active_urls, deadlines
    if return_classified_currentness:
        classified = {url: deadline for url, _active, deadline in results if deadline is not None}
        return active_urls, classified
    return active_urls


async def _filter_pdf_text_urls(
    urls: set[str],
    client: httpx.AsyncClient,
    config: tuple[re.Pattern[str], re.Pattern[str]],
    *,
    request_headers: dict[str, str] | None = None,
    allowed_origin_url: str | None = None,
) -> set[str]:
    """Classify every linked PDF as included or explicitly excluded.

    This scopes mixed first-party opportunity directories to the target
    employer without trusting filenames or sequential document IDs. A document
    that matches both classes or neither class fails the cycle, preventing an
    ownership-marker change from silently becoming a healthy zero inventory.
    Removed documents are omitted; transport, type, size, and parse failures
    fail the entire cycle closed.
    """
    if len(urls) > _MAX_PDF_EXPIRATION_VERIFICATION_URLS:
        raise ValueError(
            "DOM monitor require_pdf_text supports at most "
            f"{_MAX_PDF_EXPIRATION_VERIFICATION_URLS} discovered URLs"
        )

    include_pattern, exclude_pattern = config
    semaphore = asyncio.Semaphore(_PDF_EXPIRATION_VERIFICATION_CONCURRENCY)

    async def verify(url: str) -> tuple[str, bool]:
        async with semaphore:
            text = await _fetch_bounded_pdf_text(
                url,
                client,
                config_name="require_pdf_text",
                request_headers=request_headers,
                allowed_origin_url=allowed_origin_url,
            )
        if text is None:
            return url, False
        included = include_pattern.search(text) is not None
        excluded = exclude_pattern.search(text) is not None
        if included == excluded:
            classification = (
                "both include and exclude" if included else "neither include nor exclude"
            )
            raise ValueError(
                f"DOM require_pdf_text matched {classification} ownership markers: {url}"
            )
        return url, included

    tasks = [asyncio.create_task(verify(url)) for url in sorted(urls)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return {url for url, matches in results if matches}


async def _exclude_urls_matching_detail_selector(
    urls: set[str],
    selector: str,
    client: httpx.AsyncClient,
) -> set[str]:
    """Omit mirrored postings whose detail page matches *selector*.

    First-party boards sometimes mix email-only opportunities with roles that
    are mirrored from an ATS. Keeping both boards without filtering creates
    duplicate postings, while dropping the first-party board misses future
    email-only roles. Fetch failures propagate so an ATS or origin outage
    cannot turn a partial inventory into a successful gone-detection cycle.
    """
    if len(urls) > _MAX_JSONLD_VERIFICATION_URLS:
        raise ValueError(
            "DOM monitor exclude_detail_selector supports at most "
            f"{_MAX_JSONLD_VERIFICATION_URLS} discovered URLs"
        )

    from src.shared.http_retry import fetch_text_page_with_retry

    semaphore = asyncio.Semaphore(_JSONLD_VERIFICATION_CONCURRENCY)

    async def inspect(url: str) -> tuple[str, bool]:
        async with semaphore:
            html = await fetch_text_page_with_retry(
                client,
                url,
                retryable_statuses={401, 403},
                end_of_pagination_statuses={404, 410},
                require_nonempty=True,
                max_chars=None,
            )
        if html is None:
            return url, True
        _raise_if_bot_challenge(url, html)
        return url, LexborHTMLParser(html).css_first(selector) is not None

    tasks = [asyncio.create_task(inspect(url)) for url in sorted(urls)]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return {url for url, excluded in results if not excluded}


_VAGAS_HOST = "trabalheconosco.vagas.com.br"
_VAGAS_TENANT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

_DUALOO_HOST = "jobs.dualoo.com"
_DUALOO_PORTAL_RE = re.compile(r"/portal/([a-z0-9]+)/*$", re.IGNORECASE)
_DUALOO_JOB_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_LUCCA_HOST_SUFFIX = ".luccasoftware.com"
_LUCCA_TENANT_RE = re.compile(r"/[a-z0-9][a-z0-9-]*/?", re.IGNORECASE)
_LUCCA_JOB_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LUCCA_RICH_ROWS = {
    "row_selector": ".jobBoard-offers-item",
    "link_selector": ".jobBoard-offers-item-link[href]",
    "location_selectors": [".jobBoard-offers-item-tags > .tag:first-child"],
}
_LUCCA_EMPTY_SELECTOR = ".jobBoard-offers-empty"
_LUCCA_EMPTY_TEXT = "There are no job vacancies at the moment."

_PROSPECTIVE_CAREERCENTER_ASSET_RE = re.compile(
    r"/careercenter/(?P<medium_id>\d+)/assets/",
    re.IGNORECASE,
)
_PROSPECTIVE_CANONICAL_ASSET_HOSTS = frozenset({"ohws.prospective.ch"})
_PROSPECTIVE_JOB_UUID = (
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_PROSPECTIVE_JOB_PATH_RE = re.compile(
    rf"/(?:[^/?#]+/)+(?P<uuid>{_PROSPECTIVE_JOB_UUID})/?$",
    re.IGNORECASE,
)
_PROSPECTIVE_RICH_ROWS = {
    "row_selector": "#jobs-list .job",
    "link_selector": "a.job-title[href]",
    "total_selector": ".jobs-total .total",
    "location_selectors": [".place-of-work"],
}
_REXX_PROVIDER_HOSTS = frozenset({"rexx-systems.com", "www.rexx-systems.com"})
_REXX_JOB_PATH_FILTER = (
    r"/(?:[^/?#]+/)*(?:[^/?#]+-j\d+\.html|"
    r"(?:job-offer|stellenangebot)\.html\?yid=\d+)(?:[&#].*)?$"
)
_REXX_SESSION_TRANSFORM = {"find": r"&sid=[^&#]*", "replace": ""}


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


def _dualoo_probe_config(html: str, url: str) -> dict | None:
    """Return the stable DOM + JSON-LD preset for a Dualoo portal.

    Dualoo listing links are relative UUID routes without a job-related word
    before ``/detail``. The generic DOM heuristic therefore misses them even
    though the complete listing is present in static HTML. Scope discovery to
    Dualoo's job-card anchors and fail closed against stale/non-job links by
    requiring JobPosting JSON-LD on each detail page.
    """
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != _DUALOO_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None

    match = _DUALOO_PORTAL_RE.fullmatch(parsed.path)
    if match is None:
        return None
    portal = match.group(1)
    # Provider identity must be present even for a legitimate empty board so
    # the probe can preserve a verified-empty source configuration.
    if f'href="/css/{portal}"' not in html and 'class="JobInfoBox"' not in html:
        return None

    # Preserve an explicitly supplied default port. ``urljoin`` retains it
    # on relative detail links, so dropping it here would make the generated
    # filter reject every live URL and report a false verified-empty board.
    origin = f"https://{parsed.netloc}"
    url_filter = (
        rf"^{re.escape(origin)}/portal/{re.escape(portal)}/"
        rf"{_DUALOO_JOB_UUID}/detail(?:\?[^#]*)?$"
    )
    link_selector = "a.jobElement[href]"
    urls = _extract_links_static(
        html,
        url,
        url_matcher=re.compile(url_filter, re.IGNORECASE),
        link_selector=link_selector,
    )
    return {
        "dualoo_portal": portal,
        "urls": len(urls),
        "link_selector": link_selector,
        "url_filter": url_filter,
        "require_jsonld_jobposting": True,
    }


def _lucca_probe_config(html: str, url: str) -> dict | None:
    """Return strict rich-row extraction for Lucca/Poplee job boards.

    Lucca listing URLs end in a UUID but contain no job keyword, so generic
    DOM discovery ignores them.  The board is server-rendered and exposes
    stable provider classes for each card and its ordered metadata tags.  The
    first tag is the location; later tags contain contract or salary labels.
    Scoping both the link and location selectors to each card gives the rich
    monitor authoritative titles and locations without scraping prose from
    inconsistent detail-page layouts.
    """

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not host.endswith(_LUCCA_HOST_SUFFIX)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or _LUCCA_TENANT_RE.fullmatch(parsed.path) is None
    ):
        return None

    tree = LexborHTMLParser(html)
    if tree.css_first("body.jobBoard #jobBoardOffers") is None:
        return None

    tenant_path = parsed.path.rstrip("/")
    origin = f"https://{parsed.netloc}"
    url_filter = (
        rf"^{re.escape(origin)}{re.escape(tenant_path)}/"
        rf"[^/?#]+-{_LUCCA_JOB_UUID}/?(?:[?#].*)?$"
    )
    rich_rows = dict(_LUCCA_RICH_ROWS)
    try:
        validated_rich_rows = _validated_rich_rows(rich_rows)
        if validated_rich_rows is None:
            return None
        jobs = _extract_rich_rows_static(
            html,
            url,
            validated_rich_rows,
            re.compile(url_filter, re.IGNORECASE),
            allow_empty=True,
        )
        _validate_explicit_empty_state(
            html,
            _LUCCA_EMPTY_SELECTOR,
            _LUCCA_EMPTY_TEXT,
            {job.url for job in jobs},
            url,
        )
    except ValueError:
        return None
    return {
        "lucca_board": True,
        "urls": len(jobs),
        "url_filter": url_filter,
        "rich_rows": rich_rows,
        "empty_selector": _LUCCA_EMPTY_SELECTOR,
        "empty_text": _LUCCA_EMPTY_TEXT,
    }


def _prospective_provider_medium(html: str, url: str) -> str | None:
    """Return the one trusted CareerCenter medium proved by a complete shell."""
    try:
        parsed = urlsplit(url)
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

    tree = LexborHTMLParser(html)
    if (
        tree.css_first("body.career-center #jobs-list") is None
        or tree.css_first(".jobs-total .total") is None
    ):
        return None

    board_origin = (parsed.scheme, parsed.hostname.casefold(), port or 443)
    medium_ids: set[str] = set()
    for node in tree.css("link[href], script[src], img[src]"):
        asset_url = node.attributes.get("href") or node.attributes.get("src") or ""
        try:
            asset = urlsplit(urljoin(url, asset_url))
            asset_port = asset.port
        except ValueError:
            continue
        if asset.username is not None or asset.password is not None:
            continue
        asset_host = (asset.hostname or "").casefold()
        asset_origin = (asset.scheme, asset_host, asset_port or 443)
        is_same_origin = asset_origin == board_origin
        is_canonical_origin = (
            asset.scheme == "https"
            and asset_host in _PROSPECTIVE_CANONICAL_ASSET_HOSTS
            and asset_port in {None, 443}
        )
        if not is_same_origin and not is_canonical_origin:
            continue
        match = _PROSPECTIVE_CAREERCENTER_ASSET_RE.search(asset.path)
        if match is not None:
            medium_ids.add(match.group("medium_id"))
    return medium_ids.pop() if len(medium_ids) == 1 else None


def _prospective_canonical_path_from_html(html: str, url: str) -> str | None:
    """Derive a stable locale route whose cosmetic slug is fixed to ``job``."""
    parsed_board = urlsplit(url)
    board_origin = (parsed_board.scheme, parsed_board.hostname, parsed_board.port or 443)
    tree = LexborHTMLParser(html)
    for link in tree.css("#jobs-list .job a.job-title[href]"):
        href = link.attributes.get("href") or ""
        try:
            parsed_job = urlsplit(urljoin(url, href))
            job_origin = (parsed_job.scheme, parsed_job.hostname, parsed_job.port or 443)
        except ValueError:
            continue
        match = _PROSPECTIVE_JOB_PATH_RE.fullmatch(parsed_job.path)
        if job_origin != board_origin or match is None:
            continue
        segments = [segment for segment in parsed_job.path.split("/") if segment]
        # Prospective routes are /<locale route>/<cosmetic slug>/<uuid>. A
        # shorter path does not prove a fetchable slug-independent route.
        if len(segments) < 3:
            continue
        return "/" + "/".join([*segments[:-2], "job"]) + "/"
    return None


def _validated_prospective_canonical_path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 512 or "\x00" in value:
        raise ValueError("DOM monitor prospective_canonical_path must be a bounded path")
    parsed = urlsplit(value)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path != value
        or not value.startswith("/")
        or not value.endswith("/")
        or not segments
        or segments[-1] != "job"
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise ValueError(
            "DOM monitor prospective_canonical_path must end in a relative /job/ route"
        )
    return value


def _canonicalize_prospective_job_url(
    url: str,
    board_url: str,
    canonical_path: str,
) -> str:
    """Map localized/cosmetic Prospective links onto one UUID identity URL."""
    try:
        parsed_board = urlsplit(board_url)
        parsed_job = urlsplit(url)
        board_origin = (
            parsed_board.scheme,
            (parsed_board.hostname or "").casefold(),
            parsed_board.port or 443,
        )
        job_origin = (
            parsed_job.scheme,
            (parsed_job.hostname or "").casefold(),
            parsed_job.port or 443,
        )
    except ValueError as exc:
        raise ValueError("DOM monitor Prospective row produced an invalid URL") from exc
    match = _PROSPECTIVE_JOB_PATH_RE.fullmatch(parsed_job.path)
    if job_origin != board_origin or match is None:
        raise ValueError("DOM monitor Prospective row violated its UUID URL identity contract")
    return urlunsplit(
        (
            parsed_board.scheme,
            parsed_board.netloc,
            f"{canonical_path}{match.group('uuid').lower()}",
            "",
            "",
        )
    )


def _prospective_probe_config(html: str, url: str) -> dict | None:
    """Return a static rich-row preset for Prospective CareerCenter pages.

    Some branded Prospective boards render their complete inventory on the
    server while rejecting the provider's historical public ``medium`` JSON
    endpoint. Their detail links end in UUIDs and therefore evade the generic
    job-keyword heuristic. Recognize the provider-owned CareerCenter assets,
    scope extraction to its stable listing rows, and preserve an exact zero-job
    contract so markup or transport failures cannot delist every posting.
    """

    medium_id = _prospective_provider_medium(html, url)
    if medium_id is None:
        return None

    parsed = urlsplit(url)
    origin = f"https://{parsed.netloc}"
    url_filter = (
        rf"^{re.escape(origin)}/(?:[^/?#]+/)+{_PROSPECTIVE_JOB_UUID}"
        r"/?(?:[?#].*)?$"
    )
    canonical_path = _prospective_canonical_path_from_html(html, url)
    rich_rows = dict(_PROSPECTIVE_RICH_ROWS)
    asset_origins = {origin} | {f"https://{host}" for host in _PROSPECTIVE_CANONICAL_ASSET_HOSTS}
    asset_origin_pattern = "|".join(re.escape(candidate) for candidate in sorted(asset_origins))
    empty_states = [
        {
            "selector": "body.career-center:has(#jobs-list) .jobs-total .total",
            "exact_text": "0",
            "required_link_selector": f"link[href*='careercenter/{medium_id}/assets/']",
            "required_link_url_pattern": (
                rf"^(?:{asset_origin_pattern})/(?:public/v[12]/)?careercenter/"
                rf"{re.escape(medium_id)}/assets/[^?#]+(?:[?#].*)?$"
            ),
            "forbidden_link_selector": "#jobs-list a.job-title[href]",
        }
    ]
    try:
        validated_rich_rows = _validated_rich_rows(rich_rows)
        if validated_rich_rows is None:
            return None
        jobs = _extract_rich_rows_static(
            html,
            url,
            validated_rich_rows,
            re.compile(url_filter),
            allow_empty=True,
            url_canonicalizer=(
                None
                if canonical_path is None
                else lambda job_url: _canonicalize_prospective_job_url(
                    job_url,
                    url,
                    canonical_path,
                )
            ),
        )
        if jobs and canonical_path is None:
            return None
        _validate_explicit_empty_states(
            html,
            _validated_empty_state_list(empty_states),
            {job.url for job in jobs},
            url,
        )
    except ValueError:
        return None

    config = {
        "prospective_board": medium_id,
        "urls": len(jobs),
        "url_filter": url_filter,
        "rich_rows": rich_rows,
        "empty_states": empty_states,
    }
    if canonical_path is not None:
        config["prospective_canonical_path"] = canonical_path
    return config


def _rexx_probe_config(html: str, url: str) -> dict | None:
    """Return a clean DOM preset for Rexx Systems hosted talent portals.

    Modern Rexx boards use human-readable detail URLs ending in
    ``-j<ID>.html``. Legacy Portal7 tenants use ``job-offer.html?yid=<ID>``
    or localized equivalents such as ``stellenangebot.html?yid=<ID>`` and
    append a short-lived ``sid``. Their navigation also contains a prominent
    ``jobalert-<lang>.html`` link, which the generic job-keyword heuristic
    mistakes for a posting. Detecting the provider marker, applying its stable
    detail pattern, and removing the session token keeps probes complete and
    makes stored URLs independently fetchable by detail scrapers.
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
        "url_transform": dict(_REXX_SESSION_TRANSFORM),
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
    pagination = {
        "param_name": "page",
        "max_pages": 1_000,
    }
    tree = LexborHTMLParser(html)
    title = tree.css_first("title")
    title_text = title.text(strip=True) if title is not None else ""
    has_primary_facets = bool(
        _extract_links_static(html, url, link_selector=_TALENTSOFT_PARTITION_SELECTOR)
    )
    has_fallback_facets = bool(
        _extract_links_static(
            html,
            url,
            link_selector=_TALENTSOFT_PARTITION_FALLBACK_SELECTOR,
        )
    )
    if (
        has_primary_facets
        and has_fallback_facets
        and re.search(_TALENTSOFT_PARTITION_COUNT_REGEX, title_text, re.IGNORECASE)
    ):
        pagination.update(
            {
                "partition_selector": _TALENTSOFT_PARTITION_SELECTOR,
                "partition_fallback_selector": _TALENTSOFT_PARTITION_FALLBACK_SELECTOR,
                "partition_count_regex": _TALENTSOFT_PARTITION_COUNT_REGEX,
                "partition_result_limit": 1_000,
                "partition_validate_total": True,
                "partition_drop_params": ["changefacet"],
                "partition_stateless": True,
            }
        )

    return {
        "urls": len(urls),
        "url_filter": url_filter,
        "pagination": pagination,
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
    return _validate_css_selector(value, name="link_selector")


_ExplicitEmptyState = tuple[
    str,
    str | None,
    bool,
    str | None,
    re.Pattern[str] | None,
    str | None,
]


def _validated_empty_state_list(value: object) -> tuple[_ExplicitEmptyState, ...]:
    """Validate selector-specific exact empty states."""
    if value is None:
        return ()
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError("DOM monitor empty_states must be a list of 1 to 4 mappings")

    states: list[_ExplicitEmptyState] = []
    for item in value:
        required_keys = {"selector", "exact_text"}
        optional_keys = {
            "required_link_selector",
            "required_link_url_pattern",
            "forbidden_link_selector",
        }
        if (
            not isinstance(item, dict)
            or not required_keys.issubset(item)
            or not set(item).issubset(required_keys | optional_keys)
        ):
            raise ValueError(
                "DOM monitor empty_states entries require selector and exact_text, with an "
                "optional required_link_selector and required_link_url_pattern pair, and an "
                "optional forbidden_link_selector"
            )
        selector = _validate_css_selector(item.get("selector"), name="empty_states.selector")
        exact_text = item.get("exact_text")
        if (
            selector is None
            or not isinstance(exact_text, str)
            or not exact_text.strip()
            or len(exact_text) > 256
            or "\x00" in exact_text
        ):
            raise ValueError(
                "DOM monitor empty_states exact_text must be non-empty text up to 256 chars"
            )
        required_link_selector_raw = item.get("required_link_selector")
        required_link_url_pattern_raw = item.get("required_link_url_pattern")
        if (required_link_selector_raw is None) != (required_link_url_pattern_raw is None):
            raise ValueError(
                "DOM monitor empty_states required_link_selector and "
                "required_link_url_pattern must be configured together"
            )
        required_link_selector = None
        required_link_url_pattern = None
        if required_link_selector_raw is not None:
            required_link_selector = _validate_css_selector(
                required_link_selector_raw,
                name="empty_states.required_link_selector",
            )
            if (
                not isinstance(required_link_url_pattern_raw, str)
                or not required_link_url_pattern_raw
                or len(required_link_url_pattern_raw) > 1_024
                or "\x00" in required_link_url_pattern_raw
            ):
                raise ValueError(
                    "DOM monitor empty_states required_link_url_pattern must be a non-empty "
                    "regex up to 1024 chars"
                )
            try:
                required_link_url_pattern = re.compile(required_link_url_pattern_raw)
            except re.error as exc:
                raise ValueError(
                    "DOM monitor empty_states required_link_url_pattern must be a valid regex"
                ) from exc
        forbidden_link_selector = _validate_css_selector(
            item.get("forbidden_link_selector"),
            name="empty_states.forbidden_link_selector",
        )
        states.append(
            (
                selector,
                exact_text.strip(),
                True,
                required_link_selector,
                required_link_url_pattern,
                forbidden_link_selector,
            )
        )
    return tuple(states)


def _validate_explicit_empty_states(
    html: str,
    empty_states: tuple[_ExplicitEmptyState, ...],
    urls: set[str],
    board_url: str,
) -> None:
    """Validate explicit zero evidence and reject contradictory linked states."""
    tree = LexborHTMLParser(html)

    def marker_matches(
        empty_selector: str,
        empty_text: str | None,
        exact_text: bool,
    ) -> bool:
        markers = tree.css(empty_selector)
        if not markers:
            return False
        expected_text = re.sub(r"\s+", " ", empty_text or "").strip()
        if empty_text is None:
            return True
        marker_texts = [
            re.sub(r"\s+", " ", marker.text(separator=" ", strip=True)).strip()
            for marker in markers
        ]
        if exact_text:
            return expected_text in marker_texts
        # Preserve the legacy single-marker contract for substring matching.
        return expected_text.casefold() in marker_texts[0].casefold()

    # A selector-specific state may declare links that contradict the marker.
    # Evaluate that invariant even when normal discovery found URLs; otherwise
    # the historical empty marker plus a newly linked vacancy bypasses the
    # empty-state check through the non-empty fast path.
    for (
        empty_selector,
        empty_text,
        exact_text,
        _required_link_selector,
        _required_link_url_pattern,
        forbidden_link_selector,
    ) in empty_states:
        if (
            forbidden_link_selector is not None
            and marker_matches(empty_selector, empty_text, exact_text)
            and tree.css_first(forbidden_link_selector)
        ):
            raise ValueError(
                "DOM monitor matched an explicit empty state with forbidden links present"
            )

    if _without_board_self_urls(urls, board_url):
        return

    for (
        empty_selector,
        empty_text,
        exact_text,
        required_link_selector,
        required_link_url_pattern,
        _forbidden_link_selector,
    ) in empty_states:
        if not marker_matches(empty_selector, empty_text, exact_text):
            continue
        if required_link_selector is None:
            return
        assert required_link_url_pattern is not None
        required_links = tree.css(required_link_selector)
        if not required_links:
            continue
        if all(
            (href := link.attributes.get("href"))
            and required_link_url_pattern.fullmatch(urljoin(board_url, href))
            for link in required_links
        ):
            return
    raise ValueError(
        "DOM monitor found no job links and did not match the configured explicit empty state"
    )


def _validate_explicit_empty_state(
    html: str,
    empty_selector: str,
    empty_text: str | None,
    urls: set[str],
    board_url: str,
) -> None:
    """Validate the legacy single-selector empty-state configuration."""
    _validate_explicit_empty_states(
        html,
        ((empty_selector, empty_text, False, None, None, None),),
        urls,
        board_url,
    )


def _without_board_self_urls(urls: set[str], board_url: str) -> set[str]:
    """Remove listing-page self links, including fragment-only anchors."""
    normalized_board = board_url.rstrip("/")

    def without_fragment(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")).rstrip("/")

    return {url for url in urls if without_fragment(url) != normalized_board}


def _validate_css_selector(value: object, *, name: str) -> str | None:
    """Return one bounded CSS selector with a field-specific error."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError(f"DOM monitor {name} must be a CSS selector up to 256 chars")
    selector = value.strip()
    try:
        LexborHTMLParser("<a href='/job'>job</a>").css(selector)
    except SelectolaxError as exc:
        raise ValueError(f"DOM monitor {name} is invalid: {selector!r}") from exc
    return selector


_RichRowsConfig = tuple[
    str,
    str | None,
    str,
    str | None,
    str | None,
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    bool,
    tuple[str, str | None] | None,
    tuple[str, str | None] | None,
    frozenset[str],
    frozenset[str],
]


def _validated_rich_rows_boundary(value: object, *, name: str) -> tuple[str, str | None] | None:
    """Validate one authoritative DOM boundary for static rich rows."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"selector", "text"}:
        raise ValueError(f"DOM monitor rich_rows.{name} must contain selector and optional text")
    selector = _validate_css_selector(value.get("selector"), name=f"rich_rows.{name}.selector")
    if selector is None:
        raise ValueError(f"DOM monitor rich_rows.{name} requires selector")
    marker_text = value.get("text")
    if marker_text is not None and (
        not isinstance(marker_text, str)
        or not marker_text.strip()
        or len(marker_text) > 512
        or "\x00" in marker_text
    ):
        raise ValueError(f"DOM monitor rich_rows.{name}.text must be non-empty bounded text")
    return selector, " ".join(marker_text.split()) if marker_text is not None else None


def _validated_rich_rows_urls(
    value: object,
    *,
    name: str,
    allow_empty: bool,
) -> frozenset[str]:
    """Validate one bounded exact-URL side of a rich-row lifecycle partition."""
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"DOM monitor rich_rows.{name} must be a bounded URL list")
    if len(value) > _MAX_RICH_ROWS_LIFECYCLE_URLS:
        raise ValueError(f"DOM monitor rich_rows.{name} must be a bounded URL list")
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > 2_048 or "\x00" in item:
            raise ValueError(f"DOM monitor rich_rows.{name} must contain absolute HTTP URLs")
        parsed = urlparse(item)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"DOM monitor rich_rows.{name} must contain undecorated absolute HTTP URLs"
            )
        urls.append(item)
    if len(urls) != len(set(urls)):
        raise ValueError(f"DOM monitor rich_rows.{name} must not contain duplicate URLs")
    return frozenset(urls)


def _validated_rich_rows(value: object) -> _RichRowsConfig | None:
    """Validate optional static listing-row extraction config."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "row_selector",
        "link_selector",
        "link_attr",
        "title_selector",
        "total_selector",
        "location_selectors",
        "metadata_selectors",
        "allow_missing_locations",
        "section_start",
        "section_end",
        "active_urls",
        "inactive_urls",
    }:
        raise ValueError("DOM monitor rich_rows must be a bounded mapping")
    row_selector = _validate_css_selector(value.get("row_selector"), name="rich_rows.row_selector")
    link_selector = _validate_css_selector(
        value.get("link_selector"), name="rich_rows.link_selector"
    )
    link_attr = value.get("link_attr", "href")
    if (
        not isinstance(link_attr, str)
        or not link_attr.strip()
        or len(link_attr) > 64
        or not re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]*", link_attr.strip())
    ):
        raise ValueError("DOM monitor rich_rows.link_attr must be a valid attribute name")
    link_attr = link_attr.strip()
    title_selector = _validate_css_selector(
        value.get("title_selector"), name="rich_rows.title_selector"
    )
    total_selector = _validate_css_selector(
        value.get("total_selector"), name="rich_rows.total_selector"
    )
    if row_selector is None:
        raise ValueError("DOM monitor rich_rows requires row_selector")
    locations = value.get("location_selectors")
    if locations is None:
        locations = []
    if (
        not isinstance(locations, list)
        or len(locations) > 4
        or not all(isinstance(selector, str) and selector.strip() for selector in locations)
    ):
        raise ValueError("DOM monitor rich_rows.location_selectors must be a bounded list")
    location_selectors = tuple(
        _validate_css_selector(selector, name="rich_rows.location_selectors") or ""
        for selector in locations
    )
    metadata = value.get("metadata_selectors")
    if metadata is None:
        metadata = {}
    if (
        not isinstance(metadata, dict)
        or len(metadata) > 8
        or not all(
            isinstance(field, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", field)
            and isinstance(selector, str)
            and selector.strip()
            for field, selector in metadata.items()
        )
    ):
        raise ValueError("DOM monitor rich_rows.metadata_selectors must be a bounded mapping")
    metadata_selectors = tuple(
        (
            field,
            _validate_css_selector(selector, name="rich_rows.metadata_selectors") or "",
        )
        for field, selector in metadata.items()
    )
    allow_missing_locations = value.get("allow_missing_locations", False)
    if not isinstance(allow_missing_locations, bool):
        raise ValueError("DOM monitor rich_rows.allow_missing_locations must be a boolean")
    section_start = _validated_rich_rows_boundary(value.get("section_start"), name="section_start")
    section_end = _validated_rich_rows_boundary(value.get("section_end"), name="section_end")
    if (section_start is None) != (section_end is None):
        raise ValueError(
            "DOM monitor rich_rows.section_start and section_end must be configured together"
        )
    active_value = value.get("active_urls")
    inactive_value = value.get("inactive_urls")
    if (active_value is None) != (inactive_value is None):
        raise ValueError(
            "DOM monitor rich_rows.active_urls and inactive_urls must be configured together"
        )
    if active_value is None:
        active_urls = frozenset()
        inactive_urls = frozenset()
    else:
        active_urls = _validated_rich_rows_urls(
            active_value,
            name="active_urls",
            allow_empty=False,
        )
        inactive_urls = _validated_rich_rows_urls(
            inactive_value,
            name="inactive_urls",
            allow_empty=True,
        )
        if active_urls & inactive_urls:
            raise ValueError("DOM monitor rich_rows.active_urls and inactive_urls must be disjoint")
    return (
        row_selector,
        link_selector,
        link_attr,
        title_selector,
        total_selector,
        location_selectors,
        metadata_selectors,
        allow_missing_locations,
        section_start,
        section_end,
        active_urls,
        inactive_urls,
    )


def _rows_between_boundaries(tree, rows: list, start, end) -> list:
    """Limit selected rows to nodes strictly between two document markers."""
    if start is None:
        return rows
    ordered = list(tree.root.traverse())
    positions = {node.mem_id: index for index, node in enumerate(ordered)}

    def boundary_positions(boundary, *, after: int = -1) -> list[int]:
        selector, expected_text = boundary
        matches: list[int] = []
        for node in tree.css(selector):
            position = positions.get(node.mem_id)
            if position is None or position <= after:
                continue
            text = " ".join(node.text(separator=" ", strip=True).split())
            if expected_text is None or expected_text.casefold() in text.casefold():
                matches.append(position)
        return matches

    start_positions = boundary_positions(start)
    if not start_positions:
        raise ValueError("DOM monitor rich_rows.section_start did not match the page")
    if len(start_positions) != 1:
        raise ValueError("DOM monitor rich_rows.section_start matched multiple page elements")
    start_position = start_positions[0]
    end_positions = boundary_positions(end, after=start_position)
    if not end_positions:
        raise ValueError("DOM monitor rich_rows.section_end did not match after section_start")
    if len(end_positions) != 1:
        raise ValueError(
            "DOM monitor rich_rows.section_end matched multiple elements after section_start"
        )
    end_position = end_positions[0]
    return [row for row in rows if start_position < positions.get(row.mem_id, -1) < end_position]


def _extract_rich_rows_static(
    html: str,
    base_url: str,
    config: _RichRowsConfig,
    url_matcher: re.Pattern | None,
    *,
    allow_empty: bool = False,
    url_canonicalizer: Callable[[str], str] | None = None,
) -> list[DiscoveredJob]:
    """Extract stable URLs, titles, and joined locations from listing rows."""
    (
        row_selector,
        link_selector,
        link_attr,
        title_selector,
        total_selector,
        location_selectors,
        metadata_selectors,
        allow_missing_locations,
        section_start,
        section_end,
        active_urls,
        inactive_urls,
    ) = config
    tree = LexborHTMLParser(html)
    advertised_total: int | None = None
    if total_selector is not None:
        total_node = tree.css_first(total_selector)
        total_text = (
            re.sub(r"\s+", " ", total_node.text(separator=" ", strip=True)).strip()
            if total_node is not None
            else ""
        )
        if re.fullmatch(r"(?:0|[1-9]\d{0,5})", total_text) is None:
            raise ValueError("DOM monitor rich_rows total_selector omitted an exact job count")
        advertised_total = int(total_text)
        if advertised_total > MAX_URLS:
            raise ValueError(
                f"DOM monitor rich_rows advertised total exceeds the {MAX_URLS} URL cap"
            )
    rows = tree.css(row_selector)
    rows = _rows_between_boundaries(tree, rows, section_start, section_end)
    if not rows:
        if allow_empty and advertised_total in {None, 0}:
            return []
        raise ValueError("DOM monitor rich_rows matched no listing rows")

    jobs_by_url: dict[str, DiscoveredJob] = {}
    for index, row in enumerate(rows):
        link = row.css_first(link_selector) if link_selector is not None else row
        href = link.attributes.get(link_attr) if link is not None else None
        title_node = row.css_first(title_selector) if title_selector is not None else link
        title = title_node.text(separator=" ", strip=True).strip() if title_node is not None else ""
        if not href or not title:
            raise ValueError(f"DOM monitor rich_rows row {index} omitted its link or title")
        url = urljoin(base_url, href)
        if not url.startswith("http"):
            raise ValueError(f"DOM monitor rich_rows row {index} produced an invalid URL")
        if url_matcher is not None and not url_matcher.search(url):
            continue
        canonical_url = url_canonicalizer(url) if url_canonicalizer is not None else url
        if active_urls:
            parsed_url = urlsplit(canonical_url)
            canonical_url = urlunsplit(
                (parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", "")
            )
            if canonical_url in inactive_urls:
                continue
            if canonical_url not in active_urls:
                raise ValueError(
                    "DOM monitor rich_rows encountered an unclassified lifecycle URL: "
                    f"{canonical_url}"
                )

        location_parts: list[str] = []
        for selector in location_selectors:
            node = row.css_first(selector)
            value = node.text(separator=" ", strip=True).strip() if node is not None else ""
            if not value and not allow_missing_locations:
                raise ValueError(
                    f"DOM monitor rich_rows row {index} omitted configured location data"
                )
            if value and value not in location_parts:
                location_parts.append(value)

        metadata: dict[str, str] = {}
        for field, selector in metadata_selectors:
            node = row.css_first(selector)
            value = node.text(separator=" ", strip=True).strip() if node is not None else ""
            if not value:
                raise ValueError(
                    f"DOM monitor rich_rows row {index} omitted configured metadata {field!r}"
                )
            metadata[field] = value
        job = DiscoveredJob(
            url=canonical_url,
            title=title,
            locations=[", ".join(location_parts)] if location_parts else None,
            metadata=metadata or None,
        )
        existing = jobs_by_url.get(canonical_url)
        if existing is not None and existing != job and url_canonicalizer is None:
            raise ValueError(
                "DOM monitor rich_rows produced conflicting rows for one canonical URL: "
                f"{canonical_url}"
            )
        jobs_by_url[canonical_url] = job

    if advertised_total is not None and len(jobs_by_url) != advertised_total:
        raise ValueError(
            "DOM monitor rich_rows accepted "
            f"{len(jobs_by_url)} rows but the page advertised {advertised_total}"
        )
    if not jobs_by_url:
        raise ValueError("DOM monitor rich_rows URL filter excluded every listing row")
    if url_canonicalizer is not None:
        return [jobs_by_url[url] for url in sorted(jobs_by_url)]
    return list(jobs_by_url.values())


async def _paginate_rich_rows_static(
    board_url: str,
    pagination: dict,
    initial_jobs: list[DiscoveredJob],
    client: httpx.AsyncClient,
    rich_rows: _RichRowsConfig,
    url_matcher: re.Pattern | None,
    encoding: str | None,
) -> list[DiscoveredJob]:
    """Fetch and merge strict rich listing rows across static pages."""
    from src.shared.api_sniff import set_url_param
    from src.shared.http_retry import fetch_with_retry

    url_template = pagination.get("url_template")
    param_name = pagination.get("param_name")
    start = pagination.get("start", pagination.get("start_value", 1))
    increment = pagination.get("increment", 1)
    max_pages = min(pagination.get("max_pages", _MAX_PAGINATION_PAGES), _MAX_PAGINATION_PAGES)
    transient_403 = pagination.get("transient_403", False)
    if not isinstance(transient_403, bool):
        raise ValueError("DOM pagination transient_403 must be a boolean")
    if pagination.get("browser") or pagination.get("partition_selector"):
        raise ValueError("DOM monitor rich_rows pagination supports static sequential pages only")
    if not url_template and not isinstance(param_name, str):
        raise ValueError("DOM pagination requires param_name or url_template")

    jobs_by_url = {job.url: job for job in initial_jobs}
    value = start + increment

    for page_num in range(2, max_pages + 1):
        if url_template:
            page_url = url_template.format(page=value)
        else:
            assert isinstance(param_name, str)
            page_url = set_url_param(board_url, param_name, value)

        html = await fetch_with_retry(
            client,
            page_url,
            encoding=encoding,
            transient_403=transient_403,
            max_chars=None,
        )
        if not html:
            log.info("dom.pagination.end", page=page_num, url=page_url)
            break

        _raise_if_bot_challenge(page_url, html)
        page_jobs = _extract_rich_rows_static(
            html,
            page_url,
            rich_rows,
            url_matcher,
            allow_empty=True,
        )
        added = 0
        for job in page_jobs:
            if job.url in jobs_by_url:
                continue
            jobs_by_url[job.url] = job
            added += 1
        if not added:
            log.info("dom.pagination.no_new_urls", page=page_num)
            break

        log.debug("dom.pagination.page", page=page_num, new=added, total=len(jobs_by_url))
        value += increment

    return list(jobs_by_url.values())


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
    request_headers: dict | None = None,
    public_headers: bool = False,
    request_semaphore: asyncio.Semaphore | None = None,
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

        if public_headers and not same_origin(page_url, board_url):
            raise ValueError(f"DOM pagination refused a cross-origin page URL: {page_url}")

        if use_browser:
            html = await _fetch_via_page(
                page,
                page_url,
                transient_403=transient_403,
            )
        else:
            if request_semaphore is None:
                html = await fetch_with_retry(
                    client,
                    page_url,
                    encoding=encoding,
                    transient_403=transient_403,
                    headers=request_headers,
                    public_headers=public_headers,
                )
            else:
                async with request_semaphore:
                    html = await fetch_with_retry(
                        client,
                        page_url,
                        encoding=encoding,
                        transient_403=transient_403,
                        headers=request_headers,
                        public_headers=public_headers,
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


async def _paginate_partitioned_urls(
    board_url: str,
    initial_html: str,
    pagination: dict,
    client: httpx.AsyncClient,
    url_matcher: re.Pattern | None,
    url_transform: dict | None,
    encoding: str | None,
    link_selector: str | None,
    public_request_headers: dict[str, str] | None = None,
) -> set[str]:
    """Paginate every bounded facet partition advertised by a listing.

    Cegid Talentsoft exposes at most 1,000 results from an unfiltered search,
    even when its result counter reports several thousand vacancies. Boards
    can opt into this helper with
    ``pagination.partition_selector`` so gone detection receives the union of
    every partition instead of a silently truncated first 1,000 URLs.
    Oversized primary facets can be split once more with
    ``partition_fallback_selector`` and ``partition_result_limit``.
    ``partition_drop_params`` removes state-changing query flags before page
    numbers are appended, while ``partition_stateless`` suppresses cookies so
    concurrent ASP.NET facet requests do not serialize on one server session.

    A missing selector, empty partition, or cross-origin facet fails the whole
    monitor cycle. Accepting a partial partition union would otherwise
    tombstone every job in the missing slice.
    """
    from src.shared.http_retry import PaginationFetchError, fetch_with_retry

    partition_selector = _validate_link_selector(pagination.get("partition_selector"))
    if partition_selector is None:
        raise ValueError("DOM partitioned pagination requires partition_selector")

    drop_params = pagination.get("partition_drop_params", [])
    if (
        not isinstance(drop_params, list)
        or len(drop_params) > 16
        or any(not isinstance(param, str) or not param for param in drop_params)
    ):
        raise ValueError("DOM partition_drop_params must be a list of at most 16 names")

    def canonicalize_partition_url(url: str, inherited_url: str = board_url) -> str:
        parts = urlsplit(url)
        inherited_parts = urlsplit(inherited_url)
        current_pairs = parse_qsl(parts.query, keep_blank_values=True)
        current_names = {name for name, _ in current_pairs}
        inherited_pairs = [
            (name, value)
            for name, value in parse_qsl(inherited_parts.query, keep_blank_values=True)
            if name not in current_names
        ]
        retained = [
            (name, value)
            for name, value in inherited_pairs + current_pairs
            if name not in drop_params
        ]
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(retained, doseq=True),
                parts.fragment,
            )
        )

    partition_urls = sorted(
        {
            canonicalize_partition_url(url)
            for url in _extract_links_static(
                initial_html,
                board_url,
                link_selector=partition_selector,
            )
        }
    )
    if not partition_urls:
        raise ValueError("DOM partitioned pagination found no partition links")
    if len(partition_urls) > _MAX_PAGINATION_PARTITIONS:
        raise ValueError(
            "DOM partitioned pagination found too many partitions "
            f"({len(partition_urls)} > {_MAX_PAGINATION_PARTITIONS})"
        )

    board_origin = urlsplit(board_url)[:2]
    if any(urlsplit(url)[:2] != board_origin for url in partition_urls):
        raise ValueError("DOM partitioned pagination requires same-origin partition links")

    transient_403 = pagination.get("transient_403", False)
    partition_stateless = pagination.get("partition_stateless", False)
    if not isinstance(partition_stateless, bool):
        raise ValueError("DOM partition_stateless must be a boolean")
    fallback_selector_raw = pagination.get("partition_fallback_selector")
    fallback_selector = (
        _validate_link_selector(fallback_selector_raw)
        if fallback_selector_raw is not None
        else None
    )
    count_regex_raw = pagination.get("partition_count_regex")
    if count_regex_raw is not None and not isinstance(count_regex_raw, str):
        raise ValueError("DOM partition_count_regex must be a string")
    count_regex = re.compile(count_regex_raw, re.IGNORECASE) if count_regex_raw else None
    result_limit = pagination.get("partition_result_limit")
    if result_limit is not None and (not isinstance(result_limit, int) or result_limit < 1):
        raise ValueError("DOM partition_result_limit must be a positive integer")
    validate_total = pagination.get("partition_validate_total", False)
    if not isinstance(validate_total, bool):
        raise ValueError("DOM partition_validate_total must be a boolean")
    if fallback_selector is not None and (count_regex is None or result_limit is None):
        raise ValueError(
            "DOM partition fallback requires partition_count_regex and partition_result_limit"
        )
    if validate_total and count_regex is None:
        raise ValueError("DOM partition_validate_total requires partition_count_regex")

    request_headers = dict(public_request_headers or {})
    if partition_stateless:
        request_headers["Cookie"] = ""
    request_headers = request_headers or None
    semaphore = asyncio.Semaphore(_PAGINATION_PARTITION_CONCURRENCY)

    def extract_count(html: str, url: str) -> int:
        assert count_regex is not None
        tree = LexborHTMLParser(html)
        title = tree.css_first("title")
        match = count_regex.search(title.text(strip=True) if title is not None else "")
        if match is None:
            raise ValueError(f"DOM partition count not found: {url}")
        return int(match.group(1))

    async def fetch_partition(partition_url: str) -> tuple[str, set[str], int | None]:
        async with semaphore:
            html = await fetch_with_retry(
                client,
                partition_url,
                encoding=encoding,
                transient_403=transient_403,
                headers=request_headers,
                public_headers=bool(public_request_headers),
            )
        if not html:
            raise PaginationFetchError(
                partition_url,
                attempts=1,
                last_error="empty partition",
            )
        _raise_if_bot_challenge(partition_url, html)
        initial_urls = _extract_links_static(
            html,
            partition_url,
            url_matcher,
            link_selector,
        )
        if not initial_urls:
            raise ValueError(f"DOM partition contains no job links: {partition_url}")
        count = extract_count(html, partition_url) if count_regex is not None else None
        return html, initial_urls, count

    async def gather_cancel_on_error(tasks):
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    primary_tasks = [asyncio.create_task(fetch_partition(url)) for url in partition_urls]
    primary_results = await gather_cancel_on_error(primary_tasks)

    if validate_total:
        expected_total = extract_count(initial_html, board_url)
        primary_total = sum(count or 0 for _, _, count in primary_results)
        if primary_total != expected_total:
            raise ValueError(
                "DOM primary partition counts do not match listing total "
                f"({primary_total} != {expected_total})"
            )
    else:
        expected_total = None

    expanded: list[tuple[str, str, set[str], int | None]] = []
    for primary_index, (partition_url, (html, initial_urls, count)) in enumerate(
        zip(partition_urls, primary_results, strict=True)
    ):
        if result_limit is None or count is None or count <= result_limit:
            expanded.append((partition_url, html, initial_urls, count))
            continue
        if fallback_selector is None:
            raise ValueError(
                f"DOM partition exceeds result limit ({count} > {result_limit}): {partition_url}"
            )

        child_urls = sorted(
            {
                canonicalize_partition_url(url, partition_url)
                for url in _extract_links_static(
                    html,
                    partition_url,
                    link_selector=fallback_selector,
                )
            }
        )
        if not child_urls:
            raise ValueError(f"DOM oversized partition has no fallback links: {partition_url}")
        if len(child_urls) > _MAX_PAGINATION_PARTITIONS:
            raise ValueError(
                "DOM partition fallback found too many links "
                f"({len(child_urls)} > {_MAX_PAGINATION_PARTITIONS})"
            )
        if any(urlsplit(url)[:2] != board_origin for url in child_urls):
            raise ValueError("DOM partition fallback requires same-origin links")
        remaining_primary = len(partition_urls) - primary_index - 1
        minimum_expanded_total = len(expanded) + len(child_urls) + remaining_primary
        if minimum_expanded_total > _MAX_PAGINATION_PARTITIONS:
            raise ValueError(
                "DOM partition fallback would exceed the global partition limit "
                f"({minimum_expanded_total} > {_MAX_PAGINATION_PARTITIONS})"
            )

        child_tasks = [asyncio.create_task(fetch_partition(url)) for url in child_urls]
        child_results = await gather_cancel_on_error(child_tasks)
        child_total = sum(child_count or 0 for _, _, child_count in child_results)
        if child_total != count:
            raise ValueError(
                "DOM fallback partition counts do not match parent total "
                f"({child_total} != {count}): {partition_url}"
            )
        for child_url, (child_html, child_initial_urls, child_count) in zip(
            child_urls, child_results, strict=True
        ):
            if child_count is not None and child_count > result_limit:
                raise ValueError(
                    "DOM fallback partition still exceeds result limit "
                    f"({child_count} > {result_limit}): {child_url}"
                )
            expanded.append((child_url, child_html, child_initial_urls, child_count))

    if len(expanded) > _MAX_PAGINATION_PARTITIONS:
        raise ValueError(
            "DOM partition expansion found too many partitions "
            f"({len(expanded)} > {_MAX_PAGINATION_PARTITIONS})"
        )

    async def paginate_partition(
        partition_url: str,
        initial_urls: set[str],
    ) -> set[str]:
        return await _paginate_urls(
            partition_url,
            pagination,
            initial_urls,
            client,
            url_matcher=url_matcher,
            url_transform=url_transform,
            encoding=encoding,
            link_selector=link_selector,
            request_headers=request_headers,
            public_headers=bool(public_request_headers),
            request_semaphore=semaphore,
        )

    tasks = [
        asyncio.create_task(paginate_partition(url, initial_urls))
        for url, _, initial_urls, _ in expanded
    ]
    partition_results = await gather_cancel_on_error(tasks)

    all_urls: set[str] = set()
    for partition_num, (expanded_partition, partition_urls_found) in enumerate(
        zip(expanded, partition_results, strict=True), start=1
    ):
        partition_url, _, _, partition_count = expanded_partition
        if partition_count is not None and len(partition_urls_found) != partition_count:
            raise ValueError(
                "DOM partition URL count does not match advertised count "
                f"({len(partition_urls_found)} != {partition_count}): {partition_url}"
            )
        all_urls.update(partition_urls_found)
        log.debug(
            "dom.pagination.partition",
            partition=partition_num,
            partitions=len(expanded),
            partition_urls=len(partition_urls_found),
            total=len(all_urls),
        )

    if expected_total is not None and len(all_urls) < expected_total:
        log.info(
            "dom.pagination.partition_deduplicated",
            advertised=expected_total,
            unique_urls=len(all_urls),
            duplicates=expected_total - len(all_urls),
        )

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

    dualoo = _dualoo_probe_config(html, url)
    if dualoo is not None:
        return dualoo

    lucca = _lucca_probe_config(html, url)
    if lucca is not None:
        return lucca

    prospective = _prospective_probe_config(html, url)
    if prospective is not None:
        return prospective

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
) -> set[str] | list[DiscoveredJob] | MonitorResult:
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

    prospective_board = metadata.get("prospective_board")
    if prospective_board is not None and (
        not isinstance(prospective_board, str)
        or re.fullmatch(r"[1-9]\d{0,11}", prospective_board) is None
    ):
        raise ValueError("DOM monitor prospective_board must be a valid medium identity")
    prospective_canonical_path = _validated_prospective_canonical_path(
        metadata.get("prospective_canonical_path")
    )
    if prospective_canonical_path is not None and prospective_board is None:
        raise ValueError("DOM monitor prospective_canonical_path requires prospective_board")

    request_headers = validated_public_request_headers(
        metadata.get("request_headers"), owner="DOM monitor"
    )

    render = metadata.get("render", False)
    if render and request_headers:
        raise ValueError("DOM monitor request_headers are supported only when render=false")
    actions = metadata.get("actions")
    pagination = metadata.get("pagination")
    url_matcher = _build_url_matcher(metadata.get("url_filter"))
    url_transform = metadata.get("url_transform")
    link_selector = _validate_link_selector(metadata.get("link_selector"))
    empty_selector = _validate_css_selector(metadata.get("empty_selector"), name="empty_selector")
    empty_states = _validated_empty_state_list(metadata.get("empty_states"))
    empty_text = metadata.get("empty_text")
    if empty_text is not None and (
        not isinstance(empty_text, str)
        or not empty_text.strip()
        or len(empty_text) > 256
        or "\x00" in empty_text
    ):
        raise ValueError("DOM monitor empty_text must be non-empty text up to 256 chars")
    if isinstance(empty_text, str):
        empty_text = empty_text.strip()
    if empty_states and (empty_selector is not None or empty_text is not None):
        raise ValueError(
            "DOM monitor empty_states cannot be combined with empty_selector or empty_text"
        )
    configured_empty_states = empty_states
    if empty_selector is not None:
        configured_empty_states = ((empty_selector, empty_text, False, None, None, None),)
    empty_state_name = "empty_states" if empty_states else "empty_selector"
    rich_rows = _validated_rich_rows(metadata.get("rich_rows"))
    if rich_rows is not None and rich_rows[4] is not None and pagination:
        raise ValueError(
            "DOM monitor rich_rows total_selector supports single-page extraction only"
        )
    if prospective_board is not None and (
        render
        or rich_rows is None
        or rich_rows[4] is None
        or not configured_empty_states
        or pagination
    ):
        raise ValueError(
            "DOM monitor Prospective preset requires static single-page rich rows with "
            "an exact total and explicit zero proof"
        )
    require_jsonld_jobposting = metadata.get("require_jsonld_jobposting", False)
    if not isinstance(require_jsonld_jobposting, bool):
        raise ValueError("DOM monitor require_jsonld_jobposting must be a boolean")
    require_unexpired_pdf = _validated_unexpired_pdf_config(metadata.get("require_unexpired_pdf"))
    require_pdf_text = _validated_pdf_text_config(metadata.get("require_pdf_text"))
    exclude_detail_selector = _validate_css_selector(
        metadata.get("exclude_detail_selector"),
        name="exclude_detail_selector",
    )
    fingerprint_response = _validated_response_fingerprint_config(
        metadata.get("fingerprint_response")
    )
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

    if rich_rows is not None and (
        render
        or metadata.get("include_board_url")
        or require_jsonld_jobposting
        or require_unexpired_pdf is not None
        or require_pdf_text is not None
        or exclude_detail_selector is not None
        or fingerprint_response is not None
    ):
        raise ValueError("DOM monitor rich_rows supports static listing extraction only")

    if configured_empty_states:
        if link_selector is None and rich_rows is None:
            raise ValueError(f"DOM monitor {empty_state_name} requires link_selector or rich_rows")
        if pagination or metadata.get("include_board_url") or require_jsonld_jobposting:
            raise ValueError(f"DOM monitor {empty_state_name} supports single-page extraction only")
        if encoding is not None:
            raise ValueError(
                f"DOM monitor {empty_state_name} does not support an encoding override"
            )
    elif empty_text is not None:
        raise ValueError("DOM monitor empty_text requires empty_selector")

    if render:
        combined = {**metadata, "_board_url": board_url}

        if pw is not None:
            async with open_page(pw, combined, use_proxy=bool(metadata.get("proxy"))) as page:
                urls = await _extract_links_rendered(page, combined, url_matcher)
                if configured_empty_states:
                    _validate_explicit_empty_states(
                        await safe_content(page), configured_empty_states, urls, board_url
                    )
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
                if configured_empty_states:
                    _validate_explicit_empty_states(
                        await safe_content(page), configured_empty_states, urls, board_url
                    )
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
        if configured_empty_states:
            from src.shared.http_retry import fetch_text_page_with_retry

            # The empty marker is authoritative and may follow large inline
            # assets, but the body still needs a finite streaming cap before
            # it is handed to the HTML parser.
            html = await fetch_text_page_with_retry(
                client,
                board_url,
                headers=request_headers or None,
                public_headers=bool(request_headers),
                retryable_statuses={202, 401, 403},
                require_nonempty=True,
                max_bytes=_MAX_EXPLICIT_EMPTY_BODY_BYTES,
            )
        else:
            from src.shared.http_retry import fetch_with_retry

            html = await fetch_with_retry(
                client,
                board_url,
                headers=request_headers or None,
                public_headers=bool(request_headers),
                transient_403=True,
                retryable_statuses={202},
                encoding=encoding,
                # Rich rows are authoritative structured input. The shared
                # 500k listing-preview limit can cut a complete trailing row
                # and make a partial inventory look healthy.
                max_chars=None if rich_rows is not None else 500_000,
            )
        if not html:
            log.warning("dom.fetch_failed", board_url=board_url)
            if configured_empty_states:
                raise ValueError(
                    "DOM monitor found no job links and did not match the configured explicit "
                    "empty state"
                )
            return set()
        _raise_if_bot_challenge(board_url, html)
        if prospective_board is not None:
            detected_medium = _prospective_provider_medium(html, board_url)
            if detected_medium is None:
                raise ValueError(
                    "DOM monitor Prospective provider identity proof is missing or ambiguous"
                )
            if detected_medium != prospective_board:
                raise ValueError(
                    "DOM monitor configured Prospective medium does not match listing assets"
                )
            if (
                prospective_canonical_path is None
                and LexborHTMLParser(html).css_first("#jobs-list .job a.job-title[href]")
                is not None
            ):
                raise ValueError(
                    "DOM monitor Prospective positive inventory requires prospective_canonical_path"
                )
        if rich_rows is not None:
            jobs = _extract_rich_rows_static(
                html,
                board_url,
                rich_rows,
                url_matcher,
                allow_empty=bool(configured_empty_states),
                url_canonicalizer=(
                    None
                    if prospective_board is None or prospective_canonical_path is None
                    else lambda job_url: _canonicalize_prospective_job_url(
                        job_url,
                        board_url,
                        prospective_canonical_path,
                    )
                ),
            )
            if configured_empty_states:
                _validate_explicit_empty_states(
                    html,
                    configured_empty_states,
                    {job.url for job in jobs},
                    board_url,
                )
            if pagination:
                jobs = await _paginate_rich_rows_static(
                    board_url,
                    pagination,
                    jobs,
                    client,
                    rich_rows,
                    url_matcher,
                    encoding,
                )
            log.info("dom.complete", board_url=board_url, urls_found=len(jobs), render=False)
            if len(jobs) > MAX_URLS:
                log.warning("dom.truncated", total=len(jobs), cap=MAX_URLS)
                return truncated_rich_result(jobs)
            return jobs
        urls = _extract_links_static(html, board_url, url_matcher, link_selector)
        if configured_empty_states:
            _validate_explicit_empty_states(html, configured_empty_states, urls, board_url)
        if pagination:
            if pagination.get("partition_selector"):
                urls = await _paginate_partitioned_urls(
                    board_url,
                    html,
                    pagination,
                    client,
                    url_matcher,
                    url_transform,
                    encoding,
                    link_selector,
                    request_headers or None,
                )
            else:
                urls = await _paginate_urls(
                    board_url,
                    pagination,
                    urls,
                    client,
                    url_matcher=url_matcher,
                    url_transform=url_transform,
                    encoding=encoding,
                    link_selector=link_selector,
                    request_headers=request_headers or None,
                    public_headers=bool(request_headers),
                )

    # Exclude the board URL itself by default — it is normally the listing
    # page, not a job. Direct document boards opt in after the successful
    # fetch above so the source URL is emitted as their one job URL.
    urls = _without_board_self_urls(urls, board_url)
    if metadata.get("include_board_url"):
        urls.add(board_url)

    if require_jsonld_jobposting:
        urls = await _filter_jsonld_job_urls(urls, client)
    if require_unexpired_pdf is not None:
        currentness_candidates = set(urls)
        filtered_urls, classified_currentness = cast(
            tuple[set[str], dict[str, str]],
            await _filter_unexpired_pdf_urls(
                urls,
                client,
                require_unexpired_pdf,
                return_classified_currentness=True,
                request_headers=request_headers or None,
                allowed_origin_url=board_url,
            ),
        )
        urls = filtered_urls
        verified_currentness_empty = (
            bool(currentness_candidates)
            and not urls
            and (set(classified_currentness) == currentness_candidates)
        )
    else:
        verified_currentness_empty = False
    if require_pdf_text is not None:
        urls = await _filter_pdf_text_urls(
            urls,
            client,
            require_pdf_text,
            request_headers=request_headers or None,
            allowed_origin_url=board_url,
        )
    if fingerprint_response is not None:
        urls = await _fingerprint_response_urls(
            urls,
            client,
            fingerprint_response,
            request_headers=request_headers or None,
            allowed_origin_url=board_url,
        )

    if exclude_detail_selector is not None:
        urls = await _exclude_urls_matching_detail_selector(
            urls,
            exclude_detail_selector,
            client,
        )

    if len(urls) > MAX_URLS:
        log.warning("dom.truncated", total=len(urls), cap=MAX_URLS)
        urls = set(sorted(urls)[:MAX_URLS])

    log.info("dom.complete", board_url=board_url, urls_found=len(urls), render=render)
    if verified_currentness_empty:
        from src.core.monitor import MonitorResult

        return MonitorResult(
            urls=set(),
            verified_empty_reason=(
                "all discovered PDF jobs are outside their verified active period"
            ),
        )
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
