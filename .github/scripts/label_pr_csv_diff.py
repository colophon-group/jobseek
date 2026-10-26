#!/usr/bin/env python3
"""Return semantic CSV additions from a unified company-PR diff.

CSV sort/rebase churn can show an unchanged row once as removed and once as
added. Those rows are moves, not PR-authored configuration, so the trusted
classifier must cancel them before validating monitor/scraper types.
"""

from __future__ import annotations

import csv
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


def net_csv_changes(diff: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return semantic additions and removals with their source paths."""
    current_path: str | None = None
    added: Counter[tuple[str, str]] = Counter()
    removed: Counter[tuple[str, str]] = Counter()
    addition_order: list[tuple[str, str]] = []
    removal_order: list[tuple[str, str]] = []

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
            removal_order.append(key)

    remaining = added.copy()
    for key, count in removed.items():
        remaining[key] = max(0, remaining[key] - count)

    rows: list[tuple[str, str]] = []
    for key in addition_order:
        if remaining[key] <= 0:
            continue
        rows.append(key)
        remaining[key] -= 1
    deletions = removed - added
    removed_rows = []
    for key in removal_order:
        if deletions[key] > 0:
            removed_rows.append(key)
            deletions[key] -= 1
    return rows, removed_rows


def net_added_csv_rows(diff: str) -> list[str]:
    """Return added rows after identical removals in the same file cancel."""
    return [row for _, row in net_csv_changes(diff)[0]]


def check_reconfiguration(diff: str) -> None:
    """Keep config reconfiguration authority scoped to one company identity."""
    additions, removals = net_csv_changes(diff)
    slugs = set()
    company_additions = set()
    company_removals = set()
    widths = {"boards.csv": 7, "companies.csv": 10, "company_descriptions.csv": 5}
    for changed_rows, company_slugs in (
        (additions, company_additions),
        (removals, company_removals),
    ):
        for path, text in changed_rows:
            row = next(csv.reader([text], strict=True))
            if len(row) != widths[path.rsplit("/", 1)[-1]]:
                raise ValueError(f"{path}: changed CSV schema or malformed row")
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row[0]):
                raise ValueError(f"{path}: invalid company identity")
            slugs.add(row[0])
            if path.endswith("/companies.csv"):
                company_slugs.add(row[0])
    if len(slugs) != 1:
        raise ValueError("Reconfiguration must change exactly one company")
    if company_removals - company_additions:
        raise ValueError("Reconfiguration cannot delete or rename a company identity")


def main() -> None:
    diff = sys.stdin.read()
    if sys.argv[1:] == ["--company-slugs"]:
        additions, removals = net_csv_changes(diff)
        slugs = {next(csv.reader([text]))[0] for _, text in additions + removals}
        if slugs:
            print("\n".join(sorted(slugs)))
        return
    if sys.argv[1:] == ["--check-reconfiguration"]:
        try:
            check_reconfiguration(diff)
        except (ValueError, csv.Error) as exc:
            raise SystemExit(str(exc)) from exc
        return
    rows = net_added_csv_rows(diff)
    if rows:
        sys.stdout.write("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
