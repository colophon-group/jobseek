#!/usr/bin/env python3
"""Probe boards.csv rows that were added or changed vs a base git ref.

Used by CI to catch stale ATS slugs on PR: a new/changed board whose ATS API
returns 404 is likely a typo or a slug the ATS no longer recognises, and will
produce silent 12/12h error noise if merged.

Exit codes:
  0 — no probe returned ``fail`` (404). Warnings and skips are non-blocking.
  1 — at least one probe returned 404.
  2 — I/O or git error (e.g. base ref unavailable).

Run from the repo root. Requires uv-managed deps from ``apps/crawler``::

    cd apps/crawler && uv run python ../../scripts/probe-new-boards.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CRAWLER_SRC = REPO_ROOT / "apps" / "crawler"
DEFAULT_CSV = CRAWLER_SRC / "data" / "boards.csv"

# Make `src.probe_boards` importable when running from anywhere.
sys.path.insert(0, str(CRAWLER_SRC))

from src.probe_boards import probe_rows, rows_added_or_changed  # noqa: E402


def _git_show(ref: str, path: Path) -> str:
    """Return file contents at given ref, or empty string if the path did not
    exist at that ref (e.g. newly added file)."""
    rel = path.relative_to(REPO_ROOT)
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{rel}"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return ""


def _parse_csv(text: str) -> list[dict]:
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _format_table(results: list) -> str:
    lines = [f"{'STATUS':<8} {'MONITOR':<16} {'BOARD_SLUG':<45} MESSAGE"]
    for r in results:
        lines.append(
            f"{r.status:<8} {r.monitor_type:<16} {r.board_slug:<45} {r.message}"
        )
    return "\n".join(lines)


async def _run(base_ref: str, csv_path: Path, concurrency: int) -> int:
    try:
        base_text = _git_show(base_ref, csv_path)
    except FileNotFoundError:
        print(f"error: git binary not found", file=sys.stderr)
        return 2

    if not csv_path.exists():
        print(f"error: {csv_path} not found", file=sys.stderr)
        return 2

    head_text = csv_path.read_text()
    base_rows = _parse_csv(base_text)
    head_rows = _parse_csv(head_text)

    diff_rows = rows_added_or_changed(base_rows, head_rows)
    if not diff_rows:
        print(f"No added or changed boards vs {base_ref} — nothing to probe.")
        return 0

    print(
        f"Probing {len(diff_rows)} added/changed board(s) vs {base_ref} "
        f"with concurrency={concurrency}..."
    )
    results = await probe_rows(diff_rows, concurrency=concurrency)

    # Sort by status so failures appear at the top.
    order = {"fail": 0, "warn": 1, "skipped": 2, "ok": 3}
    results.sort(key=lambda r: (order.get(r.status, 9), r.board_slug))

    print(_format_table(results))

    fails = [r for r in results if r.status == "fail"]
    warns = [r for r in results if r.status == "warn"]
    oks = [r for r in results if r.status == "ok"]
    skips = [r for r in results if r.status == "skipped"]
    print(
        f"\nSummary: {len(fails)} fail, {len(warns)} warn, "
        f"{len(oks)} ok, {len(skips)} skipped"
    )

    if fails:
        print(
            "\nFAIL: at least one ATS API returned 404. Fix the slug in "
            "monitor_config (or remove the row if the company has shut down) "
            "before merging.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-ref",
        default="origin/main",
        help="Git ref to diff against (default: origin/main)",
    )
    p.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"Path to boards.csv (default: {DEFAULT_CSV})",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max parallel probes (default: 5)",
    )
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args.base_ref, args.csv, args.concurrency)))


if __name__ == "__main__":
    main()
