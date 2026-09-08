"""Pay-Net public applicant-tracking API monitor.

Pay-Net listing pages are Vue shells whose review controls are not anchors.
The complete posting records are available from the public JSON endpoint used
by that shell. The endpoint unusually reports a successful list response with
HTTP 201, so this monitor opts into that status explicitly while retaining the
shared retry and fail-closed response-shape checks.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.http_retry import fetch_json_page_with_retry
from src.shared.paynet import paynet_company_from_url
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

API_URL = "https://api.pay-netonline.com/applicantpublic/jobpostings"
MAX_JOBS = 50_000
MAX_RESPONSE_BYTES = 50_000_000


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if str(parsed) == value.casefold() else None


def _job_url(posting_id: str) -> str:
    query = urlencode({"JobPostingID": posting_id})
    return f"https://www.pay-netonline.com/PayNet/Applicant/Posting.aspx?{query}"


def _html_fragment(value: object) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if "<" in cleaned and ">" in cleaned:
        return cleaned
    return f"<p>{html.escape(cleaned).replace(chr(10), '<br>')}</p>"


def _description(position: dict[str, Any]) -> tuple[str | None, str | None]:
    summary = _html_fragment(position.get("Description")) or _html_fragment(position.get("Text"))
    requirements = _html_fragment(position.get("Requirements"))
    sections = [section for section in (summary, requirements) if section]
    if summary and requirements:
        sections[1] = f"<h3>Requirements</h3>\n{requirements}"
    return "\n".join(sections) or None, requirements


def _iso_date(value: object) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    for date_format in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_job(raw: object, company_id: str) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    posting_id = _canonical_uuid(raw.get("ID"))
    position = raw.get("Position")
    if posting_id is None or not isinstance(position, dict):
        return None

    title = _clean(position.get("Title"))
    description, requirements = _description(position)
    location = _clean(position.get("Location"))
    if title is None or description is None:
        return None

    company = raw.get("Company")
    company_name = _clean(company.get("Name")) if isinstance(company, dict) else None
    end_date = _iso_date(position.get("EndDate"))
    metadata = {
        key: value
        for key, value in {
            "posting_id": posting_id,
            "company_name": company_name,
            "valid_through": end_date,
        }.items()
        if value is not None
    }
    return DiscoveredJob(
        url=_job_url(posting_id),
        title=title,
        description=description,
        locations=[location] if location else None,
        date_posted=_iso_date(position.get("StartDate")),
        extras={"qualifications": requirements} if requirements else None,
        metadata=metadata,
        source_identity=f"paynet:{company_id.casefold()}:{posting_id}",
    )


async def _fetch_rows(company_id: str, client: httpx.AsyncClient) -> list[Any]:
    return await fetch_json_page_with_retry(
        client,
        API_URL,
        params={"company_id": company_id},
        headers={"Accept": "application/json"},
        expect_shape=list,
        success_statuses=(200, 201),
        max_bytes=MAX_RESPONSE_BYTES,
        retries=3,
        base_delay=0.5,
        log_event="paynet.list_backoff",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch and parse the complete public posting list for one company."""

    _ = pw
    company_id = paynet_company_from_url(board["board_url"])
    if company_id is None:
        raise ValueError(f"Invalid unfiltered Pay-Net board URL: {board['board_url']!r}")

    rows = await _fetch_rows(company_id, client)
    jobs: list[DiscoveredJob] = []
    seen_ids: set[str] = set()
    invalid = 0
    duplicates = 0
    for raw in rows[:MAX_JOBS]:
        job = _parse_job(raw, company_id)
        if job is None:
            invalid += 1
            continue
        assert job.source_identity is not None
        if job.source_identity in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(job.source_identity)
        jobs.append(job)

    truncated = len(rows) > MAX_JOBS or invalid > 0 or duplicates > 0
    log.info(
        "paynet.discovered",
        company_id=company_id,
        jobs=len(jobs),
        received=len(rows),
        invalid=invalid,
        duplicates=duplicates,
        truncated=truncated,
    )
    if rows and not jobs:
        raise ValueError(f"Pay-Net company {company_id!r} returned no valid postings")
    return truncated_rich_result(jobs) if truncated else jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect exact, unfiltered Pay-Net listing URLs using the live API."""

    _ = pw
    company_id = paynet_company_from_url(url)
    if company_id is None:
        return None
    if client is None:
        return {"company_id": company_id}
    try:
        rows = await _fetch_rows(company_id, client)
    except Exception:
        log.debug("paynet.probe_failed", company_id=company_id, exc_info=True)
        return None
    return {"company_id": company_id, "jobs": len(rows)}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    _ = metadata
    company_id = paynet_company_from_url(board_url)
    if company_id is None:
        return
    rows = await _fetch_rows(company_id, client)
    (artifact_dir / "paynet-postings.json").write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8"
    )


register("paynet", discover, cost=10, can_handle=can_handle, rich=True, save_raw=save_raw)
