"""Low-overhead Linux process-tree resource sampling for crawler evidence.

The Python browser worker launches a Playwright driver and Chromium processes.
The default Prometheus process collector sees only the Python parent, so it
cannot provide a defensible browser-runtime CPU or memory baseline.  This
module samples the current process namespace directly from ``/proc`` and
publishes bounded, label-free aggregates through metrics owned by
``src.metrics``.

CPU comes from the crawler container's cgroup-v2 usage counter. Unlike
``/proc/<pid>/stat`` polling, that accounting survives descendant exit and
also includes a child that is born and exits between sampler observations.
``/proc`` remains the source for current aggregate RSS and descendant count.
"""

from __future__ import annotations

import math
import os
import threading
import time
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
    """One immutable, validated process-tree observation generation."""

    observation_sequence: int
    observation_monotonic_seconds: float
    observation_unixtime_seconds: float
    interval_seconds: float
    root_cpu_seconds: float
    process_tree_cpu_seconds: float
    process_tree_cpu_delta_seconds: float
    root_rss_bytes: int
    process_tree_rss_bytes: int
    descendant_count: int

    def __post_init__(self) -> None:
        if self.observation_sequence <= 0:
            raise ValueError("observation_sequence must be positive")
        finite_seconds = (
            self.observation_monotonic_seconds,
            self.observation_unixtime_seconds,
            self.root_cpu_seconds,
            self.process_tree_cpu_seconds,
            self.process_tree_cpu_delta_seconds,
        )
        if not all(math.isfinite(value) for value in finite_seconds):
            raise ValueError("process-tree sample seconds must be finite")
        if not 0 < self.interval_seconds <= 1:
            raise ValueError("interval_seconds must be positive and no greater than one")
        if (
            min(
                self.root_cpu_seconds,
                self.process_tree_cpu_seconds,
                self.process_tree_cpu_delta_seconds,
                self.root_rss_bytes,
                self.process_tree_rss_bytes,
                self.descendant_count,
            )
            < 0
        ):
            raise ValueError("process-tree sample resources must be non-negative")
        if self.process_tree_cpu_seconds < self.root_cpu_seconds:
            raise ValueError("process-tree CPU cannot be lower than root CPU")
        if self.process_tree_rss_bytes < self.root_rss_bytes:
            raise ValueError("process-tree RSS cannot be lower than root RSS")


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
    """Sample one process tree with exit-safe container CPU accounting."""

    def __init__(
        self,
        *,
        root_pid: int | None = None,
        proc_root: Path = Path("/proc"),
        cgroup_cpu_stat_path: Path = Path("/sys/fs/cgroup/cpu.stat"),
        clock_ticks_per_second: int | None = None,
        page_size_bytes: int | None = None,
        interval_seconds: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = proc_root
        self.cgroup_cpu_stat_path = cgroup_cpu_stat_path
        self.clock_ticks_per_second = (
            int(os.sysconf("SC_CLK_TCK"))
            if clock_ticks_per_second is None
            else clock_ticks_per_second
        )
        self.page_size_bytes = (
            int(os.sysconf("SC_PAGE_SIZE")) if page_size_bytes is None else page_size_bytes
        )
        self.interval_seconds = interval_seconds
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        if self.root_pid <= 0:
            raise ValueError("root_pid must be positive")
        if self.clock_ticks_per_second <= 0 or self.page_size_bytes <= 0:
            raise ValueError("process sampling units must be positive")
        if not 0 < self.interval_seconds <= 1:
            raise ValueError("interval_seconds must be positive and no greater than one")
        self._previous_cgroup_cpu_seconds: float | None = None
        self._observation_sequence = 0

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

    def _read_cgroup_cpu_seconds(self) -> float:
        """Read complete role/container CPU from the cgroup-v2 counter."""

        try:
            fields = {
                parts[0]: parts[1]
                for line in self.cgroup_cpu_stat_path.read_text().splitlines()
                if len(parts := line.split()) == 2
            }
            usage_usec = int(fields["usage_usec"])
        except (KeyError, OSError, ValueError) as exc:
            raise RuntimeError("cgroup-v2 CPU usage counter is unavailable") from exc
        if usage_usec < 0:
            raise RuntimeError("cgroup-v2 CPU usage counter is negative")
        return usage_usec / 1_000_000

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

        root_rss_bytes = root.rss_pages * self.page_size_bytes
        rss_pages = root.rss_pages
        for pid in descendant_pids:
            process = stats[pid]
            rss_pages += process.rss_pages

        cgroup_cpu_seconds = self._read_cgroup_cpu_seconds()
        if self._previous_cgroup_cpu_seconds is None:
            cpu_delta_seconds = cgroup_cpu_seconds
        else:
            if cgroup_cpu_seconds < self._previous_cgroup_cpu_seconds:
                raise RuntimeError("cgroup-v2 CPU usage counter regressed")
            cpu_delta_seconds = cgroup_cpu_seconds - self._previous_cgroup_cpu_seconds
        root_cpu_seconds = root.cpu_ticks / self.clock_ticks_per_second
        rss_bytes = rss_pages * self.page_size_bytes
        if cgroup_cpu_seconds < root_cpu_seconds:
            raise RuntimeError("process-tree CPU is lower than root CPU")
        if rss_bytes < root_rss_bytes:
            raise RuntimeError("process-tree RSS is lower than root RSS")

        sample = ProcessTreeSample(
            observation_sequence=self._observation_sequence + 1,
            observation_monotonic_seconds=self.monotonic(),
            observation_unixtime_seconds=self.wall_clock(),
            interval_seconds=self.interval_seconds,
            root_cpu_seconds=root_cpu_seconds,
            process_tree_cpu_seconds=cgroup_cpu_seconds,
            process_tree_cpu_delta_seconds=cpu_delta_seconds,
            root_rss_bytes=root_rss_bytes,
            process_tree_rss_bytes=rss_bytes,
            descendant_count=len(descendant_pids),
        )
        # Only a complete, invariant-safe observation advances either public
        # generation or the exit-safe CPU baseline.
        self._observation_sequence = sample.observation_sequence
        self._previous_cgroup_cpu_seconds = cgroup_cpu_seconds
        return sample


def run_process_tree_sampler(
    sampler: ProcessTreeSampler,
    *,
    interval_seconds: float,
    stop_event: threading.Event,
    observe: Callable[[ProcessTreeSample], None],
    record_failure: Callable[[], None],
    record_gap: Callable[[int], None],
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Run bounded sampling until process shutdown.

    Sampling failures are telemetry failures, not crawler failures. They are
    counted and retried so the capture adapter can fail closed when no
    successful coverage exists.
    """

    if not 0 < interval_seconds <= 1:
        raise ValueError("interval_seconds must be positive and no greater than one")
    if interval_seconds != sampler.interval_seconds:
        raise ValueError("loop interval_seconds must equal the sampler interval")

    deadline_origin = monotonic()
    deadline_index = 0
    while not stop_event.is_set():
        deadline = deadline_origin + deadline_index * interval_seconds
        while (remaining := deadline - monotonic()) > 0:
            if stop_event.wait(remaining):
                return
        if stop_event.is_set():
            return

        try:
            observe(sampler.sample())
        except (OSError, RuntimeError, ValueError):
            record_failure()

        completed_at = monotonic()
        next_deadline_index = deadline_index + 1
        last_elapsed_deadline_index = math.floor(
            (completed_at - deadline_origin) / interval_seconds
        )
        # Correct the quotient around floating-point deadline boundaries.
        while (
            deadline_origin + (last_elapsed_deadline_index + 1) * interval_seconds <= completed_at
        ):
            last_elapsed_deadline_index += 1
        while (
            last_elapsed_deadline_index >= 0
            and deadline_origin + last_elapsed_deadline_index * interval_seconds > completed_at
        ):
            last_elapsed_deadline_index -= 1

        first_future_deadline_index = max(next_deadline_index, last_elapsed_deadline_index + 1)
        skipped_deadlines = first_future_deadline_index - next_deadline_index
        if skipped_deadlines:
            record_gap(skipped_deadlines)
        deadline_index = first_future_deadline_index
