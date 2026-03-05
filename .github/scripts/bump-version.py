#!/usr/bin/env python3
"""Pre-commit hook: auto-bump patch version when crawler files change.

Checks git staged files for changes under apps/crawler/ (excluding the
VERSION file itself). If any are found, reads apps/crawler/VERSION, bumps
the patch component, writes it back, and stages the updated file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

VERSION_PATH = Path("apps/crawler/VERSION")


def get_staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().splitlines()


def main() -> int:
    staged = get_staged_files()

    # Check if any staged files are under apps/crawler/ (excluding VERSION itself)
    crawler_changes = [
        f for f in staged
        if f.startswith("apps/crawler/") and f != str(VERSION_PATH)
    ]

    if not crawler_changes:
        return 0

    if not VERSION_PATH.exists():
        print(f"WARNING: {VERSION_PATH} not found, skipping version bump")
        return 0

    version = VERSION_PATH.read_text().strip()
    parts = version.split(".")
    if len(parts) != 3:
        print(f"WARNING: unexpected version format {version!r}, skipping bump")
        return 0

    major, minor, patch = parts
    new_version = f"{major}.{minor}.{int(patch) + 1}"
    VERSION_PATH.write_text(new_version + "\n")

    subprocess.run(["git", "add", str(VERSION_PATH)], check=True)
    print(f"Bumped version: {version} -> {new_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
