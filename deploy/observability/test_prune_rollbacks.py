#!/usr/bin/env python3
"""Behavioral tests for observability rollback retention."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import prune_rollbacks as retention  # pyright: ignore[reportMissingImports]

HERE = Path(__file__).resolve().parent
INSTALLER = HERE / "install-host.sh"
# Match the Ubuntu 22.04 production interpreter supported by the helper.
UTC = timezone.utc  # noqa: UP017


class RollbackRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "rollback"
        self.root.mkdir(mode=0o700)
        self.lock_path = self.base / "deploy.lock"
        self.lock_path.touch(mode=0o600)
        self.lock_fd = os.open(self.lock_path, os.O_RDWR)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.originals = (
            retention.ROLLBACK_ROOT,
            retention.DEPLOY_LOCK,
            retention.EXPECTED_UID,
            retention.EXPECTED_GID,
        )
        retention.ROLLBACK_ROOT = self.root
        retention.DEPLOY_LOCK = self.lock_path
        retention.EXPECTED_UID = os.getuid()
        retention.EXPECTED_GID = os.getgid()
        self.now = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        (
            retention.ROLLBACK_ROOT,
            retention.DEPLOY_LOCK,
            retention.EXPECTED_UID,
            retention.EXPECTED_GID,
        ) = self.originals
        os.close(self.lock_fd)
        self.temporary.cleanup()

    def snapshot(self, name: str, payload: bytes | None = b"alloy") -> Path:
        path = self.root / name
        path.mkdir(mode=0o700)
        if payload is not None:
            (path / "jobseek-alloy").write_bytes(payload)
        return path

    def test_empty_root_and_first_install_are_safe(self) -> None:
        empty = retention.prune_snapshots(
            protect_name=None,
            now=self.now,
            lock_fd=self.lock_fd,
        )
        self.assertEqual(empty.retained, ())
        self.assertEqual(empty.deleted, ())

        first = self.snapshot("20260812T020000Z", payload=None)
        report = retention.prune_snapshots(
            protect_name=first.name,
            now=self.now,
            lock_fd=self.lock_fd,
        )
        self.assertEqual(report.retained, (first.name,))
        self.assertEqual(report.deleted, ())
        self.assertTrue(first.is_dir())

    def test_successful_repeated_deploys_keep_three_newest_byte_valid_snapshots(self) -> None:
        names = [
            "20260808T020000Z",
            "20260809T020000Z",
            "20260810T020000Z",
            "20260811T020000Z",
            "20260812T020000Z",
        ]
        for name in names:
            self.snapshot(name, payload=name.encode())

        first = retention.prune_snapshots(
            protect_name=names[-1],
            now=self.now,
            lock_fd=self.lock_fd,
        )
        self.assertEqual(first.retained, tuple(names[-3:]))
        self.assertEqual((self.root / names[-1] / "jobseek-alloy").read_bytes(), names[-1].encode())

        next_name = "20260813T020000Z"
        self.snapshot(next_name, payload=b"next-known-good")
        second = retention.prune_snapshots(
            protect_name=next_name,
            now=datetime(2026, 8, 13, 2, 0, tzinfo=UTC),
            lock_fd=self.lock_fd,
        )
        self.assertEqual(second.retained, (names[-2], names[-1], next_name))
        self.assertEqual(
            (self.root / next_name / "jobseek-alloy").read_bytes(),
            b"next-known-good",
        )

    def test_age_limit_keeps_only_the_newest_when_other_snapshots_are_stale(self) -> None:
        for name in (
            "20260701T020000Z",
            "20260720T020000Z",
            "20260721T020000Z",
            "20260812T020000Z",
        ):
            self.snapshot(name)

        report = retention.prune_snapshots(
            protect_name="20260812T020000Z",
            now=self.now,
            lock_fd=self.lock_fd,
        )

        self.assertEqual(report.retained, ("20260812T020000Z",))
        self.assertEqual(len(report.deleted), 3)

    def test_concurrent_retention_is_rejected_without_deletion(self) -> None:
        names = [
            "20260809T020000Z",
            "20260810T020000Z",
            "20260811T020000Z",
            "20260812T020000Z",
        ]
        for name in names:
            self.snapshot(name)
        competing_fd = os.open(self.lock_path, os.O_RDWR)
        try:
            with self.assertRaisesRegex(retention.RetentionError, "lock"):
                retention.prune_snapshots(
                    protect_name=names[-1],
                    now=self.now,
                    lock_fd=competing_fd,
                )
        finally:
            os.close(competing_fd)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), names)

    def test_unexpected_entry_rejects_the_whole_root_before_deletion(self) -> None:
        names = [
            "20260808T020000Z",
            "20260809T020000Z",
            "20260810T020000Z",
            "20260811T020000Z",
            "20260812T020000Z",
        ]
        for name in names:
            self.snapshot(name)
        (self.root / "operator-notes").write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(retention.RetentionError, "unexpected rollback entry name"):
            retention.prune_snapshots(
                protect_name=names[-1],
                now=self.now,
                lock_fd=self.lock_fd,
            )

        self.assertEqual(
            sorted(path.name for path in self.root.iterdir()),
            sorted([*names, "operator-notes"]),
        )

    def test_malformed_snapshot_types_and_contents_are_never_deleted(self) -> None:
        cases = ("top-level-symlink", "timestamp-file", "unexpected-child", "child-symlink")
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                case_root = self.base / f"case-{index}"
                case_root.mkdir(mode=0o700)
                retention.ROLLBACK_ROOT = case_root
                good = case_root / "20260812T020000Z"
                good.mkdir(mode=0o700)
                if case == "top-level-symlink":
                    (case_root / "20260811T020000Z").symlink_to(good, target_is_directory=True)
                elif case == "timestamp-file":
                    (case_root / "20260811T020000Z").write_text("not a directory", encoding="utf-8")
                else:
                    malformed = case_root / "20260811T020000Z"
                    malformed.mkdir(mode=0o700)
                    if case == "unexpected-child":
                        (malformed / "unowned-file").write_text("keep", encoding="utf-8")
                    else:
                        (malformed / "jobseek-alloy").symlink_to(good)

                with self.assertRaises(retention.RetentionError):
                    retention.prune_snapshots(
                        protect_name=good.name,
                        now=self.now,
                        lock_fd=self.lock_fd,
                    )
                self.assertTrue(good.is_dir())
                self.assertTrue((case_root / "20260811T020000Z").exists())

    def test_symlinked_root_is_rejected(self) -> None:
        real_root = self.base / "real-root"
        real_root.mkdir(mode=0o700)
        snapshot = real_root / "20260812T020000Z"
        snapshot.mkdir(mode=0o700)
        linked_root = self.base / "linked-root"
        linked_root.symlink_to(real_root, target_is_directory=True)
        retention.ROLLBACK_ROOT = linked_root

        with self.assertRaisesRegex(retention.RetentionError, "symlink"):
            retention.prune_snapshots(
                protect_name=snapshot.name,
                now=self.now,
                lock_fd=self.lock_fd,
            )
        self.assertTrue(snapshot.is_dir())


class InstallerLifecycleTests(unittest.TestCase):
    def run_installer_functions(self, body: str, events: Path) -> subprocess.CompletedProcess[str]:
        script = f"""
