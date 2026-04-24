"""CLI entry point for the labeller subsystem.

Invoked by the orchestrator (a Claude Code session) via Bash. Every
subcommand is side-effect-explicit and stdout-friendly so the orchestrator
can pipe output and read files the next step needs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

import dotenv

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")


def _parse_iso_date(value: str) -> datetime:
    if value == "today":
        return datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Accept YYYY-MM-DD; make timezone-aware (UTC)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="labeller")
    sub = p.add_subparsers(dest="command", required=True)

    # --- sample --------------------------------------------------------
    s = sub.add_parser("sample", help="Sample diverse postings from the last window")
    s.add_argument("--date", default="today", help="End-of-window date (YYYY-MM-DD or 'today')")
    s.add_argument("--window-hours", type=int, default=24)
    s.add_argument("--count", type=int, required=True)
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--out", type=Path, required=True, help="Output JSON path")

    # --- prepare --------------------------------------------------------
    pp = sub.add_parser("prepare", help="Load + normalize + blocks for one posting")
    pp.add_argument("posting_id")
    pp.add_argument("--date", default="today")
    pp.add_argument("--out", type=Path, help="Override input.json path")

    # --- render-task ---------------------------------------------------
    r = sub.add_parser("render-task", help="Render a subagent task input file")
    r.add_argument("--task", required=True)
    r.add_argument("--input", type=Path, required=True, help="Path to input.json")
    r.add_argument("--out", type=Path, required=True, help="Path for rendered markdown")
    r.add_argument("--output-path", help="Subagent output JSON path (goes into the template)")
    r.add_argument("--sections", type=Path, help="Path to split-out.json (required for extract*)")
    r.add_argument("--kind", help="Section kind, required for extract_<kind> tasks")
    r.add_argument("--previous-error", default=None)

    # --- validate ------------------------------------------------------
    v = sub.add_parser("validate", help="Validate a subagent output file")
    v.add_argument("--kind", required=True, help="sections|posting|company|team|...|globals")
    v.add_argument("--file", type=Path, required=True)
    v.add_argument("--context", type=Path, help="input.json (required for sections)")

    # --- merge ---------------------------------------------------------
    m = sub.add_parser("merge", help="Assemble the final posting.json from task outputs")
    m.add_argument("--posting", required=True)
    m.add_argument("--date", required=True)
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--verdict", default="accepted", choices=["accepted", "edited", "rejected"])
    m.add_argument("--rationale", default=None)

    # --- canonicalize --------------------------------------------------
    c = sub.add_parser("canonicalize", help="Produce the canonical sidecar for a posting.json")
    c.add_argument("--file", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)

    # --- upload --------------------------------------------------------
    u = sub.add_parser("upload", help="Push samples + canonical + schemas to HuggingFace")
    u.add_argument("--date", default=None, help="Limit to single date; default: all")
    u.add_argument("--dry-run", action="store_true")

    return p


def _sync_wrap(coro):
    return asyncio.run(coro)


async def _cmd_sample(args: argparse.Namespace) -> int:
    from src.db import close_all_pools, create_local_pool

    from .sampling import Sample, sample_postings, utc_now_minute_floor

    pool = await create_local_pool()
    try:
        end = (
            _parse_iso_date(args.date).replace(hour=0, minute=0, second=0)
            if args.date != "today"
            else utc_now_minute_floor()
        )
        samples: list[Sample] = await sample_postings(
            pool,
            end_time_utc=end,
            window_hours=args.window_hours,
            count=args.count,
            seed=args.seed,
        )
    finally:
        await close_all_pools()

    payload = {
        "end_time_utc": end.isoformat(),
        "window_hours": args.window_hours,
        "requested": args.count,
        "returned": len(samples),
        "postings": [
            {"id": s.posting_id, "company_id": s.company_id, "source_url": s.source_url}
            for s in samples
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))
    print(f"{len(samples)} samples -> {args.out}")
    return 0


async def _cmd_prepare(args: argparse.Namespace) -> int:
    from src.db import close_all_pools, create_local_pool

    from .paths import runs_dir
    from .prepare import build_input, load_posting, write_input

    run_date = (
        _parse_iso_date(args.date).strftime("%Y-%m-%d")
        if args.date != "today"
        else datetime.now(tz=UTC).strftime("%Y-%m-%d")
    )
    out = args.out or (runs_dir(run_date, args.posting_id) / "input.json")

    pool = await create_local_pool()
    try:
        raw = await load_posting(pool, args.posting_id)
    finally:
        await close_all_pools()

    if raw is None:
        print(f"posting {args.posting_id} not found or has no description", file=sys.stderr)
        return 2
    payload = build_input(raw, sampled_at=datetime.now(tz=UTC))
    write_input(out, payload)
    print(f"prepared -> {out}")
    return 0


def _cmd_render_task(args: argparse.Namespace) -> int:
    from .render import render_to_file

    output_hint = args.output_path
    if output_hint is None:
        # Sensible default: same dir as --out, name derived from --task
        output_hint = str(args.out.parent / f"{args.task.replace('_', '-')}-out.json")

    render_to_file(
        args.task,
        args.input,
        args.out,
        sections_path=args.sections,
        kind=args.kind,
        output_path_hint=output_hint,
        previous_error=args.previous_error,
    )
    print(f"rendered {args.task} -> {args.out}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validate import validate_file

    errors = validate_file(args.kind, args.file, args.context)
    if errors:
        print(f"VALIDATION FAILED ({args.kind}) {args.file}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"valid: {args.file}")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    from .merge import merge_posting, write_merged

    merged = merge_posting(
        args.date,
        args.posting,
        qa_verdict=args.verdict,
        qa_rationale=args.rationale,
    )
    write_merged(args.date, args.posting, merged, target=args.out)
    print(f"merged -> {args.out}")
    return 0


def _cmd_canonicalize(args: argparse.Namespace) -> int:
    from .canonicalize import canonicalize_posting

    posting = json.loads(args.file.read_text())
    sidecar = canonicalize_posting(posting)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False))
    print(f"canonical -> {args.out}")
    mapped = sum(c["mapped"] for c in sidecar["coverage"].values())
    unmapped = sum(c["unmapped"] for c in sidecar["coverage"].values())
    total = mapped + unmapped
    pct = (mapped / total * 100) if total else 0
    print(f"coverage: {mapped}/{total} ({pct:.1f}%) ; unmapped: {unmapped}")
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    from .upload import push_to_hub

    url_or_desc = push_to_hub(run_date=args.date, dry_run=args.dry_run)
    print(url_or_desc)
    return 0


def main() -> None:
    args = build_parser().parse_args()

    # Avoid unused-import
    _ = timezone

    async_handlers = {"sample", "prepare"}

    if args.command in async_handlers:
        handlers = {
            "sample": _cmd_sample,
            "prepare": _cmd_prepare,
        }
        rc = _sync_wrap(handlers[args.command](args))
    else:
        handlers = {
            "render-task": _cmd_render_task,
            "validate": _cmd_validate,
            "merge": _cmd_merge,
            "canonicalize": _cmd_canonicalize,
            "upload": _cmd_upload,
        }
        rc = handlers[args.command](args)

    sys.exit(rc)


if __name__ == "__main__":
    main()
