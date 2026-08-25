"""API sniffer monitor.

Discovers job listings by capturing XHR/fetch requests that career pages make
to internal APIs.  Works for React SPAs, custom platforms, and any site that
loads job data via JSON APIs.

Supports two modes:

- **Rich mode** (``fields`` configured): returns ``list[DiscoveredJob]``
- **URL-only mode** (no ``fields``): returns ``set[str]``

When replaying from stored config (``api_url`` present), opens the page to
establish cookies/auth context, then replays the API via in-browser fetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
from collections.abc import Callable
from math import ceil
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, NamedTuple, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.raw import save_json_response

try:
    from src.metrics import api_sniffer_fallback_failed_total
except ImportError:
    # The slim ``jobseek-crawler-setup`` (ws CLI) wheel does not ship
    # ``src/metrics.py``. Fall back to a no-op counter so this module
    # stays importable from the ws install (which never scrapes
    # Prometheus anyway).
    class _NoopCounter:
        def labels(self, **_kwargs):
            return self

        def inc(self, *_args, **_kwargs):
            pass

    api_sniffer_fallback_failed_total = _NoopCounter()  # type: ignore[assignment]

from src.shared.api_sniff import (
    JOB_KEYWORDS,
    TITLE_FIELDS,
    ApiSnifferDomUnavailableError,
    _fetch_page_with_retry,
    auto_map_fields,
    capture_exchanges,
    clean_headers,
    detect_cms,
    detect_job_list,
    extract_items,
    extract_urls,
    extract_urls_via_dom_crossref,
    find_arrays,
    find_total_count,
    find_url_field,
    infer_pagination,
    make_browser_fetcher,
    make_http_fetcher,
    paginate_all,
    scan_page_scripts,
    set_body_param,
    set_url_param,
    trigger_interactions,
)
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_text_page_with_retry,
    is_retryable_status,
)
from src.shared.nextdata import extract_field, resolve_path
from src.shared.slug import slugify
from src.shared.truncation import truncated_rich_result, truncated_url_result

if TYPE_CHECKING:
    # httpx is imported at runtime above (used in http_fetch_with_retry).
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

_MAX_REFRESH_FIELDS = 16
_MAX_REFRESH_PATTERN_CHARS = 4_096
_MAX_REFRESH_VALUE_CHARS = 16_384
_MAX_REFRESH_PAGE_BYTES = 2_000_000
_MAX_ITEM_FILTER_FIELDS = 16
_MAX_ITEM_FILTER_VALUES = 100
_MAX_REQUIRED_PDF_PATTERN_CHARS = 1_024


class _DedupePreference(NamedTuple):
    path: str
    preferred_values: tuple[str, ...]
    fallback_by: tuple[str, ...]


class _PaginationConvergence(NamedTuple):
    max_passes: int
    required_no_growth_passes: int
    identity_paths: tuple[str, ...]
    stable_fields: tuple[str, ...]
    reject_duplicate_identities: bool


class _UrlFieldMatch(NamedTuple):
    pattern: re.Pattern[str]
    fields: tuple[tuple[str, str], ...]


MAX_ITEMS = 10_000


class ApiSnifferFallbackError(RuntimeError):
    """Raised when every replay path in ``_discover_replay`` has failed.

    Propagates up through ``monitor_one`` → ``_process_one_board_streaming``
    so the board-level ``_RECORD_FAILURE`` runs, incrementing
    ``consecutive_failures`` and auto-disabling at 5.  Without this,
    a persistently-broken sniffer board (e.g. expired CSOD JWT, geo-locked
    DiDi, CSRF-protected Workday) would silently log an empty check each
    cycle and never trip the disable threshold.
    """

    def __init__(self, message: str, *, board_url: str, api_url: str) -> None:
        super().__init__(message)
        self.board_url = board_url
        self.api_url = api_url


# ---------------------------------------------------------------------------
# AES response decryption
# ---------------------------------------------------------------------------


def _decrypt_aes_cbc(ciphertext: str, key: str, iv_mode: str = "suffix") -> object:
    """Decrypt an AES-CBC encrypted string and return parsed JSON.

    *iv_mode* controls how the IV is derived:
    - ``"suffix"`` (default): last 16 chars of *ciphertext* are the IV
      (as UTF-8 bytes), remainder is the base64-encoded ciphertext.
    - ``"fixed:<iv>"``: use a fixed IV string.

    Returns the parsed JSON object, or *None* on failure.
    """
    import base64

    try:
        from Crypto.Cipher import AES as _AES
        from Crypto.Util.Padding import unpad
    except ImportError:
        from Cryptodome.Cipher import AES as _AES
        from Cryptodome.Util.Padding import unpad

    key_bytes = key.encode("utf-8")

    if iv_mode == "suffix":
        iv_bytes = ciphertext[-16:].encode("utf-8")
        ct_b64 = ciphertext[:-16]
    elif iv_mode.startswith("fixed:"):
        iv_bytes = iv_mode[6:].encode("utf-8")
        ct_b64 = ciphertext
    else:
        return None

    decoded = base64.b64decode(ct_b64)
    cipher = _AES.new(key_bytes, _AES.MODE_CBC, iv_bytes)
    decrypted = unpad(cipher.decrypt(decoded), _AES.block_size)
    return json.loads(" " + decrypted.decode("utf-8"))


def _apply_response_decrypt(data: object, decrypt_cfg: dict) -> object:
    """Decrypt the ``Data`` field of *data* if *decrypt_cfg* is present.

    *decrypt_cfg* must contain ``key`` and optionally ``iv_mode`` (default
    ``"suffix"``).  The decrypted content replaces ``Data`` in the returned dict.
    """
    if not isinstance(data, dict):
        return data
    encrypted = data.get("Data")
    if not encrypted or not isinstance(encrypted, str):
        return data
    key = decrypt_cfg["key"]
    iv_mode = decrypt_cfg.get("iv_mode", "suffix")
    try:
        decrypted = _decrypt_aes_cbc(encrypted, key, iv_mode)
        return {**data, "Data": decrypted}
    except Exception:
        log.debug("api_sniffer.decrypt_failed", key_len=len(key))
        return data


MAX_PAGES = 50
_HTTP_MAX_PAGES = 200  # higher limit for plain httpx (no Playwright overhead)

# Defaults for Playwright navigation — configurable via monitor_config
_DEFAULT_WAIT = "load"
_DEFAULT_TIMEOUT = 20_000
_DEFAULT_SETTLE = 3  # seconds to wait after navigation for XHRs to complete

_PROSPECTIVE_MEDIUM_RE = re.compile(r"/careercenter/(?P<medium_id>\d+)(?:/|[?'\"])")
_HTML_LANG_RE = re.compile(r"<html[^>]+\blang=[\"'](?P<lang>[a-z]{2})(?:[-_][A-Z]{2})?[\"']", re.I)
_PROSPECTIVE_HOST = "ohws.prospective.ch"
_PROSPECTIVE_CAREERCENTER_PATH = re.compile(r"^/public/v[12]/careercenter/(?P<medium_id>\d+)/?$")
_PROSPECTIVE_PAGE_SIZE = 100
_PROSPECTIVE_DETECTION_RETRIES = 5
_PROSPECTIVE_DETECTION_BASE_DELAY = 0.5
_LUMESSE_API_PATH = "/fo/rest/jobs"
_LUMESSE_BOARD_PATH_RE = re.compile(r"/lumesse_jobsearch\.html/?$", re.I)


def _lumesse_config_overrides(
    board_url: str,
    api_url: str,
    items: list[dict],
    response: object,
) -> dict | None:
    """Return rich-field overrides for Lumesse TalentLink list payloads.

    TalentLink's public list response already contains the complete vacancy
    description, but it nests the title and location under ``jobFields`` and
    represents the description as titled ``customFields`` sections. The
    generic scalar auto-mapper therefore sees only an application URL and
    incorrectly leaves the monitor in URL-only mode.

    Keep this preset narrowly gated by both canonical endpoint shapes and the
    provider-specific payload schema. This avoids treating unrelated APIs
    with a coincidental ``customFields`` key as Lumesse boards.
    """
    if not items or not isinstance(response, dict):
        return None

    try:
        parsed_board = urlparse(board_url)
        parsed_api = urlparse(api_url)
        board_port = parsed_board.port
        api_port = parsed_api.port
    except ValueError:
        return None

    api_host = (parsed_api.hostname or "").casefold()
    if (
        parsed_board.scheme.casefold() != "https"
        or parsed_api.scheme.casefold() != "https"
        or parsed_board.hostname is None
        or not api_host.endswith(".recruitmentplatform.com")
        or parsed_board.username is not None
        or parsed_board.password is not None
        or parsed_api.username is not None
        or parsed_api.password is not None
        or board_port not in (None, 443)
        or api_port not in (None, 443)
        or _LUMESSE_BOARD_PATH_RE.fullmatch(parsed_board.path) is None
        or parsed_api.path.rstrip("/") != _LUMESSE_API_PATH
    ):
        return None

    for item in items[:5]:
        job_fields = item.get("jobFields")
        custom_fields = item.get("customFields")
        if (
            not isinstance(item.get("id"), (int, str))
            or not isinstance(job_fields, dict)
            or not isinstance(job_fields.get("jobTitle") or job_fields.get("SJOBTITLE"), str)
            or not isinstance(custom_fields, list)
            or not any(
                isinstance(section, dict)
                and isinstance(section.get("title"), str)
                and isinstance(section.get("content"), str)
                for section in custom_fields
            )
        ):
            return None

    globals_obj = response.get("globals")
    total = globals_obj.get("jobsCount") if isinstance(globals_obj, dict) else None
    detail_url = urljoin(board_url, "lumesse_jobdescription.html?jobId={id}")
    overrides: dict = {
        "browser": False,
        "url_template": detail_url,
        "total_path": "globals.jobsCount",
        "fields": {
            "title": "jobFields.jobTitle || jobFields.SJOBTITLE",
            "description": {
                "concat": [
                    {
                        "each": "customFields[*]",
                        "wrap": "<h3>{title}</h3>\n{content}",
                    }
                ],
                "separator": "\n\n",
            },
            "locations": "jobFields.FFIELD008_001 || jobFields.SLOVLIST2",
            "metadata.ats_job_id": "id",
            "metadata.job_number": "jobFields.jobNumber",
            "metadata.external_job_number": "jobFields.externalJobNumber",
            "metadata.scope": "jobFields.SLOVLIST7",
            "metadata.apply_url": "jobFields.applicationUrl",
        },
    }
    if isinstance(total, (int, float)):
        overrides["total"] = int(total)
    return overrides


async def _detect_prospective_config(
    url: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """Detect a Prospective CareerCenter and return a direct API config.

    Prospective supports branded career sites whose public page is server-rendered
    and whose JSON listing request therefore never appears in the XHR capture used
    by the generic sniffer.  The page still embeds a stable ``careercenter/<id>``
    asset path.  Resolve that identifier to Prospective's public ``medium`` API so
    scheduled runs use the authoritative JSON feed instead of the branded page,
    which may be protected by a WAF.
    """
    try:
        html = await fetch_text_page_with_retry(
            client,
            url,
            # Branded CareerCenter hosts can emit short 403/503 bursts while
            # their public medium API remains healthy. Detection is a bounded
            # setup-time request, so give those bursts enough time to recover
            # before falling through to the browser-based generic sniffer.
            retries=_PROSPECTIVE_DETECTION_RETRIES,
            base_delay=_PROSPECTIVE_DETECTION_BASE_DELAY,
            retryable_statuses={403},
            require_nonempty=True,
            max_chars=250_000,
            log_event="api_sniffer.prospective_page_backoff",
        )
    except PaginationFetchError:
        return None
    if not html:
        return None

    medium_match = _PROSPECTIVE_MEDIUM_RE.search(html)
    if medium_match is None:
        return None

    medium_id = medium_match.group("medium_id")
    lang_match = _HTML_LANG_RE.search(html)
    lang = lang_match.group("lang").lower() if lang_match else "de"
    api_url = f"https://ohws.prospective.ch/public/v1/medium/{medium_id}/jobs"
    params = {"lang": lang, "offset": "0", "limit": "12"}

    try:
        payload = await http_fetch_with_retry(
            client,
            "GET",
            _merge_params(api_url, params),
        )
    except PaginationFetchError:
        return None
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    if str(payload.get("medium_id")) != medium_id or not isinstance(jobs, list):
        return None

    origins: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("links"), dict):
            return None
        directlink = job["links"].get("directlink")
        if not isinstance(directlink, str):
            return None
        try:
            parsed_link = urlparse(directlink)
            port = parsed_link.port
        except ValueError:
            return None
        if (
            parsed_link.scheme.lower() != "https"
            or parsed_link.hostname is None
            or parsed_link.username is not None
            or parsed_link.password is not None
            or port not in (None, 443)
        ):
            return None
        origins.add(f"https://{parsed_link.netloc.lower()}")

    parsed_board = urlparse(url)
    try:
        board_port = parsed_board.port
    except ValueError:
        return None
    if (
        parsed_board.scheme.lower() != "https"
        or parsed_board.hostname is None
        or parsed_board.username is not None
        or parsed_board.password is not None
        or board_port not in (None, 443)
    ):
        return None
    board_origin = f"https://{parsed_board.netloc.lower()}"
    if origins and origins != {board_origin}:
        return None
    canonical_origin = board_origin

    return {
        "api_url": api_url,
        "method": "GET",
        "json_path": "jobs",
        "total_path": "total",
        "url_field": "links.directlink",
        "url_filter": rf"(?i)^{re.escape(canonical_origin)}/",
        "params": params,
        "pagination": {
            "param_name": "offset",
            "style": "offset",
            "start_value": 0,
            "increment": 12,
            "location": "query",
        },
        "fields": {
            "title": "title",
            "description": [
                "szas.sza_introduction",
                "szas.sza_tasks",
                "szas.sza_requirements",
            ],
            "locations": 'szas."sza_location.city"',
            "employment_type": {
                "path": "szas.sza_employment_type",
                "map": {
                    "Festanstellung": "full_time",
                    "Temporäre Anstellung": "temporary",
                    "Befristet": "temporary",
                    "Emploi fixe": "full_time",
                    "Emploi temporaire": "temporary",
                    "Impiego fisso": "full_time",
                    "Impiego temporaneo": "temporary",
                },
            },
            "date_posted": "start_date",
            "base_salary": "szas.sza_salary",
            "responsibilities": "szas.sza_tasks",
            "qualifications": "szas.sza_requirements",
            "valid_through": "end_date",
            "metadata.language": "language",
            "metadata.ats_job_id": "id",
            "metadata.apply_link": "szas.sza_apply_link",
        },
        "items": len(jobs),
        "total": payload.get("total"),
        "score": 100,
    }


def _materially_below_advertised_total(discovered: int, total: int | None) -> bool:
    """Return whether a trustworthy API total proves discovery incomplete."""
    if not total or total <= 0 or discovered >= total:
        return False

    # Allow one record (or 1% on large, fast-changing boards) for jobs that
    # open/close while pagination is in flight. Larger gaps are unsafe to call
    # complete: even a 5% shortfall can hide hundreds of live postings.
    tolerated_missing = max(1, ceil(total * 0.01))
    return total - discovered > tolerated_missing


def _log_incomplete_total(discovered: int, total: int | None) -> None:
    if _materially_below_advertised_total(discovered, total):
        log.warning(
            "api_sniffer.incomplete_total",
            advertised_total=total,
            discovered=discovered,
        )


def _item_result_is_truncated(
    *,
    item_count: int,
    discovered_count: int,
    total: int | None,
    cap: int,
) -> bool:
    """Evaluate caps and advertised totals against unique extracted jobs."""
    incomplete = _materially_below_advertised_total(discovered_count, total)
    if incomplete:
        _log_incomplete_total(discovered_count, total)

    truncated = item_count > cap or incomplete
    if truncated:
        log.warning(
            "api_sniffer.truncated",
            rows=item_count,
            discovered=discovered_count,
            cap=cap,
            advertised_total=total,
        )
    return truncated


def _derive_url_match(api_url: str) -> str | None:
    """Derive an ``api_url_match`` glob from a URL with rotating-token segments.

    Replaces path segments that look like rotating tokens (contain mixed
    alphanumeric chars + separators, e.g. ``apigw-x0cceuow60``) with ``*``.
    Returns ``None`` if no token-like segment is found.

    Called during ``can_handle`` (probe) so the pattern is stored in config.
    """
    parsed = urlparse(api_url)
    segments = parsed.path.strip("/").split("/")
    has_token = False
    pattern_segments = []
    for seg in segments:
        is_versioned = bool(re.match(r"^v\d+$", seg, re.I))
        has_mixed = bool(re.search(r"[a-z]", seg, re.I)) and bool(re.search(r"\d", seg))
        has_separator = bool(re.search(r"[-_]", seg)) and len(seg) > 8
        if not is_versioned and (has_mixed and has_separator):
            pattern_segments.append("*")
            has_token = True
        else:
            pattern_segments.append(seg)
    if not has_token:
        return None
    return f"{parsed.netloc}/{'/'.join(pattern_segments)}"


def _merge_params(url: str, params: dict) -> str:
    """Merge extra query params into a URL."""
    parsed = urlparse(url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    existing.update({k: [v] if isinstance(v, str) else v for k, v in params.items()})
    new_query = urlencode(existing, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _prospective_fields(items: list[dict]) -> dict:
    """Map fields shared by Prospective's public career-center payloads."""

    szas_keys = {
        key for item in items if isinstance(item.get("szas"), dict) for key in item["szas"]
    }
    fields: dict = {
        "title": "title",
        "date_posted": "start_date",
        "metadata.language": "language",
        "metadata.end_date": "end_date",
        "metadata.ats_job_id": "id",
    }

    description_parts: list[str] = []
    for heading, key in (
        ("Introduction", "sza_introduction"),
        ("Tasks", "sza_tasks"),
        ("Requirements", "sza_requirements"),
        ("Benefits", "sza_benefits"),
    ):
        if key in szas_keys:
            description_parts.extend((f"=<h3>{heading}</h3>", f"szas.{key}"))
    if description_parts:
        fields["description"] = description_parts
    if "sza_tasks" in szas_keys:
        fields["responsibilities"] = "szas.sza_tasks"
    if "sza_requirements" in szas_keys:
        fields["qualifications"] = "szas.sza_requirements"

    if "sza_location.city" in szas_keys:
        fields["locations"] = 'szas."sza_location.city"'
    if "sza_employment_type" in szas_keys:
        fields["employment_type"] = "szas.sza_employment_type"
    if "sza_pensum" in szas_keys:
        fields["metadata.pensum"] = "szas.sza_pensum"
    if "sza_salary" in szas_keys:
        fields["base_salary"] = "szas.sza_salary"
    if "sza_apply_link" in szas_keys:
        fields["metadata.apply_link"] = "szas.sza_apply_link"
    return fields


