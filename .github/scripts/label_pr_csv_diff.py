#!/usr/bin/env python3
"""Return semantic CSV additions from a unified company-PR diff.

CSV sort/rebase churn can show an unchanged row once as removed and once as
added. Those rows are moves, not PR-authored configuration, so the trusted
classifier must cancel them before validating monitor/scraper types.
"""

from __future__ import annotations

import re
import sys
from collections import Counter

CSV_PATHS = frozenset(
    {
        "apps/crawler/data/boards.csv",
        "apps/crawler/data/companies.csv",
        "apps/crawler/data/company_descriptions.csv",
    }
)
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


def net_added_csv_rows(diff: str) -> list[str]:
    """Return added rows after identical removals in the same file cancel."""
    current_path: str | None = None
    added: Counter[tuple[str, str]] = Counter()
    removed: Counter[tuple[str, str]] = Counter()
    addition_order: list[tuple[str, str]] = []

    for line in diff.splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            before, after = header.groups()
            current_path = after if before == after and after in CSV_PATHS else None
            continue
        if current_path is None or len(line) < 2:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        key = (current_path, line[1:])
        if line.startswith("+"):
            added[key] += 1
            addition_order.append(key)
        elif line.startswith("-"):
            removed[key] += 1

    remaining = added.copy()
    for key, count in removed.items():
        remaining[key] = max(0, remaining[key] - count)

    rows: list[str] = []
    for key in addition_order:
        if remaining[key] <= 0:
            continue
        rows.append(key[1])
        remaining[key] -= 1
    return rows


def main() -> None:
    rows = net_added_csv_rows(sys.stdin.read())
    if rows:
        sys.stdout.write("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
