#!/usr/bin/env python3
"""Report or apply Codex runner worktree reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _add_crawler_src_to_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "apps" / "crawler"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="archive eligible dirty state and remove verified terminal worktrees",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit the per-directory plan from JSON output",
    )
    args = parser.parse_args()

    _add_crawler_src_to_path()
    from src.workspace.codex_runner import CompanyResolverGovernor, RunnerConfig

    governor = CompanyResolverGovernor(RunnerConfig.from_env())
    report = governor.reconcile_worktrees(apply=args.apply)
    print(json.dumps(report.to_dict(include_items=not args.summary_only), sort_keys=True))
    return 0 if report.within_bounds else 2


if __name__ == "__main__":
    raise SystemExit(main())
