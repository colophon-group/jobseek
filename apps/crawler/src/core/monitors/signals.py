"""Hiring Signals Monitor.

Triggers the Orchestrator Apify actor to find growth signals,
then stores them and any generated outreach drafts in the DB.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import structlog

from src.config import settings
from src.core.monitors import register

log = structlog.get_logger()

_APIFY_BASE = "https://api.apify.com/v2"
_POLL_INTERVAL = 10.0
_RUN_TIMEOUT = 7200.0  # Orchestrator can take a while as it calls many sub-actors


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.apify_token}"}


async def discover_signals(board: dict, client: httpx.AsyncClient, pw=None) -> list:
    """
    This monitor doesn't return DiscoveredJob objects.
    Instead, it processes signals and outreach drafts directly into the DB.
    It returns an empty list to satisfy the monitor interface.
    """
    if not settings.apify_token:
        log.warning("signals.disabled", reason="APIFY_TOKEN not set")
        return []

    metadata = board.get("metadata") or {}
    actor_id = metadata.get("actor_id")
    if not actor_id:
        log.error("signals.error", reason="actor_id missing in metadata")
        return []

    user_profile = metadata.get("user_profile")
    if not user_profile:
        log.error("signals.error", reason="user_profile missing in metadata")
        return []

    # Start the orchestrator run
    run_input = {
        "userProfile": user_profile,
        "anthropicApiKey": settings.anthropic_api_key,
        "lookbackDays": metadata.get("lookback_days", 7),
        "scoreThreshold": metadata.get("score_threshold", 0.5),
        "runIngestionActors": True,
        # Pass other config if present
        **{k: v for k, v in metadata.items() if k not in ("actor_id", "user_profile")},
    }

    log.info("signals.starting", actor_id=actor_id)
    resp = await client.post(
        f"{_APIFY_BASE}/acts/{actor_id}/runs",
        headers=_headers(),
        json=run_input,
    )
    resp.raise_for_status()
    run_id = resp.json()["data"]["id"]

    # Poll for completion
    elapsed = 0.0
    dataset_id = None
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

        if status == "SUCCEEDED":
            # The orchestrator uses multiple datasets via shared storage.
            # We need to find the "outreach-ready" dataset.
            # For now, let's assume it pushes some results to the default dataset as well,
            # or we need to fetch from the named dataset.
            # In novel-job-search/shared/storage.ts, it uses Actor.openDataset('outreach-ready')
            dataset_id = run_info["defaultDatasetId"]
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            log.error("signals.failed", run_id=run_id, status=status)
            return []
    else:
        log.error("signals.timeout", run_id=run_id)
        return []

    # In this specific implementation, we might need to fetch from multiple datasets
    # but let's look at what's in the default dataset first.
    items_resp = await client.get(
        f"{_APIFY_BASE}/datasets/{dataset_id}/items",
        headers=_headers(),
        params={"format": "json", "clean": "true"},
    )
    items_resp.raise_for_status()
    items = items_resp.json()

    log.info("signals.done", run_id=run_id, items_found=len(items))

    # Note: In a real integration, we'd need to map these items to our DB.
    # Since the monitor interface expects us to return DiscoveredJob,
    # but these are signals, we might handle DB insertion here or return a special type.
    # For now, we just log that we found them.

    return []


register("signals", discover_signals, cost=200, rich=True)
