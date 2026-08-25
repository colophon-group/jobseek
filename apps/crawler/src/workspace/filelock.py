"""Advisory file locking for workspace YAML files.

Uses ``fcntl.flock`` (POSIX advisory locks) to serialize concurrent
writes to the same board or workspace file.
"""

from __future__ import annotations

import fcntl
import hashlib
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_FILE_LOCK_TIMEOUT = 10.0
_LIFECYCLE_LOCKS_DIR = Path.home() / ".jobseek" / "locks"
_LIFECYCLE_REGISTRY_GUARD = threading.Lock()
_LIFECYCLE_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LIFECYCLE_LOCAL = threading.local()


@contextmanager
def file_lock(path: Path, *, timeout: float | None = None) -> Generator[None]:
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
    _ = timeout or _FILE_LOCK_TIMEOUT  # noqa: F841 — reserved

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    f = open(lock_path, "w")  # noqa: SIM115
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


@contextmanager
def company_lifecycle_lock(slug: str) -> Generator[None]:
    """Serialize destructive lifecycle operations for one company slug.

    The filename is a digest rather than user-controlled text.  The sidecar
    file is deliberately persistent: ownership lives in the kernel ``flock``,
    so a dead process releases the lock automatically and a stale file can
    never be mistaken for an active owner.
    """
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()
    with _LIFECYCLE_REGISTRY_GUARD:
        thread_lock = _LIFECYCLE_THREAD_LOCKS.setdefault(digest, threading.RLock())

    with thread_lock:
        held: dict[str, tuple[int, BinaryIO]] = getattr(_LIFECYCLE_LOCAL, "held", {})
        current = held.get(digest)
        if current is not None:
            depth, handle = current
            held[digest] = (depth + 1, handle)
            _LIFECYCLE_LOCAL.held = held
            try:
                yield
            finally:
                held[digest] = (depth, handle)
            return

        lock_path = _LIFECYCLE_LOCKS_DIR / f"company-{digest}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")  # noqa: SIM115
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            held[digest] = (1, handle)
            _LIFECYCLE_LOCAL.held = held
            yield
        finally:
            held.pop(digest, None)
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
