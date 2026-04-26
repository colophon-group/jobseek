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
from datetime import UTC, datetime
from pathlib import Path

import dotenv

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv(".env")


def _parse_iso_date(value: str) -> datetime:
    if value == "today":
        return datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
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

    # --- prepare-pre-llm ------------------------------------------------
    # Stage A: DB → raw_input.json (+ render normalize prompt in the caller).
    pre = sub.add_parser(
        "prepare-pre-llm",
        help=(
            "Load raw HTML for one posting and write raw_input.json + raw.html"
            " (the input to the LLM normalizer)."
        ),
    )
    pre.add_argument("posting_id")
    pre.add_argument("--date", default="today")
    pre.add_argument(
        "--out-dir",
        type=Path,
        help="Override run dir (defaults to _runs/<date>/<id>/)",
    )

    # --- prepare-post-llm -----------------------------------------------
    # Stage B: read the LLM-normalized HTML + raw_input.json → input.json.
    post = sub.add_parser(
        "prepare-post-llm",
        help=(
            "Combine raw_input.json (from prepare-pre-llm) with the LLM-"
            "normalized HTML into the final input.json consumed by the"
            " splitter + per-section extractors."
        ),
    )
    post.add_argument("posting_id")
    post.add_argument("--date", default="today")
    post.add_argument(
        "--out-dir",
        type=Path,
        help="Override run dir (defaults to _runs/<date>/<id>/)",
    )

    # --- prepare (legacy, deterministic-only — kept for debug use) ------
    pp = sub.add_parser(
        "prepare",
        help=(
            "DEPRECATED deterministic-only prep (load + normalize + blocks in"
            " one shot). Prefer prepare-pre-llm + LLM normalizer + prepare-"
            "post-llm. Kept for quick local debugging of a single posting."
        ),
    )
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
    r.add_argument(
        "--extracts-dir",
        type=Path,
        help=(
            "Directory to scan for extract-<kind>-out.json files (for --task extract_globals)."
            " Defaults to the input.json's parent."
        ),
    )
    r.add_argument("--kind", help="Section kind, required for extract_<kind> tasks")
    r.add_argument("--previous-error", default=None)

    # --- validate ------------------------------------------------------
    v = sub.add_parser("validate", help="Validate a subagent output file")
    v.add_argument(
        "--kind",
        required=True,
        help="sections|team|role|requirements|preferred|benefits|globals|posting|qa",
    )
    v.add_argument("--file", type=Path, required=True)
    v.add_argument("--context", type=Path, help="input.json (required for sections)")
    v.add_argument(
        "--report",
        type=Path,
        help="For --kind qa: write the full QA rule report as JSON to this path",
    )

    # --- merge ---------------------------------------------------------
    m = sub.add_parser("merge", help="Assemble the final posting.json from task outputs")
    m.add_argument("--posting", required=True)
    m.add_argument("--date", required=True)
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--verdict", default="accepted", choices=["accepted", "rejected"])
    m.add_argument("--rationale", default=None)

    # --- upload --------------------------------------------------------
    u = sub.add_parser("upload", help="Push accepted postings + schemas to HuggingFace")
    u.add_argument("--date", default=None, help="Limit to single date; default: all")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge a re-stage of every local date when --date is not set",
    )

    # --- scrub ---------------------------------------------------------
    sc = sub.add_parser(
        "scrub",
        help=(
            "Retroactively remove rows from the published HuggingFace dataset"
            " (companion to labeller_optout.txt — see --slug)."
        ),
    )
    sc.add_argument(
        "--slug",
        default=None,
        help=(
            "Drop rows whose source.company_slug matches (case-insensitive)."
            " At least one of --slug / --posting-id is required."
        ),
    )
    sc.add_argument(
        "--posting-id",
        default=None,
        help="Drop the row with this top-level id (combine with --slug for AND-semantics).",
    )
    sc.add_argument(
        "--date",
        action="append",
        default=None,
        help=(
            "Limit scrub to this date file (data/<date>.jsonl). May be passed"
            " multiple times. Default: every dated file on the dataset."
        ),
    )
    sc.add_argument("--dry-run", action="store_true")

    return p


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


