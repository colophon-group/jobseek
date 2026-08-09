"""Typify vacancy-search monitor.

Typify's Drupal widget exposes a public form API. Large unfiltered result sets
are not stably ordered, so ordinary page-number pagination can rotate jobs out
of a cycle. The page also publishes mutually exclusive job-function filters.
This monitor discovers those filters on every run, fetches each complete
sub-cap partition, and accepts the cycle only when their union matches the
unfiltered advertised total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.core.monitors.api_sniffer import http_fetch_with_retry
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PARTITIONS = 100
MAX_PAGE_BYTES = 1_000_000
MAX_MAP_RESULTS = 2_001

_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_DATA_ID_RE = re.compile(r"\bdata-id\s*=\s*['\"](?P<id>\d+)['\"]", re.IGNORECASE)
_LANGUAGE_RE = re.compile(
    r"window\.typify\s*=\s*\{.*?\blanguage\s*:\s*['\"](?P<language>[a-z]{0,8})['\"]",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class PageConfig:
    api_url: str
    function_ids: tuple[str, ...]


class PartitionTooLargeError(ValueError):
    """A filter group still exceeds Typify's stable map-response boundary."""


def _trusted_board_parts(board_url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(board_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Typify board URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("Typify board URL is not a trusted HTTP origin")
    return parsed.hostname.lower(), parsed.netloc


def _page_config(page: str, board_url: str) -> PageConfig | None:
    """Extract the live API route and every advertised function partition."""
    if "window.typify" not in page:
        return None

    function_ids: list[str] = []
    seen: set[str] = set()
    for tag in _INPUT_RE.findall(page):
        if "cb-function" not in tag:
            continue
        match = _DATA_ID_RE.search(tag)
        if match and match.group("id") not in seen:
            seen.add(match.group("id"))
            function_ids.append(match.group("id"))
    if not function_ids or len(function_ids) > MAX_PARTITIONS:
        return None

    hostname, netloc = _trusted_board_parts(board_url)
    match = _LANGUAGE_RE.search(page)
    language = match.group("language").lower() if match else ""
    prefix = f"/{language}" if language not in {"", "nl"} else ""
    if not prefix and (hostname == "dominosjobs.de" or hostname.endswith(".dominosjobs.de")):
        prefix = "/de"
    return PageConfig(
        api_url=f"https://{netloc}{prefix}/api/vacancies",
        function_ids=tuple(function_ids),
    )


async def _load_page_config(board_url: str, client: httpx.AsyncClient) -> PageConfig | None:
    page = await fetch_text_page_with_retry(
        client,
        board_url,
        retries=3,
        base_delay=0.5,
        follow_redirects=True,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=MAX_PAGE_BYTES,
        log_event="typify.page_backoff",
    )
    return _page_config(page or "", board_url)


def _form_body(function_ids: tuple[str, ...], *, map_all: bool) -> str:
    fields: list[tuple[str, str]] = [
        ("type", "vacancy"),
        ("location", ""),
        ("km", "10"),
        ("lat", ""),
        ("lng", ""),
        ("page", "1"),
        ("map", "1" if map_all else "0"),
        ("collection_id", ""),
    ]
    fields.extend(("field_function[]", function_id) for function_id in function_ids)
    return urlencode(fields)


async def _fetch_payload(
    client: httpx.AsyncClient,
    *,
    board_url: str,
    api_url: str,
    function_ids: tuple[str, ...],
    map_all: bool,
) -> dict:
    payload = await http_fetch_with_retry(
        client,
        "POST",
        api_url,
        {
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "referer": board_url,
            "x-requested-with": "XMLHttpRequest",
        },
        _form_body(function_ids, map_all=map_all),
    )
    if not isinstance(payload, dict):
        raise ValueError("Typify vacancy API returned no JSON object")
    if payload.get("errors"):
        raise ValueError("Typify vacancy API returned errors")
    return payload


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Typify pagination {field} is invalid")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"Typify pagination {field} is invalid")


def _total(payload: dict) -> int:
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        raise ValueError("Typify response omitted pagination")
    return _nonnegative_int(pagination.get("total"), field="total")


def _partition_rows(payload: dict) -> tuple[int, list[object]]:
    total = _total(payload)
    pagination = payload["pagination"]
    total_pages = _nonnegative_int(pagination.get("total_pages"), field="total_pages")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("Typify response omitted results")
    expected_pages = 0 if total == 0 else 1
    if total_pages > expected_pages:
        raise PartitionTooLargeError(
            "Typify function partition exceeded the stable single-response boundary"
        )
    if total_pages != expected_pages or len(rows) != total:
        raise ValueError("Typify function partition returned inconsistent counts")
    return total, rows


def _parse_job(raw: object, board_url: str) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    raw_url = raw.get("url")
    if not isinstance(title, str) or not title.strip() or not isinstance(raw_url, str):
        return None
    url = urljoin(board_url, raw_url.strip())
    board_host, _netloc = _trusted_board_parts(board_url)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != board_host:
        return None

    location = raw.get("location")
    location_label = location.get("label") if isinstance(location, dict) else None
    return DiscoveredJob(
        url=url,
        title=title.strip(),
        locations=[location_label.strip()]
        if isinstance(location_label, str) and location_label.strip()
        else None,
    )


async def _detect(url: str, client: httpx.AsyncClient) -> dict | None:
    try:
        config = await _load_page_config(url, client)
        if config is None:
            return None
        payload = await _fetch_payload(
            client,
            board_url=url,
            api_url=config.api_url,
            function_ids=(),
            map_all=False,
        )
        return {"api_url": config.api_url, "jobs": _total(payload)}
    except TDMReservedError:
        raise
    except Exception:
        log.debug("typify.probe_failed", url=url, exc_info=True)
        return None


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a Typify vacancy widget and verify its public API."""
    _ = pw
    if client is None:
        return None
    return await _detect(url, client)


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return the complete union of live Typify function partitions."""
    _ = pw
    board_url = board["board_url"]
    page_config = await _load_page_config(board_url, client)
    if page_config is None:
        raise ValueError("Cannot derive Typify function partitions from board page")

    configured_api_url = (board.get("metadata") or {}).get("api_url")
    if configured_api_url and str(configured_api_url).rstrip("/") != page_config.api_url:
        raise ValueError("Configured Typify API URL no longer matches the live page")

    summary = await _fetch_payload(
        client,
        board_url=board_url,
        api_url=page_config.api_url,
        function_ids=(),
        map_all=False,
    )
    expected_total = _total(summary)
    jobs_by_url: dict[str, DiscoveredJob] = {}
    partition_rows = 0
    duplicates = 0

    def add_rows(rows: list[object]) -> None:
        nonlocal duplicates
        for raw in rows:
            job = _parse_job(raw, board_url)
            if job is None:
                raise ValueError("Typify function partition contained an invalid job")
            existing = jobs_by_url.get(job.url)
            if existing is not None:
                if existing != job:
                    raise ValueError(
                        "Typify function partitions contained conflicting duplicate jobs"
                    )
                duplicates += 1
                continue
            jobs_by_url[job.url] = job

    partitions_used = 0
    if expected_total <= MAX_MAP_RESULTS:
        payload = await _fetch_payload(
            client,
            board_url=board_url,
            api_url=page_config.api_url,
            function_ids=(),
            map_all=True,
        )
        total, rows = _partition_rows(payload)
        if total != expected_total:
            raise ValueError("Typify advertised total changed during discovery")
        partition_rows += total
        add_rows(rows)
        partitions_used = 1
    else:

        async def fetch_group(function_ids: tuple[str, ...]) -> None:
            nonlocal partition_rows, partitions_used
            payload = await _fetch_payload(
                client,
                board_url=board_url,
                api_url=page_config.api_url,
                function_ids=function_ids,
                map_all=True,
            )
            try:
                total, rows = _partition_rows(payload)
            except PartitionTooLargeError:
                if len(function_ids) == 1:
                    raise
                midpoint = len(function_ids) // 2
                await fetch_group(function_ids[:midpoint])
                await fetch_group(function_ids[midpoint:])
                return
            partition_rows += total
            partitions_used += 1
            add_rows(rows)

        if len(page_config.function_ids) == 1:
            await fetch_group(page_config.function_ids)
        else:
            midpoint = len(page_config.function_ids) // 2
            await fetch_group(page_config.function_ids[:midpoint])
            await fetch_group(page_config.function_ids[midpoint:])

    jobs = list(jobs_by_url.values())
    if partition_rows != expected_total:
        raise ValueError(
            "Typify function partition union does not match the advertised total "
            f"({len(jobs)} unique / {partition_rows} rows / {expected_total} advertised)"
        )

    truncated = expected_total > MAX_JOBS
    log_method = log.warning if truncated else log.info
    log_method(
        "typify.discovered",
        jobs=len(jobs),
        partitions=partitions_used,
        duplicates=duplicates,
        truncated=truncated,
    )
    return truncated_rich_result(jobs) if truncated else jobs


register("typify", discover, cost=10, can_handle=can_handle, rich=True)