async def _prospective_probe_config(url: str, client: httpx.AsyncClient) -> dict | None:
    """Build a direct HTTP config for a Prospective career-center URL.

    The rendered career center exposes only HTML form pagination. Its public
    medium endpoint is stable, complete, and contains rich job data, but some
    branded pages never issue a browser XHR for the sniffer to observe.
    """

    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    match = _PROSPECTIVE_CAREERCENTER_PATH.fullmatch(parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != _PROSPECTIVE_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or match is None
    ):
        return None

    medium_id = match.group("medium_id")
    raw_lang = parse_qs(parsed.query).get("lang", ["de"])[0] or "de"
    lang = (
        raw_lang[:2].lower() if re.fullmatch(r"[a-z]{2}(?:[-_][a-z]{2})?", raw_lang, re.I) else "de"
    )
    api_url = f"https://{_PROSPECTIVE_HOST}/public/v1/medium/{medium_id}/jobs"
    params = {
        "lang": lang,
        "offset": "0",
        "limit": str(_PROSPECTIVE_PAGE_SIZE),
    }
    try:
        data = await http_fetch_with_retry(client, "GET", _merge_params(api_url, params))
    except PaginationFetchError:
        log.debug("api_sniffer.prospective_probe_failed", api_url=api_url, exc_info=True)
        return None
    if not isinstance(data, dict) or str(data.get("medium_id")) != medium_id:
        return None
    items = data.get("jobs")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None

    total = data.get("total")
    runtime_page_size = len(items) or _PROSPECTIVE_PAGE_SIZE
    params["limit"] = str(runtime_page_size)
    return {
        "api_url": api_url,
        "method": "GET",
        "json_path": "jobs",
        "total_path": "total",
        "url_field": "links.directlink",
        "params": params,
        "pagination": {
            "param_name": "offset",
            "style": "offset",
            "start_value": 0,
            "increment": runtime_page_size,
            "location": "query",
        },
        "fields": _prospective_fields(items),
        "items": len(items),
        "total": total if isinstance(total, int) else len(items),
        "score": 100,
    }


def _serialize_post_data(value: object) -> str | None:
    """Normalize configured POST data to the transport's string contract.

    JSON objects are easier and safer to represent in ``boards.csv`` than a
    JSON string containing a second escaped JSON document. Existing string
    bodies (JSON, form-encoded, or multipart) remain unchanged.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    raise ValueError("post_data must be a string, JSON object, or JSON array")


def _configured_post_data(config: dict) -> str | None:
    """Read the canonical POST body without treating empty JSON as absent."""

    value = config["post_data"] if "post_data" in config else config.get("post_body")
    return _serialize_post_data(value)


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


async def can_handle(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
    diagnostics: dict | None = None,
) -> dict | None:
    """Detect whether *url* loads job data via XHR/fetch APIs.

    Returns a metadata dict suitable for use as monitor_config, or None
    if no job-list API is detected.  Prospective CareerCenter pages can be
    detected over plain HTTP; other platforms require Playwright (*pw*).

    When *diagnostics* is provided, it is populated with exchange summaries,
    script URL discoveries, and CMS detection results — even when detection
    fails.  This allows callers to show diagnostic output to the user.
    """
    prospective = await _prospective_probe_config(url, client)
    if prospective is not None:
        return prospective

    prospective_config = await _detect_prospective_config(url, client)
    if prospective_config is not None:
        return prospective_config

    if pw is None:
        return None

    from src.shared.browser import dismiss_overlays, navigate, open_page

    try:
        async with open_page(pw, {}) as page:
            page_host = urlparse(url).netloc
            exchanges = await capture_exchanges(page, page_host)

            await navigate(page, url, {"wait": _DEFAULT_WAIT, "timeout": _DEFAULT_TIMEOUT})
            await asyncio.sleep(_DEFAULT_SETTLE)

            await dismiss_overlays(page)
            await trigger_interactions(page, exchanges)

            # Scan page scripts and detect CMS while page is still open
            if diagnostics is not None:
                try:
                    diagnostics["script_urls"] = await scan_page_scripts(page)
                except Exception:
                    log.debug("api_sniffer.scan_scripts_failed", exc_info=True)
                    diagnostics["script_urls"] = []

                try:
                    diagnostics["cms"] = await detect_cms(page)
                except Exception:
                    log.debug("api_sniffer.detect_cms_failed", exc_info=True)
                    diagnostics["cms"] = None

            result = detect_job_list(exchanges, url)
            if result is None:
                # Populate exchange diagnostics even on failure
                if diagnostics is not None:
                    diagnostics["exchanges"] = [
                        {
                            "method": ex.method,
                            "url": ex.url[:120],
                            "status": ex.status,
                            "phase": ex.phase,
                            "arrays": len(find_arrays(ex.body) if ex.body else []),
                            "best_items": max(
                                (
                                    len(items)
                                    for _, items in (find_arrays(ex.body) if ex.body else [])
                                ),
                                default=0,
                            ),
                        }
                        for ex in exchanges
                    ]
                return None

            ex = result.candidate.exchange
            page_size = len(result.candidate.items)

            # Infer pagination if two matching exchanges exist
            result.pagination = infer_pagination(exchanges, ex.url, page_size)

            # Auto-map fields
            fields = auto_map_fields(result.candidate.items)

            # Split captured URL into clean base + params
            parsed_url = urlparse(ex.url)
            raw_params = parse_qs(parsed_url.query, keep_blank_values=True)

            # Params managed by pagination config — don't duplicate
            pag_params = set()
            if result.pagination:
                pag_params.add(result.pagination.param_name)

            # Separate meaningful params from the URL
            clean_params: dict[str, str | list[str]] = {}
            for k, vals in raw_params.items():
                if k in pag_params:
                    continue
                # Drop empty-valued params
                non_empty = [v for v in vals if v]
                if not non_empty:
                    continue
                clean_params[k] = non_empty[0] if len(non_empty) == 1 else non_empty

            base_url = urlunparse(parsed_url._replace(query=""))

            # Derive api_url_match for URLs with token-like path segments.
            # Stored in config so that _discover_live_url can re-capture the
            # URL at runtime if the token rotates.
            api_url_match = _derive_url_match(base_url)

            # Build metadata
            meta: dict = {
                "api_url": base_url,
                "method": ex.method,
                "json_path": result.candidate.json_path,
                "items": page_size,
                "score": result.candidate.score,
                "browser": True,
            }
            if api_url_match:
                meta["api_url_match"] = api_url_match
            if clean_params:
                meta["params"] = clean_params
            if result.url_field:
                meta["url_field"] = result.url_field
            else:
                # No URL field — try DOM cross-reference to derive url_template
                try:
                    from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

                    dom_urls = await extract_urls_via_dom_crossref(
                        page,
                        result.candidate.items,
                        url,
                    )
                    if dom_urls:
                        # Derive template from the first URL + first item
                        first_item = result.candidate.items[0]
                        id_field = None
                        for key in first_item:
                            if _ID_FIELDS.match(key):
                                id_field = key
                                break
                        if id_field:
                            first_id = str(first_item[id_field])
                            first_url = dom_urls[0]
                            # Replace the ID with a {id_field} placeholder
                            template = first_url.replace(first_id, "{" + id_field + "}")
                            meta["url_template"] = template
                except Exception:
                    log.debug("api_sniffer.dom_crossref_failed", exc_info=True)

            if result.total_count:
                meta["total"] = result.total_count
            if ex.post_data:
                meta["post_data"] = ex.post_data
            if result.pagination:
                pag = result.pagination
                meta["pagination"] = {
                    "param_name": pag.param_name,
                    "style": pag.style,
                    "start_value": pag.start_value,
                    "increment": pag.increment,
                    "location": pag.location,
                }

            # Include request headers (cleaned)
            headers = clean_headers(ex.request_headers)
            if headers:
                meta["request_headers"] = headers

            if fields:
                meta["fields"] = fields

            lumesse_overrides = _lumesse_config_overrides(
                url,
                ex.url,
                result.candidate.items,
                ex.body,
            )
            if lumesse_overrides is not None:
                # The canonical detail URL is preferable to TalentLink's
                # application form URL for stable posting identity.
                meta.pop("url_field", None)
                meta.update(lumesse_overrides)

            # Collect alternative high-scoring endpoints for user review
            from src.shared.api_sniff import ArrayCandidate as _AC
            from src.shared.api_sniff import score_candidate as _sc

            alt_candidates: list[dict] = []
            for alt_ex in exchanges:
                if alt_ex.body is None or alt_ex.url == ex.url:
                    continue
                for alt_path, alt_items in find_arrays(alt_ex.body):
                    ac = _AC(exchange=alt_ex, json_path=alt_path, items=alt_items)
                    _sc(ac, url)
                    if ac.score >= 50 and len(alt_items) >= 3:
                        alt_total = find_total_count(alt_ex.body, alt_path)
                        alt_candidates.append(
                            {
                                "url": alt_ex.url[:200],
                                "json_path": alt_path,
                                "items": len(alt_items),
                                "score": ac.score,
                                "total": alt_total,
                            }
                        )
            if alt_candidates:
                alt_candidates.sort(key=lambda c: c["score"], reverse=True)
                meta["alternatives"] = alt_candidates[:3]

            return meta

    except Exception:
        log.debug("api_sniffer.can_handle_failed", url=url, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def _validated_required_pdf_pattern(value: object) -> re.Pattern[str] | None:
    """Compile the opt-in PDF ownership/content gate for linked-document APIs."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_REQUIRED_PDF_PATTERN_CHARS
        or "\x00" in value
    ):
        raise ValueError("api_sniffer require_pdf_pattern must contain 1-1024 characters")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError("api_sniffer require_pdf_pattern is invalid") from exc