source {shlex_quote(INSTALLER)} crawler
restore_previous() {{ printf 'rollback\\n' >> {shlex_quote(events)}; }}
prune_rollback_snapshots() {{
  printf 'prune\\n' >> {shlex_quote(events)}
  return "${{PRUNE_STATUS:-0}}"
}}
ROLLBACK_PATH=/var/lib/jobseek-observability/rollback/20260812T020000Z
ROLLBACK_ARMED=1
trap rollback_on_exit EXIT
{body}
"""
        return subprocess.run(
            ["bash", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_failed_install_rolls_back_and_never_prunes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events"
            result = self.run_installer_functions("false", events)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(events.read_text(encoding="utf-8"), "rollback\n")

    def test_success_disarms_rollback_before_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events"
            result = self.run_installer_functions("disarm_rollback_and_prune", events)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events.read_text(encoding="utf-8"), "prune\n")

    def test_retention_failure_after_success_does_not_roll_back_accepted_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events = Path(temporary) / "events"
            result = self.run_installer_functions(
                "PRUNE_STATUS=9 disarm_rollback_and_prune",
                events,
            )
            self.assertEqual(result.returncode, 9)
            self.assertEqual(events.read_text(encoding="utf-8"), "prune\n")


def shlex_quote(path: Path) -> str:
    import shlex

    return shlex.quote(str(path))


if __name__ == "__main__":
    unittest.main()
