#!/usr/bin/env python3
"""Install or verify the crawler reconciliation deployment contract."""

from __future__ import annotations

import argparse
import grp
import os
import re
import tempfile
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/var/lib/jobseek-reconciliation")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StateError(RuntimeError):
    """The deployed reconciliation state is missing or invalid."""


def read_revision(state_root: Path) -> str:
    try:
        revision = (state_root / "deployed-sha").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StateError("reconciliation deployment revision is unavailable") from exc
    if not REVISION_RE.fullmatch(revision):
        raise StateError("reconciliation deployment revision is invalid")
    return revision


def read_wrapper_sha256(state_root: Path) -> str:
    try:
        wrapper_sha256 = (state_root / "wrapper-sha256").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise StateError("reconciliation wrapper digest is unavailable") from exc
    if not SHA256_RE.fullmatch(wrapper_sha256):
        raise StateError("reconciliation wrapper digest is invalid")
    return wrapper_sha256


def _install_state_value(
    state_root: Path,
    filename: str,
    value: str,
    *,
    uid: int | None,
    gid: int | None,
) -> None:
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=state_root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="ascii") as handle:
            handle.write(f"{value}\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o640)
            if uid is not None or gid is not None:
                os.fchown(handle.fileno(), -1 if uid is None else uid, -1 if gid is None else gid)
        os.replace(temporary, state_root / filename)
        directory_fd = os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def install_revision(
    state_root: Path,
    revision: str,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Atomically replace missing/corrupt state with an auditable revision."""
    if not REVISION_RE.fullmatch(revision):
        raise StateError("deployment revision must be a full lowercase Git commit SHA")

    state_root.mkdir(parents=True, exist_ok=True)
    os.chmod(state_root, 0o750)
    if uid is not None or gid is not None:
        os.chown(state_root, -1 if uid is None else uid, -1 if gid is None else gid)

    _install_state_value(
        state_root,
        "deployed-sha",
        revision,
        uid=uid,
        gid=gid,
    )


def install_wrapper_sha256(
    state_root: Path,
    wrapper_sha256: str,
    *,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Atomically publish the installed wrapper's exact content digest."""
    if not SHA256_RE.fullmatch(wrapper_sha256):
        raise StateError("wrapper digest must be a full lowercase SHA-256")
    if not state_root.is_dir():
        raise StateError("reconciliation state directory is unavailable")
    _install_state_value(
        state_root,
        "wrapper-sha256",
        wrapper_sha256,
        uid=uid,
        gid=gid,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--revision", required=True)
    install.add_argument("--wrapper-sha256", required=True)
    install.add_argument("--group", default="deploy")

    check = subparsers.add_parser("check")
    check.add_argument("--expected-revision")
    check.add_argument("--expected-wrapper-sha256")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "install":
            if os.geteuid() != 0:
                raise StateError("revision installation must run as root")
            try:
                gid = grp.getgrnam(args.group).gr_gid
            except KeyError as exc:
                raise StateError(f"required group {args.group!r} is unavailable") from exc
            install_revision(args.state_root, args.revision, uid=0, gid=gid)
            install_wrapper_sha256(
                args.state_root,
                args.wrapper_sha256,
                uid=0,
                gid=gid,
            )
            revision = read_revision(args.state_root)
        else:
            revision = read_revision(args.state_root)
            if args.expected_revision and revision != args.expected_revision:
                raise StateError("deployed reconciliation revision does not match the expected SHA")
        wrapper_sha256 = read_wrapper_sha256(args.state_root)
        if (
            args.command == "check"
            and args.expected_wrapper_sha256
            and wrapper_sha256 != args.expected_wrapper_sha256
        ):
            raise StateError("deployed reconciliation wrapper does not match the expected SHA-256")
    except StateError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(f"verified reconciliation wrapper {wrapper_sha256} at revision {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
