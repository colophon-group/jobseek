"""Universia branded-jobboard monitor.

Universia jobboards expose a public configuration document that binds the
human-readable board slug to a full entity UUID.  The public job-posting API
then returns complete schema.org-shaped records scoped to that UUID.  This
adapter resolves and validates that identity before accepting an explicit
empty inventory or any returned job.
"""

from __future__ import annotations

import math
import re
from urllib.parse import parse_qsl, urlparse

import httpx
import structlog

from src.core.enum_normalize import normalize_employment_type, normalize_job_location_type
from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_RESPONSE_BYTES = 20_000_000

_API_ORIGIN = "https://api-manager.universia.net"
_LISTING_URL = f"{_API_ORIGIN}/orientacion-job-posting/v1/api/job-posting"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,126}[a-z0-9]$|^[a-z0-9]$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _slug_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "jobboard.universia.net"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    slug = parsed.path.strip("/").lower()
    return slug if _SLUG_RE.fullmatch(slug) else None


def _config_url(slug: str) -> str:
    return f"{_API_ORIGIN}/empleo/entities/v2/jobboard/slug/{slug}/config/"


def _required_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Universia response contains invalid {field}")
    return value


async def _resolve_board(
    client: httpx.AsyncClient,
    slug: str,
) -> tuple[str, str | None]:
    url = _config_url(slug)
    response = await client.get(
        url,
        params={"status": "published"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    if response.status_code in {404, 410}:
        raise BoardGoneError(
            f"Universia board {slug!r} no longer exists",
            url=url,
            status_code=response.status_code,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("slug") != slug:
        raise ValueError("Universia configuration returned a mismatched board slug")
    entity = payload.get("entity")
    board_id = entity.get("id") if isinstance(entity, dict) else None
    if not isinstance(board_id, str) or _UUID_RE.fullmatch(board_id) is None:
        raise ValueError("Universia configuration omitted a valid entity UUID")
    if entity.get("entityType") not in {"company", "university"}:
        raise ValueError("Universia configuration returned an unsupported entity type")

    language = None
    languages = payload.get("languages")
    if isinstance(languages, list) and languages:
        candidate = languages[0]
        if isinstance(candidate, str) and re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", candidate):
            language = candidate[:2]
    return board_id.lower(), language


async def _fetch_page(
    client: httpx.AsyncClient,
    *,
    board_id: str,
    offset: int,
) -> dict:
    return await fetch_json_page_with_retry(
        client,
        _LISTING_URL,
        expect_shape=dict,
        params={
            "offset": offset,
            "limit": PAGE_SIZE,
            "filterAddressCountry": "false",
            "postingType": ["job", "internship"],
            "dateFrom": "",
            "boards": board_id,
        },
        headers={"Accept": "application/json"},
        max_bytes=MAX_RESPONSE_BYTES,
        retryable_statuses={202, 403, 429},
        log_event="universia.list_backoff",
    )


def _parse_page(
    payload: dict,
    *,
    board_id: str,
    requested_offset: int,
) -> tuple[list[dict], int]:
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("Universia listing response omitted results")
    offset = _required_non_negative_int(payload.get("offset"), "offset")
    limit = _required_non_negative_int(payload.get("limit"), "limit")
    size = _required_non_negative_int(payload.get("size"), "size")
    total = _required_non_negative_int(payload.get("total"), "total")
    total_pages = _required_non_negative_int(payload.get("totalPages"), "totalPages")
    if offset != requested_offset or limit != PAGE_SIZE or size != len(rows):
        raise ValueError("Universia listing pagination does not match the request")
    if total_pages != math.ceil(total / PAGE_SIZE):
        raise ValueError("Universia listing totalPages does not match total")
    expected = min(PAGE_SIZE, max(0, total - requested_offset))
    if len(rows) != expected:
        raise ValueError(
            f"Universia listing at offset {requested_offset} returned {len(rows)} rows, "
            f"expected {expected}"
        )

    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Universia listing contains a non-object job")
        job_id = row.get("identifier")
        boards = row.get("boards")
        if (
            not isinstance(job_id, str)
            or _UUID_RE.fullmatch(job_id) is None
            or not isinstance(boards, list)
            or board_id not in {str(item).lower() for item in boards}
            or row.get("status") != "published"
        ):
            raise ValueError("Universia listing contains an invalid board-scoped identity")
        if job_id.lower() in seen:
            raise ValueError("Universia listing page contains duplicate job identities")
        seen.add(job_id.lower())
    return rows, total


async def _fetch_all(client: httpx.AsyncClient, board_id: str) -> tuple[list[dict], bool]:
    first_payload = await _fetch_page(client, board_id=board_id, offset=0)
    first, total = _parse_page(first_payload, board_id=board_id, requested_offset=0)
    target = min(total, MAX_JOBS)
    rows = first[:target]
    seen = {str(row["identifier"]).lower() for row in rows}

    for offset in range(PAGE_SIZE, target, PAGE_SIZE):
        payload = await _fetch_page(client, board_id=board_id, offset=offset)
        page, page_total = _parse_page(
            payload,
            board_id=board_id,
            requested_offset=offset,
        )
        if page_total != total:
            raise ValueError("Universia listing total changed during pagination")
        page_ids = {str(row["identifier"]).lower() for row in page}
        if seen & page_ids:
            raise ValueError("Universia listing repeated jobs across pages")
        rows.extend(page[: max(0, target - len(rows))])
        seen.update(page_ids)

    if len(rows) != target:
        raise ValueError(f"Universia discovered {len(rows)} jobs, expected {target}")
    return rows, total > MAX_JOBS


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def _job_url(value: object, *, job_id: str, board_id: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Universia job {job_id} omitted its public URL")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Universia job {job_id} returned an invalid public URL") from exc
    path = re.fullmatch(
        rf"/[a-z]{{2}}/empleo/{re.escape(job_id)}/[a-z0-9][a-z0-9-]*\.html",
        parsed.path,
        re.IGNORECASE,
    )
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_items)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "www.universia.net"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or path is None
        or parsed.fragment
        or len(query_items) != len(query)
        or query.get("referer", "").lower() != board_id
        or any(key not in {"referer", "entityid"} for key in query)
        or ("entityid" in query and query["entityid"].lower() != board_id)
    ):
        raise ValueError(f"Universia job {job_id} returned an untrusted public URL")
    return value


def _locations(row: dict) -> list[str] | None:
    location = row.get("jobLocation")
    address = location.get("address") if isinstance(location, dict) else None
    if not isinstance(address, dict):
        return None
    street = _clean_text(address.get("streetAddress"))
    if street:
        return [street]
    values: list[str] = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = _clean_text(address.get(key))
        if value and value not in values:
            values.append(value)
    return [", ".join(values)] if values else None


def _description(row: dict, job_id: str) -> tuple[str, dict | None]:
    main = row.get("description")
    if not isinstance(main, str) or not main.strip():
        raise ValueError(f"Universia job {job_id} omitted its description")
    sections = [main.strip()]
    extras: dict[str, str] = {}
    requirements = row.get("requirements")
    if isinstance(requirements, str) and requirements.strip():
        sections.extend(["<h3>Requirements</h3>", requirements.strip()])
        extras["qualifications"] = requirements.strip()
    education = row.get("incentiveCompensation")
    if isinstance(education, str) and education.strip():
        sections.extend(["<h3>Education</h3>", education.strip()])
        extras["qualifications"] = "".join(
            part for part in (extras.get("qualifications"), education.strip()) if part
        )
    valid_through = _clean_text(row.get("validThrough"))
    if valid_through:
        extras["valid_through"] = valid_through
    return "".join(sections), extras or None


def _parse_job(row: dict, *, board_id: str, language: str | None) -> DiscoveredJob:
    job_id = str(row["identifier"]).lower()
    title = _clean_text(row.get("title"))
    if not title:
        raise ValueError(f"Universia job {job_id} omitted its title")
    description, extras = _description(row, job_id)
    metadata: dict[str, object] = {"universia_job_id": job_id, "universia_board_id": board_id}
    for source, target in (
        ("postingType", "posting_type"),
        ("educationalLevel", "educational_level"),
        ("totalJobOpenings", "total_job_openings"),
    ):
        if row.get(source) is not None:
            metadata[target] = row[source]
    role = row.get("role")
    if isinstance(role, dict) and (role_name := _clean_text(role.get("name"))):
        metadata["role"] = role_name
    contract = row.get("contractType")
    if isinstance(contract, dict) and (contract_name := _clean_text(contract.get("name"))):
        metadata["contract_type"] = contract_name

    return DiscoveredJob(
        url=_job_url(row.get("url"), job_id=job_id, board_id=board_id),
        title=title,
        description=description,
        locations=_locations(row),
        employment_type=normalize_employment_type(_clean_text(row.get("employmentType"))),
        job_location_type=normalize_job_location_type(
            _clean_text(row.get("jobLocationType")), default=None
        ),
        date_posted=_clean_text(row.get("datePosted")),
        language=language,
        extras=extras,
        metadata=metadata,
        source_identity=f"universia:{board_id}:{job_id}",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Resolve one Universia board and return its complete public inventory."""
    _ = pw
    slug = _slug_from_url(board["board_url"])
    if slug is None:
        raise ValueError(f"Unsupported Universia board URL: {board['board_url']!r}")
    board_id, detected_language = await _resolve_board(client, slug)
    metadata = board.get("metadata") or {}
    configured_id = metadata.get("board_id")
    if configured_id is not None and str(configured_id).lower() != board_id:
        raise ValueError("Universia configured board_id no longer matches the public board")
    language = metadata.get("language") or detected_language
    if not isinstance(language, str) or re.fullmatch(r"[a-z]{2}", language) is None:
        language = detected_language

    rows, truncated = await _fetch_all(client, board_id)
    jobs = [_parse_job(row, board_id=board_id, language=language) for row in rows]
    log.info(
        "universia.discovered",
        slug=slug,
        board_id=board_id,
        jobs=len(jobs),
        truncated=truncated,
    )
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize exact Universia branded boards and verify their public API."""
    _ = pw
    slug = _slug_from_url(url)
    if slug is None:
        return None
    result: dict[str, object] = {"slug": slug}
    if client is None:
        return result
    try:
        board_id, language = await _resolve_board(client, slug)
        rows, _truncated = await _fetch_all(client, board_id)
    except BoardGoneError:
        return None
    except Exception:
        log.debug("universia.probe_failed", slug=slug, exc_info=True)
        return result
    result.update({"board_id": board_id, "jobs": len(rows)})
    if language:
        result["language"] = language
    return result


register("universia", discover, cost=10, can_handle=can_handle, rich=True)
