"""Single-host process locks for inventory cache and GitHub write runs."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class InventoryRunBusyError(RuntimeError):
    """Another inventory process owns the single-writer lock."""


@contextmanager
def exclusive_run_lock(path: Path) -> Iterator[None]:
    """Acquire *path* without waiting, and release it on every exit path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InventoryRunBusyError(f"inventory run already active ({path})") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\n".encode())
        os.fsync(fd)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