def _run_date_from(value: str) -> str:
    if value == "today":
        return datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return _parse_iso_date(value).strftime("%Y-%m-%d")


async def _cmd_prepare_pre_llm(args: argparse.Namespace) -> int:
    from src.db import close_all_pools, create_local_pool

    from .paths import runs_dir
    from .prepare import RAW_INPUT_FILE, build_raw_input, load_posting, write_json

    run_date = _run_date_from(args.date)
    base = args.out_dir or runs_dir(run_date, args.posting_id)
    base.mkdir(parents=True, exist_ok=True)

    pool = await create_local_pool()
    try:
        raw = await load_posting(pool, args.posting_id)
    finally:
        await close_all_pools()

    if raw is None:
        print(f"posting {args.posting_id} not found or has no description", file=sys.stderr)
        return 2
    payload = build_raw_input(raw, sampled_at=datetime.now(tz=UTC))
    raw_input_path = base / RAW_INPUT_FILE
    write_json(raw_input_path, payload)
    print(f"prepared-pre-llm -> {raw_input_path}")
    return 0


def _cmd_prepare_post_llm(args: argparse.Namespace) -> int:
    from .paths import runs_dir
    from .prepare import NORMALIZED_FILE, RAW_INPUT_FILE, finalize_input, write_json

    run_date = _run_date_from(args.date)
    base = args.out_dir or runs_dir(run_date, args.posting_id)
    raw_input_path = base / RAW_INPUT_FILE
    normalized_path = base / NORMALIZED_FILE
    input_path = base / "input.json"

    if not raw_input_path.exists():
        print(f"missing {raw_input_path} — run prepare-pre-llm first", file=sys.stderr)
        return 2
    if not normalized_path.exists():
        print(
            f"missing {normalized_path} — the LLM normalize step must produce it",
            file=sys.stderr,
        )
        return 2

    raw_input = json.loads(raw_input_path.read_text())
    normalized_html = normalized_path.read_text()
    try:
        payload = finalize_input(raw_input, normalized_html)
    except ValueError as e:
        print(f"finalize failed: {e}", file=sys.stderr)
        return 3
    write_json(input_path, payload)
    print(f"prepared-post-llm -> {input_path} (blocks={len(payload['input']['blocks'])})")
    return 0


async def _cmd_prepare(args: argparse.Namespace) -> int:
    """Deterministic-only prep. Kept for quick debugging.

    Shares the DB-load + build_raw_input with prepare-pre-llm, then runs
    finalize_input with the raw HTML as if it were already normalized. In
    practice this is equivalent to the old single-shot prepare; use the two-
    stage flow for production runs.
    """
    from src.db import close_all_pools, create_local_pool

    from .paths import runs_dir
    from .prepare import build_raw_input, finalize_input, load_posting, write_json

    run_date = _run_date_from(args.date)
    out = args.out or (runs_dir(run_date, args.posting_id) / "input.json")

    pool = await create_local_pool()
    try:
        raw = await load_posting(pool, args.posting_id)
    finally:
        await close_all_pools()

    if raw is None:
        print(f"posting {args.posting_id} not found or has no description", file=sys.stderr)
        return 2
    raw_input = build_raw_input(raw, sampled_at=datetime.now(tz=UTC))
    try:
        payload = finalize_input(raw_input, raw.description_html_raw)
    except ValueError as e:
        print(f"finalize failed: {e}", file=sys.stderr)
        return 3
    write_json(out, payload)
    print(f"prepared -> {out}")
    return 0


