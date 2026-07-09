#!/usr/bin/env python3
"""Run one Hetzner-local Codex company resolver governor pass."""

from __future__ import annotations

import sys
from pathlib import Path


def _add_crawler_src_to_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    src = repo / "apps" / "crawler"
    sys.path.insert(0, str(src))


def main() -> int:
    _add_crawler_src_to_path()
    from src.workspace.codex_runner import main as runner_main

    return runner_main()


if __name__ == "__main__":
    raise SystemExit(main())
