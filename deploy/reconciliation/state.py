#!/usr/bin/env python3
"""Install or verify the crawler reconciliation deployment revision."""

from __future__ import annotations

import argparse
import grp
import os
import re
import tempfile
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/var/lib/jobseek-reconciliation")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


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

    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".deployed-sha.", dir=state_root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(temporary_fd, "w", encoding="ascii") as handle:
            handle.write(f"{revision}\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o640)
            if uid is not None or gid is not None:
                os.fchown(handle.fileno(), -1 if uid is None else uid, -1 if gid is None else gid)
        os.replace(temporary, state_root / "deployed-sha")
        directory_fd = os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--revision", required=True)
    install.add_argument("--group", default="deploy")

    check = subparsers.add_parser("check")
    check.add_argument("--expected-revision")
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
            revision = read_revision(args.state_root)
        else:
            revision = read_revision(args.state_root)
            if args.expected_revision and revision != args.expected_revision:
                raise StateError("deployed reconciliation revision does not match the expected SHA")
    except StateError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(f"verified reconciliation deployment revision {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
