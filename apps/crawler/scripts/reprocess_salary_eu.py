#!/usr/bin/env python
"""Compatibility wrapper for ``crawler reprocess-salary-eu``."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _main() -> int:
    from src.salary_reprocess import main

    return asyncio.run(main())


if __name__ == "__main__":
    raise SystemExit(_main())
