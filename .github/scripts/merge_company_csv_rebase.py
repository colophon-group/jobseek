#!/usr/bin/env python3
"""Apply one rebased commit's semantic CSV changes to the current upstream file."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

Row = dict[str, str]
Key = tuple[str, ...]


def _read_csv(text: str, *, source: str) -> tuple[list[str], list[Row]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError(f"{source}: missing CSV header")
    rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError(f"{source}: row has more values than the header")
    return list(reader.fieldnames), rows


def _key_and_sort(
    path: str, headers: list[str]
) -> tuple[Callable[[Row], Key], Callable[[Row], tuple[object, ...]]]:
    name = Path(path).name
    if name == "boards.csv":
        required = {"company_slug", "board_slug"}
        if not required.issubset(headers):
            raise ValueError(f"{path}: missing key columns {sorted(required - set(headers))}")

        def board_key(row: Row) -> Key:
            return (row["company_slug"], row["board_slug"])

        return board_key, board_key
    if "slug" in headers:

        def slug_key(row: Row) -> Key:
            return (row["slug"],)

        return slug_key, slug_key
    if "id" in headers:

        def id_key(row: Row) -> Key:
            return (row["id"],)

        def sort_key(row: Row) -> tuple[object, ...]:
            value = row["id"]
            return (0, int(value)) if value.isdecimal() else (1, value)

        return id_key, sort_key
    raise ValueError(f"{path}: no supported stable row key")


def _index(rows: list[Row], key: Callable[[Row], Key], *, source: str) -> dict[Key, Row]:
    indexed: dict[Key, Row] = {}
    for row in rows:
        row_key = key(row)
        if not all(row_key):
            raise ValueError(f"{source}: empty row key")
        if row_key in indexed:
            raise ValueError(f"{source}: duplicate row key {row_key!r}")
        indexed[row_key] = row
    return indexed


def merge_csv_text(current_text: str, parent_text: str, feature_text: str, path: str) -> str:
    """Three-way merge row changes from a feature commit and restore canonical order."""
    current_headers, current_rows = _read_csv(current_text, source="current upstream")
    parent_headers, parent_rows = _read_csv(parent_text, source="feature parent")
    feature_headers, feature_rows = _read_csv(feature_text, source="feature commit")
    if current_headers != parent_headers or current_headers != feature_headers:
        raise ValueError(f"{path}: CSV headers changed; refusing automatic resolution")

    key, sort_key = _key_and_sort(path, current_headers)
    current = _index(current_rows, key, source="current upstream")
    parent = _index(parent_rows, key, source="feature parent")
    feature = _index(feature_rows, key, source="feature commit")

    deleted = parent.keys() - feature.keys()
    changed = {row_key for row_key, row in feature.items() if parent.get(row_key) != row}

    for row_key in deleted:
        if row_key not in current:
            continue
        if current[row_key] != parent[row_key]:
            raise ValueError(f"{path}: upstream also changed deleted row {row_key!r}")
        del current[row_key]

    for row_key in changed:
        feature_row = feature[row_key]
        if row_key in parent:
            if row_key not in current:
                raise ValueError(f"{path}: upstream deleted changed row {row_key!r}")
            if current[row_key] not in (parent[row_key], feature_row):
                raise ValueError(f"{path}: both sides changed row {row_key!r}")
        elif row_key in current and current[row_key] != feature_row:
            raise ValueError(f"{path}: both sides added different row {row_key!r}")
        current[row_key] = feature_row

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=current_headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(current.values(), key=sort_key))
    return output.getvalue()


def _git_show(revision: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{revision}:{path}"],
        text=True,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"usage: {Path(sys.argv[0]).name} PATH [REBASE_HEAD]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    head = sys.argv[2] if len(sys.argv) == 3 else "REBASE_HEAD"
    try:
        merged = merge_csv_text(
            Path(path).read_text(),
            _git_show(f"{head}^", path),
            _git_show(head, path),
            path,
        )
        Path(path).write_text(merged)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Cannot safely auto-resolve {path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
