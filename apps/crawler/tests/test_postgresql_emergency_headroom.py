from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/backups/postgresql/emergency-headroom.sh"


def _run(mount: Path, action: str) -> subprocess.CompletedProcess[str]:
    bin_dir = mount / "test-bin"
    bin_dir.mkdir(exist_ok=True)
    fallocate = bin_dir / "fallocate"
    fallocate.write_text(
        '#!/bin/sh\ntest "$1" = --length\ndd if=/dev/zero of="$3" bs="$2" count=1 2>/dev/null\n',
        encoding="utf-8",
    )
    fallocate.chmod(0o755)
    return subprocess.run(
        ["bash", str(SCRIPT), action],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "JOBSEEK_POSTGRES_ALLOW_TEST_FS": "1",
            "JOBSEEK_POSTGRES_DATA_MOUNT": str(mount),
            "JOBSEEK_POSTGRES_RESERVE_BYTES": "1048576",
            "JOBSEEK_POSTGRES_MIN_FREE_AFTER_RESERVE_BYTES": "1",
        },
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="production helper requires GNU stat")
def test_reserve_is_allocated_idempotent_and_releasable(tmp_path: Path) -> None:
    first = _run(tmp_path, "reserve")
    second = _run(tmp_path, "reserve")
    status = _run(tmp_path, "status")
    reserve = tmp_path / ".jobseek-postgresql-emergency-reserve"

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert status.returncode == 0, status.stderr
    assert reserve.stat().st_size == 1_048_576
    assert reserve.stat().st_blocks * 512 >= 1_048_576

    released = _run(tmp_path, "release")
    assert released.returncode == 0, released.stderr
    assert not reserve.exists()


def test_reserve_refuses_a_symlink(tmp_path: Path) -> None:
    reserve = tmp_path / ".jobseek-postgresql-emergency-reserve"
    reserve.symlink_to(tmp_path / "unrelated")

    result = _run(tmp_path, "release")

    assert result.returncode != 0
    assert reserve.is_symlink()
