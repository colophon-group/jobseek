"""Tests for the persistent Typesense snapshot mount verifier."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify-typesense-snapshot-mount.py"
SPEC = importlib.util.spec_from_file_location("verify_typesense_snapshot_mount", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mounts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mounts
SPEC.loader.exec_module(mounts)


def test_fstab_contract_requires_one_uuid_entry_and_safety_options() -> None:
    target = Path("/mnt/jobseek-typesense-backup")
    entry = mounts._parse_fstab_entry(
        "UUID=12345678-abcd /mnt/jobseek-typesense-backup ext4 defaults,nodev,nosuid,noexec 0 2\n",
        target,
    )

    assert entry.uuid == "12345678-abcd"
    assert entry.filesystem == "ext4"
    assert {"nodev", "nosuid", "noexec"} <= entry.options


@pytest.mark.parametrize(
    "line",
    (
        "/dev/sdb /mnt/jobseek-typesense-backup ext4 defaults,nodev,nosuid,noexec 0 2",
        "UUID=12345678-abcd /mnt/jobseek-typesense-backup ext4 defaults,nodev,nosuid 0 2",
    ),
)
def test_fstab_contract_rejects_nonpersistent_or_unsafe_entries(line: str) -> None:
    with pytest.raises(mounts.VerificationError):
        mounts._parse_fstab_entry(line, Path("/mnt/jobseek-typesense-backup"))


def test_fstab_contract_rejects_duplicate_mount_entries() -> None:
    line = "UUID=12345678-abcd /mnt/jobseek-typesense-backup ext4 defaults,nodev,nosuid,noexec 0 2"
    with pytest.raises(mounts.VerificationError, match="exactly one"):
        mounts._parse_fstab_entry(f"{line}\n{line}\n", Path("/mnt/jobseek-typesense-backup"))


def test_findmnt_exact_mountpoint_uses_attached_option_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    findmnt = bin_dir / "findmnt"
    findmnt.write_text(
        "#!/bin/sh\n"
        'test "$#" -eq 4\n'
        'test "$1" = --mountpoint=/mnt/jobseek-typesense-backup\n'
        'test "$2" = --noheadings\n'
        'test "$3" = --output\n'
        'test "$4" = SOURCE\n'
        "printf '%s\\n' /dev/sdb\n",
        encoding="utf-8",
    )
    findmnt.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    assert mounts._findmnt_exact(Path("/mnt/jobseek-typesense-backup"), "SOURCE") == "/dev/sdb"
