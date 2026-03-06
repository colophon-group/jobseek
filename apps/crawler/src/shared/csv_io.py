"""Lightweight CSV read/write helpers.

These are extracted from csvtool so that workspace commands can manipulate
CSV files without pulling in polars or structlog at import time.
"""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV file and return (headers, rows_as_dicts)."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    """Write rows back to a CSV file."""
    with open(path, "w", newline="\n") as f:
        writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
