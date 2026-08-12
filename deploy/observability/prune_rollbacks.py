#!/usr/bin/env python3
"""Fail-closed retention for installer-owned observability rollback snapshots."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROLLBACK_ROOT = Path("/var/lib/jobseek-observability/rollback")
DEPLOY_LOCK = Path("/run/jobseek-observability-deploy.lock")
DEPLOY_LOCK_FD = 9
RETAIN_COUNT = 3
MAX_AGE = timedelta(days=14)
FUTURE_SKEW = timedelta(minutes=5)
EXPECTED_UID = 0
EXPECTED_GID = 0
# The crawler host remains on Ubuntu 22.04 with system Python 3.10.
UTC = timezone.utc  # noqa: UP017
SNAPSHOT_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
MOUNT_ID_RE = re.compile(r"^mnt_id:\s+([1-9][0-9]*)$")
ALLOWED_FILES = frozenset(
    {
        "jobseek-alloy",
        "jobseek-host-observability",
        "alloy-host.alloy",
        "alloy.env",
        "host.env",
        "deployed-sha",
        "jobseek-alloy.service",
        "jobseek-host-observability.service",
        "jobseek-host-observability.timer",
    }
)


class RetentionError(RuntimeError):
    """Rollback retention cannot prove that deletion is safe."""


@dataclass(frozen=True)
class Snapshot:
    name: str
    created_at: datetime
    device: int
    inode: int


@dataclass(frozen=True)
class RetentionReport:
    retained: tuple[str, ...]
    deleted: tuple[str, ...]


def _require_exact_path(path: Path, *, directory: bool) -> os.stat_result:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RetentionError(f"required retention path is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise RetentionError(f"retention path must not be a symlink: {path}")
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(metadata.st_mode):
        raise RetentionError(f"retention path has an unexpected type: {path}")
    if resolved != path:
        raise RetentionError(f"retention path does not resolve to its exact location: {path}")
    if metadata.st_uid != EXPECTED_UID or metadata.st_gid != EXPECTED_GID:
        raise RetentionError(f"retention path has unexpected ownership: {path}")
    return metadata


def _require_deploy_lock(lock_fd: int) -> None:
    lock_metadata = _require_exact_path(DEPLOY_LOCK, directory=False)
    try:
        fd_metadata = os.fstat(lock_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        raise RetentionError("observability deployment lock is not held") from exc
    if (fd_metadata.st_dev, fd_metadata.st_ino) != (
        lock_metadata.st_dev,
        lock_metadata.st_ino,
    ):
        raise RetentionError("retention lock descriptor is not the deployment lock")


def _mount_id_for_fd(fd: int) -> int:
    try:
        lines = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RetentionError("Linux mount identity is unavailable for retention") from exc
    matches = [match for line in lines if (match := MOUNT_ID_RE.fullmatch(line))]
    if len(matches) != 1:
        raise RetentionError("Linux mount identity is missing or ambiguous")
    return int(matches[0].group(1))


def _open_root() -> tuple[int, int]:
    metadata = _require_exact_path(ROLLBACK_ROOT, directory=True)
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RetentionError("rollback root must be root-owned mode 0700")
    try:
        root_fd = os.open(
            ROLLBACK_ROOT,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise RetentionError("rollback root cannot be opened without following links") from exc
    opened = os.fstat(root_fd)
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(root_fd)
        raise RetentionError("rollback root changed while retention was starting")
    try:
        mount_id = _mount_id_for_fd(root_fd)
    except RetentionError:
        os.close(root_fd)
        raise
    return root_fd, mount_id


def _parse_timestamp(name: str) -> datetime:
    if SNAPSHOT_RE.fullmatch(name) is None:
        raise RetentionError(f"unexpected rollback entry name: {name}")
    try:
        parsed = datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RetentionError(f"invalid rollback timestamp: {name}") from exc
    if parsed.strftime("%Y%m%dT%H%M%SZ") != name:
        raise RetentionError(f"non-canonical rollback timestamp: {name}")
    return parsed


def _open_snapshot(root_fd: int, root_mount_id: int, snapshot: Snapshot) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        snapshot_fd = os.open(snapshot.name, flags, dir_fd=root_fd)
    except OSError as exc:
        raise RetentionError(f"rollback snapshot cannot be opened safely: {snapshot.name}") from exc
    opened = os.fstat(snapshot_fd)
    if (opened.st_dev, opened.st_ino) != (snapshot.device, snapshot.inode):
        os.close(snapshot_fd)
        raise RetentionError(f"rollback snapshot changed while opening: {snapshot.name}")
    try:
        snapshot_mount_id = _mount_id_for_fd(snapshot_fd)
    except RetentionError:
        os.close(snapshot_fd)
        raise
    if snapshot_mount_id != root_mount_id:
        os.close(snapshot_fd)
        raise RetentionError(f"rollback snapshot is on a different mount: {snapshot.name}")
    return snapshot_fd


def _validated_entry_names(
    snapshot_fd: int,
    root_mount_id: int,
    snapshot_name: str,
) -> tuple[str, ...]:
    try:
        with os.scandir(snapshot_fd) as entries:
            names = tuple(entry.name for entry in entries)
    except OSError as exc:
        raise RetentionError(
            f"rollback snapshot cannot be enumerated safely: {snapshot_name}"
        ) from exc
    for name in names:
        if name not in ALLOWED_FILES:
            raise RetentionError(
                f"rollback snapshot contains an unexpected entry: {snapshot_name}/{name}"
            )
        try:
            child = os.stat(name, dir_fd=snapshot_fd, follow_symlinks=False)
        except OSError as exc:
            raise RetentionError(
                f"rollback snapshot entry is unavailable: {snapshot_name}/{name}"
            ) from exc
        if not stat.S_ISREG(child.st_mode) or child.st_uid != EXPECTED_UID or child.st_nlink != 1:
            raise RetentionError(
                f"rollback snapshot entry is not an owned regular file: {snapshot_name}/{name}"
            )
        try:
            child_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=snapshot_fd)
        except OSError as exc:
            raise RetentionError(
                f"rollback snapshot entry cannot be opened safely: {snapshot_name}/{name}"
            ) from exc
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (child.st_dev, child.st_ino):
                raise RetentionError(
                    f"rollback snapshot entry changed while opening: {snapshot_name}/{name}"
                )
            if _mount_id_for_fd(child_fd) != root_mount_id:
                raise RetentionError(
                    f"rollback snapshot entry is on a different mount: {snapshot_name}/{name}"
                )
        finally:
            os.close(child_fd)
    return tuple(sorted(names))


def _validate_snapshot(
    root_fd: int,
    root_mount_id: int,
    name: str,
    *,
    expected: Snapshot | None = None,
) -> Snapshot:
    created_at = _parse_timestamp(name)
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except OSError as exc:
        raise RetentionError(f"rollback snapshot is unavailable: {name}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RetentionError(f"rollback entry is not a directory: {name}")
    if metadata.st_uid != EXPECTED_UID or metadata.st_gid != EXPECTED_GID:
        raise RetentionError(f"rollback snapshot has unexpected ownership: {name}")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RetentionError(f"rollback snapshot must be mode 0700: {name}")
    if expected is not None and (metadata.st_dev, metadata.st_ino) != (
        expected.device,
        expected.inode,
    ):
        raise RetentionError(f"rollback snapshot changed before deletion: {name}")

    snapshot = Snapshot(name, created_at, metadata.st_dev, metadata.st_ino)
    snapshot_fd = _open_snapshot(root_fd, root_mount_id, snapshot)
    try:
        _validated_entry_names(snapshot_fd, root_mount_id, name)
    finally:
        os.close(snapshot_fd)
    return snapshot


def _delete_snapshot(root_fd: int, root_mount_id: int, expected: Snapshot) -> None:
    snapshot = _validate_snapshot(
        root_fd,
        root_mount_id,
        expected.name,
        expected=expected,
    )
    snapshot_fd = _open_snapshot(root_fd, root_mount_id, snapshot)
    try:
        # Snapshots are deliberately flat. Revalidate and unlink only the
        # allowlisted regular files through the already-verified directory fd;
        # never recurse into an entry that appears after validation.
        for name in _validated_entry_names(snapshot_fd, root_mount_id, snapshot.name):
            try:
                os.unlink(name, dir_fd=snapshot_fd)
            except OSError as exc:
                raise RetentionError(
                    f"rollback snapshot entry could not be removed safely: {snapshot.name}/{name}"
                ) from exc
        os.fsync(snapshot_fd)
    finally:
        os.close(snapshot_fd)
    try:
        os.rmdir(snapshot.name, dir_fd=root_fd)
    except OSError as exc:
        raise RetentionError(
            f"rollback snapshot could not be removed safely: {snapshot.name}"
        ) from exc


def _list_snapshots(root_fd: int, root_mount_id: int, *, now: datetime) -> list[Snapshot]:
    snapshots: list[Snapshot] = []
    try:
        with os.scandir(root_fd) as entries:
            names = [entry.name for entry in entries]
    except OSError as exc:
        raise RetentionError("rollback root cannot be enumerated safely") from exc
    for name in names:
        snapshot = _validate_snapshot(root_fd, root_mount_id, name)
        if snapshot.created_at > now + FUTURE_SKEW:
            raise RetentionError(f"rollback timestamp is unexpectedly in the future: {name}")
        snapshots.append(snapshot)
    return sorted(snapshots, key=lambda snapshot: (snapshot.created_at, snapshot.name))


def prune_snapshots(
    *,
    protect_name: str | None,
    now: datetime | None = None,
    lock_fd: int = DEPLOY_LOCK_FD,
) -> RetentionReport:
    """Validate the full root, then retain a bounded recent rollback set."""
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise RetentionError("retention clock must be timezone-aware")
    current_time = current_time.astimezone(UTC)
    _require_deploy_lock(lock_fd)
    root_fd, root_mount_id = _open_root()
    try:
        snapshots = _list_snapshots(root_fd, root_mount_id, now=current_time)
        if protect_name is not None:
            _parse_timestamp(protect_name)
            if not snapshots or snapshots[-1].name != protect_name:
                raise RetentionError("accepted rollback is not the newest validated snapshot")

        newest_names = {snapshot.name for snapshot in snapshots[-RETAIN_COUNT:]}
        newest_name = snapshots[-1].name if snapshots else None
        cutoff = current_time - MAX_AGE
        to_delete = [
            snapshot
            for snapshot in snapshots
            if snapshot.name != newest_name
            and (snapshot.name not in newest_names or snapshot.created_at < cutoff)
        ]

        # The complete root is validated before the first deletion. Revalidate
        # each target by inode and contents immediately before flat,
        # fd-relative removal.
        for snapshot in to_delete:
            _delete_snapshot(root_fd, root_mount_id, snapshot)
        if to_delete:
            os.fsync(root_fd)
        deleted = tuple(snapshot.name for snapshot in to_delete)
        retained = tuple(snapshot.name for snapshot in snapshots if snapshot.name not in deleted)
        return RetentionReport(retained=retained, deleted=deleted)
    finally:
        os.close(root_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protect-name", required=True)
    args = parser.parse_args()
    try:
        report = prune_snapshots(protect_name=args.protect_name)
    except RetentionError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        "Observability rollback retention complete; "
        f"retained={len(report.retained)} deleted={len(report.deleted)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
