"""Jarvi embedded careers monitor.

Jarvi's public SDK embeds a per-company API key on the customer's careers
page.  The public offers endpoint returns complete job records, including the
nested custom fields used for title, description, location, contract type,
and salary.
"""

from __future__ import annotations

import html
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.core.monitors.raw import save_json_response
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_URL = "https://functions.prod.jarvi.tech/v1/public-api/rest/v2/offers"
MAX_JOBS = 50_000

_SDK_RE = re.compile(r"data-sdk\s*=\s*(['\"])jarvi\1", re.IGNORECASE)
_PUBLIC_KEY_RE = re.compile(
    r"data-public-api-key\s*=\s*(['\"])(?P<key>[^'\"]+)\1",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(
    r"data-currency\s*=\s*(['\"])(?P<currency>[A-Za-z]{3})\1",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_LOCATION_TEXT_RE = re.compile(
    r"Localisation\s*:\s*(?P<location>.*?)"
    r"(?=</p>|<br\s*/?>|(?:\s+-\s+)?Contrat\s*:|Rémunération\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)


def _embed_metadata(page: str) -> dict | None:
    """Extract Jarvi's public SDK configuration from a careers page."""
    if not _SDK_RE.search(page):
        return None
    key_match = _PUBLIC_KEY_RE.search(page)
    if not key_match:
        return None

    metadata = {"public_api_key": html.unescape(key_match.group("key")).strip()}
    currency_match = _CURRENCY_RE.search(page)
    if currency_match:
        metadata["currency"] = currency_match.group("currency").upper()
    return metadata


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAG_RE.sub(" ", value))
    cleaned = " ".join(text.split())
    return cleaned or None


def _field_values(raw: dict, purpose: str) -> list[dict]:
    values = raw.get("fieldsValues")
    if not isinstance(values, list):
        return []
    return [
        value
        for value in values
        if isinstance(value, dict)
        and isinstance(value.get("field"), dict)
        and value["field"].get("purpose") == purpose
    ]


def _field_value(raw: dict, purpose: str) -> str | None:
    for field in _field_values(raw, purpose):
        value = field.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _field_choice(raw: dict, purpose: str) -> str | None:
    for field in _field_values(raw, purpose):
        choice = field.get("fieldValue")
        if not isinstance(choice, dict):
            continue
        for key in ("technicalValue", "name"):
            value = choice.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _locations(raw: dict) -> list[str] | None:
    locations: list[str] = []
    for field in _field_values(raw, "joboffer_location"):
        location = field.get("location")
        if not isinstance(location, dict):
            continue
        name = next(
            (
                location.get(key)
                for key in ("formattedAddress", "search", "locality")
                if isinstance(location.get(key), str) and location[key].strip()
            ),
            None,
        )
        if name and name not in locations:
            locations.append(name.strip())
    if not locations:
        for purpose in ("joboffer_profile_description", "joboffer_description"):
            content = html.unescape(_field_value(raw, purpose) or "").replace("&nbsp;", " ")
            match = _LOCATION_TEXT_RE.search(content)
            fallback = _clean_text(match.group("location")) if match else None
            if fallback:
                locations.append(fallback.strip(" -"))
                break
    return locations or None


def _job_url(board_url: str, raw: dict, title: str) -> str | None:
    short_id = raw.get("shortId") or raw.get("id")
    if not isinstance(short_id, str) or not short_id.strip():
        return None

    normalized = unicodedata.normalize("NFD", title.casefold())
    ascii_title = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9-]", "", re.sub(r"\s+", "-", ascii_title)).strip("-")
    query_value = f"{short_id.strip()}/{slug}" if slug else short_id.strip()

    parsed = urlsplit(board_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "q"]
    query.append(("q", query_value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _number(value: str | None) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _salary(raw: dict, currency: str | None) -> dict | None:
    is_public = (_field_value(raw, "joboffer_salary_is_public") or "").casefold() == "true"
    if not is_public:
        return None
    minimum = _number(_field_value(raw, "joboffer_salary_per_year_min"))
    maximum = _number(_field_value(raw, "joboffer_salary_per_year_max"))
    if minimum is None and maximum is None:
        return None
    return {
        "currency": currency or "EUR",
        "min": minimum,
        "max": maximum,
        "unit": "year",
    }


def _parse_job(raw: dict, board_url: str, currency: str | None) -> DiscoveredJob | None:
    """Map one Jarvi offer to the crawler's rich job shape."""
    title = _clean_text(_field_value(raw, "joboffer_title") or raw.get("name"))
    if not title:
        return None
    url = _job_url(board_url, raw, title)
    if not url:
        return None

    company_description = _field_value(raw, "joboffer_company_description")
    responsibilities = _field_value(raw, "joboffer_description")
    qualifications = _field_value(raw, "joboffer_profile_description")
    description_parts = [
        part for part in (company_description, responsibilities, qualifications) if part
    ]

    remote_days = _number(_field_value(raw, "joboffer_remote_days_per_week"))
    job_location_type = "hybrid" if remote_days is not None and remote_days > 0 else None

    employment_type = _field_choice(raw, "joboffer_contract_type")
    if not employment_type:
        is_fulltime = (_field_value(raw, "joboffer_is_fulltime") or "").casefold()
        employment_type = "full_time" if is_fulltime == "true" else None

    extras = {
        key: value
        for key, value in {
            "responsibilities": responsibilities,
            "qualifications": qualifications,
        }.items()
        if value
    }
    metadata = {
        key: value
        for key, value in {
            "source_id": raw.get("id"),
            "short_id": raw.get("shortId"),
            "updated_at": raw.get("updatedAt"),
            "minimum_years_experience": _number(
                _field_value(raw, "joboffer_min_years_of_experience")
            ),
        }.items()
        if value not in (None, "")
    }

    return DiscoveredJob(
        url=url,
        title=title,
        description="\n".join(description_parts) or None,
        locations=_locations(raw),
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=raw.get("publishedAt") or None,
        base_salary=_salary(raw, currency),
        extras=extras or None,
        metadata=metadata or None,
    )


def _offers_from_payload(payload: object) -> tuple[list[dict], int | None] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    offers = [offer for offer in payload["data"] if isinstance(offer, dict)]
    total = payload.get("total")
    return offers, total if isinstance(total, int) else None


async def _fetch_offers(
    client: httpx.AsyncClient,
    public_api_key: str,
    *,
    limit: int,
) -> tuple[list[dict], int | None]:
    response = await client.get(
        API_URL,
        params={"limit": limit},
        headers={"x-api-key": public_api_key},
        follow_redirects=True,
    )
    response.raise_for_status()
    parsed = _offers_from_payload(response.json())
    if parsed is None:
        raise ValueError("Jarvi offers payload is missing its data array")
    return parsed


async def _board_metadata(board: dict, client: httpx.AsyncClient) -> dict:
    metadata = dict(board.get("metadata") or {})
    if metadata.get("public_api_key"):
        return metadata

    page = await fetch_page_text(board["board_url"], client, max_chars=5_000_000)
    embedded = _embed_metadata(page or "")
    if not embedded:
        raise ValueError(f"Jarvi SDK configuration not found at {board['board_url']!r}")
    metadata.update(embedded)
    return metadata


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch and map every active offer from Jarvi's public API."""
    _ = pw
    metadata = await _board_metadata(board, client)
    offers, total = await _fetch_offers(client, metadata["public_api_key"], limit=MAX_JOBS)
    jobs = [
        job
        for raw in offers
        if (job := _parse_job(raw, board["board_url"], metadata.get("currency")))
    ]
    log.info("jarvi.discovered", board_url=board["board_url"], jobs=len(jobs), total=total)
    if len(offers) >= MAX_JOBS or (total is not None and total > len(offers)):
        log.warning("jarvi.truncated", total=total or len(offers), cap=MAX_JOBS)
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a Jarvi SDK embed and validate its public offers feed."""
    _ = pw
    if client is None:
        return None
    page = await fetch_page_text(url, client, max_chars=5_000_000)
    metadata = _embed_metadata(page or "")
    if not metadata:
        return None
    try:
        _offers, total = await _fetch_offers(client, metadata["public_api_key"], limit=1)
    except Exception:
        log.debug("jarvi.probe_failed", board_url=url, exc_info=True)
        return None
    metadata["jobs"] = total if total is not None else len(_offers)
    return metadata


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board_metadata = await _board_metadata(
        {"board_url": board_url, "metadata": metadata},
        client,
    )
    await save_json_response(
        artifact_dir,
        client,
        API_URL,
        filename="jarvi-offers.json",
        params={"limit": MAX_JOBS},
        headers={"x-api-key": board_metadata["public_api_key"]},
        follow_redirects=True,
    )


register("jarvi", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