async def _apply_pdf_document_gate(
    result: list[DiscoveredJob] | set[str] | MonitorResult,
    client: httpx.AsyncClient,
    metadata: dict,
) -> list[DiscoveredJob] | set[str] | MonitorResult:
    """Apply bounded ownership and expiry checks to API-linked PDFs."""
    required_pattern = _validated_required_pdf_pattern(metadata.get("require_pdf_pattern"))
    unexpired_value = metadata.get("require_unexpired_pdf")
    if required_pattern is None and unexpired_value is None:
        return result
    if required_pattern is None or unexpired_value is None:
        raise ValueError(
            "api_sniffer PDF document gate requires both require_pdf_pattern "
            "and require_unexpired_pdf"
        )
    from src.core.monitor import MonitorResult as _MonitorResult
    from src.core.monitors.dom import (
        _filter_unexpired_pdf_urls,
        _validated_unexpired_pdf_config,
    )

    wrapped = result if isinstance(result, _MonitorResult) else None
    if wrapped is not None:
        urls = wrapped.urls
    elif isinstance(result, set):
        urls = result
    elif isinstance(result, list):
        urls = {job.url for job in result}
    else:
        raise ValueError("api_sniffer PDF document gate received an unsupported result type")

    unexpired_config = _validated_unexpired_pdf_config(unexpired_value)
    if unexpired_config is None:
        raise ValueError("api_sniffer require_unexpired_pdf must be configured")
    filtered, deadlines = await _filter_unexpired_pdf_urls(
        urls,
        client,
        unexpired_config,
        required_text_pattern=required_pattern,
        raise_on_required_text_mismatch=True,
        return_deadlines=True,
    )

    def attach_deadline(job: DiscoveredJob) -> DiscoveredJob:
        deadline = deadlines.get(job.url)
        if deadline is not None:
            job.extras = {**(job.extras or {}), "valid_through": deadline}
        return job

    if wrapped is not None:
        wrapped.urls = filtered
        if wrapped.jobs_by_url is not None:
            wrapped.jobs_by_url = {
                url: attach_deadline(job)
                for url, job in wrapped.jobs_by_url.items()
                if url in filtered
            }
        return wrapped
    if isinstance(result, list):
        return [attach_deadline(job) for job in result if job.url in filtered]
    return filtered


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob] | set[str] | MonitorResult:
    """Discover jobs via API sniffing.

    - **HTTP mode** (config has ``api_url``, no ``browser``): plain httpx
      fetch — no Playwright needed.
    - **Replay mode** (config has ``api_url`` + ``browser: true``): navigate
      to board_url to establish cookies, then replay via in-browser fetch.
    - **Auto-discover mode** (no ``api_url``): full capture + detect pipeline.
    """
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]
    api_url = metadata.get("api_url")
    if not api_url and metadata.get("item_filter") is not None:
        raise ValueError("api_sniffer item_filter requires a configured api_url")
    if not api_url and (
        metadata.get("pagination_convergence") is not None
        or metadata.get("url_field_match") is not None
    ):
        raise ValueError(
            "api_sniffer pagination convergence and URL matching require a configured api_url"
        )
    if not api_url and (
        metadata.get("require_pdf_pattern") is not None
        or metadata.get("require_unexpired_pdf") is not None
    ):
        raise ValueError("api_sniffer PDF document gate requires a configured api_url")

    # Plain HTTP mode — no Playwright needed (pw passed for api_url_match fallback)
    if api_url and not metadata.get("browser"):
        result = await _discover_http(board, client, metadata, pw=pw)
        return await _apply_pdf_document_gate(result, client, metadata)

    if api_url:
        # Replay mode — browser preferred, HTTP fallback
        if pw is not None:
            result = await _discover_replay(board_url, metadata, pw, client=client)
        else:
            log.warning("api_sniffer.no_playwright_fallback_http", board_url=board_url)
            result = await _discover_http(board, client, metadata, pw=pw)
        return await _apply_pdf_document_gate(result, client, metadata)

    if pw is None:
        log.error("api_sniffer.no_playwright", board_url=board_url)
        return set()
    return await _discover_auto(board_url, metadata, pw)


# ---------------------------------------------------------------------------
# Plain HTTP helpers
# ---------------------------------------------------------------------------

_DEFAULT_HREF_RE = re.compile(r'href=["\']([^"\'#][^"\']*)["\']')
_HTML_WITH_LINKS_RE = re.compile(r"<[a-z][\s\S]*?href=", re.IGNORECASE)
_SIMPLE_ITEM_PATH_RE = re.compile(
    r"^(?P<root>[A-Za-z_][A-Za-z0-9_-]*)"
    r"(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[(?:\d+|\*)?\]))*$"
)


def _item_path_root(path: object) -> str | None:
    """Return a safe top-level key for a simple JMESPath expression.

    Compaction is deliberately disabled for filters, pipes, expressions, or
    whole-object selectors: retaining a larger object is preferable to
    changing extraction semantics for an unusual board configuration.
    """
    if not isinstance(path, str) or not path or path.startswith("="):
        return None
    match = _SIMPLE_ITEM_PATH_RE.fullmatch(path)
    return match.group("root") if match else None


def _collect_spec_roots(spec: object, roots: set[str]) -> bool:
    """Collect item roots used by an ``extract_field`` spec if it is safe."""
    if isinstance(spec, str):
        if spec.startswith("="):
            return True
        root = _item_path_root(spec)
        if root is None:
            return False
        roots.add(root)
        return True
    if isinstance(spec, list):
        return all(_collect_spec_roots(part, roots) for part in spec)
    if not isinstance(spec, dict):
        return False
    if "concat" in spec:
        return _collect_spec_roots(spec["concat"], roots)
    if "lookup_from" in spec and "key_from" in spec:
        return _collect_spec_roots(spec["key_from"], roots)
    if "path" in spec:
        return _collect_spec_roots(spec["path"], roots)
    if "each" in spec:
        return _collect_spec_roots(spec["each"], roots)
    return False


def _build_item_projector(
    fields_map: dict,
    url_field: str | None,
    url_template: str | None,
    url_template_fields: dict[str, str],
    slug_fields: list[str] | None = None,
    preserve_paths: list[str] | None = None,
) -> Callable[[dict], dict] | None:
    """Build a conservative projector for explicitly configured rich APIs.

    Auto-detection and URL-less configurations need the whole object, so they
    intentionally opt out.  For explicit rich configs, only top-level roots
    used by field extraction and URL construction survive pagination.  Any
    scalar absolute URL is also retained so ``_extract_rich`` keeps its
    existing URL fallback behaviour.
    """
    if not fields_map or not (url_field or url_template):
        return None

    roots: set[str] = set()
    if not all(_collect_spec_roots(spec, roots) for spec in fields_map.values()):
        return None

    if url_field:
        root = _item_path_root(url_field)
        if root is None:
            return None
        roots.add(root)

    if url_template:
        try:
            parsed_fields = list(Formatter().parse(url_template))
        except ValueError:
            return None
        for _literal, field_name, format_spec, _conversion in parsed_fields:
            if field_name is None:
                continue
            if "{" in format_spec:
                return None
            alias = field_name.split(".", 1)[0].split("[", 1)[0]
            if alias == "slug" and slug_fields:
                continue
            alias_path = url_template_fields.get(alias)
            root = _item_path_root(alias_path) if alias_path is not None else _item_path_root(alias)
            if root is None:
                return None
            roots.add(root)

    for path in url_template_fields.values():
        root = _item_path_root(path)
        if root is None:
            return None
        roots.add(root)

    for path in slug_fields or []:
        root = _item_path_root(path)
        if root is None:
            return None
        roots.add(root)

    for path in preserve_paths or []:
        root = _item_path_root(path)
        if root is None:
            return None
        roots.add(root)

    required = frozenset(roots)

    def _project(item: dict) -> dict:
        return {
            key: value
            for key, value in item.items()
            if key in required
            or (isinstance(value, str) and value.startswith(("http://", "https://")))
        }

    return _project


def _validated_slug_fields(config: dict) -> list[str]:
    """Return a well-formed list of item paths used for ``{slug}``."""
    value = config.get("slug_fields")
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(path, str) and path.strip() for path in value
    ):
        raise ValueError("api_sniffer slug_fields must be a list of non-empty field paths")
    return [path.strip() for path in value]


def _validated_item_filter(
    config: dict,
) -> tuple[
    dict[str, frozenset[str]],
    dict[str, frozenset[str]],
    dict[str, tuple[re.Pattern[str], ...]],
    tuple[str, ...],
    _DedupePreference | None,
]:
    """Validate an optional post-pagination item scope and stable dedupe key."""
    value = config.get("item_filter")
    if value is None:
        return {}, {}, {}, (), None
    if not isinstance(value, dict) or not value:
        raise ValueError("api_sniffer item_filter must be a non-empty mapping")
    if set(value) - {
        "include",
        "exclude",
        "exclude_regex",
        "dedupe_by",
        "dedupe_preference",
    }:
        raise ValueError("api_sniffer item_filter contains unsupported keys")

    include = value.get("include") if "include" in value else None
    if "include" in value and (
        not isinstance(include, dict) or not include or len(include) > _MAX_ITEM_FILTER_FIELDS
    ):
        raise ValueError("api_sniffer item_filter.include must be a non-empty bounded mapping")
    if include is None:
        include = {}
    normalized_include: dict[str, frozenset[str]] = {}
    for path, included_values in include.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("api_sniffer item_filter include paths must be non-empty strings")
        if (
            not isinstance(included_values, list)
            or not included_values
            or len(included_values) > _MAX_ITEM_FILTER_VALUES
            or not all(isinstance(item, str) and item for item in included_values)
        ):
            raise ValueError(
                "api_sniffer item_filter include values must be bounded non-empty string lists"
            )
        normalized_path = path.strip()
        try:
            extract_field({}, normalized_path)
        except Exception as exc:
            raise ValueError("api_sniffer item_filter include paths must be valid") from exc
        normalized_include[normalized_path] = frozenset(included_values)

    exclude = value.get("exclude") or {}
    if not isinstance(exclude, dict) or len(exclude) > _MAX_ITEM_FILTER_FIELDS:
        raise ValueError("api_sniffer item_filter.exclude must be a bounded mapping")
    normalized: dict[str, frozenset[str]] = {}
    for path, excluded_values in exclude.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError("api_sniffer item_filter exclude paths must be non-empty strings")
        if (
            not isinstance(excluded_values, list)
            or not excluded_values
            or len(excluded_values) > _MAX_ITEM_FILTER_VALUES
            or not all(isinstance(item, str) and item for item in excluded_values)
        ):
            raise ValueError(
                "api_sniffer item_filter exclude values must be bounded non-empty string lists"
            )
        normalized_path = path.strip()
        try:
            extract_field({}, normalized_path)
        except Exception as exc:
            raise ValueError("api_sniffer item_filter exclude paths must be valid") from exc
        normalized[normalized_path] = frozenset(excluded_values)

    exclude_regex = value.get("exclude_regex") or {}
    if not isinstance(exclude_regex, dict) or len(exclude_regex) > _MAX_ITEM_FILTER_FIELDS:
        raise ValueError("api_sniffer item_filter.exclude_regex must be a bounded mapping")
    normalized_regex: dict[str, tuple[re.Pattern[str], ...]] = {}
    for path, patterns in exclude_regex.items():
        if not isinstance(path, str) or not path.strip():
            raise ValueError(
                "api_sniffer item_filter exclude_regex paths must be non-empty strings"
            )
        if (
            not isinstance(patterns, list)
            or not patterns
            or len(patterns) > _MAX_ITEM_FILTER_VALUES
            or not all(
                isinstance(pattern, str) and pattern and len(pattern) <= _MAX_REFRESH_PATTERN_CHARS
                for pattern in patterns
            )
        ):
            raise ValueError(
                "api_sniffer item_filter exclude_regex patterns must be bounded "
                "non-empty string lists"
            )
        normalized_path = path.strip()
        try:
            extract_field({}, normalized_path)
        except Exception as exc:
            raise ValueError(
                "api_sniffer item_filter exclude_regex paths and patterns must be valid"
            ) from exc
        try:
            normalized_regex[normalized_path] = tuple(re.compile(pattern) for pattern in patterns)
        except re.error as exc:
            raise ValueError(
                "api_sniffer item_filter exclude_regex paths and patterns must be valid"
            ) from exc

    dedupe_by = value.get("dedupe_by")
    if dedupe_by is None:
        dedupe_paths: tuple[str, ...] = ()
    elif (
        not isinstance(dedupe_by, list)
        or not dedupe_by
        or len(dedupe_by) > _MAX_ITEM_FILTER_FIELDS
        or not all(isinstance(path, str) and path.strip() for path in dedupe_by)
    ):
        raise ValueError("api_sniffer item_filter.dedupe_by must be a bounded non-empty path list")
    else:
        dedupe_paths = tuple(path.strip() for path in dedupe_by)
    for dedupe_path in dedupe_paths:
        try:
            extract_field({}, dedupe_path)
        except Exception as exc:
            raise ValueError("api_sniffer item_filter.dedupe_by path must be valid") from exc

    preference_value = value.get("dedupe_preference")
    preference: _DedupePreference | None = None
    if preference_value is not None:
        if not dedupe_paths:
            raise ValueError("api_sniffer item_filter.dedupe_preference requires dedupe_by")
        if not isinstance(preference_value, dict) or set(preference_value) != {
            "path",
            "preferred_values",
            "fallback_by",
        }:
            raise ValueError(
                "api_sniffer item_filter.dedupe_preference must contain exactly "
                "path, preferred_values, and fallback_by"
            )
        preference_path = preference_value.get("path")
        preferred_values = preference_value.get("preferred_values")
        fallback_by = preference_value.get("fallback_by")
        if not isinstance(preference_path, str) or not preference_path.strip():
            raise ValueError(
                "api_sniffer item_filter.dedupe_preference.path must be a non-empty path"
            )
        if (
            not isinstance(preferred_values, list)
            or not preferred_values
            or len(preferred_values) > _MAX_ITEM_FILTER_VALUES
            or not all(isinstance(item, str) and item for item in preferred_values)
            or len(set(preferred_values)) != len(preferred_values)
        ):
            raise ValueError(
                "api_sniffer item_filter.dedupe_preference.preferred_values must be a "
                "bounded unique non-empty string list"
            )
        if (
            not isinstance(fallback_by, list)
            or not fallback_by
            or len(fallback_by) > _MAX_ITEM_FILTER_FIELDS
            or not all(isinstance(path, str) and path.strip() for path in fallback_by)
        ):
            raise ValueError(
                "api_sniffer item_filter.dedupe_preference.fallback_by must be a "
                "bounded non-empty path list"
            )
        normalized_preference_path = preference_path.strip()
        normalized_fallback = tuple(path.strip() for path in fallback_by)
        if normalized_fallback[0] != normalized_preference_path:
            raise ValueError(
                "api_sniffer item_filter.dedupe_preference.fallback_by must start with path"
            )
        for path in (normalized_preference_path, *normalized_fallback):
            try:
                extract_field({}, path)
            except Exception as exc:
                raise ValueError(
                    "api_sniffer item_filter.dedupe_preference paths must be valid"
                ) from exc
        preference = _DedupePreference(
            normalized_preference_path,
            tuple(preferred_values),
            normalized_fallback,
        )
    if not normalized_include and not normalized and not normalized_regex and not dedupe_paths:
        raise ValueError("api_sniffer item_filter must include, exclude, or deduplicate items")
    return normalized_include, normalized, normalized_regex, dedupe_paths, preference


