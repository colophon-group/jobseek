"""Dispatch the company-discovery-actor on Apify and stream results.

Usage:
    uv run discover                      # fire-and-forget (prints run URL)
    uv run discover --wait               # wait for completion, print summary
    uv run discover --wait --export csv  # wait + dump results to CSV
    uv run discover --status <run_id>    # check status of a previous run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time

import httpx
import structlog

from src.config import settings

log = structlog.get_logger()

_APIFY_BASE = "https://api.apify.com/v2"
_ACTOR_NAME = "company-discovery-actor"
_POLL_INTERVAL = 10.0
_TIMEOUT = 1800.0  # 30 min


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.apify_token}"}


def _actor_id() -> str:
    """Resolve full actor ID from APIFY_TOKEN owner."""
    return f"~/{_ACTOR_NAME}"


async def _dispatch(input_override: dict | None = None) -> dict:
    actor_input = input_override or {}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_APIFY_BASE}/acts/{_actor_id()}/runs",
            headers=_headers(),
            json=actor_input,
            params={"memory": 4096, "timeout": int(_TIMEOUT)},
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def _poll(run_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        elapsed = 0.0
        while elapsed < _TIMEOUT:
            resp = await client.get(
                f"{_APIFY_BASE}/actor-runs/{run_id}",
                headers=_headers(),
            )
            resp.raise_for_status()
            run = resp.json()["data"]
            status = run["status"]

            bar = "█" * int(elapsed / _TIMEOUT * 30)
            print(f"\r  ⏳ {status:<12} [{bar:<30}] {int(elapsed)}s", end="", flush=True)

            if status == "SUCCEEDED":
                print()
                return run
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print()
                raise RuntimeError(f"Run {run_id} ended: {status}")

            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

        raise TimeoutError(f"Run {run_id} did not finish within {_TIMEOUT}s")


async def _fetch_dataset(dataset_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{_APIFY_BASE}/datasets/{dataset_id}/items",
            headers=_headers(),
            params={"format": "json", "clean": "true"},
        )
        resp.raise_for_status()
        return resp.json()


async def _check_status(run_id: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_APIFY_BASE}/actor-runs/{run_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        run = resp.json()["data"]
        print(f"  Run:     {run_id}")
        print(f"  Status:  {run['status']}")
        print(f"  Started: {run.get('startedAt', '?')}")
        print(f"  Finished:{run.get('finishedAt', '—')}")
        if run.get("defaultDatasetId"):
            print(f"  Dataset: https://console.apify.com/storage/datasets/{run['defaultDatasetId']}")
        print(f"  Console: https://console.apify.com/actors/runs/{run_id}")


def _print_summary(items: list[dict]) -> None:
    total_jobs = sum(item.get("estimated_jobs", 0) for item in items)
    by_source: dict[str, dict] = {}
    for item in items:
        src = item.get("source", "unknown")
        s = by_source.setdefault(src, {"companies": 0, "jobs": 0})
        s["companies"] += 1
        s["jobs"] += item.get("estimated_jobs", 0)

    print(f"\n  🏢 {len(items):,} companies | 💼 {total_jobs:,} estimated jobs\n")
    print(f"  {'Source':<30} {'Companies':>10} {'Jobs':>12}")
    print(f"  {'─' * 30} {'─' * 10} {'─' * 12}")
    for src, s in sorted(by_source.items(), key=lambda x: -x[1]["jobs"]):
        print(f"  {src:<30} {s['companies']:>10,} {s['jobs']:>12,}")


def _export_csv(items: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name", "job_board_url", "estimated_jobs", "source"])
        writer.writeheader()
        for item in items:
            writer.writerow({k: item.get(k, "") for k in writer.fieldnames})
    print(f"\n  📁 Exported {len(items):,} rows → {path}")


async def _run(args: argparse.Namespace) -> None:
    if not settings.apify_token:
        print("  ❌ APIFY_TOKEN not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    if args.status:
        await _check_status(args.status)
        return

    # Build input overrides
    actor_input: dict = {}
    if args.sources:
        actor_input["sources"] = args.sources.split(",")

    print(f"  🚀 Dispatching {_ACTOR_NAME}...")
    run_data = await _dispatch(actor_input)
    run_id = run_data["id"]
    console_url = f"https://console.apify.com/actors/runs/{run_id}"

    print(f"  Run ID:  {run_id}")
    print(f"  Console: {console_url}")

    if not args.wait:
        print(f"\n  Run is in progress. Check with:\n    uv run discover --status {run_id}")
        return

    print()
    run = await _poll(run_id)
    dataset_id = run["defaultDatasetId"]
    items = await _fetch_dataset(dataset_id)
    _print_summary(items)

    if args.export == "csv":
        _export_csv(items, "discovered_companies.csv")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="discover",
        description="Dispatch company-discovery-actor on Apify",
    )
    parser.add_argument("--wait", action="store_true", help="Wait for run to complete and show results")
    parser.add_argument("--export", choices=["csv"], help="Export results (requires --wait)")
    parser.add_argument("--sources", type=str, help="Comma-separated sources (e.g. greenhouse,megaemployers)")
    parser.add_argument("--status", type=str, metavar="RUN_ID", help="Check status of a previous run")
    args = parser.parse_args()

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
