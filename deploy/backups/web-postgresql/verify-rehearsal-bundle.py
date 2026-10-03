#!/usr/bin/env python3
"""Validate only reviewed, SHA-bound, root-owned rehearsal artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path


def verify_bundle(root: Path, revision: str, manifest_hash: str, target: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest_hash
    ):
        raise ValueError("invalid rehearsal identity")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe rehearsal root")
    manifest_path = root / "manifest.json"
    metadata = manifest_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or manifest_path.is_symlink():
        raise ValueError("unsafe rehearsal manifest")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_hash:
        raise ValueError("rehearsal manifest digest differs")
    manifest = json.loads(raw)
    if (
        manifest.get("version") != 1
        or manifest.get("sourceClean") is not True
        or manifest.get("sourceRevision") != revision
    ):
        raise ValueError("rehearsal source identity differs")
    if not re.fullmatch(r"node:24-alpine@sha256:[0-9a-f]{64}", manifest.get("runtimeImage", "")):
        raise ValueError("rehearsal runtime must be immutable")
    migrations = manifest.get("migrations")
    if not isinstance(migrations, list) or not migrations or len(migrations) > 2:
        raise ValueError("rehearsal target allowlist differs")
    reviewed = {
        "0100_company_references": "0099_product_news_consent",
        "0101_company_reference_selection_contract": "0100_company_references",
    }
    expected_files = {
        "rehearse.mjs",
        "company-reference-dependencies.json",
        "drizzle/meta/_journal.json",
    }
    tags = []
    for migration in migrations:
        if not isinstance(migration, dict):
            raise ValueError("unreviewed rehearsal migration")
        tag = migration.get("tag")
        if (
            tag not in reviewed
            or type(migration.get("createdAt")) is not int
            or not re.fullmatch(r"[0-9a-f]{64}", migration.get("hash", ""))
        ):
            raise ValueError("unreviewed rehearsal migration")
        tags.append(tag)
        expected_files.update((f"drizzle/{tag}.sql", f"drizzle/{reviewed[tag]}.sql"))
    if len(set(tags)) != len(tags) or target not in tags:
        raise ValueError("rehearsal migration is not allowlisted")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != expected_files:
        raise ValueError("rehearsal resource boundary differs")
    actual_files = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual_files != expected_files | {"manifest.json"}:
        raise ValueError("unexpected rehearsal resources")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink rehearsal resource")
    for name, digest in files.items():
        path = root / name
        if (
            not re.fullmatch(r"[a-z0-9_./-]+", name)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
        ):
            raise ValueError("unsafe rehearsal path")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("rehearsal resource digest differs")
    selected = next(row for row in migrations if row["tag"] == target)
    if (
        hashlib.sha256((root / f"drizzle/{target}.sql").read_bytes()).hexdigest()
        != selected["hash"]
    ):
        raise ValueError("rehearsal target SQL differs")
    return {
        "sourceRevision": revision,
        "runtimeImage": manifest["runtimeImage"],
        "manifestSha256": manifest_hash,
        "target": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("revision")
    parser.add_argument("manifest_hash")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                verify_bundle(args.root, args.revision, args.manifest_hash, args.target),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    except (OSError, ValueError, TypeError, KeyError):
        raise SystemExit("Rehearsal artifact verification failed") from None


if __name__ == "__main__":
    main()