def _validated_pagination_convergence(
    config: dict,
    dedupe_paths: tuple[str, ...],
) -> _PaginationConvergence | None:
    """Validate an opt-in bounded proof for unstable paginated inventories."""
    value = config.get("pagination_convergence")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "max_passes",
        "required_no_growth_passes",
        "identity_by",
        "stable_fields",
    }:
        raise ValueError(
            "api_sniffer pagination_convergence must be a mapping containing only "
            "max_passes, required_no_growth_passes, identity_by, and stable_fields"
        )

    pagination = config.get("pagination")
    if not isinstance(pagination, dict) or pagination.get("style") not in {"offset", "page"}:
        raise ValueError("api_sniffer pagination_convergence requires page or offset pagination")
    explicit_identity = value.get("identity_by")
    if explicit_identity is None:
        identity_paths = dedupe_paths
        if not identity_paths:
            raise ValueError(
                "api_sniffer pagination_convergence requires identity_by or item_filter.dedupe_by"
            )
    elif (
        not isinstance(explicit_identity, list)
        or not explicit_identity
        or len(explicit_identity) > _MAX_ITEM_FILTER_FIELDS
        or not all(isinstance(path, str) and path.strip() for path in explicit_identity)
    ):
        raise ValueError(
            "api_sniffer pagination_convergence.identity_by must be a bounded non-empty path list"
        )
    else:
        identity_paths = tuple(path.strip() for path in explicit_identity)

    stable_value = value.get("stable_fields")
    if stable_value is None:
        stable_fields: tuple[str, ...] = ()
    elif (
        explicit_identity is None
        or not isinstance(stable_value, list)
        or not stable_value
        or len(stable_value) > _MAX_ITEM_FILTER_FIELDS
        or not all(isinstance(path, str) and path.strip() for path in stable_value)
    ):
        raise ValueError(
            "api_sniffer pagination_convergence.stable_fields requires explicit "
            "identity_by and a bounded non-empty path list"
        )
    else:
        stable_fields = tuple(path.strip() for path in stable_value)

    for path in (*identity_paths, *stable_fields):
        try:
            extract_field({}, path)
        except Exception as exc:
            raise ValueError(
                "api_sniffer pagination_convergence identity/projection paths must be valid"
            ) from exc

    max_passes = value.get("max_passes")
    required_no_growth = value.get("required_no_growth_passes")
    if isinstance(max_passes, bool) or not isinstance(max_passes, int) or not 3 <= max_passes <= 8:
        raise ValueError(
            "api_sniffer pagination_convergence.max_passes must be an integer from 3 to 8"
        )
    if (
        isinstance(required_no_growth, bool)
        or not isinstance(required_no_growth, int)
        or not 2 <= required_no_growth < max_passes
    ):
        raise ValueError(
            "api_sniffer pagination_convergence.required_no_growth_passes must be "
            "an integer from 2 to max_passes - 1"
        )
    return _PaginationConvergence(
        max_passes,
        required_no_growth,
        identity_paths,
        stable_fields,
        explicit_identity is not None,
    )


def _validated_url_field_match(
    config: dict,
    pagination_convergence: _PaginationConvergence | None,
) -> _UrlFieldMatch | None:
    """Validate an exact URL-to-item cross-field contract."""
    value = config.get("url_field_match")
    if value is None:
        return None
    if pagination_convergence is None:
        raise ValueError("api_sniffer url_field_match requires pagination_convergence")
    if not isinstance(config.get("url_field"), str) or not config["url_field"].strip():
        raise ValueError("api_sniffer url_field_match requires url_field")
    if not isinstance(value, dict) or set(value) != {"pattern", "fields"}:
        raise ValueError("api_sniffer url_field_match must contain exactly pattern and fields")
    pattern_value = value.get("pattern")
    fields_value = value.get("fields")
    if (
        not isinstance(pattern_value, str)
        or not pattern_value
        or len(pattern_value) > _MAX_REFRESH_PATTERN_CHARS
    ):
        raise ValueError("api_sniffer url_field_match.pattern must be a bounded string")
    if (
        not isinstance(fields_value, dict)
        or not fields_value
        or len(fields_value) > _MAX_ITEM_FILTER_FIELDS
        or not all(
            isinstance(group, str) and group and isinstance(path, str) and path.strip()
            for group, path in fields_value.items()
        )
    ):
        raise ValueError(
            "api_sniffer url_field_match.fields must be a bounded non-empty group-to-path mapping"
        )
    try:
        pattern = re.compile(pattern_value)
    except re.error as exc:
        raise ValueError("api_sniffer url_field_match.pattern must be valid") from exc
    if set(pattern.groupindex) != set(fields_value):
        raise ValueError("api_sniffer url_field_match named groups must exactly match fields")
    normalized_fields = tuple(sorted((group, path.strip()) for group, path in fields_value.items()))
    for _group, path in normalized_fields:
        try:
            extract_field({}, path)
        except Exception as exc:
            raise ValueError("api_sniffer url_field_match field paths must be valid") from exc
    return _UrlFieldMatch(pattern, normalized_fields)


def _matches_url_field_contract(
    item: dict,
    url_field: str,
    url_field_match: _UrlFieldMatch | None,
) -> bool:
    if url_field_match is None:
        return True
    raw_url = extract_field(item, url_field)
    if not isinstance(raw_url, str) or not raw_url:
        return False
    match = url_field_match.pattern.fullmatch(raw_url)
    if match is None:
        return False
    for group, path in url_field_match.fields:
        expected = extract_field(item, path)
        if not isinstance(expected, str) or not expected or match.group(group) != expected:
            return False
    return True


def _advertised_total(payload: object, total_path: str | None, json_path: str) -> int | None:
    if total_path:
        if not isinstance(payload, dict):
            return None
        raw_total = resolve_path(payload, total_path)
    else:
        raw_total = find_total_count(payload, json_path)
    if isinstance(raw_total, bool) or not isinstance(raw_total, (int, float)):
        return None
    total = int(raw_total)
    return total if total >= 0 else None


def _truncated_empty_result(fields_map: dict[str, str]):
    """Return an empty partial result without authorizing gone detection."""
    return truncated_rich_result([]) if fields_map else truncated_url_result(set())


async def _paginate_until_converged(
    *,
    fetch_fn,
    method: str,
    api_url: str,
    request_headers: dict,
    post_data: str | None,
    initial_data: object,
    initial_items: list[dict],
    json_path: str,
    total_path: str | None,
    total_count: int | None,
    pagination_config: dict,
    max_pages: int,
    identity_paths: tuple[str, ...],
    stable_fields: tuple[str, ...] = (),
    reject_duplicate_identities: bool = False,
    max_passes: int,
    required_no_growth_passes: int,
    item_projector,
    item_validator: Callable[[dict], bool] | None = None,
) -> tuple[list[dict], bool]:
    """Union bounded full passes and prove convergence before allowing delists.

    Some APIs expose an advertised row count but reshuffle non-uniquely sorted
    offset or numbered pages between requests. A single pass can therefore contain the
    advertised number of rows while omitting live identities and repeating
    others. This opt-in path accumulates stable identities across complete
    passes. It is healthy only after two or more consecutive full passes add
    no identities and every observed advertised total remains unchanged.
    """
    from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult, PaginationInfo

    expected_total = total_count
    accumulated: dict[tuple[str, ...], dict] = {}
    accumulated_projections: dict[tuple[str, ...], object] = {}
    no_growth_passes = 0

    def payload_total(payload: object) -> int | None:
        return _advertised_total(payload, total_path, json_path)

    def has_configured_item_list(payload: object) -> bool:
        """Require the configured list schema on every response in the proof."""
        if json_path == "$":
            configured_items = payload
        elif isinstance(payload, dict):
            configured_items = resolve_path(payload, json_path)
        else:
            return False
        return isinstance(configured_items, list) and all(
            isinstance(item, dict) for item in configured_items
        )

    def stable_projection(item: dict) -> tuple[object, bool]:
        if not stable_fields:
            return item, True
        projection: list[str] = []
        for path in stable_fields:
            value = extract_field(item, path)
            if value is None or value == "":
                return (), False
            try:
                projection.append(
                    json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                )
            except (TypeError, ValueError):
                return (), False
        return tuple(projection), True

    def identity_map(
        items: list[dict],
    ) -> tuple[dict[tuple[str, ...], dict], dict[tuple[str, ...], object], bool]:
        mapped: dict[tuple[str, ...], dict] = {}
        projections: dict[tuple[str, ...], object] = {}
        valid = True
        for item in items:
            if item_validator is not None and not item_validator(item):
                valid = False
                continue
            parts = [extract_field(item, path) for path in identity_paths]
            if not all(isinstance(part, str) and part for part in parts):
                valid = False
                continue
            identity = tuple(cast(str, part) for part in parts)
            projection, projection_valid = stable_projection(item)
            if not projection_valid:
                valid = False
                continue
            previous = mapped.get(identity)
            if previous is not None and (
                reject_duplicate_identities or projections[identity] != projection
            ):
                # Explicit raw identities must occur exactly once per pass.
                # Legacy convergence configs retain their prior allowance for
                # byte-equivalent duplicate rows while still rejecting drift.
                valid = False
            # Preserve the first observation. Even though a conflict makes
            # the cycle partial below, returning the last duplicate would
            # still expose a nondeterministic mixed snapshot to callers.
            mapped.setdefault(identity, item)
            projections.setdefault(identity, projection)
        return mapped, projections, valid

    pass_data = initial_data
    pass_items = initial_items
    for pass_number in range(1, max_passes + 1):
        if not has_configured_item_list(pass_data):
            log.warning(
                "api_sniffer.pagination_convergence_invalid_list_shape",
                pass_number=pass_number,
            )
            return list(accumulated.values()), False
        totals_valid = payload_total(pass_data) == expected_total and expected_total is not None
        list_shapes_valid = True

        async def validating_fetch(
            fetch_method: str,
            fetch_url: str,
            fetch_headers: dict,
            fetch_body: str | None,
        ) -> object:
            nonlocal list_shapes_valid, totals_valid
            payload = await fetch_fn(fetch_method, fetch_url, fetch_headers, fetch_body)
            if payload_total(payload) != expected_total:
                totals_valid = False
            if not has_configured_item_list(payload):
                list_shapes_valid = False
            return payload

        pagination = PaginationInfo(
            param_name=pagination_config["param_name"],
            style=pagination_config["style"],
            start_value=pagination_config.get("start_value", 0),
            increment=pagination_config.get("increment", 1),
            location=pagination_config.get("location", "query"),
        )
        result = JobListResult(
            candidate=ArrayCandidate(
                exchange=Exchange(
                    method=method,
                    url=api_url,
                    request_headers=request_headers,
                    post_data=post_data,
                    status=200,
                    body=pass_data,
                    content_type="application/json",
                    phase="load",
                ),
                json_path=json_path,
                items=pass_items,
            ),
            url_field=None,
            total_count=expected_total,
            pagination=pagination,
        )
        rows = await paginate_all(
            validating_fetch,
            result,
            max_pages,
            item_projector=item_projector,
        )
        pass_identities, pass_projections, identities_valid = identity_map(rows)
        new_identities = set(pass_identities) - set(accumulated)
        cross_pass_conflict = any(
            identity in accumulated_projections
            and accumulated_projections[identity] != pass_projections[identity]
            for identity in pass_identities
        )
        identities_valid = identities_valid and not cross_pass_conflict
        # Retain the first stable record for each identity. A later differing
        # record invalidates the proof instead of winning by request order.
        for identity, item in pass_identities.items():
            accumulated.setdefault(identity, item)
            accumulated_projections.setdefault(identity, pass_projections[identity])
        complete_pass = (
            totals_valid
            and list_shapes_valid
            and identities_valid
            and expected_total is not None
            and len(rows) == expected_total
            and len(accumulated) <= expected_total
        )
        inventory_complete = expected_total is not None and len(accumulated) == expected_total
        log.info(
            "api_sniffer.pagination_convergence_pass",
            pass_number=pass_number,
            rows=len(rows),
            identities=len(pass_identities),
            accumulated=len(accumulated),
            new_identities=len(new_identities),
            advertised_total=expected_total,
            complete=complete_pass,
            inventory_complete=inventory_complete,
        )
        if not complete_pass:
            log.warning(
                "api_sniffer.pagination_convergence_incomplete",
                pass_number=pass_number,
                advertised_total=expected_total,
            )
            return list(accumulated.values()), False

        if pass_number > 1:
            no_growth_passes = no_growth_passes + 1 if not new_identities else 0
            if inventory_complete and no_growth_passes >= required_no_growth_passes:
                return list(accumulated.values()), True

        if pass_number == max_passes:
            break
        pass_data = await _fetch_page_with_retry(
            fetch_fn,
            method,
            api_url,
            clean_headers(request_headers),
            post_data,
        )
        pass_items = extract_items(pass_data, json_path)

    log.warning(
        "api_sniffer.pagination_convergence_exhausted",
        max_passes=max_passes,
        accumulated=len(accumulated),
        advertised_total=expected_total,
    )
    return list(accumulated.values()), False


