#!/usr/bin/env python3
"""Fail-closed verifier for the persistent Typesense snapshot filesystem."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
UUID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
REQUIRED_OPTIONS = frozenset({"nodev", "nosuid", "noexec"})


class VerificationError(RuntimeError):
    """The mount is not the exact persistent filesystem contract."""


@dataclass(frozen=True)
class FstabEntry:
    uuid: str
    target: Path
    filesystem: str
    options: frozenset[str]


def _parse_fstab_entry(text: str, target: Path) -> FstabEntry:
    matches: list[FstabEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = shlex.split(line, comments=True)
        if len(fields) < 4 or Path(fields[1]) != target:
            continue
        source, _, filesystem, raw_options = fields[:4]
        if not source.startswith("UUID="):
            raise VerificationError("snapshot fstab source must be UUID=<filesystem-uuid>")
        uuid = source.removeprefix("UUID=")
        if UUID_RE.fullmatch(uuid) is None:
            raise VerificationError("snapshot fstab UUID is malformed")
        options = frozenset(option for option in raw_options.split(",") if option)
        if not options >= REQUIRED_OPTIONS:
            raise VerificationError("snapshot fstab options must include nodev,nosuid,noexec")
        matches.append(FstabEntry(uuid, target, filesystem, options))
    if len(matches) != 1:
        raise VerificationError("snapshot mount must have exactly one /etc/fstab UUID entry")
    return matches[0]


def _run(argv: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"{argv[0]} is unavailable or timed out") from exc
    if result.returncode:
        raise VerificationError(f"{argv[0]} failed while verifying snapshot persistence")
    return result.stdout.strip()


def _findmnt_exact(mount: Path, output: str) -> str:
    result = _run(
        (
            "findmnt",
            f"--mountpoint={mount}",
            "--noheadings",
            "--output",
            output,
        )
    )
    rows = result.splitlines()
    if len(rows) != 1 or not rows[0].strip():
        raise VerificationError("snapshot mount must resolve to exactly one mounted filesystem")
    return rows[0].strip()


def _allocated_bytes(path: Path) -> int:
    output = _run(("du", "--summarize", "--one-file-system", "--block-size=1", str(path))).split()
    try:
        value = int(output[0])
    except (IndexError, ValueError) as exc:
        raise VerificationError("live Typesense allocation is not measurable") from exc
    if value < 0:
        raise VerificationError("live Typesense allocation is invalid")
    return value


def _device_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return os.major(metadata.st_rdev), os.minor(metadata.st_rdev)


def verify(
    mount: Path,
    live_data: Path,
    fstab: Path,
    *,
    minimum_capacity: int,
    minimum_free: int,
    growth_reserve: int,
) -> None:
    try:
        mount_metadata = mount.lstat()
        live_resolved = live_data.resolve(strict=True)
        fstab_text = fstab.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerificationError("snapshot mount, live data, or fstab is unavailable") from exc
    if stat.S_ISLNK(mount_metadata.st_mode) or not mount.is_mount():
        raise VerificationError("snapshot path is not an exact mounted filesystem")
    if (
        mount_metadata.st_uid != 0
        or mount_metadata.st_gid != 0
        or stat.S_IMODE(mount_metadata.st_mode) != 0o700
    ):
        raise VerificationError("snapshot mount must be root-owned mode 0700")
    if mount_metadata.st_dev in {Path("/").stat().st_dev, live_resolved.stat().st_dev}:
        raise VerificationError("snapshot filesystem is not isolated from root and live data")

    entry = _parse_fstab_entry(fstab_text, mount)
    configured_device = Path(_run(("blkid", "-U", entry.uuid))).resolve(strict=True)
    mounted_source_text = _findmnt_exact(mount, "SOURCE")
    mounted_source = Path(mounted_source_text.split("[", 1)[0]).resolve(strict=True)
    if _device_identity(configured_device) != _device_identity(mounted_source):
        raise VerificationError("mounted snapshot device differs from the persisted UUID source")
    current_fields = _findmnt_exact(mount, "FSTYPE,OPTIONS").split(maxsplit=1)
    if len(current_fields) != 2 or current_fields[0] != entry.filesystem:
        raise VerificationError("mounted snapshot filesystem type differs from fstab")
    current_options = frozenset(current_fields[1].split(","))
    if not current_options >= REQUIRED_OPTIONS:
        raise VerificationError("active snapshot mount lacks required safety options")

    usage = shutil.disk_usage(mount)
    live_allocated = _allocated_bytes(live_resolved)
    required_free = minimum_free + growth_reserve + live_allocated
    if usage.total < minimum_capacity:
        raise VerificationError("snapshot filesystem is smaller than 20 GiB")
    if usage.free < required_free:
        raise VerificationError("snapshot filesystem lacks snapshot, growth, and floor headroom")


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount", type=Path, default=Path("/mnt/jobseek-typesense-backup"))
    parser.add_argument("--live-data", type=Path, default=Path("/mnt/typesense-data"))
    parser.add_argument("--fstab", type=Path, default=Path("/etc/fstab"))
    parser.add_argument("--minimum-capacity", type=_positive, default=20 * GIB)
    parser.add_argument("--minimum-free", type=_positive, default=8 * GIB)
    parser.add_argument("--growth-reserve", type=_positive, default=4 * GIB)
    args = parser.parse_args(argv)
    try:
        verify(
            args.mount,
            args.live_data,
            args.fstab,
            minimum_capacity=args.minimum_capacity,
            minimum_free=args.minimum_free,
            growth_reserve=args.growth_reserve,
        )
    except VerificationError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print("Typesense snapshot mount persistence and headroom verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
