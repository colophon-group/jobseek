"""SeamlessHiring public jobs API monitor.

Boards on ``*.seamlesshiring.com`` expose active postings through the public
candidate API at ``/v2/jobs/job-list``.  The endpoint is authoritative even
when empty and returns rich posting records when roles are published.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.monitors import DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

_HOST_RE = re.compile(r"^([a-z0-9-]+)\.seamlesshiring\.com$", re.IGNORECASE)
_PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = 10_000


def _tenant_from_url(url: str) -> str | None:
    try:
        host = httpx.URL(url).host
    except (TypeError, ValueError):
        return None
    match = _HOST_RE.match(host or "")
    return match.group(1).lower() if match else None


def _base_url(tenant: str) -> str:
    return f"https://{tenant}.seamlesshiring.com"


def _parse_job(post: dict, tenant: str) -> DiscoveredJob | None:
    job_id = post.get("id")
    if job_id is None:
        return None

    description_parts = [post.get("summary"), post.get("details")]
    description = (
        "\n".join(str(part).strip() for part in description_parts if str(part or "").strip())
        or None
    )

    location = post.get("location") or post.get("city")
    if not location and isinstance(post.get("location_details"), dict):
        location = post["location_details"].get("name")

    metadata: dict = {"id": job_id}
    if post.get("expiry_date"):
        metadata["valid_through"] = post["expiry_date"]
    if post.get("position"):
        metadata["position"] = post["position"]

    return DiscoveredJob(
        url=f"{_base_url(tenant)}/job/view/{job_id}",
        title=post.get("title"),
        description=description,
        locations=[str(location).strip()] if str(location or "").strip() else None,
        employment_type=post.get("job_type") or None,
        job_location_type=normalize_job_location_type(post.get("work_style"), default=None),
        date_posted=post.get("post_date") or post.get("created_at"),
        metadata=metadata,
    )


async def _fetch_page(tenant: str, page: int, client: httpx.AsyncClient) -> dict:
    response = await client.get(
        f"{_base_url(tenant)}/v2/jobs/job-list",
        params={"limit": _PAGE_SIZE, "page": page},
        headers={"Accept": "application/json"},
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    jobs = (payload.get("data") or {}).get("jobs") if isinstance(payload, dict) else None
    total = jobs.get("total") if isinstance(jobs, dict) else None
    if (
        not isinstance(jobs, dict)
        or not isinstance(jobs.get("data"), list)
        or type(total) is not int
        or total < 0
    ):
        raise ValueError(f"Unexpected SeamlessHiring jobs response for tenant {tenant!r}")
    return jobs


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    metadata = board.get("metadata") or {}
    tenant = metadata.get("tenant") or _tenant_from_url(board["board_url"])
    if not tenant:
        raise ValueError(
            f"Cannot derive SeamlessHiring tenant from board URL {board['board_url']!r} "
            "and no tenant in metadata"
        )

    jobs: list[DiscoveredJob] = []
    seen_ids: set[str] = set()
    advertised: int | None = None
    page = 1

    while len(jobs) < MAX_JOBS:
        if page > MAX_PAGES:
            raise ValueError(
                f"SeamlessHiring pagination exceeded {MAX_PAGES} pages for tenant {tenant!r}"
            )
        page_data = await _fetch_page(tenant, page, client)
        page_advertised = page_data["total"]
        if advertised is None:
            advertised = page_advertised
        elif page_advertised != advertised:
            raise ValueError(
                f"SeamlessHiring job count changed during pagination for tenant "
                f"{tenant!r}: {advertised} -> {page_advertised}"
            )

        jobs_before_page = len(jobs)
        for post in page_data["data"]:
            if not isinstance(post, dict) or post.get("id") is None:
                continue
            key = str(post["id"])
            if key in seen_ids:
                continue
            parsed = _parse_job(post, tenant)
            if parsed:
                seen_ids.add(key)
                jobs.append(parsed)
                if len(jobs) >= MAX_JOBS:
                    break

        next_page = page_data.get("next_page_url")
        if not next_page:
            break
        if len(jobs) == jobs_before_page:
            raise ValueError(f"SeamlessHiring pagination made no progress for tenant {tenant!r}")
        page += 1

    assert advertised is not None
    if advertised > len(jobs):
        log.warning(
            "seamlesshiring.truncated",
            tenant=tenant,
            fetched=len(jobs),
            advertised=advertised,
        )
        return truncated_rich_result(jobs)
    if advertised < len(jobs):
        raise ValueError(
            f"SeamlessHiring returned {len(jobs)} unique jobs for advertised total "
            f"{advertised} for tenant {tenant!r}"
        )
    if len(jobs) >= MAX_JOBS:
        log.warning("seamlesshiring.truncated", tenant=tenant, total=len(jobs), cap=MAX_JOBS)
        return truncated_rich_result(jobs)

    log.info("seamlesshiring.discovered", tenant=tenant, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    tenant = _tenant_from_url(url)
    if not tenant:
        return None
    if client is None:
        return {"tenant": tenant}

    try:
        page_data = await _fetch_page(tenant, 1, client)
    except (httpx.HTTPError, ValueError, TypeError):
        return None

    return {"tenant": tenant, "jobs": page_data["total"]}


register("seamlesshiring", discover, cost=10, can_handle=can_handle, rich=True)