def _apply_item_filter(
    items: list[dict],
    item_filter: tuple[
        dict[str, frozenset[str]],
        dict[str, frozenset[str]],
        dict[str, tuple[re.Pattern[str], ...]],
        tuple[str, ...],
        _DedupePreference | None,
    ],
    advertised_total: int | None,
) -> tuple[list[dict], int | None]:
    """Apply an intentional source partition without masking upstream truncation."""
    include, exclude, exclude_regex, dedupe_by, dedupe_preference = item_filter
    if not include and not exclude and not exclude_regex and not dedupe_by:
        return items, advertised_total

    original_count = len(items)
    scoped: list[dict] = []
    for item in items:
        included = True
        for path, accepted in include.items():
            value = extract_field(item, path)
            values = value if isinstance(value, list) else [value]
            if not any(
                isinstance(candidate, str) and candidate in accepted for candidate in values
            ):
                included = False
                break
        if not included:
            continue

        for path, rejected in exclude.items():
            value = extract_field(item, path)
            values = value if isinstance(value, list) else [value]
            if any(isinstance(candidate, str) and candidate in rejected for candidate in values):
                break
        else:
            for path, patterns in exclude_regex.items():
                value = extract_field(item, path)
                values = value if isinstance(value, list) else [value]
                if any(
                    isinstance(candidate, str) and pattern.search(candidate)
                    for candidate in values
                    for pattern in patterns
                ):
                    break
            else:
                scoped.append(item)

    if dedupe_by:
        grouped: dict[tuple[str, ...], list[dict]] = {}
        for item in scoped:
            identity_parts = [extract_field(item, path) for path in dedupe_by]
            if all(isinstance(part, str) and part for part in identity_parts):
                identity = tuple(cast(str, part) for part in identity_parts)
                grouped.setdefault(identity, []).append(item)

        if dedupe_preference is None:
            winners = {identity: group[0] for identity, group in grouped.items()}
        else:
            preferred_rank = {
                value: index for index, value in enumerate(dedupe_preference.preferred_values)
            }

            def preference_key(item: dict) -> tuple[int | str, ...]:
                preference = extract_field(item, dedupe_preference.path)
                fallback = [extract_field(item, path) for path in dedupe_preference.fallback_by]
                if (
                    not isinstance(preference, str)
                    or not preference
                    or not all(isinstance(value, str) and value for value in fallback)
                ):
                    raise ValueError(
                        "api_sniffer item_filter.dedupe_preference paths must resolve "
                        "to non-empty strings"
                    )
                return (
                    preferred_rank.get(preference, len(preferred_rank)),
                    *(cast(str, value) for value in fallback),
                )

            winners = {
                identity: min(group, key=preference_key) for identity, group in grouped.items()
            }

        deduped: list[dict] = []
        for item in scoped:
            identity_parts = [extract_field(item, path) for path in dedupe_by]
            if not all(isinstance(part, str) and part for part in identity_parts):
                deduped.append(item)
                continue
            identity = tuple(cast(str, part) for part in identity_parts)
            if winners[identity] is item:
                deduped.append(item)
        scoped = deduped

    # Remove the intentionally excluded/deduplicated rows from the upstream
    # total as well. Any pre-existing source gap is preserved, so the normal
    # small-drift tolerance still applies and a materially short response
    # continues to fail closed.
    removed_count = original_count - len(scoped)
    scoped_total = (
        max(0, advertised_total - removed_count) if advertised_total is not None else None
    )
    log.info(
        "api_sniffer.item_filter_applied",
        before=original_count,
        after=len(scoped),
        advertised_total=advertised_total,
        scoped_total=scoped_total,
    )
    return scoped, scoped_total


def score_array(path: str, items: list[dict], api_url: str) -> int:
    """Score an array-of-dicts as a likely job list.

    Uses lightweight heuristics: job keywords in path/URL, presence of
    title and URL fields, and item count as a minor factor.
    """
    score = 0
    sample_keys: set[str] = set()
    for it in items[:5]:
        sample_keys.update(it.keys())

    # Job keywords in array path
    if JOB_KEYWORDS.search(path):
        score += 30
    # Job keywords in API URL
    if JOB_KEYWORDS.search(api_url):
        score += 5
    # Has title-like field
    if any(TITLE_FIELDS.match(k) for k in sample_keys):
        score += 20
    # Has URL-like field
    if find_url_field(items):
        score += 15
    # Reasonable array size (not a tiny filter list)
    if len(items) >= 3:
        score += 5
    return score


def pick_best_array(
    arrays: list[tuple[str, list[dict]]],
    api_url: str,
) -> tuple[str, list[dict]]:
    """Pick the best candidate array from *arrays* using job-list scoring."""
    return max(arrays, key=lambda x: (score_array(x[0], x[1], api_url), len(x[1])))


def find_html_strings(obj: object, path: str = "") -> list[tuple[str, str]]:
    """Find string values in a JSON structure that look like HTML with links.

    Returns ``[(dot_path, html_string), ...]`` sorted by string length
    (longest first — likely the main content).
    """
    results: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            child = f"{path}.{key}" if path else key
            if isinstance(val, str) and len(val) > 100 and _HTML_WITH_LINKS_RE.search(val):
                results.append((child, val))
            elif isinstance(val, (dict, list)):
                results.extend(find_html_strings(val, child))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            results.extend(find_html_strings(val, f"{path}[{i}]"))
    results.sort(key=lambda x: len(x[1]), reverse=True)
    return results


# Retry budget for the api_sniffer monitor's first-page + HTML-pagination
# httpx fetches. Matches ``fetch_with_retry`` defaults: 3 total attempts
# with exponential backoff and full jitter starting at 1s — symmetric
# with the accenture monitor (#2735) for cross-monitor consistency.
_API_SNIFFER_FETCH_RETRIES = 3
_API_SNIFFER_FETCH_BASE_DELAY = 1.0


async def http_fetch_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict | None = None,
    body: str | None = None,
    *,
    retries: int = _API_SNIFFER_FETCH_RETRIES,
    base_delay: float = _API_SNIFFER_FETCH_BASE_DELAY,
    raise_non_retryable: bool = False,
) -> dict | None:
    """Fetch JSON via httpx with bounded retries (#2733).

    Returns:
        - Parsed JSON dict on HTTP 200.
        - ``None`` on 404 / 410 (legitimate end-of-pagination, or stale
          rotating-token URL — the caller's ``api_url_match`` browser
          fallback path interprets ``None`` as "go look up the live URL").
        - ``None`` on other non-retryable 4xx (auth, forbidden, bad
          request) with a warning, mirroring the lenient stop semantic
          used by ``fetch_with_retry`` on the dom/sitemap path. Callers that
          cannot safely accept partial data pass ``raise_non_retryable=True``
          to preserve the status in :class:`PaginationFetchError` instead.

    Raises:
        :class:`PaginationFetchError` after exhausting *retries* on
        retryable HTTP statuses (5xx including Cloudflare 520-526/530,
        plus 408/425/429) or arbitrary network exceptions (timeout,
        connection reset, JSON parse error). The caller is expected to
        propagate so ``_process_one_board_streaming`` records the run
        as a failure rather than a partial success — closing the same
        silent-truncation hole the dom/sitemap fix (#2722) and PCSX/
        accenture follow-ups (#2734/#2735) addressed for their paths.

        Note on JSON parse failures: ``resp.json()`` raising (malformed
        body, captcha HTML, anti-bot challenge served as 200) takes the
        retry path. Captcha responses won't recover within the retry
        budget — that's accepted: failing loud is preferable to
        silently treating a captcha page as end-of-pagination. The
        ``last_error`` field on the raised exception ("JSONDecodeError")
        is what an operator pattern-matches in logs to recognise this
        case.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` — exponential
    with full jitter.
    """
    from src.shared.tdm import TDMReservedError
    from src.shared.tdm import check_response as _tdm_check

    last_exc: BaseException | None = None
    last_status: int | None = None

    for attempt in range(retries):
        try:
            kw: dict = {"headers": headers or {}, "timeout": 30}
            if method.upper() == "POST" and body:
                kw["content"] = body
                kw["headers"].setdefault("content-type", "application/json")
            resp = await client.request(method.upper(), url, **kw)
            resp.raise_for_status()
            # TDM-Reservation respect (#2842). Header-only on the JSON
            # API path. Runs after ``raise_for_status`` so we only check
            # successful responses (errors fall through to the existing
            # ``HTTPStatusError`` branch).
            _tdm_check(resp)
            return resp.json()
        except TDMReservedError:
            # Publisher policy declaration — propagate, never retry.
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_status = status
            last_exc = exc
            if status in (404, 410):
                return None
            if not is_retryable_status(status):
                # Other 4xx (auth, forbidden, bad-request) — not transient,
                # not "end of pagination" canonically. Lenient stop with
                # a warning so anomalies surface in logs.
                log.warning(
                    "api_sniffer.http_fetch_non_retryable_status",
                    url=url,
                    status=status,
                )
                if raise_non_retryable:
                    raise PaginationFetchError(
                        url,
                        attempts=attempt + 1,
                        last_status=status,
                    ) from exc
                return None
            # else retryable — fall through to backoff
        except Exception as exc:  # noqa: BLE001 — timeout, network, parse error
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "api_sniffer.http_fetch_backoff",
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


async def http_fetch(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict | None = None,
    body: str | None = None,
) -> dict | None:
    """Lenient JSON fetcher: returns parsed JSON or ``None`` on any error.

    Thin wrapper over :func:`http_fetch_with_retry` that catches
    :class:`PaginationFetchError` and returns ``None`` so legacy callers
    (the api_sniffer scraper at ``scrapers/api_sniffer.py:440``) keep
    their existing "any failure → None" contract while still benefiting
    from the bounded retry budget.

    New monitor / pagination callers should prefer
    :func:`http_fetch_with_retry` directly so persistent transient
    failures fail loudly rather than silently truncating to the empty
    result set (#2733).
    """
    try:
        return await http_fetch_with_retry(client, method, url, headers, body)
    except PaginationFetchError as exc:
        log.warning(
            "api_sniffer.http_fetch_failed",
            url=url,
            attempts=exc.attempts,
            last_status=exc.last_status,
            last_error=exc.last_error,
        )
        return None


def _extract_urls_from_html(
    html: str,
    board_url: str,
    url_regex: str | None = None,
) -> set[str]:
    """Extract URLs from an HTML string via regex.

    Default regex captures all ``href`` attribute values.  A custom
    *url_regex* (with one capture group) can be supplied to match other
    patterns.
    """
    pattern = re.compile(url_regex) if url_regex else _DEFAULT_HREF_RE
    urls: set[str] = set()
    for match in pattern.finditer(html):
        raw = match.group(1)
        if raw.startswith(("javascript:", "mailto:")):
            continue
        urls.add(urljoin(board_url, raw))
    return urls


async def _refresh_post_data(
    client: httpx.AsyncClient,
    board_url: str,
    post_data: str | None,
    refresh_config: dict | None,
) -> str | None:
    """Refresh dynamic POST fields from the current board page.

    Some APIs protect otherwise public listing requests with a short-lived
    token embedded in the careers page. ``post_data_refresh.fields`` maps a
    POST field name to a regex with exactly one capture group. Fetching the
    source page through the board client also establishes any cookies tied to
    that token before the API request is replayed.
    """
    if not refresh_config:
        return post_data
    if not post_data:
        raise ValueError("post_data_refresh requires post_data")

    fields = refresh_config.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("post_data_refresh.fields must be a non-empty mapping")
    if len(fields) > _MAX_REFRESH_FIELDS:
        raise ValueError(f"post_data_refresh supports at most {_MAX_REFRESH_FIELDS} fields")

    source_url = refresh_config.get("source_url") or board_url
    if not isinstance(source_url, str) or not source_url:
        raise ValueError("post_data_refresh.source_url must be a non-empty URL")
    html = await fetch_text_page_with_retry(
        client,
        source_url,
        timeout=30,
        follow_redirects=True,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=_MAX_REFRESH_PAGE_BYTES,
        log_event="api_sniffer.refresh_page_backoff",
    )
    if html is None:
        raise RuntimeError("post_data_refresh source page returned no content")

    refreshed = post_data
    for field, pattern in fields.items():
        if not isinstance(field, str) or not isinstance(pattern, str):
            raise ValueError("post_data_refresh fields and patterns must be strings")
        if not field or len(field) > 256:
            raise ValueError("post_data_refresh field names must contain 1-256 characters")
        if not pattern or len(pattern) > _MAX_REFRESH_PATTERN_CHARS:
            raise ValueError(
                f"post_data_refresh patterns must contain 1-{_MAX_REFRESH_PATTERN_CHARS} characters"
            )
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid post_data_refresh pattern for {field!r}") from exc
        if compiled.groups != 1:
            raise ValueError(
                f"post_data_refresh pattern for {field!r} must have exactly one capture group"
            )
        match = compiled.search(html)
        value = match.group(1) if match is not None else None
        if value is None or not value or len(value) > _MAX_REFRESH_VALUE_CHARS:
            raise ValueError(
                f"post_data_refresh pattern for {field!r} did not match one bounded value"
            )
        updated = set_body_param(refreshed, field, value)
        if updated == refreshed:
            raise ValueError(f"post_data_refresh field {field!r} is missing from post_data")
        refreshed = updated

    log.info("api_sniffer.post_data_refreshed", fields=sorted(fields))
    return refreshed


