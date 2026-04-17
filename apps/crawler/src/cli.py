"""CLI entry point for the crawler.

Subcommand-based interface that dispatches to the appropriate worker,
exporter, drain, or dev-testing function. All concurrency is configured
via environment variables / config.py, not CLI flags.

Entry point: ``crawler = "src.cli:main"`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import uuid

import dotenv
import structlog

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")

from src.config import settings  # noqa: E402
from src.db import close_all_pools, create_local_pool, create_pool  # noqa: E402
from src.metrics import start_metrics_server  # noqa: E402
from src.shared.http import create_http_client  # noqa: E402
from src.shared.logging import setup_logging  # noqa: E402

log = structlog.get_logger()

_rand = uuid.uuid4().hex[:8]
WORKER_ID = f"{settings.worker_id_prefix}-{_rand}" if settings.worker_id_prefix else _rand


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="crawler")
    sub = parser.add_subparsers(dest="command", required=True)

    # Production subcommands
    sub.add_parser("run", help="Worker instance (all non-browser profiles)")
    sub.add_parser("run-browser", help="Browser instance (browser profiles only)")

    export_p = sub.add_parser("export", help="CDC exporter (local Postgres -> Supabase)")
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

    sub.add_parser("sync", help="CSV -> local Postgres + Supabase + Redis")

    recon_p = sub.add_parser("reconcile", help="Reconciliation")
    recon_g = recon_p.add_mutually_exclusive_group()
    recon_g.add_argument("--full", action="store_true", help="Touch all rows")
    recon_g.add_argument("--bootstrap", action="store_true", help="Bootstrap local from Supabase")

    sub.add_parser("backfill-locations", help="Enqueue re-scrapes for jobs missing locations")

    sub.add_parser("backfill-typesense", help="Full re-index of job_posting to Typesense")

    sub.add_parser("refresh-typesense", help="Refresh Typesense counts + reconcile watchlists")

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

    # Phase 1: local alert pipeline
    mark_p = sub.add_parser(
        "mark-candidates",
        help="Flag postings that pass cheap filters as enrichment candidates",
    )
    mark_p.add_argument(
        "--filters",
        default="data/alert-filters.yaml",
        help="Path to filters YAML (default: data/alert-filters.yaml)",
    )

    enrich_local_p = sub.add_parser(
        "enrich-local",
        help="Enrich flagged postings via sync Gemini calls (local mode)",
    )
    enrich_local_p.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Postings per claim iteration (default: 20)",
    )
    enrich_local_p.add_argument(
        "--rate-limit-rpm",
        type=int,
        default=None,
        help="Gemini calls per minute (default: from ENRICH_RATE_LIMIT_RPM env, fallback 15)",
    )

    alert_p = sub.add_parser(
        "alert",
        help="Print visa-sponsoring entry-level jobs as JSON",
    )
    alert_p.add_argument(
        "--filters",
        default="data/alert-filters.yaml",
        help="Path to filters YAML (default: data/alert-filters.yaml)",
    )
    alert_p.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="Output format (default: json)",
    )

    parse_resume_p = sub.add_parser(
        "parse-resume",
        help="Extract resume skills profile to ai/resume-parsed.yaml",
    )
    parse_resume_p.add_argument(
        "--resume",
        required=True,
        help="Path to resume file (.tex, .pdf, .txt, .md)",
    )
    parse_resume_p.add_argument(
        "--output",
        default="ai/resume-parsed.yaml",
        help="Output YAML path (default: ai/resume-parsed.yaml)",
    )

    score_p = sub.add_parser(
        "score",
        help="Score filtered jobs against parsed resume",
    )
    score_p.add_argument(
        "--filters",
        default="data/alert-filters.yaml",
        help="Path to filters YAML (default: data/alert-filters.yaml)",
    )
    score_p.add_argument(
        "--resume",
        default="ai/resume-parsed.yaml",
        help="Path to parsed resume YAML (default: ai/resume-parsed.yaml)",
    )

    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    setup_logging(settings.log_level)

    log.info("cli.starting", command=args.command, worker_id=WORKER_ID)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
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
            supa_pool = await create_pool()
            from src.exporter import run_exporter_with_reconciliation

            await run_exporter_with_reconciliation(local_pool, supa_pool, shutdown_event)

        elif args.command == "drain":
            start_metrics_server(settings.metrics_port)
            local_pool = await create_local_pool()
            from src.workers.r2_drain import r2_drain_loop

            await r2_drain_loop(local_pool, shutdown_event)

        elif args.command == "sync":
            from src.sync import run_sync

            await run_sync()

        elif args.command == "backfill-locations":
            local_pool = await create_local_pool()
            from src.backfill import backfill_locations

            await backfill_locations(local_pool)

        elif args.command == "backfill-typesense":
            local_pool = await create_local_pool()
            supa_pool = await create_pool()
            from src.exporter import backfill_typesense

            await backfill_typesense(local_pool, supa_pool)

        elif args.command == "refresh-typesense":
            local_pool = await create_local_pool()
            supa_pool = await create_pool()
            from src.sync import refresh_typesense_counts, sync_watchlists_typesense
            from src.typesense_client import get_typesense_client

            ts_client = get_typesense_client()
            if not ts_client:
                log.error("refresh-typesense: Typesense not configured")
            else:
                async with local_pool.acquire() as local_conn, supa_pool.acquire() as supa_conn:
                    await refresh_typesense_counts(local_conn, ts_client)
                    await sync_watchlists_typesense(supa_conn, ts_client)
                log.info("refresh-typesense: done")

        elif args.command == "reconcile":
            local_pool = await create_local_pool()
            supa_pool = await create_pool()
            from src.exporter import run_reconciliation

            await run_reconciliation(local_pool, supa_pool)

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

        elif args.command == "mark-candidates":
            local_pool = await create_local_pool()
            from src.core.enrich.local import mark_candidates_from_yaml

            result = await mark_candidates_from_yaml(local_pool, args.filters)
            print(
                f"mark-candidates: {result['marked']} candidates flagged, "
                f"{result['cleared']} cleared"
            )

        elif args.command == "enrich-local":
            local_pool = await create_local_pool()
            from src.core.enrich.local import run_sync_enrich
            from src.core.enrich.providers import create_sync_provider

            rpm = args.rate_limit_rpm or settings.enrich_rate_limit_rpm
            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )
            result = await run_sync_enrich(
                local_pool,
                provider,
                batch_size=args.batch_size,
                rate_limit_rpm=rpm,
            )
            print(
                f"enrich-local: enriched={result['enriched']} "
                f"failed={result['failed']} skipped={result['skipped']}"
            )

        elif args.command == "alert":
            import json as _json

            local_pool = await create_local_pool()
            from src.core.enrich.local import _build_exclude_regex, load_filter_config
            from src.queries.alert import run_alert_query

            cfg = load_filter_config(args.filters)
            exclude_regex = _build_exclude_regex(cfg.exclude_title_patterns)
            experience_max = cfg.require.experience_max if cfg.require.experience_max is not None else 9999

            async with local_pool.acquire() as conn:
                rows = await run_alert_query(
                    conn,
                    experience_max=experience_max,
                    exclude_title_regex=exclude_regex,
                    limit=cfg.output.limit,
                    work_permit_support=cfg.require.work_permit_support,
                )

            log.info("alert.query", row_count=len(rows))

            if args.format == "json":
                # Convert non-serializable types
                output = []
                for r in rows:
                    d = dict(r)
                    for k, v in d.items():
                        if hasattr(v, "isoformat"):
                            d[k] = v.isoformat()
                    output.append(d)
                print(_json.dumps(output, indent=2, ensure_ascii=False))
            else:
                # table format
                if not rows:
                    print("No matching jobs.")
                else:
                    print(f"{'Title':<50} {'Company':<30} {'Visa':<8} {'First seen'}")
                    print("-" * 100)
                    for r in rows:
                        print(
                            f"{str(r.get('title') or '')[:49]:<50} "
                            f"{str(r.get('company_name') or '')[:29]:<30} "
                            f"{str(r.get('work_permit_support') or ''):<8} "
                            f"{str(r.get('first_seen_at') or '')[:10]}"
                        )

        elif args.command == "parse-resume":
            from src.core.enrich.providers import create_sync_provider
            from src.core.score.resume import parse_resume_with_llm, save_resume

            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )
            parsed = await parse_resume_with_llm(args.resume, provider)
            save_resume(parsed, args.output)
            print(
                f"Resume parsed: {len(parsed.technologies)} technologies, "
                f"{len(parsed.keywords)} keywords → {args.output}"
            )

        elif args.command == "score":
            import asyncio as _asyncio
            from pathlib import Path

            import yaml

            from src.core.enrich.providers import create_sync_provider
            from src.core.score.explain import explain_match
            from src.core.score.overlap import compute_overlap
            from src.core.score.resume import load_resume, resume_hash
            from src.queries.score import fetch_unscored_jobs, upsert_explanation, upsert_score

            resume_path = Path(args.resume)
            if not resume_path.exists():
                print(
                    f"Error: {resume_path} not found. "
                    "Run `crawler parse-resume --resume <path>` first."
                )
                raise SystemExit(1)

            filters_raw = yaml.safe_load(Path(args.filters).read_text(encoding="utf-8"))
            exclude_patterns = filters_raw.get("exclude_title_patterns") or []
            exclude_regex = "|".join(exclude_patterns) if exclude_patterns else "(?!)"
            experience_max = (filters_raw.get("require") or {}).get("experience_max")
            explain_top_n = (filters_raw.get("score") or {}).get("explain_top_n", 20)
            rate_limit_rpm: int = settings.enrich_rate_limit_rpm

            parsed = load_resume(resume_path)
            r_hash = resume_hash(parsed)

            local_pool = await create_local_pool()
            async with local_pool.acquire() as conn:
                jobs = await fetch_unscored_jobs(
                    conn,
                    resume_hash=r_hash,
                    exclude_title_regex=exclude_regex,
                    experience_max=experience_max,
                )

            if not jobs:
                print("No new jobs to score.")
                raise SystemExit(0)

            scored = [(job, compute_overlap(parsed, job)) for job in jobs]
            scored.sort(key=lambda x: x[1], reverse=True)

            async with local_pool.acquire() as conn:
                for job, score in scored:
                    await upsert_score(
                        conn,
                        posting_id=str(job["posting_id"]),
                        resume_hash=r_hash,
                        overlap_score=score,
                    )

            provider = create_sync_provider(
                settings.enrich_provider or "gemini",
                settings.enrich_model or "gemini-2.0-flash",
                settings.enrich_api_key,
            )

            top_jobs = scored[:explain_top_n]
            explained = 0
            for i, (job, _score) in enumerate(top_jobs):
                if i > 0:
                    await _asyncio.sleep(60 / rate_limit_rpm)
                try:
                    explanation = await explain_match(parsed, job, provider)
                    async with local_pool.acquire() as conn:
                        await upsert_explanation(
                            conn,
                            posting_id=str(job["posting_id"]),
                            explanation=explanation,
                        )
                    explained += 1
                except Exception as exc:
                    log.warning(
                        "score.explain_failed",
                        posting_id=str(job["posting_id"]),
                        error=str(exc),
                    )

            top = scored[0]
            print(
                f"Scored {len(scored)} jobs. "
                f"Explained top {explained}. "
                f"Top match: {top[0].get('title')} @ {top[0].get('company_name')} "
                f"(score {top[1]})"
            )

    finally:
        log.info("cli.shutting_down")
        await close_all_pools()
        log.info("cli.stopped")


def main() -> None:
    asyncio.run(run())
