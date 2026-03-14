"""Apify Meta Careers monitor.

Triggers the Meta Careers Scraper Apify actor, waits for it to finish,
then returns the full job dataset as DiscoveredJob objects.

Board metadata fields:
    actor_id   — Apify actor ID, e.g. "myuser/meta-careers-scraper"
    max_jobs   — optional int limit (0 = all)

Environment:
    APIFY_TOKEN — Apify API token (required)
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from src.config import settings
from src.core.monitors import DiscoveredJob, register

log = structlog.get_logger()

_APIFY_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL = 5.0  # seconds between run-status polls
_RUN_TIMEOUT = 3600.0  # max seconds to wait for actor run


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.apify_token}"}


def _map_job(item: dict) -> DiscoveredJob | None:
    url = item.get("url")
    if not url:
        return None

    item_metadata: dict = {}
    teams = item.get("teams") or []
    sub_teams = item.get("subTeams") or []
    if teams:
        item_metadata["teams"] = teams
    if sub_teams:
        item_metadata["sub_teams"] = sub_teams

    extras: dict = {}
    if item.get("responsibilities"):
        extras["responsibilities"] = item["responsibilities"]
    if item.get("qualifications"):
        extras["qualifications"] = item["qualifications"]

    return DiscoveredJob(
        url=url,
        title=item.get("title"),
        description=item.get("description"),
        locations=item.get("locations") or None,
        employment_type=item.get("employmentType"),
        job_location_type=item.get("jobLocationType"),
        date_posted=item.get("datePosted"),
        extras=extras or None,
        metadata=item_metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    if not settings.apify_token:
        raise RuntimeError("APIFY_TOKEN is not set — cannot run apify_meta monitor")

    metadata = board.get("metadata") or {}
    actor_id = metadata.get("actor_id")
    if not actor_id:
        raise ValueError(
            f"apify_meta monitor requires actor_id in board metadata for {board['board_url']!r}"
        )

    max_jobs = int(metadata.get("max_jobs", 0))
    fetch_descriptions = bool(metadata.get("fetch_descriptions", True))

    # Start the actor run
    run_input: dict = {"fetchDescriptions": fetch_descriptions}
    if max_jobs:
        run_input["maxJobs"] = max_jobs

    log.info(
        "apify_meta.starting",
        actor_id=actor_id,
        max_jobs=max_jobs or "all",
        fetch_descriptions=fetch_descriptions,
    )
    resp = await client.post(
        f"{_APIFY_BASE}/acts/{actor_id}/runs",
        headers=_headers(),
        json=run_input,
    )
    resp.raise_for_status()
    run_data = resp.json()["data"]
    run_id = run_data["id"]
    log.info("apify_meta.run_started", run_id=run_id, actor_id=actor_id)

    # Poll until the run finishes
    elapsed = 0.0
    while elapsed < _RUN_TIMEOUT:
        await asyncio.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL

        status_resp = await client.get(
            f"{_APIFY_BASE}/actor-runs/{run_id}",
            headers=_headers(),
        )
        status_resp.raise_for_status()
        run_info = status_resp.json()["data"]
        status = run_info["status"]

        log.info("apify_meta.poll", run_id=run_id, status=status, elapsed_s=int(elapsed))

        if status == "SUCCEEDED":
            dataset_id = run_info["defaultDatasetId"]
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify actor run {run_id} ended with status {status!r}")
    else:
        raise TimeoutError(f"Apify actor run {run_id} did not finish within {_RUN_TIMEOUT}s")

    # Fetch all dataset items
    log.info("apify_meta.fetching_dataset", dataset_id=dataset_id)
    items_resp = await client.get(
        f"{_APIFY_BASE}/datasets/{dataset_id}/items",
        headers=_headers(),
        params={"format": "json", "clean": "true"},
    )
    items_resp.raise_for_status()
    items = items_resp.json()

    jobs: list[DiscoveredJob] = []
    for item in items:
        job = _map_job(item)
        if job:
            jobs.append(job)

    log.info("apify_meta.done", run_id=run_id, total=len(jobs))
    return jobs


register("apify_meta", discover, cost=50, rich=True)