def _matches_explicit_empty_response(data: object, config: object) -> bool:
    """Validate provider-specific markers for a successful empty response."""
    if not isinstance(config, dict) or not config:
        raise ValueError("empty_response must be a non-empty path-to-value mapping")
    for path, expected in config.items():
        if not isinstance(path, str) or not path or len(path) > 256:
            raise ValueError("empty_response paths must contain 1-256 characters")
        if isinstance(expected, (dict, list)):
            raise ValueError("empty_response expected values must be JSON scalars")
        if resolve_path(data, path) != expected:
            return False
    return True


async def _discover_http(
    board: dict,
    client: httpx.AsyncClient,
    config: dict,
    pw=None,
) -> list[DiscoveredJob] | set[str] | MonitorResult:
    """Discover jobs via plain httpx — no Playwright needed.

    After fetching JSON from *api_url*, the content at *json_path* is
    inspected:

    - **string** → HTML mode: extract URLs via regex.
    - **list** → items mode: use standard item extraction.

    When *json_path* is omitted, auto-detects the best candidate in the
    response (largest array-of-dicts, or longest HTML string with links).
    Also auto-detects *total_path*, *url_field*, and *fields* when not
    explicitly configured.

    If the initial fetch fails and ``api_url_match`` is configured with
    *pw* available, opens a browser to discover the live URL and retries
    via HTTP.
    """
    board_url = board["board_url"]
    api_url = config["api_url"]
    params = config.get("params")
    if params:
        api_url = _merge_params(api_url, params)
    method = config.get("method", "GET")
    json_path = config.get("json_path")
    url_field = config.get("url_field")
    url_template = config.get("url_template")
    url_template_fields = config.get("url_template_fields") or {}
    slug_fields = _validated_slug_fields(config)
    item_filter = _validated_item_filter(config)
    pagination_convergence = _validated_pagination_convergence(config, item_filter[3])
    url_field_match = _validated_url_field_match(config, pagination_convergence)
    item_filter_paths = [
        *item_filter[0],
        *item_filter[1],
        *item_filter[2],
        *item_filter[3],
    ]
    if item_filter[4] is not None:
        item_filter_paths.extend(item_filter[4].fallback_by)
    if pagination_convergence is not None:
        item_filter_paths.extend(pagination_convergence.identity_paths)
        item_filter_paths.extend(pagination_convergence.stable_fields)
    if url_field_match is not None:
        item_filter_paths.extend(path for _group, path in url_field_match.fields)
    url_regex = config.get("url_regex")
    total_path = config.get("total_path")
    post_data = _configured_post_data(config)
    request_headers = config.get("request_headers") or config.get("headers") or {}
    fields_map: dict[str, str] = config.get("fields") or {}
    pagination_config = config.get("pagination")

    post_data = await _refresh_post_data(
        client,
        board_url,
        post_data,
        config.get("post_data_refresh"),
    )
    headers = clean_headers(request_headers)

    # -- first page --------------------------------------------------------
    # Strict variant raises on persistent transient (5xx, network) so the
    # whole board run is recorded as a failure instead of silently
    # returning "no items" — the same shape of bug as #2722. ``None`` is
    # reserved for 404/410 (URL stale → browser fallback below) and other
    # non-retryable 4xx (lenient stop).
    api_url_match = config.get("api_url_match")
    data = await http_fetch_with_retry(client, method, api_url, headers, post_data)

    if data is None and api_url_match and pw is not None:
        # Stored URL may be stale (rotating token).  Open browser to discover
        # the live URL, then retry via plain HTTP.
        from src.shared.browser import BROWSER_KEYS, open_page

        wait = config.get("wait", _DEFAULT_WAIT)
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        settle = config.get("settle", _DEFAULT_SETTLE)
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

        route_params = config.get("route_params")
        async with open_page(pw, browser_config, use_proxy=bool(config.get("proxy"))) as page:
            fresh_url, captured_data = await _discover_live_url(
                page,
                board_url,
                api_url,
                api_url_match,
                wait,
                timeout,
                settle,
                route_params=route_params,
            )
        if captured_data is not None:
            # Use the response the page's own JS already fetched
            log.info("api_sniffer.using_captured_response", url=fresh_url[:80])
            api_url = fresh_url
            data = captured_data
        elif fresh_url != api_url:
            # URL changed but no response captured — retry via HTTP.
            # Strict variant: a persistent 5xx on the freshly-discovered
            # URL raises, so we don't silently return empty for a real
            # outage. End-of-pagination (404) still falls through.
            log.info("api_sniffer.http_retry_live_url", old=api_url[:80], new=fresh_url[:80])
            api_url = fresh_url
            data = await http_fetch_with_retry(client, method, api_url, headers, post_data)

    empty_response = config.get("empty_response")
    if data is None:
        if empty_response is not None:
            raise ValueError("API did not return the configured explicit empty response")
        if pagination_convergence:
            log.warning("api_sniffer.pagination_convergence_missing_response")
            return _truncated_empty_result(fields_map)
        return list() if fields_map else set()

    # -- decrypt encrypted response field ----------------------------------
    decrypt_cfg = config.get("response_decrypt")
    if decrypt_cfg:
        data = _apply_response_decrypt(data, decrypt_cfg)

    # -- auto-detect json_path when not configured -------------------------
    content: object = None
    if json_path is not None:
        content = resolve_path(data, json_path) if json_path else data
        # json_path_values: treat a dict-of-items as its values list.
        # Some APIs (e.g. TalentClue) return {"jobs": {"<id>": {...}}}
        # rather than {"jobs": [{...}]}.
        if config.get("json_path_values") and isinstance(content, dict):
            content = list(content.values())
    else:
        # Try arrays first (items mode), then HTML strings
        arrays = find_arrays(data)
        if arrays:
            best_path, best_items = pick_best_array(arrays, api_url)
            json_path = best_path
            content = best_items
            log.info("api_sniffer.auto_json_path", path=json_path, items=len(best_items))
        else:
            html_hits = find_html_strings(data)
            if html_hits:
                json_path = html_hits[0][0]
                content = html_hits[0][1]
                log.info("api_sniffer.auto_json_path_html", path=json_path)
            else:
                json_path = ""
                content = data

    # -- auto-detect total_path when not configured ------------------------
    total: int | None = None
    if total_path:
        raw_total = resolve_path(data, total_path)
        if isinstance(raw_total, (int, float)):
            total = int(raw_total)
    elif json_path:
        total = find_total_count(data, json_path)
        if total is not None:
            log.info("api_sniffer.auto_total", total=total)

    # -- HTML string mode --------------------------------------------------
    if isinstance(content, str):
        all_urls = _extract_urls_from_html(content, board_url, url_regex)
        if not all_urls and empty_response is not None:
            if _matches_explicit_empty_response(data, empty_response):
                log.info("api_sniffer.explicit_empty_response")
            else:
                raise ValueError(
                    "API response contained no job links and did not match the configured "
                    "empty response"
                )
        log.info(
            "api_sniffer.http_html_page",
            page=1,
            urls=len(all_urls),
            total=total,
        )

        if pagination_config and all_urls:
            page_size = pagination_config.get("page_size", len(all_urls))
            page_cap = pagination_config.get("max_pages", _HTTP_MAX_PAGES)
            max_pages = page_cap
            if total and page_size:
                max_pages = min(ceil(total / page_size), page_cap)

            pag_param = pagination_config["param_name"]
            pag_start = pagination_config.get("start_value", 0)
            pag_increment = pagination_config.get("increment", 1)
            pag_location = pagination_config.get("location", "query")

            current_value = pag_start + pag_increment
            pages_fetched = 1

            while pages_fetched < max_pages:
                if pag_location == "query":
                    fetch_url = set_url_param(api_url, pag_param, current_value)
                    fetch_body = post_data
                else:
                    fetch_url = api_url
                    fetch_body = set_body_param(post_data, pag_param, current_value)

                # Strict pagination semantic (#2733). ``http_fetch_with_retry``
                # returns ``None`` only on legitimate 404/410 end-of-pagination
                # or non-retryable 4xx; persistent 5xx / network errors raise
                # ``PaginationFetchError``, which propagates out of
                # ``_discover_http`` -> ``discover`` ->
                # ``_process_one_board_streaming``'s ``except Exception``
                # so the run is recorded as a failure. No silent truncation.
                page_data = await http_fetch_with_retry(
                    client,
                    method,
                    fetch_url,
                    headers,
                    fetch_body,
                )
                if page_data is None:
                    break

                page_content = resolve_path(page_data, json_path) if json_path else page_data
                if not isinstance(page_content, str) or not page_content.strip():
                    break

                new_urls = _extract_urls_from_html(page_content, board_url, url_regex)
                if not new_urls - all_urls:
                    break
                all_urls |= new_urls

                pages_fetched += 1
                current_value += pag_increment

            log.info(
                "api_sniffer.http_html_done",
                pages=pages_fetched,
                urls=len(all_urls),
            )

        # Convergence is defined over stable item identities and full row
        # counts. A schema drift into HTML cannot satisfy that proof.
        truncated = bool(pagination_convergence) or _materially_below_advertised_total(
            len(all_urls), total
        )
        _log_incomplete_total(len(all_urls), total)
        return truncated_url_result(all_urls) if truncated else all_urls

    # -- list/items mode ---------------------------------------------------
    if isinstance(content, list):
        from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult, PaginationInfo

        items = [item for item in content if isinstance(item, dict)]
        if not items and empty_response is not None:
            if not isinstance(data, dict) or not _matches_explicit_empty_response(
                data, empty_response
            ):
                raise ValueError(
                    "API returned an empty job list that did not match the configured "
                    "empty response"
                )
            log.info("api_sniffer.explicit_empty_response")
        item_projector = _build_item_projector(
            fields_map,
            url_field,
            url_template,
            url_template_fields,
            slug_fields,
            item_filter_paths,
        )

        pagination_proven = True
        if pagination_config and (items or pagination_convergence):
            pag = PaginationInfo(
                param_name=pagination_config["param_name"],
                style=pagination_config.get("style", "page"),
                start_value=pagination_config.get("start_value", 0),
                increment=pagination_config.get("increment", 1),
                location=pagination_config.get("location", "query"),
            )
            ex = Exchange(
                method=method,
                url=api_url,
                request_headers=request_headers,
                post_data=post_data,
                status=200,
                body=data,
                content_type="application/json",
                phase="load",
            )
            cand = ArrayCandidate(exchange=ex, json_path=json_path or "$", items=items)
            job_result = JobListResult(
                candidate=cand,
                url_field=url_field,
                total_count=total,
                pagination=pag,
            )
            page_cap = pagination_config.get("max_pages", _HTTP_MAX_PAGES)
            fetch_fn = make_http_fetcher(client)
            if pagination_convergence:
                items, pagination_proven = await _paginate_until_converged(
                    fetch_fn=fetch_fn,
                    method=method,
                    api_url=api_url,
                    request_headers=request_headers,
                    post_data=post_data,
                    initial_data=data,
                    initial_items=items,
                    json_path=json_path or "$",
                    total_path=total_path,
                    total_count=total,
                    pagination_config=pagination_config,
                    max_pages=page_cap,
                    identity_paths=pagination_convergence.identity_paths,
                    stable_fields=pagination_convergence.stable_fields,
                    reject_duplicate_identities=(
                        pagination_convergence.reject_duplicate_identities
                    ),
                    max_passes=pagination_convergence.max_passes,
                    required_no_growth_passes=(pagination_convergence.required_no_growth_passes),
                    item_projector=item_projector,
                    item_validator=(
                        (
                            lambda item: _matches_url_field_contract(
                                item,
                                cast(str, url_field),
                                url_field_match,
                            )
                        )
                        if url_field_match is not None
                        else None
                    ),
                )
            else:
                items = await paginate_all(
                    fetch_fn,
                    job_result,
                    page_cap,
                    item_projector=item_projector,
                )
                total = job_result.total_count
        elif item_projector:
            items = [item_projector(item) for item in items]

        source_item_count = len(items)
        filter_total = None if pagination_convergence else total
        items, total = _apply_item_filter(items, item_filter, filter_total)
        if pagination_convergence:
            total = len(items) if pagination_proven else None

        # MAX_ITEMS cap (#3216 / #3267). Don't slice silently: keep every
        # item so the URLs the monitor *did* collect are still inserted,
        # but flag the cycle as truncated so the board processor skips
        # ``_MARK_GONE_BY_TIMESTAMP`` and the unseen tail beyond the cap
        # is not tombstoned. Matches the pattern used by the 29 monitors
        # migrated in #3266 (lever, workday, greenhouse, ...).
        max_items = config.get("max_items", MAX_ITEMS)

        log.info("api_sniffer.http_items_done", items=len(items))

        # -- auto-detect url_field when not configured ---------------------
        if not url_field and not url_template and items:
            url_field = find_url_field(items)
            if url_field:
                log.info("api_sniffer.auto_url_field", field=url_field)

        # -- auto-detect fields when not configured ------------------------
        if not fields_map and items:
            fields_map = auto_map_fields(items)
            if fields_map:
                log.info("api_sniffer.auto_fields", fields=list(fields_map.keys()))

        if fields_map:
            jobs = _extract_rich(
                items,
                fields_map,
                url_field,
                url_template,
                board_url,
                root=data,
                url_template_fields=url_template_fields,
                slug_fields=slug_fields,
            )
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len({job.url for job in jobs}),
                total=total,
                cap=max_items,
            )
            return truncated_rich_result(jobs) if truncated else jobs
        if url_template:
            urls_from_tpl = _extract_urls_from_template(
                items,
                url_template,
                board_url,
                url_template_fields=url_template_fields,
                slug_fields=slug_fields,
            )
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len(urls_from_tpl),
                total=total,
                cap=max_items,
            )
            return truncated_url_result(urls_from_tpl) if truncated else urls_from_tpl
        # Support nested url_field paths (e.g. "data.apply_url")
        if url_field and ("." in url_field or "[" in url_field):
            urls: set[str] = set()
            for item in items:
                raw = extract_field(item, url_field)
                if isinstance(raw, str) and raw:
                    urls.add(urljoin(board_url, raw))
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len(urls),
                total=total,
                cap=max_items,
            )
            return truncated_url_result(urls) if truncated else urls
        urls = set(extract_urls(items, url_field, board_url))
        truncated = not pagination_proven or _item_result_is_truncated(
            item_count=source_item_count,
            discovered_count=len(urls),
            total=total,
            cap=max_items,
        )
        return truncated_url_result(urls) if truncated else urls

    if pagination_convergence:
        log.warning(
            "api_sniffer.pagination_convergence_invalid_list_shape",
            content_type=type(content).__name__,
        )
        return _truncated_empty_result(fields_map)

    if empty_response is not None:
        if not isinstance(data, dict):
            raise ValueError("explicit empty-response validation requires a JSON object")
        if _matches_explicit_empty_response(data, empty_response):
            log.info("api_sniffer.explicit_empty_response")
            return list() if fields_map else set()
        raise ValueError("API response was neither a job list nor the configured empty response")

    log.warning(
        "api_sniffer.unexpected_content_type",
        json_path=json_path,
        content_type=type(content).__name__,
    )
    return list() if fields_map else set()


