"""Low-overhead Linux process-tree resource sampling for crawler evidence.

The Python browser worker launches a Playwright driver and Chromium processes.
The default Prometheus process collector sees only the Python parent, so it
cannot provide a defensible browser-runtime CPU or memory baseline.  This
module samples the current process namespace directly from ``/proc`` and
publishes bounded, label-free aggregates through metrics owned by
``src.metrics``.

CPU is accumulated as deltas keyed by ``(pid, start_time_ticks)``.  The start
time protects against PID reuse, while retaining the total in a Prometheus
counter preserves the final observation for descendants that later exit.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessStat:
    """The fields needed from one Linux ``/proc/<pid>/stat`` record."""

    pid: int
    parent_pid: int
    start_time_ticks: int
    cpu_ticks: int
    rss_pages: int


@dataclass(frozen=True)
class ProcessTreeSample:
    """One resource observation rooted at the crawler Python process."""

    descendant_cpu_delta_seconds: float
    process_tree_rss_bytes: int
    process_tree_peak_rss_bytes: int
    descendant_count: int


def parse_proc_stat(raw: str) -> ProcessStat:
    """Parse a Linux proc stat line, including command names with spaces."""

    opening = raw.find("(")
    closing = raw.rfind(")")
    if opening <= 0 or closing <= opening:
        raise ValueError("malformed proc stat command field")
    try:
        pid = int(raw[:opening].strip())
        fields = raw[closing + 1 :].split()
        # fields[0] is kernel field 3 (state); see proc_pid_stat(5).
        parent_pid = int(fields[1])
        user_ticks = int(fields[11])
        system_ticks = int(fields[12])
        start_time_ticks = int(fields[19])
        rss_pages = max(0, int(fields[21]))
    except (IndexError, ValueError) as exc:
        raise ValueError("malformed proc stat numeric fields") from exc
    return ProcessStat(
        pid=pid,
        parent_pid=parent_pid,
        start_time_ticks=start_time_ticks,
        cpu_ticks=max(0, user_ticks) + max(0, system_ticks),
        rss_pages=rss_pages,
    )


class ProcessTreeSampler:
    """Sample one process tree and retain monotonic descendant CPU."""

    def __init__(
        self,
        *,
        root_pid: int | None = None,
        proc_root: Path = Path("/proc"),
        clock_ticks_per_second: int | None = None,
        page_size_bytes: int | None = None,
    ) -> None:
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = proc_root
        self.clock_ticks_per_second = (
            int(os.sysconf("SC_CLK_TCK"))
            if clock_ticks_per_second is None
            else clock_ticks_per_second
        )
        self.page_size_bytes = (
            int(os.sysconf("SC_PAGE_SIZE")) if page_size_bytes is None else page_size_bytes
        )
        if self.root_pid <= 0:
            raise ValueError("root_pid must be positive")
        if self.clock_ticks_per_second <= 0 or self.page_size_bytes <= 0:
            raise ValueError("process sampling units must be positive")
        self._active_descendant_cpu: dict[tuple[int, int], int] = {}
        self._peak_rss_bytes = 0

    def _read_stats(self) -> dict[int, ProcessStat]:
        stats: dict[int, ProcessStat] = {}
        for entry in self.proc_root.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                parsed = parse_proc_stat((entry / "stat").read_text())
            except (OSError, ValueError):
                # Processes can exit between directory enumeration and read.
                # A later sample either observes the replacement identity or
                # leaves the already accumulated CPU untouched.
                continue
            stats[parsed.pid] = parsed
        return stats

    def sample(self) -> ProcessTreeSample:
        stats = self._read_stats()
        root = stats.get(self.root_pid)
        if root is None:
            raise RuntimeError("crawler root process is absent from procfs snapshot")

        children: dict[int, list[int]] = defaultdict(list)
        for process in stats.values():
            children[process.parent_pid].append(process.pid)

        descendant_pids: list[int] = []
        queue = deque(children.get(self.root_pid, []))
        seen = {self.root_pid}
        while queue:
            pid = queue.popleft()
            if pid in seen:
                continue
            seen.add(pid)
            if pid not in stats:
                continue
            descendant_pids.append(pid)
            queue.extend(children.get(pid, []))

        next_active: dict[tuple[int, int], int] = {}
        descendant_cpu_delta_ticks = 0
        rss_pages = root.rss_pages
        for pid in descendant_pids:
            process = stats[pid]
            identity = (process.pid, process.start_time_ticks)
            previous_ticks = self._active_descendant_cpu.get(identity, 0)
            descendant_cpu_delta_ticks += max(0, process.cpu_ticks - previous_ticks)
            next_active[identity] = process.cpu_ticks
            rss_pages += process.rss_pages

        # Drop exited identities after their last observed delta. A reused PID
        # has a new start-time identity and begins from its own cumulative CPU.
        self._active_descendant_cpu = next_active
        rss_bytes = rss_pages * self.page_size_bytes
        self._peak_rss_bytes = max(self._peak_rss_bytes, rss_bytes)
        return ProcessTreeSample(
            descendant_cpu_delta_seconds=(descendant_cpu_delta_ticks / self.clock_ticks_per_second),
            process_tree_rss_bytes=rss_bytes,
            process_tree_peak_rss_bytes=self._peak_rss_bytes,
            descendant_count=len(descendant_pids),
        )


def run_process_tree_sampler(
    sampler: ProcessTreeSampler,
    *,
    interval_seconds: float,
    stop_event: threading.Event,
    observe: Callable[[ProcessTreeSample], None],
    record_failure: Callable[[], None],
) -> None:
    """Run bounded sampling until process shutdown.

    Sampling failures are telemetry failures, not crawler failures. They are
    counted and retried so the capture adapter can fail closed when no
    successful coverage exists.
    """

    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while not stop_event.is_set():
        try:
            observe(sampler.sample())
        except (OSError, RuntimeError, ValueError):
            record_failure()
        stop_event.wait(interval_seconds)
