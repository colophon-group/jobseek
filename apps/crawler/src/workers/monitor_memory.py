"""Best-effort monitor-process memory measurements and reclamation."""

from __future__ import annotations

import ctypes
import gc
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_CGROUP_MEMORY_PATHS = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)


@dataclass(frozen=True, slots=True)
class MemoryReclaim:
    """Best-effort allocator reclamation measurements."""

    collected_objects: int
    malloc_trimmed: bool | None
    rss_before_bytes: int | None
    rss_after_bytes: int | None
    cgroup_before_bytes: int | None
    cgroup_after_bytes: int | None

    @property
    def rss_reclaimed_bytes(self) -> int:
        if self.rss_before_bytes is None or self.rss_after_bytes is None:
            return 0
        return max(0, self.rss_before_bytes - self.rss_after_bytes)


def reclaim_process_memory() -> MemoryReclaim:
    """Collect cycles and return free glibc pages to the Linux cgroup.

    CPython reference counting frees most monitor objects immediately, but
    both cyclic objects and libc arenas can otherwise keep a worker near its
    previous high-water mark. All operations are best-effort and portable;
    ``malloc_trim`` is used only when libc exports it.
    """

    rss_before = process_rss_bytes()
    cgroup_before = cgroup_memory_bytes()
    collected = gc.collect()
    malloc_trimmed = _malloc_trim()
    return MemoryReclaim(
        collected_objects=collected,
        malloc_trimmed=malloc_trimmed,
        rss_before_bytes=rss_before,
        rss_after_bytes=process_rss_bytes(),
        cgroup_before_bytes=cgroup_before,
        cgroup_after_bytes=cgroup_memory_bytes(),
    )


def process_rss_bytes() -> int | None:
    """Read current RSS without adding a runtime dependency."""

    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        return None


def cgroup_memory_bytes() -> int | None:
    for path in _CGROUP_MEMORY_PATHS:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def _malloc_trim() -> bool | None:
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(None)
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return bool(trim(0))
    except (AttributeError, OSError):
        return None
