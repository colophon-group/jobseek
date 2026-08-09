"""Inploi candidate-experience API monitor.

Inploi career sites expose a public SDK key and a job-search segment in the
server-rendered page. The browser SDK uses those values with the public
``/search/results`` endpoint. This monitor replays that request directly and
maps the structured list response to canonical career-site job URLs.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type, normalize_salary_unit
from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_URL = "https://api.inploi.com/search/results"
MAX_JOBS = 50_000
PAGE_SIZE = 5_000

_KEY_RE = re.compile(r"\bpk_[A-Za-z0-9_-]{12,}\b")
_SEGMENT_RE = re.compile(
    r'\\?"segment_ids\\?"\s*,\s*\\?"segment\\?"\s*,\s*'
    r'\\?"(?P<segment>\d+)\\?"'
)


def _page_metadata(page: str) -> tuple[str, str] | None:
    """Extract the public SDK key and default job-search segment."""
    if "inploi" not in page.casefold():
        return None
    key = _KEY_RE.search(page)
    segment = _SEGMENT_RE.search(page)
    if not key or not segment:
        return None
    return key.group(0), segment.group("segment")


def _search_url(board_url: str) -> str:
    parsed = urlsplit(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/search"


def _job_url(board_url: str, job_id: object, template: str | None = None) -> str:
    if template:
        return template.format(id=job_id)
    parsed = urlsplit(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/job/{job_id}"


def _locations(raw: dict) -> list[str] | None:
    parts: list[str] = []
    for key in ("town", "city", "country"):
        value = raw.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value and value not in parts:
                parts.append(value)
    return [", ".join(parts)] if parts else None


def _number(value: object) -> int | float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _salary(raw: dict) -> dict | None:
    if raw.get("pay_display") is False or raw.get("pay_mask") is True:
        return None
    minimum = _number(raw.get("pay_min"))
    maximum = _number(raw.get("pay_max"))
    single = _number(raw.get("pay"))
    if minimum is None and maximum is None and single is not None:
        minimum = maximum = single
    if minimum is None and maximum is None:
        return None

    raw_unit = str(raw.get("pay_type") or "").casefold()
    unit = normalize_salary_unit(raw_unit.removesuffix("ly"))
    return {
        "currency": raw.get("pay_currency") or raw.get("currency_code"),
        "min": minimum,
        "max": maximum,
        "unit": unit or raw_unit or None,
    }


def _parse_job(
    raw: dict,
    board_url: str,
    job_url_template: str | None = None,
) -> DiscoveredJob | None:
    job_id = raw.get("id")
    title = raw.get("title")
    if job_id in (None, "") or not isinstance(title, str) or not title.strip():
        return None

    custom = raw.get("custom_data") if isinstance(raw.get("custom_data"), dict) else {}
    metadata = {
        key: value
        for key, value in {
            "id": job_id,
            "external_ref": raw.get("external_ref"),
            "company_name": raw.get("company_name"),
            "category": raw.get("category"),
            "valid_through": custom.get("expiry_date"),
        }.items()
        if value not in (None, "")
    }
    raw_location_type = raw.get("location_type")
    job_location_type = (
        "onsite"
        if str(raw_location_type).casefold() == "location"
        else normalize_job_location_type(raw_location_type, default=None)
    )

    return DiscoveredJob(
        url=_job_url(board_url, job_id, job_url_template),
        title=title.strip(),
        locations=_locations(raw),
        employment_type=raw.get("employment_type") or raw.get("contract_type") or None,
        job_location_type=job_location_type,
        date_posted=raw.get("created_at") or custom.get("open_date"),
        base_salary=_salary(raw),
        metadata=metadata or None,
    )


async def _fetch_page(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    segment_id: str,
    page: int,
    page_size: int,
) -> dict:
    response = await client.get(
        API_URL,
        params=[
            ("filters[segment_ids][0]", segment_id),
            ("query", ""),
            ("page", str(page)),
            ("per_page", str(page_size)),
        ],
        headers={"Accept": "application/json", "x-publishable-key": api_key},
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Inploi search response is not a paginated job list")
    return payload


async def _detect(url: str, client: httpx.AsyncClient) -> dict | None:
    candidates = [url]
    search_url = _search_url(url)
    if search_url != url:
        candidates.append(search_url)

    for candidate in candidates:
        page = await fetch_page_text(candidate, client)
        metadata = _page_metadata(page or "")
        if not metadata:
            continue
        api_key, segment_id = metadata
        try:
            payload = await _fetch_page(
                client,
                api_key=api_key,
                segment_id=segment_id,
                page=1,
                page_size=1,
            )
        except Exception:
            log.debug("inploi.probe_failed", url=candidate, exc_info=True)
            continue
        pagination = payload.get("pagination")
        total = pagination.get("total") if isinstance(pagination, dict) else None
        result = {
            "api_key": api_key,
            "segment_id": segment_id,
            "search_url": candidate,
        }
        if isinstance(total, int):
            result["jobs"] = total
        return result
    return None


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect an Inploi career site and verify its public search endpoint."""
    _ = pw
    if client is None:
        return None
    return await _detect(url, client)


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch every job in the configured Inploi search segment."""
    _ = pw
    config = board.get("metadata") or {}
    api_key = config.get("api_key")
    segment_id = str(config.get("segment_id") or "")
    if not api_key or not segment_id:
        detected = await _detect(board["board_url"], client)
        if not detected:
            raise ValueError("Cannot derive Inploi api_key and segment_id from board page")
        api_key = detected["api_key"]
        segment_id = detected["segment_id"]

    page_size = min(max(int(config.get("page_size", PAGE_SIZE)), 1), MAX_JOBS)
    jobs: list[DiscoveredJob] = []
    page = 1
    while len(jobs) < MAX_JOBS:
        payload = await _fetch_page(
            client,
            api_key=api_key,
            segment_id=segment_id,
            page=page,
            page_size=page_size,
        )
        items = payload["data"]
        for raw in items:
            if isinstance(raw, dict):
                parsed = _parse_job(raw, board["board_url"], config.get("job_url_template"))
                if parsed:
                    jobs.append(parsed)

        pagination = payload.get("pagination")
        last_page = pagination.get("last_page") if isinstance(pagination, dict) else None
        if not items or not isinstance(last_page, int) or page >= last_page:
            break
        page += 1

    if len(jobs) >= MAX_JOBS:
        log.warning("inploi.truncated", segment_id=segment_id, cap=MAX_JOBS)
        return truncated_rich_result(jobs[:MAX_JOBS])
    return jobs


register("inploi", discover, cost=10, can_handle=can_handle, rich=True)
