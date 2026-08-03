#!/usr/bin/env python3
"""Reconcile pgBackRest retention without exposing adjacent host secrets."""

from __future__ import annotations

import argparse
import os
import re
from contextlib import suppress
from pathlib import Path

RETENTION = {
    "repo1-retention-full": "4",
    "repo1-retention-diff": "7",
    "repo1-retention-archive": "2",
    "repo1-retention-archive-type": "diff",
}
_SECTION = re.compile(r"^\s*\[([^]]+)]\s*(?:[#;].*)?$")
_OPTION = re.compile(r"^\s*([A-Za-z0-9-]+)\s*=")


class ConfigurationError(RuntimeError):
    """The existing pgBackRest configuration is ambiguous or unsafe to edit."""


def reconcile(text: str) -> str:
    lines = text.splitlines()
    sections = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (match := _SECTION.match(line))
    ]
    global_sections = [index for index, name in sections if name == "global"]
    if len(global_sections) != 1:
        raise ConfigurationError("pgBackRest config must contain exactly one [global] section")

    start = global_sections[0] + 1
    end = next((index for index, _name in sections if index >= start), len(lines))
    found: dict[str, list[int]] = {key: [] for key in RETENTION}
    for index in range(start, end):
        match = _OPTION.match(lines[index])
        if match and match.group(1) in found:
            found[match.group(1)].append(index)
    duplicates = [key for key, indexes in found.items() if len(indexes) > 1]
    if duplicates:
        raise ConfigurationError(
            "duplicate pgBackRest retention options: " + ", ".join(sorted(duplicates))
        )

    for key, value in RETENTION.items():
        indexes = found[key]
        if indexes:
            lines[indexes[0]] = f"{key}={value}"
        else:
            lines.insert(end, f"{key}={value}")
            end += 1
    return "\n".join(lines) + "\n"


def update(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = reconcile(original)
    if updated == original:
        return False
    metadata = path.stat()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, metadata.st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    changed = update(args.path)
    print(f"pgBackRest retention contract {'updated' if changed else 'already exact'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
