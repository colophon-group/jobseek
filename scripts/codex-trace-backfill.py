#!/usr/bin/env python3
"""Export quality-gated Codex resolver training bundles."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "apps" / "crawler"))
    from src.workspace.trace_backfill import main as backfill_main

    return backfill_main()


if __name__ == "__main__":
    raise SystemExit(main())
