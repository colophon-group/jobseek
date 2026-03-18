#!/usr/bin/env python3
"""Sort CSV data files by their first column (slug), preserving headers.

Usage:
    python scripts/sort_csvs.py          # sort in-place
    python scripts/sort_csvs.py --check  # exit 1 if any file is unsorted
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CSV_FILES = [
    "companies.csv",
    "boards.csv",
    "company_descriptions.csv",
]


def _read_csv(path: Path) -> tuple[str, list[list[str]]]:
    """Return (header_line, rows) where rows are raw field lists."""
    text = path.read_text(encoding="utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return "", []
    header = rows[0]
    data = rows[1:]
    return header, data


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def main() -> int:
    check_only = "--check" in sys.argv
    unsorted: list[str] = []

    for name in CSV_FILES:
        path = DATA_DIR / name
        if not path.exists():
            continue

        header, rows = _read_csv(path)
        sorted_rows = sorted(rows, key=lambda r: r[0] if r else "")

        if rows != sorted_rows:
            unsorted.append(name)
            if check_only:
                # Find the first out-of-order slug
                for i in range(1, len(rows)):
                    if rows[i][0] < rows[i - 1][0]:
                        print(f"  {name}: '{rows[i][0]}' < '{rows[i - 1][0]}' at row {i + 2}")
                        break
            else:
                _write_csv(path, header, sorted_rows)
                print(f"  sorted {name} ({len(rows)} rows)")

    if check_only:
        if unsorted:
            print(f"FAIL: {len(unsorted)} file(s) not slug-sorted: {', '.join(unsorted)}")
            return 1
        print("OK: all CSV files are slug-sorted")
        return 0

    if not unsorted:
        print("All CSV files were already sorted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
