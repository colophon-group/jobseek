"""Advisory file locking for workspace YAML files.

Uses ``fcntl.flock`` (POSIX advisory locks) to serialize concurrent
writes to the same board or workspace file.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from src.config import settings


@contextmanager
def file_lock(path: Path, *, timeout: float | None = None) -> Generator[None, None, None]:
    """Acquire an advisory lock on *path* (blocking).

    Creates a ``.lock`` sidecar file next to the target.  The lock is
    released when the context manager exits.

    Parameters
    ----------
    path:
        The file to protect (e.g. ``boards/careers.yaml``).
    timeout:
        Unused for now — ``flock`` blocks indefinitely.  Reserved for
        future non-blocking implementation.
    """
    _ = timeout or settings.ws_file_lock_timeout  # noqa: F841 — reserved

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    f = open(lock_path, "w")  # noqa: SIM115
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