def _cmd_render_task(args: argparse.Namespace) -> int:
    from .render import render_to_file

    output_hint = args.output_path
    if output_hint is None:
        output_hint = str(args.out.parent / f"{args.task.replace('_', '-')}-out.json")

    render_to_file(
        args.task,
        args.input,
        args.out,
        sections_path=args.sections,
        extracts_dir=args.extracts_dir,
        kind=args.kind,
        output_path_hint=output_hint,
        previous_error=args.previous_error,
    )
    print(f"rendered {args.task} -> {args.out}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validate import qa_report, validate_file

    if args.kind == "qa" and args.report:
        # Also write the full QA report to the requested path (used by orchestrator).
        try:
            data = json.loads(args.file.read_text())
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"could not load posting: {e}", file=sys.stderr)
            return 2
        report = qa_report(data)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))

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
    write_merged(args.out, merged)
    print(f"merged -> {args.out}")
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    from .upload import UploadGuardError, push_to_hub

    try:
        out = push_to_hub(
            run_date=args.date,
            dry_run=args.dry_run,
            confirm=args.confirm,
        )
    except UploadGuardError as e:
        print(f"upload refused: {e}", file=sys.stderr)
        return 2
    print(out)
    return 0


def _cmd_scrub(args: argparse.Namespace) -> int:
    from .scrub import ScrubFilter, ScrubGuardError, scrub

    dates = frozenset(args.date) if args.date else frozenset()
    predicate = ScrubFilter(slug=args.slug, posting_id=args.posting_id, dates=dates)
    try:
        result = scrub(predicate, dry_run=args.dry_run)
    except ScrubGuardError as e:
        print(f"scrub refused: {e}", file=sys.stderr)
        return 2
    print(result.render())
    return 0


# Per-subcommand list of args holding paths that must be inside
# LABELLER_DATA_ROOT. Validated up-front in main() before dispatch.
# Add new subcommand path-args here when extending the CLI; the test
# suite asserts every path-typed arg in build_parser() is covered.
_SANDBOXED_PATH_ARGS: dict[str, tuple[str, ...]] = {
    "sample": ("out",),
    "prepare-pre-llm": ("out_dir",),
    "prepare-post-llm": ("out_dir",),
    "prepare": ("out",),
    "render-task": ("input", "out", "sections", "extracts_dir"),
    "validate": ("file", "context", "report"),
    "merge": ("out",),
    # `upload` operates only on data_root() / its own staging dir; no
    # operator-supplied paths.
    "upload": (),
    # `scrub` only acts against the remote HF dataset + an OS tempdir.
    "scrub": (),
}


def _sandbox_paths(args: argparse.Namespace) -> int:
    """Reject any --out / --file / --report path that escapes LABELLER_DATA_ROOT.

    Returns 0 on success, 4 if any path escapes (logged to stderr).
    """
    from .paths import PathSandboxError, assert_under_data_root

    for attr in _SANDBOXED_PATH_ARGS.get(args.command, ()):
        value = getattr(args, attr, None)
        if value is None:
            continue
        try:
            assert_under_data_root(value)
        except PathSandboxError as e:
            print(f"path sandbox: refusing --{attr.replace('_', '-')}: {e}", file=sys.stderr)
            return 4
    return 0


def main() -> None:
    args = build_parser().parse_args()

    rc = _sandbox_paths(args)
    if rc != 0:
        sys.exit(rc)

    async_handlers = {"sample", "prepare", "prepare-pre-llm"}

    if args.command in async_handlers:
        handlers = {
            "sample": _cmd_sample,
            "prepare": _cmd_prepare,
            "prepare-pre-llm": _cmd_prepare_pre_llm,
        }
        rc = asyncio.run(handlers[args.command](args))
    else:
        handlers = {
            "render-task": _cmd_render_task,
            "validate": _cmd_validate,
            "merge": _cmd_merge,
            "upload": _cmd_upload,
            "scrub": _cmd_scrub,
            "prepare-post-llm": _cmd_prepare_post_llm,
        }
        rc = handlers[args.command](args)

    sys.exit(rc)


if __name__ == "__main__":
    main()