async def _discover_live_url(
    page,
    board_url: str,
    api_url: str,
    api_url_match: str,
    wait: str,
    timeout: int,
    settle: float,
    route_params: dict[str, str] | None = None,
) -> tuple[str, object | None]:
    """Navigate and capture the live API URL + response matching *api_url_match*.

    When APIs use rotating tokens in the URL (e.g. ``gateway.example.com/TOKEN/v1/jobs``),
    the stored ``api_url`` goes stale. This helper navigates the page, intercepts
    responses matching the glob, and returns ``(updated_api_url, response_json)``.

    When *route_params* is provided, matching requests are intercepted via
    ``page.route()`` and their query parameters are overridden before the
    page's own JS sends them.  This lets us e.g. increase ``pageSize`` to
    fetch all items in one request — using the page's native request
    mechanism (bypasses bot protection that blocks injected ``fetch()``).

    Falls back to ``(api_url, None)`` if no match.
    """
    from fnmatch import fnmatch

    from src.shared.browser import BrowserNavigationHTTPStatusError, navigate

    live_response = None  # Playwright Response object

    def _on_response(resp):
        nonlocal live_response
        if live_response:
            return
        parsed = urlparse(resp.url)
        if fnmatch(f"{parsed.netloc}{parsed.path}", api_url_match):
            live_response = resp

    page.on("response", _on_response)

    # Optionally modify the page's own outgoing requests
    if route_params:

        async def _modify_request(route):
            parsed = urlparse(route.request.url)
            params = parse_qs(parsed.query, keep_blank_values=True)
            for k, v in route_params.items():
                params[k] = [str(v)]
            new_query = urlencode(params, doseq=True)
            new_url = urlunparse(parsed._replace(query=new_query))
            log.debug("api_sniffer.route_modified", params=route_params)
            await route.continue_(url=new_url)

        # Convert fnmatch glob to a Playwright glob (** prefix for protocol+host)
        await page.route(f"**/{api_url_match.split('/', 1)[-1]}*", _modify_request)

    try:
        await navigate(page, board_url, {"wait": wait, "timeout": timeout})
    except BrowserNavigationHTTPStatusError:
        raise
    except Exception:
        log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)
    await asyncio.sleep(settle)

    if live_response:
        live_url = live_response.url
        live_base = urlunparse(urlparse(live_url)._replace(query=""))
        stored_base = urlunparse(urlparse(api_url)._replace(query=""))
        updated_url = api_url
        if live_base != stored_base:
            log.info(
                "api_sniffer.live_url_updated",
                stored=stored_base[:80],
                live=live_base[:80],
            )
            updated_url = api_url.replace(stored_base, live_base)

        # Try to read the response body (already available, page's JS fetched it)
        try:
            data = await live_response.json()
            log.info("api_sniffer.live_response_captured", url=live_url[:80])
            return updated_url, data
        except Exception:
            log.debug("api_sniffer.live_response_read_failed", exc_info=True)
            return updated_url, None

    return api_url, None


async def _discover_replay(
    board_url: str,
    config: dict,
    pw,
    client=None,
) -> list[DiscoveredJob] | set[str] | MonitorResult:
    """Replay a stored API call, optionally paginating.

    Supports HTTP fallback: if the in-browser fetch fails and *client* is
    provided, retries with plain httpx.  Browser config keys (``headless``,
    ``user_agent``) are forwarded to Playwright.
    """
    from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult, PaginationInfo
    from src.shared.browser import (
        BROWSER_KEYS,
        BrowserNavigationHTTPStatusError,
        navigate,
        open_page,
    )

    api_url = config["api_url"]
    params = config.get("params")
    if params:
        api_url = _merge_params(api_url, params)
    method = config.get("method", "GET")
    json_path = config.get("json_path", "$")
    url_field = config.get("url_field")
    url_template = config.get("url_template")
    url_template_fields = config.get("url_template_fields") or {}
    slug_fields = _validated_slug_fields(config)
    item_filter = _validated_item_filter(config)
    pagination_convergence = _validated_pagination_convergence(config, item_filter[3])
    url_field_match = _validated_url_field_match(config, pagination_convergence)
    item_filter_paths = [
        *item_filter[0],
        *item_filter[1],
        *item_filter[2],
        *item_filter[3],
    ]
    if item_filter[4] is not None:
        item_filter_paths.extend(item_filter[4].fallback_by)
    if pagination_convergence is not None:
        item_filter_paths.extend(pagination_convergence.identity_paths)
        item_filter_paths.extend(pagination_convergence.stable_fields)
    if url_field_match is not None:
        item_filter_paths.extend(path for _group, path in url_field_match.fields)
    post_data = _configured_post_data(config)
    request_headers = config.get("request_headers", {})
    fields_map: dict[str, str] = config.get("fields") or {}
    pagination_config = config.get("pagination")
    total_path = config.get("total_path")
    api_url_match = config.get("api_url_match")
    route_params = config.get("route_params")

    wait = config.get("wait", _DEFAULT_WAIT)
    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    settle = config.get("settle", _DEFAULT_SETTLE)

    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

    async with open_page(pw, browser_config, use_proxy=bool(config.get("proxy"))) as page:
        # route_params requires upfront navigation to intercept the page's
        # own request and modify its params.  Otherwise, navigate just to
        # establish cookies, then try the stored URL via replay first.
        captured_data = None
        if api_url_match and route_params:
            api_url, captured_data = await _discover_live_url(
                page,
                board_url,
                api_url,
                api_url_match,
                wait,
                timeout,
                settle,
                route_params=route_params,
            )
        else:
            # Navigate to board_url to establish cookies/auth context.
            # Capture exchanges so we can refresh stale auth headers from
            # the requests the page's own JS fires during load.
            api_parsed = urlparse(api_url)
            nav_exchanges = await capture_exchanges(page, api_parsed.netloc)
            try:
                await navigate(page, board_url, {"wait": wait, "timeout": timeout})
            except BrowserNavigationHTTPStatusError:
                raise
            except Exception:
                log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)
            await asyncio.sleep(settle)

            # If the page hit the same API endpoint, use its fresh headers
            # (auth tokens / session headers refreshed by the page's JS).
            for ex in nav_exchanges:
                ex_parsed = urlparse(ex.url)
                if ex_parsed.netloc == api_parsed.netloc and ex_parsed.path == api_parsed.path:
                    request_headers = ex.request_headers
                    log.info("api_sniffer.headers_refreshed", url=ex.url[:80])
                    break

        # Replay the API call — try browser first, fall back to HTTP
        headers = clean_headers(request_headers)
        fetch_fn = make_browser_fetcher(page)
        using_http = False
        data = captured_data  # may already have data from route_params capture
        if data is None:
            try:
                data = await fetch_fn(method, api_url, headers, post_data)
            except Exception:
                # Stored URL may be stale — try live URL discovery
                if api_url_match:
                    log.info("api_sniffer.retry_with_live_url", pattern=api_url_match)
                    fresh_url, fresh_data = await _discover_live_url(
                        page,
                        board_url,
                        api_url,
                        api_url_match,
                        wait,
                        timeout,
                        settle,
                        route_params=route_params,
                    )
                    if fresh_data is not None:
                        api_url = fresh_url
                        data = fresh_data
                    elif fresh_url != api_url:
                        api_url = fresh_url
                        with contextlib.suppress(Exception):
                            data = await fetch_fn(method, api_url, headers, post_data)

            if data is None and client is not None:
                log.warning(
                    "api_sniffer.browser_fetch_failed_fallback_http",
                    api_url=api_url,
                    exc_info=not using_http,
                )
                fetch_fn = make_http_fetcher(client)
                using_http = True
                try:
                    data = await fetch_fn(method, api_url, headers, post_data)
                except Exception as exc:
                    # Every replay path has now failed: in-browser fetch, live-URL
                    # rediscovery (if any), and HTTP fallback.  Propagate so the
                    # board processor records a failure and the consecutive-failure
                    # counter can trip the auto-disable at 5.
                    log.warning(
                        "api_sniffer.http_fallback_failed",
                        api_url=api_url,
                        board_url=board_url,
                        exc_info=True,
                    )
                    api_sniffer_fallback_failed_total.labels(reason="http_fallback").inc()
                    raise ApiSnifferFallbackError(
                        f"api_sniffer fallback exhausted for {api_url}: {exc}",
                        board_url=board_url,
                        api_url=api_url,
                    ) from exc

            if data is None:
                # Browser fetch returned None and no HTTP client was available
                # (or the fallback path consumed the exception without data).
                log.warning(
                    "api_sniffer.replay_failed",
                    api_url=api_url,
                    board_url=board_url,
                    exc_info=True,
                )
                api_sniffer_fallback_failed_total.labels(reason="replay_failed").inc()
                raise ApiSnifferFallbackError(
                    f"api_sniffer replay failed for {api_url} (no data returned)",
                    board_url=board_url,
                    api_url=api_url,
                )

        # Decrypt encrypted response field
        decrypt_cfg = config.get("response_decrypt")
        if decrypt_cfg:
            data = _apply_response_decrypt(data, decrypt_cfg)

        # json_path_values: treat a dict-of-items at json_path as its values list.
        # Some APIs (e.g. TalentClue) return {"jobs": {"<id>": {...}}} rather
        # than {"jobs": [{...}]}; coerce before extract_items.
        items: list[dict] | None = None
        if config.get("json_path_values") and json_path:
            resolved = resolve_path(data, json_path)
            if isinstance(resolved, dict):
                items = [v for v in resolved.values() if isinstance(v, dict)]

        if items is None:
            items = extract_items(data, json_path)
        if pagination_convergence:
            if json_path == "$":
                configured_items = data
            elif isinstance(data, dict):
                configured_items = resolve_path(data, json_path)
            else:
                configured_items = None
            if not isinstance(configured_items, list):
                log.warning(
                    "api_sniffer.pagination_convergence_invalid_list_shape",
                    content_type=type(configured_items).__name__,
                )
                return _truncated_empty_result(fields_map)
        if not items and not pagination_convergence:
            log.warning("api_sniffer.no_items", api_url=api_url, json_path=json_path)
            return list() if fields_map else set()

        total_count: int | None = None
        if total_path:
            raw_total = resolve_path(data, total_path)
            if isinstance(raw_total, (int, float)):
                total_count = int(raw_total)
        elif json_path:
            total_count = find_total_count(data, json_path)

        item_projector = _build_item_projector(
            fields_map,
            url_field,
            url_template,
            url_template_fields,
            slug_fields,
            item_filter_paths,
        )

        # Paginate if configured
        pagination_proven = True
        if pagination_config and (items or pagination_convergence):
            pag = PaginationInfo(
                param_name=pagination_config["param_name"],
                style=pagination_config["style"],
                start_value=pagination_config["start_value"],
                increment=pagination_config["increment"],
                location=pagination_config["location"],
            )
            ex = Exchange(
                method=method,
                url=api_url,
                request_headers=request_headers,
                post_data=post_data,
                status=200,
                body=data,
                content_type="application/json",
                phase="load",
            )
            cand = ArrayCandidate(exchange=ex, json_path=json_path, items=items)
            job_result = JobListResult(
                candidate=cand,
                url_field=url_field,
                total_count=total_count,
                pagination=pag,
            )
            default_cap = _HTTP_MAX_PAGES if using_http else MAX_PAGES
            max_pg = pagination_config.get("max_pages", default_cap)
            # When total_count is known, raise cap to _HTTP_MAX_PAGES so
            # APIs with small page sizes are not silently truncated.
            if total_count and items and max_pg < _HTTP_MAX_PAGES:
                needed = (total_count + len(items) - 1) // len(items)
                if needed > max_pg:
                    max_pg = min(needed, _HTTP_MAX_PAGES)
            if pagination_convergence:
                items, pagination_proven = await _paginate_until_converged(
                    fetch_fn=fetch_fn,
                    method=method,
                    api_url=api_url,
                    request_headers=request_headers,
                    post_data=post_data,
                    initial_data=data,
                    initial_items=items,
                    json_path=json_path,
                    total_path=total_path,
                    total_count=total_count,
                    pagination_config=pagination_config,
                    max_pages=max_pg,
                    identity_paths=pagination_convergence.identity_paths,
                    stable_fields=pagination_convergence.stable_fields,
                    reject_duplicate_identities=(
                        pagination_convergence.reject_duplicate_identities
                    ),
                    max_passes=pagination_convergence.max_passes,
                    required_no_growth_passes=(pagination_convergence.required_no_growth_passes),
                    item_projector=item_projector,
                    item_validator=(
                        (
                            lambda item: _matches_url_field_contract(
                                item,
                                cast(str, url_field),
                                url_field_match,
                            )
                        )
                        if url_field_match is not None
                        else None
                    ),
                )
            else:
                items = await paginate_all(
                    fetch_fn,
                    job_result,
                    max_pg,
                    item_projector=item_projector,
                )
                total_count = job_result.total_count
        elif item_projector:
            items = [item_projector(item) for item in items]

        source_item_count = len(items)
        filter_total = None if pagination_convergence else total_count
        items, total_count = _apply_item_filter(items, item_filter, filter_total)
        if pagination_convergence:
            total_count = len(items) if pagination_proven else None

        # MAX_ITEMS cap (#3216 / #3267). Don't slice silently: keep every
        # item so the URLs the monitor *did* collect are still inserted,
        # but flag the cycle as truncated so the board processor skips
        # ``_MARK_GONE_BY_TIMESTAMP`` and the unseen tail beyond the cap
        # is not tombstoned. Matches the pattern used by the 29 monitors
        # migrated in #3266 (lever, workday, greenhouse, ...).
        # Build URL map via DOM cross-ref if no url_field and no url_template
        url_map: dict[str, str] | None = None
        if not url_field and not url_template:
            try:
                from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

                dom_urls = await extract_urls_via_dom_crossref(page, items, board_url)
                if dom_urls:
                    id_f = None
                    for key in items[0]:
                        if _ID_FIELDS.match(key):
                            id_f = key
                            break
                    if id_f:
                        url_map = {}
                        for item, u in zip(items, dom_urls, strict=False):
                            url_map[str(item.get(id_f, ""))] = u
            except Exception:
                log.debug("api_sniffer.dom_crossref_degraded", exc_info=True)

        if fields_map:
            jobs = _extract_rich(
                items,
                fields_map,
                url_field,
                url_template,
                board_url,
                url_map=url_map,
                root=data,
                url_template_fields=url_template_fields,
                slug_fields=slug_fields,
            )
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len({job.url for job in jobs}),
                total=total_count,
                cap=MAX_ITEMS,
            )
            return truncated_rich_result(jobs) if truncated else jobs

        # URL-only mode
        if url_template:
            urls_from_tpl = _extract_urls_from_template(
                items,
                url_template,
                board_url,
                url_template_fields=url_template_fields,
                slug_fields=slug_fields,
            )
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len(urls_from_tpl),
                total=total_count,
                cap=MAX_ITEMS,
            )
            return truncated_url_result(urls_from_tpl) if truncated else urls_from_tpl
        urls = extract_urls(items, url_field, board_url)
        if not urls and url_map:
            urls_from_map = set(url_map.values())
            truncated = not pagination_proven or _item_result_is_truncated(
                item_count=source_item_count,
                discovered_count=len(urls_from_map),
                total=total_count,
                cap=MAX_ITEMS,
            )
            return truncated_url_result(urls_from_map) if truncated else urls_from_map
        if not urls:
            try:
                urls = await extract_urls_via_dom_crossref(page, items, board_url)
            except Exception:
                log.debug("api_sniffer.dom_crossref_degraded", exc_info=True)
        urls_set = set(urls)
        truncated = not pagination_proven or _item_result_is_truncated(
            item_count=source_item_count,
            discovered_count=len(urls_set),
            total=total_count,
            cap=MAX_ITEMS,
        )
        return truncated_url_result(urls_set) if truncated else urls_set


