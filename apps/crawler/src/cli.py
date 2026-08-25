"""CLI entry point for the crawler.

Subcommand-based interface that dispatches to the appropriate worker,
exporter, drain, or dev-testing function. All concurrency is configured
via environment variables / config.py, not CLI flags.

Entry point: ``crawler = "src.cli:main"`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path
from typing import Any, cast

import asyncpg
import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.config import settings  # noqa: E402
from src.db import (  # noqa: E402
    close_all_pools,
    create_local_pool,
    create_web_pool,
)
from src.metrics import start_metrics_server  # noqa: E402
from src.shared.http import create_http_client  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402
from src.shared.output import tty_message  # noqa: E402

log = structlog.get_logger()

_rand = uuid.uuid4().hex[:8]
WORKER_ID = f"{settings.worker_id_prefix}-{_rand}" if settings.worker_id_prefix else _rand


async def _await_task_or_shutdown[T](
    task: asyncio.Task[T],
    shutdown_event: asyncio.Event,
) -> T | None:
    """Cancel one-shot work when the process receives SIGTERM or SIGINT."""

    shutdown_task = asyncio.create_task(shutdown_event.wait())
    try:
        done, _pending = await asyncio.wait(
            (task, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done:
            return await task

        task.cancel()
        try:
            return await task
        except asyncio.CancelledError:
            return None
    finally:
        if not task.done():
            task.cancel()
        shutdown_task.cancel()
        await asyncio.gather(task, shutdown_task, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="crawler")
    sub = parser.add_subparsers(dest="command", required=True)

    # Production subcommands
    sub.add_parser("run", help="Worker instance (all non-browser profiles)")
    sub.add_parser("run-browser", help="Browser instance (browser profiles only)")

    export_p = sub.add_parser(
        "export",
        help="CDC exporter (local Postgres -> Typesense)",
    )
    export_p.add_argument(
        "--batch-size",
        type=int,
        default=settings.export_batch_limit,
    )
    export_p.add_argument(
        "--interval",
        type=int,
        default=settings.export_interval,
    )

    sub.add_parser("drain", help="R2 drain instance")

    sub.add_parser("sync", help="CSV -> local Postgres + Redis + Typesense")

    sub.add_parser(
        "repair-nw-provider-cutover",
        help="Reapply the bounded NW Teamtailor-to-WTTJ identity repair",
    )

    sub.add_parser(
        "repair-location-taxonomy-source",
        help="One-shot retained web DB -> local location slug/coordinate repair",
    )

    ats_inventory_p = sub.add_parser(
        "ats-inventory",
        help="Validate and cache the data-only ats-scrapers company inventory",
    )
    ats_inventory_p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("ATS_INVENTORY_CACHE_DIR", ".cache/ats-inventory")),
        help="Persistent checksum-addressed cache directory",
    )
    ats_inventory_p.add_argument(
        "--manifest-url",
        default="https://storage.stapply.ai/jobhive/v1/manifest.json",
        help="Published v2 manifest URL (restricted to storage.stapply.ai/jobhive/v1/)",
    )
    ats_inventory_p.add_argument(
        "--support-issues",
        choices=("off", "plan", "create"),
        default="off",
        help="Reconcile unsupported families; refill mode also creates them automatically",
    )
    ats_inventory_p.add_argument(
        "--candidate-issues",
        choices=("off", "plan", "report", "dry-run", "refill"),
        default="off",
        help="Plan candidates, report/simulate the queue, or refill it with GitHub issues",
    )
    ats_inventory_p.add_argument(
        "--candidate-limit",
        type=int,
        default=100,
        help="Maximum impact-ranked rows to explain in candidate plan mode",
    )
    ats_inventory_p.add_argument(
        "--queue-low-water",
        type=int,
        default=450,
        help="Refill only when resolver-available company requests fall below this count",
    )
    ats_inventory_p.add_argument(
        "--queue-target",
        type=int,
        default=500,
        help="Resolver-available company-request target after refill",
    )
    ats_inventory_p.add_argument(
        "--queue-hard-cap",
        type=int,
        default=600,
        help="Maximum total open company-request issues",
    )
    ats_inventory_p.add_argument(
        "--queue-per-tick-cap",
        type=int,
        default=25,
        help="Maximum issues created by one invocation",
    )
    ats_inventory_p.add_argument(
        "--queue-daily-cap",
        type=int,
        default=50,
        help="Maximum issues created per UTC day",
    )
    ats_inventory_p.add_argument(
        "--queue-rollout-cap",
        type=int,
        choices=(1, 5, 25),
        default=1,
        help="Canary stage cap; deliberately advance from 1 to 5 to 25",
    )
    ats_inventory_p.add_argument(
        "--queue-claim-ttl-hours",
        type=float,
        default=4.0,
        help="Age after which a ws claim no longer reduces resolver availability",
    )
    ats_inventory_p.add_argument(
        "--queue-jitter-min-seconds",
        type=float,
        default=1.0,
        help="Minimum delay between sequential GitHub creates",
    )
    ats_inventory_p.add_argument(
        "--queue-jitter-max-seconds",
        type=float,
        default=3.0,
        help="Maximum delay between sequential GitHub creates",
    )
    ats_inventory_p.add_argument(
        "--github-rate-reserve",
        type=int,
        default=100,
        help="Stop GitHub reads/writes at or below this primary-rate remaining count",
    )
    crawler_root = Path(__file__).resolve().parent.parent
    ats_inventory_p.add_argument(
        "--companies-file",
        type=Path,
        default=crawler_root / "data" / "companies.csv",
        help="Checked-in company registry used for exact and soft matches",
    )
    ats_inventory_p.add_argument(
        "--boards-file",
        type=Path,
        default=crawler_root / "data" / "boards.csv",
        help="Checked-in board registry used for exact URL and ATS-tenant matches",
    )
    ats_inventory_p.add_argument(
        "--candidate-ledger",
        type=Path,
        default=None,
        help="Durable SQLite ledger (default: <cache-dir>/candidates/ledger.sqlite)",
    )
    ats_inventory_p.add_argument(
        "--github-repo",
        default=os.environ.get("ATS_INVENTORY_GITHUB_REPO", "colophon-group/jobseek"),
        help="Repository used for unsupported-family issues",
    )
    ats_inventory_p.add_argument(
        "--github-token-env",
        default="GH_TOKEN",
        help="Environment variable containing an injected GitHub token",
    )
    ats_inventory_p.add_argument(
        "--github-token-file",
        type=Path,
        default=(
            Path(os.environ["ATS_INVENTORY_GITHUB_TOKEN_FILE"])
            if os.environ.get("ATS_INVENTORY_GITHUB_TOKEN_FILE")
            else None
        ),
        help="Ephemeral GitHub token file (preferred for the production container)",
    )
    ats_inventory_p.add_argument(
        "--retention",
        type=int,
        default=7,
        help="Maximum recent validated manifest snapshots to retain",
    )
    ats_inventory_p.add_argument(
        "--max-cache-mib",
        type=int,
        default=256,
        help="Maximum steady-state cache size in MiB",
    )
    ats_inventory_p.add_argument(
        "--impact",
        action="store_true",
        help="Refresh compact per-company impact from changed per-ATS Parquet artifacts",
    )
    ats_inventory_p.add_argument(
        "--impact-max-cache-mib",
        type=int,
        default=768,
        help="Maximum impact-cache bytes including one transient Parquet download",
    )
    ats_inventory_p.add_argument(
        "--impact-max-artifact-mib",
        type=int,
        default=512,
        help="Maximum accepted size of one per-ATS Parquet artifact",
    )
    ats_inventory_p.add_argument(
        "--impact-free-reserve-mib",
        type=int,
        default=512,
        help="Free disk space retained while streaming a changed artifact",
    )

    recon_p = sub.add_parser(
        "reconcile",
        help="Deterministic local -> Typesense reconciliation",
    )
    recon_p.add_argument(
        "--repair",
        action="store_true",
        help="Apply and verify idempotent repairs (default is read-only)",
    )
    recon_p.add_argument(
        "--full",
        action="store_true",
        help="Process the full remaining 256-partition cycle",
    )
    recon_p.add_argument(
        "--fresh-cycle",
        action="store_true",
        help=(
            "Start the selected target(s) at partition 0 instead of resuming "
            "durable progress (requires --repair --full)"
        ),
    )
    recon_p.add_argument(
        "--max-partitions",
        type=int,
        default=16,
        help="Bounded partitions per target when --full is not set (default: 16)",
    )
    recon_p.add_argument(
        "--start-partition",
        type=int,
        default=0,
        help="Read-only starting partition, 0-255 (repair resumes durable state)",
    )
    recon_p.add_argument(
        "--target",
        choices=("typesense",),
        default="typesense",
        help="Derived store to inspect (Typesense only)",
    )

    sub.add_parser("backfill-locations", help="Enqueue re-scrapes for jobs missing locations")

    backfill_desc_p = sub.add_parser(
        "backfill-descriptions",
        help=(
            "Reset next_scrape_at = now() on rich-monitor postings whose "
            "description is missing because the board's enrich config flipped "
            "AFTER the rows were inserted via the no-enrich path "
            "(_INSERT_RICH_JOB). Restricted by --slug; defaults to the 20 "
            "stuck companies from #2996. The _DIFF_BATCH self-heal in #2996 "
            "prevents the bug for FUTURE config flips, this CLI cleans up "
            "the historical backlog."
        ),
    )
    backfill_desc_p.add_argument(
        "--slug",
        action="append",
        default=None,
        help=(
            "Limit to this company slug. Pass multiple times for several "
            "companies. Default: the 20 stuck companies from #2996."
        ),
    )
    backfill_desc_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the count that would be affected; make no writes.",
    )

    reprocess_exp_p = sub.add_parser(
        "reprocess-experience",
        help=(
            "Recompute experience_min/experience_max from stored descriptions. "
            "Default scope targets likely #3289 stale rows: active postings "
            "with month or decimal-year requirements and current "
            "experience_min NULL or 5."
        ),
    )
    reprocess_exp_p.add_argument(
        "--slug",
        action="append",
        default=None,
        help="Limit to this company slug. Pass multiple times for several companies.",
    )
    reprocess_exp_p.add_argument(
        "--all-candidates",
        action="store_true",
        help="Scan all active postings with stored descriptions instead of only likely stale rows.",
    )
    reprocess_exp_p.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of postings to scan per database batch (default 1000).",
    )
    reprocess_exp_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many changed postings; useful for staged production runs.",
    )
    reprocess_exp_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run extraction and report would-change rows; make no writes.",
    )

    reprocess_salary_p = sub.add_parser(
        "reprocess-salary-eu",
        help=(
            "Recompute salary fields from stored descriptions for the EU "
            "country sets used by #3324/#3341/#3359."
        ),
    )
    from src.salary_reprocess import add_salary_reprocess_arguments

    add_salary_reprocess_arguments(reprocess_salary_p)

    reprocess_occ_p = sub.add_parser(
        "reprocess-occupations",
        help=(
            "Recompute occupation_id from posting titles for rows likely stale "
            "after taxonomy splits (#3360)."
        ),
    )
    from src.occupation_reprocess import add_occupation_reprocess_arguments

    add_occupation_reprocess_arguments(reprocess_occ_p)

    retry_p = sub.add_parser(
        "retry-stalled-scrapes",
        help=(
            "Reset next_scrape_at for postings stuck in transient-3-strike "
            "state (is_active=true, next_scrape_at IS NULL, scrape_failures "
            ">= 3, last_scraped_at older than --max-age-days). Operator "
            "recovery for the gap described in #2738 / "
            "docs/03-crawler-architecture.md 'Delisting model'."
        ),
    )
    retry_p.add_argument(
        "--max-age-days",
        type=int,
        default=7,
        help=(
            "Only target postings whose last_scraped_at is older than this "
            "many days (default 7). A 0 means 'every stalled posting "
            "regardless of age' — use with care."
        ),
    )
    retry_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the count that would be affected; make no writes.",
    )

    sub.add_parser("backfill-typesense", help="Full re-index of job_posting to Typesense")

    sub.add_parser(
        "verify-typesense-taxonomies",
        help="Strict local-Postgres -> Typesense taxonomy readiness gate",
    )

    sub.add_parser("refresh-typesense", help="Refresh Typesense counts + reconcile watchlists")

    refresh_currency_p = sub.add_parser(
        "refresh-currency-rates",
        help="Fetch ECB daily reference rates and upsert currency_rate",
    )
    refresh_currency_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse ECB rates, but do not write currency_rate",
    )

    setup_ts_p = sub.add_parser(
        "setup-typesense",
        help="Create / update Typesense collections + aliases (idempotent)",
    )
    setup_ts_p.add_argument(
        "--force",
        action="store_true",
        help="Drop existing collections and recreate from scratch",
    )

    indexnow_p = sub.add_parser(
        "notify-indexnow",
        help="Push changed company URLs to IndexNow (Bing/Yandex/Seznam/Naver/Yep)",
    )
    indexnow_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the diff and log the count without POSTing or recording hashes",
    )

    sub.add_parser(
        "invalidate-typeahead",
        help=(
            "POST to the web-side typeahead invalidation endpoint to drop "
            "stale *-suggest:* caches after a CSV/taxonomy change."
        ),
    )

    phantom_p = sub.add_parser(
        "sweep-phantoms",
        help=(
            "Classify terminal boards and delist active postings only for "
            "removed configuration or spaced provider-gone confirmations"
        ),
    )
    phantom_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the classified candidate count without changing postings",
    )
    phantom_p.add_argument(
        "--chunk-size",
        type=int,
        default=1_000,
        help="Rows committed per transaction (default: 1000; max: 10000)",
    )
    phantom_p.add_argument(
        "--max-chunks",
        type=int,
        default=100,
        help="Maximum committed chunks in this resumable invocation (default: 100)",
    )

    board_p = sub.add_parser("board", help="Dev testing for a single board")
    board_p.add_argument("slug", help="Board slug to process")
    board_p.add_argument("--dry-run", action="store_true", help="No DB writes")
    board_p.add_argument("-v", "--verbose", action="store_true", help="Log all fields")
    board_p.add_argument(
        "--pcsx-full-crawl",
        action="store_true",
        help=(
            "Force a full PCSX crawl on eightfold boards, ignoring the "
            "watermark. Used for manual backfills of large boards (e.g. "
            "Starbucks) before enabling steady-state incremental mode."
        ),
    )

    retire_p = sub.add_parser(
        "retire-stale-boards",
        help=(
            "Fail-closed retirement evidence report. Database terminal state "
            "selects candidates, then current provider-native probes split "
            "verified gone, live again, inconclusive, integration-broken, "
            "and zero-board registry orphan sections. Executable removal "
            "output requires current gone evidence plus durable spaced "
            "confirmations."
        ),
    )
    retire_p.add_argument(
        "--days",
        type=int,
        default=14,
        help=(
            "Minimum days since last_success_at before a board is a candidate "
            "(default 14). Boards with NULL last_success_at always qualify."
        ),
    )
    retire_p.add_argument(
        "--format",
        choices=["md", "json", "shell"],
        default="md",
        help=(
            "Output format: `md` evidence report, `json` structured reason "
            "codes, or `shell` commands only for candidates that pass every "
            "provider and durable-confirmation gate."
        ),
    )
    retire_p.add_argument(
        "--probe-concurrency",
        type=int,
        default=5,
        help="Concurrent provider-native liveness probes (default: 5; max: 20)",
    )

    prune_p = sub.add_parser(
        "prune-scrape-queues",
        help=(
            "Drop scrapes_<wtype>:<domain> zset entries older than N days "
            "and their scrape:<task_id> hashes. Operational cleanup for "
            "domains whose shared rate limit can't drain faster than the "
            "monitor re-enqueues."
        ),
    )
    prune_p.add_argument(
        "--older-than-days",
        type=float,
        default=7.0,
        help="Entries with next_scrape_at before now - this many days are purged (default 7).",
    )
    prune_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be removed; do not write.",
    )

    deadletter_p = sub.add_parser(
        "deadletters",
        help="Inspect or explicitly resolve lifecycle-classified monitor deadletters",
    )
    deadletter_p.add_argument(
        "action",
        choices=("inspect", "retry", "prune"),
        help="Inspect all entries, retry active entries, or prune authoritative stale entries",
    )
    deadletter_p.add_argument(
        "--entry",
        action="append",
        default=None,
        help=(
            "Exact '<wtype>:<member>' selector from inspect output. Repeat for multiple "
            "entries. Required for retry/prune."
        ),
    )
    deadletter_p.add_argument(
        "--apply",
        action="store_true",
        help="Perform selected retry/prune mutations (default is dry-run)",
    )

    redis_capacity_p = sub.add_parser(
        "redis-capacity",
        help="Inspect budgets, prune orphan scrape configs, or rebuild schedules",
    )
    redis_capacity_p.add_argument("action", choices=("inspect", "prune", "rebuild"))
    redis_capacity_p.add_argument(
        "--format",
        choices=("json", "prometheus"),
        default="json",
        help="Inspect output format (default: json)",
    )
    redis_capacity_p.add_argument(
        "--apply",
        action="store_true",
        help="Apply prune/rebuild mutations (default is dry-run)",
    )
    redis_capacity_p.add_argument(
        "--cursor",
        type=int,
        default=0,
        help="Redis SCAN cursor returned by a prior prune invocation",
    )
    redis_capacity_p.add_argument(
        "--max-scanned",
        type=int,
        default=50_000,
        help="Maximum scrape hashes classified by one prune invocation",
    )
    redis_capacity_p.add_argument(
        "--max-delete",
        type=int,
        default=50_000,
        help="Maximum scrape hashes unlinked by one prune invocation",
    )
    redis_capacity_p.add_argument(
        "--after-id",
        default=None,
        help="Posting UUID cursor returned by a prior rebuild invocation",
    )
    redis_capacity_p.add_argument(
        "--limit",
        type=int,
        default=10_000,
        help="Maximum durable schedules selected by one rebuild invocation",
    )

    args = parser.parse_args()
    if args.command == "reconcile" and args.fresh_cycle and not (args.repair and args.full):
        parser.error("reconcile --fresh-cycle requires --repair --full")
    return args


async def run() -> None:
    args = parse_args()
    setup_logging(settings.log_level)

    log.info("cli.starting", command=args.command, worker_id=WORKER_ID)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_event.set)

    try:
        if args.command == "run":
            start_metrics_server(settings.metrics_port)
            local_pool = await create_local_pool()
            http = create_http_client()
            try:
                from src.workers.pipeline import run_pipeline

                await run_pipeline(local_pool, http, shutdown_event, browser=False)
            finally:
                await http.aclose()

        elif args.command == "run-browser":
            start_metrics_server(settings.metrics_port)
            local_pool = await create_local_pool()
            http = create_http_client()
            try:
                from src.workers.pipeline import run_pipeline

                await run_pipeline(local_pool, http, shutdown_event, browser=True)
            finally:
                await http.aclose()

        elif args.command == "export":
            start_metrics_server(settings.metrics_port)
            # Apply CLI overrides to settings
            settings.export_batch_limit = args.batch_size
            settings.export_interval = args.interval
            local_pool = await create_local_pool()
            from src.exporter import run_exporter

            await run_exporter(local_pool, None, shutdown_event)

        elif args.command == "drain":
            start_metrics_server(settings.metrics_port)
            local_pool = await create_local_pool()
            from src.workers.r2_drain import r2_drain_loop

            await r2_drain_loop(local_pool, shutdown_event)

        elif args.command == "sync":
            from src.sync import run_sync

            await run_sync()

        elif args.command == "repair-nw-provider-cutover":
            local_pool = await create_local_pool()
            from src.nw_provider_cutover import reapply_nw_provider_cutover

            async with local_pool.acquire() as acquired_connection:
                connection = cast(asyncpg.Connection, acquired_connection)
                await reapply_nw_provider_cutover(connection)

        elif args.command == "repair-location-taxonomy-source":
            local_pool = await create_local_pool()
            source_pool = await create_web_pool()
            from src.location_taxonomy_repair import repair_location_taxonomy_source

            summary = await repair_location_taxonomy_source(source_pool, local_pool)
            sys.stdout.write(json.dumps(summary.to_dict(), sort_keys=True) + "\n")
            sys.stdout.flush()

        elif args.command == "ats-inventory":
            from datetime import UTC, datetime, timedelta

            import httpx

            from src.ats_inventory.github import (
                GitHubRateLimitError,
                GitHubSupportIssueClient,
                read_injected_github_token,
                reconcile_support_issues,
            )
            from src.ats_inventory.impact import ImpactCache
            from src.ats_inventory.locking import exclusive_run_lock
            from src.ats_inventory.source import InventorySource

            timeout = httpx.Timeout(60.0, read=180.0)

            def rate_limit_report(mode: str, error: GitHubRateLimitError) -> dict[str, Any]:
                now = int(datetime.now(UTC).timestamp())
                return {
                    "mode": mode,
                    "status": "rate_limited_preflight",
                    "actions": [],
                    "rate_remaining": github.rate_remaining if github is not None else None,
                    "rate_reset": error.reset_at,
                    "retry_after": error.retry_after,
                    "retry_at": error.retry_at(now=now),
                }

            if args.candidate_limit < 1:
                raise ValueError("--candidate-limit must be at least 1")
            with exclusive_run_lock(args.cache_dir / "runner.lock"):
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                    headers={"User-Agent": "jobseek-ats-inventory/1"},
                ) as client:
                    source = InventorySource(
                        args.cache_dir,
                        client,
                        manifest_url=args.manifest_url,
                        retention=args.retention,
                        max_cache_bytes=args.max_cache_mib * 1024 * 1024,
                    )
                    snapshot = await source.sync()
                    report = snapshot.to_report()
                    report["impact"] = {"enabled": args.impact}
                    impact_snapshot = None
                    impact = ImpactCache(
                        args.cache_dir / "impact",
                        client,
                        max_cache_bytes=args.impact_max_cache_mib * 1024 * 1024,
                        max_artifact_bytes=args.impact_max_artifact_mib * 1024 * 1024,
                        min_free_bytes=args.impact_free_reserve_mib * 1024 * 1024,
                    )
                    if args.impact:
                        impact_snapshot = await impact.sync(snapshot)
                        report["impact"] = {
                            "enabled": True,
                            **impact_snapshot.to_report(),
                        }
                    report["support_issues"] = {"mode": args.support_issues, "actions": []}
                    github = None
                    if args.support_issues != "off" or args.candidate_issues != "off":
                        token = read_injected_github_token(
                            env_name=args.github_token_env,
                            token_file=args.github_token_file,
                        )
                        github = GitHubSupportIssueClient(
                            client,
                            repo=args.github_repo,
                            token=token,
                            rate_limit_reserve=args.github_rate_reserve,
                        )
                    automatic_support_mode = (
                        "create"
                        if args.candidate_issues == "refill"
                        else "plan"
                        if args.candidate_issues == "dry-run"
                        else "off"
                    )
                    preflight_rate_error = None
                    if args.support_issues != "off" or automatic_support_mode != "off":
                        assert github is not None
                        support_create = (
                            args.support_issues == "create" or automatic_support_mode == "create"
                        )
                        support_mode = "create" if support_create else "plan"
                        try:
                            actions = await reconcile_support_issues(
                                snapshot,
                                github,
                                create=support_create,
                            )
                        except GitHubRateLimitError as exc:
                            preflight_rate_error = exc
                            report["support_issues"] = {
                                **rate_limit_report(support_mode, exc),
                                "automatic": automatic_support_mode != "off",
                            }
                        else:
                            report["support_issues"] = {
                                "mode": support_mode,
                                "automatic": automatic_support_mode != "off",
                                "actions": [action.to_dict() for action in actions],
                                "rate_remaining": github.rate_remaining,
                                "rate_reset": github.rate_reset,
                            }
                    report["candidate_issues"] = {
                        "mode": args.candidate_issues,
                        "actions": [],
                    }
                    if preflight_rate_error is not None and args.candidate_issues != "off":
                        report["candidate_issues"] = rate_limit_report(
                            args.candidate_issues, preflight_rate_error
                        )
                    elif args.candidate_issues == "plan":
                        from dataclasses import asdict

                        from src.ats_inventory.candidate_issues import (
                            CandidateIssueCoordinator,
                        )
                        from src.ats_inventory.candidates import Candidate, LocalRegistryIndex
                        from src.ats_inventory.ledger import CandidateLedger

                        assert github is not None
                        if not snapshot.coverage.candidate_generation_allowed:
                            raise RuntimeError(
                                "candidate generation is quarantined below the coverage gate"
                            )
                        if impact_snapshot is None:
                            impact_snapshot = impact.load_current()
                        if (
                            impact_snapshot.manifest_sha256 != snapshot.manifest_sha256
                            or impact_snapshot.inventory_sha256 != snapshot.inventory_sha256
                        ):
                            raise RuntimeError(
                                "cached impact does not match the current inventory; rerun with "
                                "--impact"
                            )
                        local = LocalRegistryIndex.from_csv(args.companies_file, args.boards_file)
                        ledger_path = args.candidate_ledger or (
                            args.cache_dir / "candidates" / "ledger.sqlite"
                        )
                        coordinator = await CandidateIssueCoordinator.bootstrap(
                            client=github,
                            local=local,
                            ledger=CandidateLedger(ledger_path),
                        )
                        candidate_actions = []
                        for company in impact_snapshot.ranked()[: args.candidate_limit]:
                            candidate = Candidate.from_impact(company)
                            candidate_actions.append(
                                await coordinator.create(candidate, dry_run=True)
                            )
                        report["candidate_issues"] = {
                            "mode": "plan",
                            "considered": len(candidate_actions),
                            "eligible": sum(
                                action.action == "would_create" for action in candidate_actions
                            ),
                            "actions": [action.to_dict() for action in candidate_actions],
                            "ledger_reconciliation": asdict(coordinator.reconciliation),
                            "rate_remaining": github.rate_remaining,
                            "rate_reset": github.rate_reset,
                        }
                    elif args.candidate_issues in {"report", "dry-run", "refill"}:
                        from dataclasses import asdict
                        from typing import Literal

                        from src.ats_inventory.candidate_issues import (
                            CandidateIssueCoordinator,
                        )
                        from src.ats_inventory.candidates import LocalRegistryIndex
                        from src.ats_inventory.ledger import CandidateLedger
                        from src.ats_inventory.queue import QueuePolicy, QueueRefiller

                        assert github is not None
                        policy = QueuePolicy(
                            low_water=args.queue_low_water,
                            target=args.queue_target,
                            hard_cap=args.queue_hard_cap,
                            per_tick_cap=args.queue_per_tick_cap,
                            daily_cap=args.queue_daily_cap,
                            rollout_cap=args.queue_rollout_cap,
                            claim_ttl_seconds=int(args.queue_claim_ttl_hours * 60 * 60),
                            jitter_min_seconds=args.queue_jitter_min_seconds,
                            jitter_max_seconds=args.queue_jitter_max_seconds,
                        )
                        ledger_path = args.candidate_ledger or (
                            args.cache_dir / "candidates" / "ledger.sqlite"
                        )
                        ledger = CandidateLedger(ledger_path)
                        queue_mode = cast(
                            Literal["report", "dry-run", "refill"],
                            args.candidate_issues,
                        )
                        try:
                            items = await github.list_candidate_work_items()
                            claims = await github.list_recent_claims(
                                since=datetime.now(UTC)
                                - timedelta(seconds=policy.claim_ttl_seconds)
                            )
                        except GitHubRateLimitError as exc:
                            report["candidate_issues"] = rate_limit_report(queue_mode, exc)
                        else:
                            coordinator = await CandidateIssueCoordinator.bootstrap(
                                client=github,
                                local=LocalRegistryIndex.from_csv(
                                    args.companies_file, args.boards_file
                                ),
                                ledger=ledger,
                                items=items,
                            )
                            companies = ()
                            admission_block = None
                            if queue_mode in {"dry-run", "refill"}:
                                if not snapshot.coverage.candidate_generation_allowed:
                                    admission_block = "coverage_quarantined"
                                else:
                                    if impact_snapshot is None:
                                        impact_snapshot = impact.load_current()
                                    if (
                                        impact_snapshot.manifest_sha256 != snapshot.manifest_sha256
                                        or impact_snapshot.inventory_sha256
                                        != snapshot.inventory_sha256
                                    ):
                                        raise RuntimeError(
                                            "cached impact does not match the current inventory; "
                                            "rerun with --impact"
                                        )
                                    companies = impact_snapshot.ranked()
                            queue_report = await QueueRefiller(
                                coordinator=coordinator,
                                ledger=ledger,
                                items=items,
                                claims=claims,
                                policy=policy,
                                refresh_open_count=github.count_open_company_requests,
                            ).run(
                                companies,
                                mode=queue_mode,
                                admission_block=admission_block,
                            )
                            report["candidate_issues"] = {
                                **queue_report.to_dict(),
                                "ledger_reconciliation": asdict(coordinator.reconciliation),
                            }
                    log.info("ats_inventory.complete", report=report)
                    tty_message(json.dumps(report, indent=2, sort_keys=True))

        elif args.command == "backfill-locations":
            local_pool = await create_local_pool()
            from src.backfill import backfill_locations

            await backfill_locations(local_pool)

        elif args.command == "backfill-descriptions":
            local_pool = await create_local_pool()
            from src.backfill import backfill_descriptions

            n = await backfill_descriptions(
                local_pool,
                company_slugs=args.slug,
                dry_run=args.dry_run,
            )
            log.info(
                "backfill_descriptions.done",
                dry_run=args.dry_run,
                count=n,
                slugs=args.slug,
            )

        elif args.command == "reprocess-experience":
            local_pool = await create_local_pool()
            from src.backfill import reprocess_experience

            summary = await reprocess_experience(
                local_pool,
                company_slugs=args.slug,
                only_suspect=not args.all_candidates,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
                limit=args.limit,
            )
            log.info(
                "reprocess_experience.done",
                dry_run=args.dry_run,
                scanned_postings=summary.scanned_postings,
                changed_postings=summary.changed_postings,
                updated_postings=summary.updated_postings,
                only_suspect=not args.all_candidates,
                slugs=args.slug,
            )

        elif args.command == "reprocess-salary-eu":
            from src.salary_reprocess import run_from_args

            exit_code = await run_from_args(args)
            if exit_code != 0:
                raise SystemExit(exit_code)

        elif args.command == "reprocess-occupations":
            from src.occupation_reprocess import run_from_args

            exit_code = await run_from_args(args)
            if exit_code != 0:
                raise SystemExit(exit_code)

        elif args.command == "retry-stalled-scrapes":
            local_pool = await create_local_pool()
            from src.retry_stalled import count_stalled_scrapes, retry_stalled_scrapes

            if args.dry_run:
                count = await count_stalled_scrapes(local_pool, args.max_age_days)
                log.info(
                    "retry_stalled.dry_run",
                    candidates=count,
                    max_age_days=args.max_age_days,
                )
            else:
                await retry_stalled_scrapes(local_pool, max_age_days=args.max_age_days)

        elif args.command == "backfill-typesense":
            from src.cron_metrics import cron_run

            async with cron_run("backfill-typesense"):
                local_pool = await create_local_pool()
                from src.exporter import backfill_typesense

                await backfill_typesense(local_pool)

        elif args.command == "verify-typesense-taxonomies":
            local_pool = await create_local_pool()
            from src.taxonomy_readiness import run_cli
            from src.typesense_client import get_typesense_client

            exit_code = await run_cli(local_pool, get_typesense_client())
            if exit_code != 0:
                raise SystemExit(exit_code)

        elif args.command == "refresh-typesense":
            from src.cron_metrics import cron_run

            async with cron_run("refresh-typesense"):
                local_pool = await create_local_pool()
                web_pool = await create_web_pool()
                from src.sync import refresh_typesense_counts, sync_watchlists_typesense
                from src.typesense_client import get_typesense_client

                ts_client = get_typesense_client()
                if not ts_client:
                    # Treat as failure so the cron metric records a
                    # non-success — a misconfigured environment isn't
                    # silent any more.
                    log.error("refresh-typesense: Typesense not configured")
                    raise RuntimeError("refresh-typesense: Typesense not configured")
                async with local_pool.acquire() as local_conn, web_pool.acquire() as web_conn:
                    local_connection = cast(asyncpg.Connection, local_conn)
                    web_connection = cast(asyncpg.Connection, web_conn)
                    await refresh_typesense_counts(local_connection, ts_client)
                    await sync_watchlists_typesense(web_connection, local_connection, ts_client)
                log.info("refresh-typesense: done")

        elif args.command == "refresh-currency-rates":
            from src.cron_metrics import cron_run
            from src.scripts.refresh_currency_rates import refresh_currency_rates

            async with cron_run("refresh-currency-rates"):
                local_pool = None if args.dry_run else await create_local_pool()
                http = create_http_client()
                try:
                    result = await refresh_currency_rates(
                        local_pool,
                        http,
                        dry_run=args.dry_run,
                    )
                finally:
                    await http.aclose()
                log.info(
                    "refresh-currency-rates.done",
                    dry_run=result.dry_run,
                    rate_date=result.rate_date.isoformat(),
                    updated_at=result.updated_at.isoformat(),
                    count=result.count,
                )

        elif args.command == "setup-typesense":
            from src.typesense_schema import run_setup

            run_setup(force=args.force)

        elif args.command == "notify-indexnow":
            start_metrics_server(settings.metrics_port)
            local_pool = await create_local_pool()
            http = create_http_client()
            try:
                from src.indexnow import notify_indexnow

                await notify_indexnow(local_pool, http, dry_run=args.dry_run)
            finally:
                await http.aclose()

        elif args.command == "invalidate-typeahead":
            from src.notify_invalidate import notify_invalidate_typeahead

            http = create_http_client()
            try:
                ok = await notify_invalidate_typeahead(http)
                if not ok:
                    log.warning("invalidate-typeahead: completed with warnings")
            finally:
                await http.aclose()

        elif args.command == "sweep-phantoms":
            local_pool = await create_local_pool()
            from src.phantom_sweep import refresh_derived_surfaces, sweep_phantom_postings

            summary = await _await_task_or_shutdown(
                asyncio.create_task(
                    sweep_phantom_postings(
                        local_pool,
                        dry_run=args.dry_run,
                        chunk_size=args.chunk_size,
                        max_chunks=args.max_chunks,
                    )
                ),
                shutdown_event,
            )
            if summary is not None:
                log.info(
                    "phantom_sweep.done",
                    dry_run=summary.dry_run,
                    eligible_boards=summary.eligible_boards,
                    configured_disabled_boards=summary.configured_disabled_boards,
                    candidate_postings=summary.candidate_postings,
                    updated_postings=summary.updated_postings,
                    remaining_postings=summary.remaining_postings,
                    chunks_committed=summary.chunks_committed,
                    complete=summary.complete,
                )
                if not summary.dry_run:
                    await refresh_derived_surfaces(local_pool)

        elif args.command == "reconcile":
            local_pool = await create_local_pool()
            from src.reconciliation import run_reconciliation

            await _await_task_or_shutdown(
                asyncio.create_task(
                    run_reconciliation(
                        local_pool,
                        None,
                        repair=args.repair,
                        full=args.full,
                        fresh_cycle=args.fresh_cycle,
                        max_partitions=args.max_partitions,
                        start_partition=args.start_partition,
                        target_scope=args.target,
                    )
                ),
                shutdown_event,
            )

        elif args.command == "board":
            local_pool = await create_local_pool()
            http = create_http_client()
            try:
                from src.processing.board import dry_run_single_board, run_single_board

                if args.dry_run:
                    from playwright.async_api import async_playwright

                    async with async_playwright() as pw:
                        await dry_run_single_board(
                            local_pool,
                            http,
                            args.slug,
                            verbose=args.verbose,
                            pw=pw,
                            pcsx_force_full_crawl=args.pcsx_full_crawl,
                        )
                else:
                    await run_single_board(
                        local_pool,
                        http,
                        args.slug,
                        pcsx_force_full_crawl=args.pcsx_full_crawl,
                    )
            finally:
                await http.aclose()

        elif args.command == "prune-scrape-queues":
            from src.redis_queue import prune_stale_scrape_queues

            result = await prune_stale_scrape_queues(
                older_than_days=args.older_than_days,
                dry_run=args.dry_run,
            )
            log.info("prune.scrape_queues.done", dry_run=args.dry_run, **result)

        elif args.command == "deadletters":
            local_pool = await create_local_pool()
            from src.deadletters import resolve_deadletters

            result = await resolve_deadletters(
                local_pool,
                action=args.action,
                selected_refs=args.entry,
                apply=args.apply,
            )
            output = json.dumps(result, indent=2, sort_keys=True)
            log.info(
                "deadletters.complete",
                action=args.action,
                dry_run=not args.apply,
                selected=result["selected"],
                counts=result["counts"],
            )
            tty_message(output)

        elif args.command == "redis-capacity":
            from src.redis_capacity import (
                format_prometheus,
                inventory,
                prune_orphan_scrape_configs,
                rebuild_scrape_schedules,
            )

            if args.action == "inspect":
                snapshot = await inventory()
                output = (
                    format_prometheus(snapshot)
                    if args.format == "prometheus"
                    else json.dumps(snapshot, indent=2, sort_keys=True)
                )
            elif args.action == "prune":
                result = await prune_orphan_scrape_configs(
                    cursor=args.cursor,
                    max_scanned=args.max_scanned,
                    max_delete=args.max_delete,
                    apply=args.apply,
                )
                output = json.dumps(result, indent=2, sort_keys=True)
            else:
                local_pool = await create_local_pool()
                result = await rebuild_scrape_schedules(
                    local_pool,
                    after_id=args.after_id,
                    limit=args.limit,
                    apply=args.apply,
                )
                output = json.dumps(result, indent=2, sort_keys=True)
            # This command is also invoked non-interactively by the host
            # metrics sampler and recovery runbooks; stdout is its stable
            # machine interface, unlike tty_message's interactive-only path.
            sys.stdout.write(output)
            if not output.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()

        elif args.command == "retire-stale-boards":
            from src.retire_stale_boards import report_stale_boards

            local_pool = await create_local_pool()
            async with local_pool.acquire() as conn:
                output = await report_stale_boards(
                    cast(asyncpg.Connection, conn),
                    days=args.days,
                    fmt=args.format,
                    concurrency=args.probe_concurrency,
                )
            log.info(
                "retire_stale_boards.report",
                days=args.days,
                format=args.format,
                probe_concurrency=args.probe_concurrency,
                output=output,
            )
            tty_message(output)

    finally:
        log.info("cli.shutting_down")
        await close_all_pools()
        log.info("cli.stopped")


def main() -> None:
    asyncio.run(run())
