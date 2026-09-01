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

import json
import math
import multiprocessing
import os
import select
import socket
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Final, Literal, Protocol

SamplingGapReason = Literal["scheduler_late", "collection_overrun"]
SamplerTimingKind = Literal["wake_lateness", "collection", "handoff"]

SAMPLER_TIMING_BUCKETS: Final[tuple[float, ...]] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)
_SAMPLER_FRAME_VERSION = 1
_MAX_SAMPLER_FRAME_BYTES = 64 * 1024
_SAMPLER_SOCKET_BUFFER_BYTES = 256 * 1024
_MAX_SAMPLER_CLOCK_AHEAD_SECONDS = 1.0


class WaitableEvent(Protocol):
    """The subset shared by threading.Event and the child stop socket."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class _SocketStopEvent:
    """Pollable one-way shutdown signal that survives abrupt child death."""

    def __init__(self, receiver: socket.socket) -> None:
        self._receiver = receiver
        self._stopped = False

    def _poll(self, timeout: float | None) -> bool:
        if self._stopped:
            return True
        readable, _writable, _exceptional = select.select(
            [self._receiver],
            [],
            [],
            timeout,
        )
        if not readable:
            return False
        with suppress(OSError):
            self._receiver.recv(1)
        self._stopped = True
        return True

    def is_set(self) -> bool:
        return self._poll(0.0)

    def wait(self, timeout: float | None = None) -> bool:
        return self._poll(timeout)

    def close(self) -> None:
        self._receiver.close()


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
    cpu_source: str = "container-cgroup-v2"

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
        if self.cpu_source != "container-cgroup-v2":
            raise ValueError("process-tree CPU source is invalid")


@dataclass(frozen=True)
class TimingHistogramSnapshot:
    """One cumulative, bounded timing histogram from the sampler process."""

    bucket_counts: tuple[int, ...]
    count: int
    sum_seconds: float

    def __post_init__(self) -> None:
        if len(self.bucket_counts) != len(SAMPLER_TIMING_BUCKETS):
            raise ValueError("sampler timing histogram bucket count is invalid")
        if self.count < 0 or any(value < 0 for value in self.bucket_counts):
            raise ValueError("sampler timing histogram counts must be non-negative")
        if any(
            right < left
            for left, right in zip(self.bucket_counts, self.bucket_counts[1:], strict=False)
        ):
            raise ValueError("sampler timing histogram buckets must be cumulative")
        if self.bucket_counts and self.bucket_counts[-1] > self.count:
            raise ValueError("sampler timing histogram bucket exceeds total count")
        if not math.isfinite(self.sum_seconds) or self.sum_seconds < 0:
            raise ValueError("sampler timing histogram sum must be finite and non-negative")

    @classmethod
    def empty(cls) -> TimingHistogramSnapshot:
        return cls(
            bucket_counts=(0,) * len(SAMPLER_TIMING_BUCKETS),
            count=0,
            sum_seconds=0.0,
        )

    def __add__(self, other: TimingHistogramSnapshot) -> TimingHistogramSnapshot:
        return TimingHistogramSnapshot(
            bucket_counts=tuple(
                left + right
                for left, right in zip(
                    self.bucket_counts,
                    other.bucket_counts,
                    strict=True,
                )
            ),
            count=self.count + other.count,
            sum_seconds=self.sum_seconds + other.sum_seconds,
        )

    def contains(self, earlier: TimingHistogramSnapshot) -> bool:
        """Return whether this cumulative snapshot can follow ``earlier``."""

        return (
            self.count >= earlier.count
            and self.sum_seconds >= earlier.sum_seconds
            and all(
                current >= previous
                for current, previous in zip(
                    self.bucket_counts,
                    earlier.bucket_counts,
                    strict=True,
                )
            )
        )

    def prometheus_buckets(self) -> list[tuple[str, int]]:
        buckets = [
            (str(boundary), count)
            for boundary, count in zip(
                SAMPLER_TIMING_BUCKETS,
                self.bucket_counts,
                strict=True,
            )
        ]
        buckets.append(("+Inf", self.count))
        return buckets


@dataclass(frozen=True)
class SamplerMetricsSnapshot:
    """Complete cumulative evidence consumed by the parent metrics collector."""

    sample: ProcessTreeSample | None
    successes: int
    failures: int
    scheduler_late_gaps: int
    collection_overrun_gaps: int
    starts: int
    wake_lateness: TimingHistogramSnapshot
    collection_duration: TimingHistogramSnapshot
    handoff_duration: TimingHistogramSnapshot

    def __post_init__(self) -> None:
        if (
            min(
                self.successes,
                self.failures,
                self.scheduler_late_gaps,
                self.collection_overrun_gaps,
                self.starts,
            )
            < 0
        ):
            raise ValueError("sampler metric counters must be non-negative")

    @property
    def gaps(self) -> int:
        return self.scheduler_late_gaps + self.collection_overrun_gaps


@dataclass(frozen=True)
class _SamplerChildSnapshot:
    emitted_monotonic_seconds: float
    sample: ProcessTreeSample | None
    successes: int
    failures: int
    scheduler_late_gaps: int
    collection_overrun_gaps: int
    wake_lateness: TimingHistogramSnapshot
    collection_duration: TimingHistogramSnapshot
    handoff_duration: TimingHistogramSnapshot

    def __post_init__(self) -> None:
        if not math.isfinite(self.emitted_monotonic_seconds):
            raise ValueError("sampler frame monotonic timestamp must be finite")
        if self.emitted_monotonic_seconds < 0:
            raise ValueError("sampler frame monotonic timestamp must be non-negative")
        if (
            min(
                self.successes,
                self.failures,
                self.scheduler_late_gaps,
                self.collection_overrun_gaps,
            )
            < 0
        ):
            raise ValueError("sampler frame counters must be non-negative")
        if self.sample is None:
            if self.successes != 0:
                raise ValueError("sampler frame without a sample cannot report successes")
        elif self.sample.observation_sequence != self.successes:
            raise ValueError("sampler frame sequence must equal successful samples")
        elif self.sample.observation_monotonic_seconds > self.emitted_monotonic_seconds:
            raise ValueError("sampler sample cannot be observed after its frame was emitted")

    def contains(self, earlier: _SamplerChildSnapshot) -> bool:
        """Reject regressions or reordered frames from one child generation."""

        current_sequence = self.sample.observation_sequence if self.sample is not None else 0
        earlier_sequence = earlier.sample.observation_sequence if earlier.sample is not None else 0
        sample_time_is_monotonic = (
            self.sample is None
            or earlier.sample is None
            or self.sample.observation_monotonic_seconds
            >= earlier.sample.observation_monotonic_seconds
        )
        unchanged_sequence_is_immutable = (
            current_sequence != earlier_sequence or self.sample == earlier.sample
        )
        return (
            self.emitted_monotonic_seconds > earlier.emitted_monotonic_seconds
            and current_sequence >= earlier_sequence
            and sample_time_is_monotonic
            and unchanged_sequence_is_immutable
            and self.successes >= earlier.successes
            and self.failures >= earlier.failures
            and self.scheduler_late_gaps >= earlier.scheduler_late_gaps
            and self.collection_overrun_gaps >= earlier.collection_overrun_gaps
            and self.wake_lateness.contains(earlier.wake_lateness)
            and self.collection_duration.contains(earlier.collection_duration)
            and self.handoff_duration.contains(earlier.handoff_duration)
        )


class _TimingHistogram:
    """Mutable child-local accumulator serialized in every IPC snapshot."""

    def __init__(self) -> None:
        self._bucket_counts = [0] * len(SAMPLER_TIMING_BUCKETS)
        self._count = 0
        self._sum_seconds = 0.0

    def observe(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("sampler timing must be finite and non-negative")
        self._count += 1
        self._sum_seconds += seconds
        for index, boundary in enumerate(SAMPLER_TIMING_BUCKETS):
            if seconds <= boundary:
                self._bucket_counts[index] += 1

    def snapshot(self) -> TimingHistogramSnapshot:
        return TimingHistogramSnapshot(
            bucket_counts=tuple(self._bucket_counts),
            count=self._count,
            sum_seconds=self._sum_seconds,
        )


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


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields are invalid")


def _require_nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_finite_number(value: object, field: str, *, nonnegative: bool = True) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or (nonnegative and numeric < 0):
        raise ValueError(f"{field} must be finite and non-negative")
    return numeric


_PROCESS_TREE_SAMPLE_KEYS = {
    "observation_sequence",
    "observation_monotonic_seconds",
    "observation_unixtime_seconds",
    "interval_seconds",
    "root_cpu_seconds",
    "process_tree_cpu_seconds",
    "process_tree_cpu_delta_seconds",
    "root_rss_bytes",
    "process_tree_rss_bytes",
    "descendant_count",
    "cpu_source",
}


def _sample_payload(sample: ProcessTreeSample) -> dict[str, int | float | str]:
    return {
        "observation_sequence": sample.observation_sequence,
        "observation_monotonic_seconds": sample.observation_monotonic_seconds,
        "observation_unixtime_seconds": sample.observation_unixtime_seconds,
        "interval_seconds": sample.interval_seconds,
        "root_cpu_seconds": sample.root_cpu_seconds,
        "process_tree_cpu_seconds": sample.process_tree_cpu_seconds,
        "process_tree_cpu_delta_seconds": sample.process_tree_cpu_delta_seconds,
        "root_rss_bytes": sample.root_rss_bytes,
        "process_tree_rss_bytes": sample.process_tree_rss_bytes,
        "descendant_count": sample.descendant_count,
        "cpu_source": sample.cpu_source,
    }


def _decode_sample(value: object) -> ProcessTreeSample | None:
    if value is None:
        return None
    payload = _require_mapping(value, "sampler frame sample")
    _require_exact_keys(payload, _PROCESS_TREE_SAMPLE_KEYS, "sampler frame sample")
    return ProcessTreeSample(
        observation_sequence=_require_nonnegative_int(
            payload["observation_sequence"],
            "sampler frame sample observation_sequence",
        ),
        observation_monotonic_seconds=_require_finite_number(
            payload["observation_monotonic_seconds"],
            "sampler frame sample observation_monotonic_seconds",
        ),
        observation_unixtime_seconds=_require_finite_number(
            payload["observation_unixtime_seconds"],
            "sampler frame sample observation_unixtime_seconds",
        ),
        interval_seconds=_require_finite_number(
            payload["interval_seconds"],
            "sampler frame sample interval_seconds",
        ),
        root_cpu_seconds=_require_finite_number(
            payload["root_cpu_seconds"],
            "sampler frame sample root_cpu_seconds",
        ),
        process_tree_cpu_seconds=_require_finite_number(
            payload["process_tree_cpu_seconds"],
            "sampler frame sample process_tree_cpu_seconds",
        ),
        process_tree_cpu_delta_seconds=_require_finite_number(
            payload["process_tree_cpu_delta_seconds"],
            "sampler frame sample process_tree_cpu_delta_seconds",
        ),
        root_rss_bytes=_require_nonnegative_int(
            payload["root_rss_bytes"],
            "sampler frame sample root_rss_bytes",
        ),
        process_tree_rss_bytes=_require_nonnegative_int(
            payload["process_tree_rss_bytes"],
            "sampler frame sample process_tree_rss_bytes",
        ),
        descendant_count=_require_nonnegative_int(
            payload["descendant_count"],
            "sampler frame sample descendant_count",
        ),
        cpu_source=_require_string(
            payload["cpu_source"],
            "sampler frame sample cpu_source",
        ),
    )


_TIMING_HISTOGRAM_KEYS = {"bucket_counts", "count", "sum_seconds"}


def _histogram_payload(histogram: TimingHistogramSnapshot) -> dict[str, object]:
    return {
        "bucket_counts": list(histogram.bucket_counts),
        "count": histogram.count,
        "sum_seconds": histogram.sum_seconds,
    }


def _decode_histogram(value: object, field: str) -> TimingHistogramSnapshot:
    payload = _require_mapping(value, field)
    _require_exact_keys(payload, _TIMING_HISTOGRAM_KEYS, field)
    raw_buckets = payload["bucket_counts"]
    if not isinstance(raw_buckets, list):
        raise ValueError(f"{field} bucket_counts must be an array")
    return TimingHistogramSnapshot(
        bucket_counts=tuple(
            _require_nonnegative_int(item, f"{field} bucket count") for item in raw_buckets
        ),
        count=_require_nonnegative_int(payload["count"], f"{field} count"),
        sum_seconds=_require_finite_number(payload["sum_seconds"], f"{field} sum_seconds"),
    )


_SAMPLER_FRAME_KEYS = {
    "version",
    "emitted_monotonic_seconds",
    "sample",
    "successes",
    "failures",
    "scheduler_late_gaps",
    "collection_overrun_gaps",
    "timings",
}
_SAMPLER_TIMING_KEYS = {"wake_lateness", "collection_duration", "handoff_duration"}


def _encode_sampler_snapshot(snapshot: _SamplerChildSnapshot) -> bytes:
    payload = {
        "version": _SAMPLER_FRAME_VERSION,
        "emitted_monotonic_seconds": snapshot.emitted_monotonic_seconds,
        "sample": _sample_payload(snapshot.sample) if snapshot.sample is not None else None,
        "successes": snapshot.successes,
        "failures": snapshot.failures,
        "scheduler_late_gaps": snapshot.scheduler_late_gaps,
        "collection_overrun_gaps": snapshot.collection_overrun_gaps,
        "timings": {
            "wake_lateness": _histogram_payload(snapshot.wake_lateness),
            "collection_duration": _histogram_payload(snapshot.collection_duration),
            "handoff_duration": _histogram_payload(snapshot.handoff_duration),
        },
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_SAMPLER_FRAME_BYTES:
        raise ValueError("sampler frame exceeds the bounded IPC size")
    return encoded


def _decode_sampler_snapshot(raw: bytes) -> _SamplerChildSnapshot:
    if not raw or len(raw) > _MAX_SAMPLER_FRAME_BYTES:
        raise ValueError("sampler frame size is invalid")
    try:
        decoded: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("sampler frame is not complete JSON") from exc
    payload = _require_mapping(decoded, "sampler frame")
    _require_exact_keys(payload, _SAMPLER_FRAME_KEYS, "sampler frame")
    if payload["version"] != _SAMPLER_FRAME_VERSION:
        raise ValueError("sampler frame version is unsupported")
    timings = _require_mapping(payload["timings"], "sampler frame timings")
    _require_exact_keys(timings, _SAMPLER_TIMING_KEYS, "sampler frame timings")
    return _SamplerChildSnapshot(
        emitted_monotonic_seconds=_require_finite_number(
            payload["emitted_monotonic_seconds"],
            "sampler frame emitted_monotonic_seconds",
        ),
        sample=_decode_sample(payload["sample"]),
        successes=_require_nonnegative_int(payload["successes"], "sampler frame successes"),
        failures=_require_nonnegative_int(payload["failures"], "sampler frame failures"),
        scheduler_late_gaps=_require_nonnegative_int(
            payload["scheduler_late_gaps"],
            "sampler frame scheduler_late_gaps",
        ),
        collection_overrun_gaps=_require_nonnegative_int(
            payload["collection_overrun_gaps"],
            "sampler frame collection_overrun_gaps",
        ),
        wake_lateness=_decode_histogram(
            timings["wake_lateness"],
            "sampler frame wake_lateness",
        ),
        collection_duration=_decode_histogram(
            timings["collection_duration"],
            "sampler frame collection_duration",
        ),
        handoff_duration=_decode_histogram(
            timings["handoff_duration"],
            "sampler frame handoff_duration",
        ),
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
    stop_event: WaitableEvent,
    observe: Callable[[ProcessTreeSample], None],
    record_failure: Callable[[], None],
    record_gap: Callable[[SamplingGapReason, int], None],
    record_timing: Callable[[SamplerTimingKind, float], None] | None = None,
    flush: Callable[[], None] | None = None,
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

        collection_started_at = monotonic()
        if record_timing is not None:
            record_timing(
                "wake_lateness",
                max(0.0, collection_started_at - deadline),
            )
        collection_completed_at = collection_started_at
        try:
            sample = sampler.sample()
        except (OSError, RuntimeError, ValueError):
            collection_completed_at = monotonic()
            if record_timing is not None:
                record_timing(
                    "collection",
                    max(0.0, collection_completed_at - collection_started_at),
                )
            record_failure()
        else:
            collection_completed_at = monotonic()
            if record_timing is not None:
                record_timing(
                    "collection",
                    max(0.0, collection_completed_at - collection_started_at),
                )
            try:
                observe(sample)
            except (OSError, RuntimeError, ValueError):
                record_failure()

        # One datagram is the complete atomic publication boundary for this
        # cycle. Its serialization and send are part of handoff time. The
        # resulting duration and gap classification are cumulative state and
        # are therefore carried by the next cycle's indivisible datagram.
        if flush is not None:
            try:
                flush()
            except (OSError, RuntimeError, ValueError):
                record_failure()

        completed_at = monotonic()
        if record_timing is not None:
            record_timing(
                "handoff",
                max(0.0, completed_at - collection_completed_at),
            )
        next_deadline_index = deadline_index + 1
        last_elapsed_deadline_index = _last_elapsed_deadline_index(
            deadline_origin,
            interval_seconds,
            completed_at,
        )

        first_future_deadline_index = max(next_deadline_index, last_elapsed_deadline_index + 1)
        skipped_deadlines = first_future_deadline_index - next_deadline_index
        if skipped_deadlines:
            last_scheduler_late_index = _last_elapsed_deadline_index(
                deadline_origin,
                interval_seconds,
                collection_started_at,
            )
            scheduler_late = max(
                0,
                min(first_future_deadline_index, last_scheduler_late_index + 1)
                - next_deadline_index,
            )
            collection_overrun = skipped_deadlines - scheduler_late
            if scheduler_late:
                record_gap("scheduler_late", scheduler_late)
            if collection_overrun:
                record_gap("collection_overrun", collection_overrun)
        deadline_index = first_future_deadline_index


def _last_elapsed_deadline_index(
    deadline_origin: float,
    interval_seconds: float,
    observed_at: float,
) -> int:
    """Return the final deadline at or before one monotonic timestamp."""

    index = math.floor((observed_at - deadline_origin) / interval_seconds)
    # Correct the quotient around floating-point deadline boundaries.
    while deadline_origin + (index + 1) * interval_seconds <= observed_at:
        index += 1
    while index >= 0 and deadline_origin + index * interval_seconds > observed_at:
        index -= 1
    return index


class _SamplerChildAccumulator:
    """Own counters in the isolated child and emit cumulative atomic frames."""

    def __init__(self, sender: socket.socket) -> None:
        self._sender = sender
        self.sample: ProcessTreeSample | None = None
        self.successes = 0
        self.failures = 0
        self.scheduler_late_gaps = 0
        self.collection_overrun_gaps = 0
        self.wake_lateness = _TimingHistogram()
        self.collection_duration = _TimingHistogram()
        self.handoff_duration = _TimingHistogram()

    def observe(self, sample: ProcessTreeSample) -> None:
        self.sample = sample
        self.successes += 1

    def record_failure(self) -> None:
        self.failures += 1

    def record_gap(self, reason: SamplingGapReason, count: int) -> None:
        if count <= 0:
            return
        if reason == "scheduler_late":
            self.scheduler_late_gaps += count
        else:
            self.collection_overrun_gaps += count

    def record_timing(self, kind: SamplerTimingKind, seconds: float) -> None:
        if kind == "wake_lateness":
            self.wake_lateness.observe(seconds)
        elif kind == "collection":
            self.collection_duration.observe(seconds)
        else:
            self.handoff_duration.observe(seconds)

    def snapshot(self) -> _SamplerChildSnapshot:
        return _SamplerChildSnapshot(
            emitted_monotonic_seconds=time.monotonic(),
            sample=self.sample,
            successes=self.successes,
            failures=self.failures,
            scheduler_late_gaps=self.scheduler_late_gaps,
            collection_overrun_gaps=self.collection_overrun_gaps,
            wake_lateness=self.wake_lateness.snapshot(),
            collection_duration=self.collection_duration.snapshot(),
            handoff_duration=self.handoff_duration.snapshot(),
        )

    def flush(self) -> None:
        try:
            self._sender.send(_encode_sampler_snapshot(self.snapshot()))
        except BlockingIOError:
            # The parent was unable to drain while stalled. Cadence remains
            # independent, and the next deliverable frame carries the
            # fail-closed failure count.
            self.failures += 1


def _run_process_tree_sampler_process(
    *,
    root_pid: int,
    proc_root: Path,
    cgroup_cpu_stat_path: Path,
    interval_seconds: float,
    sender: socket.socket,
    stop_receiver: socket.socket,
) -> None:
    """Child entry point: cadence and collection never share the parent GIL."""

    sender.setblocking(False)
    stop_event = _SocketStopEvent(stop_receiver)
    accumulator = _SamplerChildAccumulator(sender)
    sampler = ProcessTreeSampler(
        root_pid=root_pid,
        proc_root=proc_root,
        cgroup_cpu_stat_path=cgroup_cpu_stat_path,
        interval_seconds=interval_seconds,
    )
    try:
        run_process_tree_sampler(
            sampler,
            interval_seconds=interval_seconds,
            stop_event=stop_event,
            observe=accumulator.observe,
            record_failure=accumulator.record_failure,
            record_gap=accumulator.record_gap,
            record_timing=accumulator.record_timing,
            flush=accumulator.flush,
        )
    finally:
        stop_event.close()
        sender.close()


@dataclass
class _SamplerChild:
    process: BaseProcess
    receiver: socket.socket
    stop_sender: socket.socket
    started_monotonic_seconds: float
    snapshot: _SamplerChildSnapshot | None = None
    last_valid_frame_received_monotonic_seconds: float | None = None


class ProcessTreeSamplerProcess:
    """Supervise an isolated same-cgroup sampler and consume atomic frames."""

    def __init__(
        self,
        *,
        root_pid: int | None = None,
        proc_root: Path = Path("/proc"),
        cgroup_cpu_stat_path: Path = Path("/sys/fs/cgroup/cpu.stat"),
        interval_seconds: float = 0.5,
        process_context: Any | None = None,
        stale_after_seconds: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if root_pid is not None and root_pid <= 0:
            raise ValueError("root_pid must be positive")
        if not 0 < interval_seconds <= 1:
            raise ValueError("interval_seconds must be positive and no greater than one")
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = proc_root
        self.cgroup_cpu_stat_path = cgroup_cpu_stat_path
        self.interval_seconds = interval_seconds
        self._context = process_context or multiprocessing.get_context("spawn")
        self._stale_after_seconds = (
            max(5.0, interval_seconds * 10) if stale_after_seconds is None else stale_after_seconds
        )
        if self._stale_after_seconds <= interval_seconds:
            raise ValueError("stale_after_seconds must exceed the sample interval")
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._child: _SamplerChild | None = None
        self._closed = False
        self._starts = 0
        self._local_failures = 0
        self._success_offset = 0
        self._failure_offset = 0
        self._scheduler_late_offset = 0
        self._collection_overrun_offset = 0
        self._wake_lateness_offset = TimingHistogramSnapshot.empty()
        self._collection_duration_offset = TimingHistogramSnapshot.empty()
        self._handoff_duration_offset = TimingHistogramSnapshot.empty()
        self._last_sample: ProcessTreeSample | None = None
        self._stale_reported = False
        self._start_child()

    @property
    def child_pid(self) -> int | None:
        with self._lock:
            if self._child is None:
                return None
            return self._child.process.pid

    def _start_child(self) -> None:
        receiver, sender = socket.socketpair(type=socket.SOCK_DGRAM)
        receiver.setblocking(False)
        receiver.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_RCVBUF,
            _SAMPLER_SOCKET_BUFFER_BYTES,
        )
        sender.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_SNDBUF,
            _SAMPLER_SOCKET_BUFFER_BYTES,
        )
        stop_sender, stop_receiver = socket.socketpair(type=socket.SOCK_DGRAM)
        process = self._context.Process(
            target=_run_process_tree_sampler_process,
            kwargs={
                "root_pid": self.root_pid,
                "proc_root": self.proc_root,
                "cgroup_cpu_stat_path": self.cgroup_cpu_stat_path,
                "interval_seconds": self.interval_seconds,
                "sender": sender,
                "stop_receiver": stop_receiver,
            },
            name="crawler-process-tree-sampler",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            receiver.close()
            sender.close()
            stop_sender.close()
            stop_receiver.close()
            raise
        sender.close()
        stop_receiver.close()
        self._child = _SamplerChild(
            process=process,
            receiver=receiver,
            stop_sender=stop_sender,
            started_monotonic_seconds=self._monotonic(),
        )
        self._starts += 1
        self._stale_reported = False

    def _drain_frames(self, child: _SamplerChild) -> None:
        while True:
            try:
                raw, _ancillary, flags, _address = child.receiver.recvmsg(_MAX_SAMPLER_FRAME_BYTES)
            except BlockingIOError:
                return
            except OSError:
                self._local_failures += 1
                return
            if flags & socket.MSG_TRUNC:
                self._local_failures += 1
                continue
            try:
                snapshot = _decode_sampler_snapshot(raw)
            except ValueError:
                self._local_failures += 1
                continue
            received_at = self._monotonic()
            sample = snapshot.sample
            if (
                snapshot.emitted_monotonic_seconds > received_at + _MAX_SAMPLER_CLOCK_AHEAD_SECONDS
                or (sample is not None and sample.interval_seconds != self.interval_seconds)
                or (
                    sample is not None
                    and self._last_sample is not None
                    and sample.observation_monotonic_seconds
                    < self._last_sample.observation_monotonic_seconds
                )
                or (child.snapshot is not None and not snapshot.contains(child.snapshot))
            ):
                self._local_failures += 1
                continue
            child.snapshot = snapshot
            child.last_valid_frame_received_monotonic_seconds = received_at
            if sample is not None:
                self._last_sample = sample
            self._stale_reported = False

    def _roll_child_into_offsets(self, child: _SamplerChild) -> None:
        snapshot = child.snapshot
        if snapshot is None:
            return
        self._success_offset += snapshot.successes
        self._failure_offset += snapshot.failures
        self._scheduler_late_offset += snapshot.scheduler_late_gaps
        self._collection_overrun_offset += snapshot.collection_overrun_gaps
        self._wake_lateness_offset += snapshot.wake_lateness
        self._collection_duration_offset += snapshot.collection_duration
        self._handoff_duration_offset += snapshot.handoff_duration

    def _stop_child(self, child: _SamplerChild) -> None:
        if child.process.is_alive():
            with suppress(OSError):
                child.stop_sender.send(b"\0")
        child.process.join(timeout=max(1.0, self.interval_seconds * 3))
        if child.process.is_alive():
            child.process.kill()
            child.process.join(timeout=1.0)
        if child.process.is_alive():
            raise RuntimeError("process-tree sampler child did not exit")
        child.stop_sender.close()
        child.receiver.close()
        child.process.close()

    def _restart_child(self, child: _SamplerChild) -> None:
        self._drain_frames(child)
        self._roll_child_into_offsets(child)
        self._stop_child(child)
        self._child = None
        self._start_child()

    def _restart_if_unhealthy(self, child: _SamplerChild, now: float) -> None:
        dead = not child.process.is_alive()
        last_received = (
            child.last_valid_frame_received_monotonic_seconds
            if child.last_valid_frame_received_monotonic_seconds is not None
            else child.started_monotonic_seconds
        )
        stale = now - last_received > self._stale_after_seconds
        if not dead and not stale:
            return
        if not self._stale_reported:
            self._local_failures += 1
            self._stale_reported = True
        self._restart_child(child)

    def snapshot(self) -> SamplerMetricsSnapshot:
        """Drain complete frames and return one monotonic aggregate generation."""

        with self._lock:
            if self._closed or self._child is None:
                raise RuntimeError("process-tree sampler process is closed")
            child = self._child
            self._drain_frames(child)
            self._restart_if_unhealthy(child, self._monotonic())
            child = self._child
            assert child is not None
            current = child.snapshot
            return SamplerMetricsSnapshot(
                sample=self._last_sample,
                successes=self._success_offset + (current.successes if current else 0),
                failures=(
                    self._failure_offset
                    + self._local_failures
                    + (current.failures if current else 0)
                ),
                scheduler_late_gaps=(
                    self._scheduler_late_offset + (current.scheduler_late_gaps if current else 0)
                ),
                collection_overrun_gaps=(
                    self._collection_overrun_offset
                    + (current.collection_overrun_gaps if current else 0)
                ),
                starts=self._starts,
                wake_lateness=(
                    self._wake_lateness_offset
                    + (current.wake_lateness if current else TimingHistogramSnapshot.empty())
                ),
                collection_duration=(
                    self._collection_duration_offset
                    + (current.collection_duration if current else TimingHistogramSnapshot.empty())
                ),
                handoff_duration=(
                    self._handoff_duration_offset
                    + (current.handoff_duration if current else TimingHistogramSnapshot.empty())
                ),
            )

    def close(self) -> None:
        """Stop the child without allowing a shutdown-time restart."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            child = self._child
            self._child = None
            if child is not None:
                self._stop_child(child)
