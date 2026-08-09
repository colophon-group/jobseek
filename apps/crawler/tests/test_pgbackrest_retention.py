from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/backups/postgresql/configure-retention.py"
SPEC = importlib.util.spec_from_file_location("configure_pgbackrest_retention", SCRIPT)
assert SPEC and SPEC.loader
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


def test_reconcile_preserves_secret_and_sets_bounded_archive_retention() -> None:
    original = """[jobseek]
pg1-path=/data

[global]
repo1-cipher-pass=do-not-print-or-replace
repo1-retention-full=99

[global:archive-push]
process-max=2
"""

    updated = retention.reconcile(original)

    assert "repo1-cipher-pass=do-not-print-or-replace" in updated
    for key, value in retention.RETENTION.items():
        assert updated.count(f"{key}={value}") == 1
    assert updated.index("repo1-retention-archive-type=diff") < updated.index(
        "[global:archive-push]"
    )


def test_reconcile_rejects_duplicate_retention_keys() -> None:
    with pytest.raises(retention.ConfigurationError, match="duplicate"):
        retention.reconcile("[global]\nrepo1-retention-full=4\nrepo1-retention-full=5\n")


def test_update_is_atomic_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "pgbackrest.conf"
    path.write_text("[global]\nrepo1-cipher-pass=secret\n", encoding="utf-8")
    path.chmod(0o600)

    assert retention.update(path) is True
    assert retention.update(path) is False
    assert path.stat().st_mode & 0o777 == 0o600
    assert "secret" in path.read_text(encoding="utf-8")
