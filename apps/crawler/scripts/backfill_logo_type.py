#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ALLOWED_LOGO_TYPES = {"wordmark", "wordmark+icon", "icon"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back-fill empty logo_type values in companies.csv from a mapping CSV."
    )
    parser.add_argument(
        "--companies",
        type=Path,
        default=Path("apps/crawler/data/companies.csv"),
        help="Path to companies.csv",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=Path("apps/crawler/data/logo_type_backfill.csv"),
        help="Path to logo_type mapping CSV",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write updates to companies.csv. Without this flag, runs as dry-run.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Also replace non-empty companies.csv logo_type values with mapping values.",
    )
    return parser.parse_args()


def load_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Mapping file not found: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    required = {"slug", "logo_type"}
    got = set(rows[0].keys()) if rows else set()
    missing = required - got
    if missing:
        raise ValueError(f"Mapping CSV missing required columns: {sorted(missing)}")

    mapping: dict[str, str] = {}
    for row in rows:
        slug = (row.get("slug") or "").strip()
        logo_type = (row.get("logo_type") or "").strip()
        if not slug:
            raise ValueError("Mapping CSV contains an empty slug")
        if logo_type not in ALLOWED_LOGO_TYPES:
            raise ValueError(
                f"Invalid logo_type {logo_type!r} for slug {slug!r}. "
                f"Allowed: {sorted(ALLOWED_LOGO_TYPES)}"
            )
        if slug in mapping and mapping[slug] != logo_type:
            raise ValueError(
                f"Conflicting logo_type values for slug {slug!r}: "
                f"{mapping[slug]!r} vs {logo_type!r}"
            )
        mapping[slug] = logo_type
    return mapping


def load_companies(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"companies.csv not found: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "slug" not in fieldnames or "logo_type" not in fieldnames:
        raise ValueError("companies.csv must include 'slug' and 'logo_type' columns")

    return fieldnames, rows


def write_companies(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def main() -> int:
    args = parse_args()
    mapping = load_mapping(args.mapping)
    fieldnames, rows = load_companies(args.companies)

    missing_mapping: list[str] = []
    unknown_in_mapping = sorted(set(mapping) - {row["slug"] for row in rows})
    updated: list[tuple[str, str, str]] = []
    skipped_existing: list[tuple[str, str, str]] = []

    for row in rows:
        slug = (row.get("slug") or "").strip()
        current = (row.get("logo_type") or "").strip()
        target = mapping.get(slug)

        if not target:
            if not current:
                missing_mapping.append(slug)
            continue

        if current and current != target and not args.overwrite_existing:
            skipped_existing.append((slug, current, target))
            continue

        if (not current) or (args.overwrite_existing and current != target):
            row["logo_type"] = target
            updated.append((slug, current, target))

    print(f"companies rows: {len(rows)}")
    print(f"mapping rows: {len(mapping)}")
    print(f"updates planned: {len(updated)}")

    if missing_mapping:
        print(f"missing mapping for empty logo_type rows: {len(missing_mapping)}")
        for slug in sorted(missing_mapping):
            print(f"  - {slug}")

    if unknown_in_mapping:
        print(f"mapping slugs not found in companies.csv: {len(unknown_in_mapping)}")
        for slug in unknown_in_mapping:
            print(f"  - {slug}")

    if skipped_existing:
        print(f"skipped due to existing non-empty logo_type: {len(skipped_existing)}")
        for slug, current, target in skipped_existing:
            print(f"  - {slug}: current={current!r}, target={target!r}")

    if updated:
        print("changes:")
        for slug, current, target in updated:
            print(f"  - {slug}: {current!r} -> {target!r}")

    if args.write:
        write_companies(args.companies, fieldnames, rows)
        print(f"wrote: {args.companies}")
    else:
        print("dry-run only. Re-run with --write to apply updates.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI safety
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