async def _discover_auto(
    board_url: str,
    config: dict,
    pw,
) -> list[DiscoveredJob] | set[str] | MonitorResult:
    """Full auto-discover: capture exchanges, detect, paginate."""
    from src.shared.browser import (
        BROWSER_KEYS,
        BrowserNavigationHTTPStatusError,
        dismiss_overlays,
        navigate,
        open_page,
    )

    fields_map: dict[str, str] = config.get("fields") or {}

    wait = config.get("wait", _DEFAULT_WAIT)
    timeout = config.get("timeout", _DEFAULT_TIMEOUT)
    settle = config.get("settle", _DEFAULT_SETTLE)

    browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

    async with open_page(pw, browser_config, use_proxy=bool(config.get("proxy"))) as page:
        page_host = urlparse(board_url).netloc
        exchanges = await capture_exchanges(page, page_host)

        try:
            await navigate(page, board_url, {"wait": wait, "timeout": timeout})
        except BrowserNavigationHTTPStatusError:
            raise
        except Exception:
            log.warning("api_sniffer.navigation_failed", board_url=board_url, exc_info=True)

        await asyncio.sleep(settle)
        await dismiss_overlays(page)
        captured_result = None
        try:
            await trigger_interactions(page, exchanges)
        except ApiSnifferDomUnavailableError:
            # A navigation timeout can leave Playwright on a transient
            # document with no body. If the page already emitted a usable
            # jobs response, the DOM fallback is unnecessary; otherwise fail
            # the cycle instead of converting an origin/navigation failure
            # into an authoritative empty result.
            captured_result = detect_job_list(exchanges, board_url)
            if captured_result is None:
                raise
            log.warning(
                "api_sniffer.interactions_skipped_no_dom",
                board_url=board_url,
                captured_exchanges=len(exchanges),
            )

        result = captured_result or detect_job_list(exchanges, board_url)
        if result is None:
            log.warning("api_sniffer.no_api_detected", board_url=board_url)
            return list() if fields_map else set()

        page_size = len(result.candidate.items)
        result.pagination = infer_pagination(
            exchanges,
            result.candidate.exchange.url,
            page_size,
        )

        items = await paginate_all(make_browser_fetcher(page), result, MAX_PAGES)

        # MAX_ITEMS cap (#3216 / #3267 / #3336). Don't slice silently: keep
        # every item so the URLs the monitor *did* collect are still
        # inserted, but flag the cycle as truncated so the board processor
        # skips ``_MARK_GONE_BY_TIMESTAMP`` and the unseen tail beyond the
        # cap is not tombstoned. Matches the pattern used by the 29
        # monitors migrated in #3266 and the sibling
        # ``_discover_http`` / ``_discover_replay`` paths wired in #3334.
        # Auto-map fields if not configured
        if not fields_map:
            fields_map = auto_map_fields(items)

        url_field = result.url_field

        # Build URL map via DOM cross-ref if no url_field
        url_map: dict[str, str] | None = None
        if not url_field and items:
            from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

            dom_urls = await extract_urls_via_dom_crossref(page, items, board_url)
            if dom_urls:
                id_f = None
                for key in items[0]:
                    if _ID_FIELDS.match(key):
                        id_f = key
                        break
                if id_f:
                    url_map = {}
                    for item, u in zip(items, dom_urls, strict=False):
                        url_map[str(item.get(id_f, ""))] = u

        if fields_map:
            # First-page body is the best available root; lookup tables
            # typically sit at response level, not per item.
            root = result.candidate.exchange.body if result and result.candidate else None
            jobs = _extract_rich(
                items,
                fields_map,
                url_field,
                None,
                board_url,
                url_map=url_map,
                root=root,
            )
            truncated = _item_result_is_truncated(
                item_count=len(items),
                discovered_count=len({job.url for job in jobs}),
                total=result.total_count,
                cap=MAX_ITEMS,
            )
            return truncated_rich_result(jobs) if truncated else jobs

        urls = extract_urls(items, url_field, board_url)
        if not urls and url_map:
            urls_from_map = set(url_map.values())
            truncated = _item_result_is_truncated(
                item_count=len(items),
                discovered_count=len(urls_from_map),
                total=result.total_count,
                cap=MAX_ITEMS,
            )
            return truncated_url_result(urls_from_map) if truncated else urls_from_map
        if not urls:
            urls = await extract_urls_via_dom_crossref(page, items, board_url)
        urls_set = set(urls)
        truncated = _item_result_is_truncated(
            item_count=len(items),
            discovered_count=len(urls_set),
            total=result.total_count,
            cap=MAX_ITEMS,
        )
        return truncated_url_result(urls_set) if truncated else urls_set


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_rich(
    items: list[dict],
    fields_map: dict[str, str | list[str]],
    url_field: str | None,
    url_template: str | None,
    board_url: str,
    url_map: dict[str, str] | None = None,
    *,
    root: dict | None = None,
    url_template_fields: dict[str, str] | None = None,
    slug_fields: list[str] | None = None,
) -> list[DiscoveredJob]:
    """Extract DiscoveredJob objects from items using field mapping.

    *url_map* is an optional pre-built mapping from item ID to URL
    (e.g. from DOM cross-reference).

    *root* is the top-level response object; required by field specs
    that use ``lookup_from`` (sibling-table joins for ATS payloads that
    ship a compact listing alongside a shared lookup dict). Callers that
    paginate and stitch items from multiple responses should pass the
    first page's root — the lookup tables are response-level constants
    in the ATSes we've seen.
    """
    from urllib.parse import urljoin

    # Build id_field lookup for url_map
    id_field = None
    if url_map and items:
        from src.shared.api_sniff import ID_FIELDS as _ID_FIELDS

        for key in items[0]:
            if _ID_FIELDS.match(key):
                id_field = key
                break

    jobs: list[DiscoveredJob] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # Build URL
        url = None
        if url_template:
            with contextlib.suppress(KeyError, IndexError, ValueError):
                url = _format_url_template(
                    item,
                    url_template,
                    url_template_fields,
                    slug_fields,
                )
        if not url and url_map and id_field:
            item_id = str(item.get(id_field, ""))
            url = url_map.get(item_id)
        if not url and url_field:
            needs_extract = "." in url_field or "[" in url_field
            raw = extract_field(item, url_field) if needs_extract else item.get(url_field)
            if isinstance(raw, str) and raw:
                url = urljoin(board_url, raw)
        if not url:
            # Try to find any URL in the item
            for val in item.values():
                if isinstance(val, str) and val.startswith(("http://", "https://")):
                    url = val
                    break
        if not url:
            continue

        kwargs: dict[str, object] = {"url": url}
        metadata_fields: dict[str, object] = {}
        extras: dict[str, object] = {}

        for target, spec in fields_map.items():
            if isinstance(spec, list):
                # Multi-field concatenation: extract each path and join
                parts: list[str] = []
                for s in spec:
                    v = extract_field(item, s, root=root)
                    if v is not None:
                        parts.append(v if isinstance(v, str) else " ".join(v))
                value = "\n\n".join(parts) if parts else None
            else:
                value = extract_field(item, spec, root=root)
            if value is None:
                continue
            # Wildcard paths such as ``modularContent[].text`` naturally
            # resolve to a list.  Description is a scalar HTML field on
            # DiscoveredJob, so preserve every fragment in source order
            # instead of leaking a list into downstream text processing.
            if target == "description" and isinstance(value, list):
                value = "\n\n".join(part for part in value if part)
                if not value:
                    continue
            if target.startswith("metadata."):
                metadata_fields[target.removeprefix("metadata.")] = value
            elif target in (
                "title",
                "description",
                "employment_type",
                "job_location_type",
                "date_posted",
            ):
                kwargs[target] = value
            elif target == "locations":
                kwargs["locations"] = value if isinstance(value, list) else [value]
            elif target in ("skills", "responsibilities", "qualifications"):
                extras[target] = value if isinstance(value, list) else [value]
            elif target == "valid_through":
                extras["valid_through"] = value
            elif target == "base_salary":
                kwargs["base_salary"] = value
            else:
                metadata_fields[target] = value

        if metadata_fields:
            kwargs["metadata"] = metadata_fields
        if extras:
            kwargs["extras"] = extras

        jobs.append(DiscoveredJob(**kwargs))

    return jobs


def _extract_urls_from_template(
    items: list[dict],
    url_template: str,
    board_url: str,
    *,
    url_template_fields: dict[str, str] | None = None,
    slug_fields: list[str] | None = None,
) -> set[str]:
    """Build URL-only set from items using a URL template."""
    from urllib.parse import urljoin

    urls: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            url = _format_url_template(
                item,
                url_template,
                url_template_fields,
                slug_fields,
            )
            urls.add(urljoin(board_url, url))
        except (KeyError, IndexError, ValueError):
            continue
    return urls


def _format_url_template(
    item: dict,
    url_template: str,
    url_template_fields: dict[str, str] | None,
    slug_fields: list[str] | None = None,
) -> str:
    """Render a job URL from top-level fields plus explicit nested aliases.

    ``str.format_map`` handles top-level scalar item fields, which covers most
    APIs. Some ATS payloads keep the public posting ID in a nested custom-field
    array while exposing a separate top-level ID for detail API calls. The
    alias map lets a config name those nested values without mutating the API
    response or embedding Python-format indexing syntax in the URL.
    """
    values = {k: v for k, v in item.items() if isinstance(v, (str, int, float))}
    for alias, path in (url_template_fields or {}).items():
        if not isinstance(alias, str) or not isinstance(path, str):
            continue
        value = extract_field(item, path)
        if isinstance(value, (str, int, float)):
            values[alias] = value
    if slug_fields:
        slug_parts = []
        for path in slug_fields:
            value = extract_field(item, path)
            if value is not None:
                slug = slugify(str(value))
                if slug:
                    slug_parts.append(slug)
        if slug_parts:
            values["slug"] = "-".join(slug_parts)
    return url_template.format_map(values)


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    api_url = metadata.get("api_url")
    if not api_url:
        return
    await save_json_response(
        artifact_dir,
        client,
        api_url,
        follow_redirects=True,
    )


register("api_sniffer", discover, cost=80, can_handle=can_handle, save_raw=save_raw)
