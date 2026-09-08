#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


def canonical(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


def census() -> list[dict[str, str]]:
    return sorted(
        (
            {"name": canonical(str(item.metadata["Name"])), "version": item.version}
            for item in importlib.metadata.distributions()
            if item.metadata["Name"]
        ),
        key=lambda row: (row["name"], row["version"]),
    )


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.corpus.read_text(encoding="utf-8"))["source"]
    executable = Path(sys.executable)
    rows = census()
    versions = {row["name"]: row["version"] for row in rows}
    expected = source["python_runtime_packages"]
    absent = [
        name for name in source["python_absent_runtime_packages"] if canonical(name) not in versions
    ]
    if platform.python_version() != source["python_runtime"]:
        raise RuntimeError("unexpected Python patch version")
    if versions != expected or absent != source["python_absent_runtime_packages"]:
        raise RuntimeError("locked Python distribution census mismatch")
    release = os_release()
    if any(release.get(key) != value for key, value in source["python_os_release"].items()):
        raise RuntimeError("Python base OS release mismatch")
    record = {
        "sys_executable": sys.executable,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "runtime": platform.python_version(),
        "lock_sha256": hashlib.sha256(args.lock.read_bytes()).hexdigest(),
        "runtime_package_versions": expected,
        "absent_runtime_packages": absent,
        "installed_distributions": rows,
        "os_release": release,
    }
    args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
