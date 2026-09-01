"""Deterministic process-tree resource sampler tests."""

from __future__ import annotations

import ctypes
import json
import math
import multiprocessing
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from src.runtime_cost.process_tree import (
    SAMPLER_STRICT_TIMING_LIMIT_SECONDS,
    ProcessTreeSample,
    ProcessTreeSampler,
    ProcessTreeSamplerProcess,
    TimingHistogramSnapshot,
    _decode_sampler_snapshot,
    _encode_sampler_snapshot,
    _SamplerChild,
    _SamplerChildAccumulator,
    _SamplerChildSnapshot,
    _TimingHistogram,
    parse_proc_stat,
    run_process_tree_sampler,
)


def _stat(
    pid: int,
    parent_pid: int,
    *,
    user_ticks: int = 0,
    system_ticks: int = 0,
    start_time_ticks: int = 1,
    rss_pages: int = 0,
    command: str = "crawler",
) -> str:
    fields = ["0"] * 22
    fields[0] = "S"
    fields[1] = str(parent_pid)
    fields[11] = str(user_ticks)
    fields[12] = str(system_ticks)
    fields[19] = str(start_time_ticks)
    fields[21] = str(rss_pages)
    return f"{pid} ({command}) {' '.join(fields)}\n"


def _snapshot(proc_root: Path, records: dict[int, str]) -> None:
    for child in proc_root.iterdir():
        if not child.name.isdecimal():
            continue
        stat = child / "stat"
        if stat.exists():
            stat.unlink()
        child.rmdir()
    for pid, raw in records.items():
        process = proc_root / str(pid)
        process.mkdir()
        (process / "stat").write_text(raw)


def _cgroup_cpu(tmp_path: Path, usage_usec: int) -> Path:
    cpu_stat = tmp_path / "cpu.stat"
    cpu_stat.write_text(f"usage_usec {usage_usec}\nuser_usec 0\nsystem_usec 0\n")
    return cpu_stat


def _sample(sequence: int, observed_at: float, interval_seconds: float) -> ProcessTreeSample:
    return ProcessTreeSample(
        observation_sequence=sequence,
        observation_monotonic_seconds=observed_at,
        observation_unixtime_seconds=1_800_000_000 + observed_at,
        interval_seconds=interval_seconds,
        root_cpu_seconds=1,
        process_tree_cpu_seconds=2,
        process_tree_cpu_delta_seconds=0.1,
        root_rss_bytes=1,
        process_tree_rss_bytes=2,
        descendant_count=1,
    )


class _VirtualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _VirtualStopEvent:
    def __init__(
        self,
        clock: _VirtualClock,
        *,
        oversleep_seconds: list[float] | None = None,
    ) -> None:
        self.clock = clock
        self.waits: list[float] = []
        self.stopped = False
        self.oversleep_seconds = iter(oversleep_seconds or [])

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.waits.append(timeout)
        self.clock.advance(timeout + next(self.oversleep_seconds, 0.0))
        return self.stopped


class _WorkingSampler:
    def __init__(
        self,
        clock: _VirtualClock,
        *,
        interval_seconds: float,
        work_seconds: list[float],
        errors: set[int] | None = None,
    ) -> None:
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.work_seconds = iter(work_seconds)
        self.errors = errors or set()
        self.attempts = 0
        self.started_at: list[float] = []

    def sample(self) -> ProcessTreeSample:
        self.attempts += 1
        self.started_at.append(self.clock.monotonic())
        self.clock.advance(next(self.work_seconds))
        if self.attempts in self.errors:
            raise RuntimeError("synthetic collection failure")
        return _sample(self.attempts, self.clock.monotonic(), self.interval_seconds)


def _run_virtual_sampler(
    sampler: _WorkingSampler,
    *,
    observations: int,
    oversleep_seconds: list[float] | None = None,
) -> tuple[list[ProcessTreeSample], list[tuple[str, int]], int, _VirtualStopEvent]:
    stop_event = _VirtualStopEvent(
        sampler.clock,
        oversleep_seconds=oversleep_seconds,
    )
    observed: list[ProcessTreeSample] = []
    gaps: list[tuple[str, int]] = []
    failures = 0

    def observe(sample: ProcessTreeSample) -> None:
        observed.append(sample)
        if len(observed) == observations:
            stop_event.set()

    def record_failure() -> None:
        nonlocal failures
        failures += 1

    run_process_tree_sampler(
        sampler,  # type: ignore[arg-type]
        interval_seconds=sampler.interval_seconds,
        stop_event=stop_event,  # type: ignore[arg-type]
        observe=observe,
        record_failure=record_failure,
        record_gap=lambda reason, count: gaps.append((reason, count)),
        monotonic=sampler.clock.monotonic,
    )
    return observed, gaps, failures, stop_event


def test_parse_proc_stat_accepts_spaces_and_parentheses_in_command() -> None:
    parsed = parse_proc_stat(
        _stat(
            42,
            7,
            user_ticks=120,
            system_ticks=30,
            start_time_ticks=999,
            rss_pages=64,
            command="chromium (renderer) worker",
        )
    )

    assert parsed.pid == 42
    assert parsed.parent_pid == 7
    assert parsed.cpu_ticks == 150
    assert parsed.start_time_ticks == 999
    assert parsed.rss_pages == 64


def test_sampler_uses_cgroup_cpu_and_current_nested_rss(tmp_path: Path) -> None:
    _snapshot(
        tmp_path,
        {
            10: _stat(10, 1, user_ticks=100, rss_pages=10),
            11: _stat(11, 10, user_ticks=100, start_time_ticks=11, rss_pages=20),
            12: _stat(12, 11, system_ticks=50, start_time_ticks=12, rss_pages=30),
            99: _stat(99, 1, user_ticks=5000, rss_pages=1000),
        },
    )
    sampler = ProcessTreeSampler(
        root_pid=10,
        proc_root=tmp_path,
        cgroup_cpu_stat_path=_cgroup_cpu(tmp_path, 1_500_000),
        clock_ticks_per_second=100,
        page_size_bytes=4096,
        interval_seconds=0.5,
        monotonic=iter((10.0, 10.5)).__next__,
        wall_clock=iter((1_800_000_010.0, 1_800_000_010.5)).__next__,
    )

    first = sampler.sample()
    assert first.observation_sequence == 1
    assert first.observation_monotonic_seconds == 10
    assert first.observation_unixtime_seconds == 1_800_000_010
    assert first.interval_seconds == 0.5
    assert first.root_cpu_seconds == 1
    assert first.process_tree_cpu_seconds == 1.5
    assert first.descendant_count == 2
    assert first.process_tree_cpu_delta_seconds == 1.5
    assert first.root_rss_bytes == 10 * 4096
    assert first.process_tree_rss_bytes == 60 * 4096

    # PID 12 exits and PID 13 appears. Cgroup accounting retains all CPU used
    # by the exited process instead of depending on a final /proc observation.
    _snapshot(
        tmp_path,
        {
            10: _stat(10, 1, user_ticks=120, rss_pages=10),
            11: _stat(11, 10, user_ticks=125, start_time_ticks=11, rss_pages=20),
            13: _stat(13, 11, user_ticks=10, start_time_ticks=13, rss_pages=5),
        },
    )
    _cgroup_cpu(tmp_path, 1_850_000)
    second = sampler.sample()
    assert second.observation_sequence == 2
    assert second.observation_monotonic_seconds == 10.5
    assert second.observation_unixtime_seconds == 1_800_000_010.5
    assert second.root_cpu_seconds == pytest.approx(1.2)
    assert second.process_tree_cpu_seconds == pytest.approx(1.85)
    assert second.process_tree_cpu_delta_seconds == pytest.approx(0.35)
    assert second.root_rss_bytes == 10 * 4096
    assert second.process_tree_rss_bytes == 35 * 4096


def test_sampler_accounts_for_child_born_and_exited_between_polls(tmp_path: Path) -> None:
    _snapshot(
        tmp_path,
        {
            20: _stat(20, 1, rss_pages=1),
        },
    )
    sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=tmp_path,
        cgroup_cpu_stat_path=_cgroup_cpu(tmp_path, 2_000_000),
        clock_ticks_per_second=100,
        page_size_bytes=4096,
    )
    assert sampler.sample().process_tree_cpu_delta_seconds == 2

    # No child is visible in either snapshot, but the container counter has
    # retained CPU consumed by a short-lived child between them.
    _snapshot(
        tmp_path,
        {
            20: _stat(20, 1, rss_pages=1),
        },
    )
    _cgroup_cpu(tmp_path, 3_000_000)
    second = sampler.sample()
    assert second.descendant_count == 0
    assert second.process_tree_cpu_delta_seconds == pytest.approx(1.0)


def test_sampler_fails_closed_when_cgroup_cpu_regresses(tmp_path: Path) -> None:
    _snapshot(tmp_path, {20: _stat(20, 1, rss_pages=1)})
    sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=tmp_path,
        cgroup_cpu_stat_path=_cgroup_cpu(tmp_path, 2_000_000),
    )
    sampler.sample()
    _cgroup_cpu(tmp_path, 1_000_000)

    with pytest.raises(RuntimeError, match="counter regressed"):
        sampler.sample()


def test_sampler_withholds_invalid_root_tree_cpu_generation(tmp_path: Path) -> None:
    _snapshot(tmp_path, {20: _stat(20, 1, user_ticks=100, rss_pages=2)})
    sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=tmp_path,
        cgroup_cpu_stat_path=_cgroup_cpu(tmp_path, 500_000),
        clock_ticks_per_second=100,
        page_size_bytes=4096,
    )

    with pytest.raises(RuntimeError, match="tree CPU is lower than root CPU"):
        sampler.sample()

    _cgroup_cpu(tmp_path, 1_500_000)
    recovered = sampler.sample()
    assert recovered.observation_sequence == 1
    assert recovered.process_tree_cpu_delta_seconds == 1.5
    assert recovered.process_tree_cpu_seconds >= recovered.root_cpu_seconds
    assert recovered.process_tree_rss_bytes >= recovered.root_rss_bytes


def test_sampler_fails_closed_when_root_is_missing(tmp_path: Path) -> None:
    _snapshot(tmp_path, {30: _stat(30, 1)})
    sampler = ProcessTreeSampler(root_pid=31, proc_root=tmp_path)

    with pytest.raises(RuntimeError, match="root process is absent"):
        sampler.sample()


def test_sampler_handles_high_process_count_and_disappearing_proc_entries(
    tmp_path: Path,
) -> None:
    records = {20: _stat(20, 1, user_ticks=100, rss_pages=2)}
    records.update({pid: _stat(pid, 20, user_ticks=1, rss_pages=1) for pid in range(1000, 1600)})
    _snapshot(tmp_path, records)
    # One descendant disappears after enumeration in production. Leaving its
    # directory without stat deterministically exercises the same fail-open
    # proc read while preserving the rest of the complete generation.
    (tmp_path / "1599" / "stat").unlink()
    sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=tmp_path,
        cgroup_cpu_stat_path=_cgroup_cpu(tmp_path, 10_000_000),
        clock_ticks_per_second=100,
        page_size_bytes=4096,
        interval_seconds=0.5,
    )

    started_at = time.monotonic()
    sample = sampler.sample()
    elapsed = time.monotonic() - started_at

    assert sample.descendant_count == 599
    assert sample.root_rss_bytes == 2 * 4096
    assert sample.process_tree_rss_bytes == 601 * 4096
    assert sample.process_tree_cpu_seconds >= sample.root_cpu_seconds
    assert elapsed < sampler.interval_seconds


def test_sampling_loop_is_deadline_anchored_with_nonzero_work() -> None:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=[0.2, 0.2, 0.2],
    )

    observed, gaps, failures, stop_event = _run_virtual_sampler(sampler, observations=3)

    assert len(observed) == 3
    assert sampler.started_at == pytest.approx([0, 0.5, 1.0])
    assert stop_event.waits == pytest.approx([0.3, 0.3])
    assert gaps == []
    assert failures == 0


def test_sampling_loop_counts_exact_overrun_gaps_without_catch_up() -> None:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=[1.2, 0.1],
    )

    observed, gaps, failures, stop_event = _run_virtual_sampler(sampler, observations=2)

    assert len(observed) == 2
    assert sampler.started_at == pytest.approx([0, 1.5])
    assert stop_event.waits == pytest.approx([0.3])
    assert gaps == [("collection_overrun", 2)]
    assert failures == 0


def test_sampling_loop_counts_deadline_boundary_as_skipped() -> None:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=[1.5, 0],
    )

    _, gaps, _, _ = _run_virtual_sampler(sampler, observations=2)

    assert sampler.started_at == pytest.approx([0, 2])
    assert gaps == [("collection_overrun", 3)]


def test_sampling_failure_and_overrun_gap_are_accounted_independently() -> None:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=[1.2, 0.1],
        errors={1},
    )

    observed, gaps, failures, _ = _run_virtual_sampler(sampler, observations=1)

    assert len(observed) == 1
    assert sampler.started_at == pytest.approx([0, 1.5])
    assert gaps == [("collection_overrun", 2)]
    assert failures == 1


def test_sampling_loop_classifies_wake_lateness_and_collection_overrun_exactly() -> None:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=[0.1, 0.4],
    )

    observed, gaps, failures, _ = _run_virtual_sampler(
        sampler,
        observations=2,
        oversleep_seconds=[0.8],
    )

    assert len(observed) == 2
    assert sampler.started_at == pytest.approx([0, 1.3])
    assert gaps == [("scheduler_late", 1), ("collection_overrun", 1)]
    assert sum(count for _reason, count in gaps) == 2
    assert failures == 0


def _run_adversarial_cycles(
    *,
    oversleep_seconds: list[float],
    collection_seconds: list[float],
    handoff_seconds: list[float],
    handoff_errors: set[int] | None = None,
) -> tuple[
    list[float],
    list[tuple[str, int]],
    list[tuple[str, float]],
    int,
]:
    clock = _VirtualClock()
    sampler = _WorkingSampler(
        clock,
        interval_seconds=0.5,
        work_seconds=collection_seconds,
    )
    stop_event = _VirtualStopEvent(clock, oversleep_seconds=oversleep_seconds)
    gaps: list[tuple[str, int]] = []
    timings: list[tuple[str, float]] = []
    failures = 0
    observations = 0
    handoffs = iter(handoff_seconds)
    handoff_attempt = 0

    def observe(_sample: ProcessTreeSample) -> None:
        nonlocal observations
        observations += 1
        if observations == len(collection_seconds):
            stop_event.set()

    def record_failure() -> None:
        nonlocal failures
        failures += 1

    def flush() -> None:
        nonlocal handoff_attempt
        handoff_attempt += 1
        clock.advance(next(handoffs))
        if handoff_attempt in (handoff_errors or set()):
            raise OSError("synthetic final handoff failure")

    run_process_tree_sampler(
        sampler,  # type: ignore[arg-type]
        interval_seconds=0.5,
        stop_event=stop_event,
        observe=observe,
        record_failure=record_failure,
        record_gap=lambda reason, count: gaps.append((reason, count)),
        record_timing=lambda kind, seconds: timings.append((kind, seconds)),
        flush=flush,
        monotonic=clock.monotonic,
    )
    return sampler.started_at, gaps, timings, failures


@pytest.mark.parametrize(
    (
        "oversleep_seconds",
        "collection_seconds",
        "handoff_seconds",
        "expected_starts",
        "expected_gaps",
        "expected_timing_sums",
    ),
    [
        ([], [0.0, 0.0], [0.0, 0.0], [0.0, 0.5], [], (0.0, 0.0, 0.0)),
        (
            [1.1],
            [0.0, 0.0],
            [0.0, 0.0],
            [0.0, 1.6],
            [("scheduler_late", 2)],
            (1.1, 0.0, 0.0),
        ),
        (
            [],
            [1.1, 0.0],
            [0.0, 0.0],
            [0.0, 1.5],
            [("collection_overrun", 2)],
            (0.0, 1.1, 0.0),
        ),
        (
            [],
            [0.0, 0.0],
            [0.6, 0.0],
            [0.0, 1.0],
            [("collection_overrun", 1)],
            (0.0, 0.0, 0.6),
        ),
        (
            [],
            [0.0, 0.0],
            [1.1, 0.0],
            [0.0, 1.5],
            [("collection_overrun", 2)],
            (0.0, 0.0, 1.1),
        ),
        (
            [0.6],
            [0.0, 0.6],
            [0.0, 0.4],
            [0.0, 1.1],
            [("scheduler_late", 1), ("collection_overrun", 2)],
            (0.6, 0.6, 0.4),
        ),
    ],
)
def test_sampling_loop_attributes_every_deadline_after_complete_handoff(
    oversleep_seconds: list[float],
    collection_seconds: list[float],
    handoff_seconds: list[float],
    expected_starts: list[float],
    expected_gaps: list[tuple[str, int]],
    expected_timing_sums: tuple[float, float, float],
) -> None:
    starts, gaps, timings, failures = _run_adversarial_cycles(
        oversleep_seconds=oversleep_seconds,
        collection_seconds=collection_seconds,
        handoff_seconds=handoff_seconds,
    )

    timing_sums = tuple(
        sum(seconds for observed_kind, seconds in timings if observed_kind == kind)
        for kind in ("wake_lateness", "collection", "handoff")
    )
    assert starts == pytest.approx(expected_starts)
    assert gaps == expected_gaps
    assert sum(count for _reason, count in gaps) == sum(count for _reason, count in expected_gaps)
    assert timing_sums == pytest.approx(expected_timing_sums)
    assert failures == 0


def test_failed_final_handoff_is_timed_and_causally_attributed() -> None:
    starts, gaps, timings, failures = _run_adversarial_cycles(
        oversleep_seconds=[],
        collection_seconds=[0.0, 0.0],
        handoff_seconds=[1.1, 0.0],
        handoff_errors={1},
    )

    assert starts == pytest.approx([0.0, 1.5])
    assert gaps == [("collection_overrun", 2)]
    assert sum(count for reason, count in gaps if reason == "scheduler_late") == 0
    assert [seconds for kind, seconds in timings if kind == "handoff"] == pytest.approx([1.1, 0.0])
    assert failures == 1


class _LongScheduleSampler:
    def __init__(self, clock: _VirtualClock, interval_seconds: float) -> None:
        self.clock = clock
        self.interval_seconds = interval_seconds
        self.attempts = 0
        self.first_started_at: float | None = None
        self.last_started_at: float | None = None

    def sample(self) -> ProcessTreeSample:
        self.attempts += 1
        started_at = self.clock.monotonic()
        if self.first_started_at is None:
            self.first_started_at = started_at
        self.last_started_at = started_at
        return _sample(self.attempts, started_at, self.interval_seconds)


@pytest.mark.parametrize("interval_seconds", [0.5, 1.0])
def test_sampling_loop_conserves_simulated_86400_second_schedule(
    interval_seconds: float,
) -> None:
    clock = _VirtualClock()
    sampler = _LongScheduleSampler(clock, interval_seconds)
    stop_event = _VirtualStopEvent(clock)
    expected_samples = int(86_400 / interval_seconds)
    observed = 0
    gaps: list[tuple[str, int]] = []
    timings = {
        "wake_lateness": _TimingHistogram(),
        "collection": _TimingHistogram(),
        "handoff": _TimingHistogram(),
    }

    def observe(_sample: ProcessTreeSample) -> None:
        nonlocal observed
        observed += 1
        if observed == expected_samples:
            stop_event.set()

    run_process_tree_sampler(
        sampler,  # type: ignore[arg-type]
        interval_seconds=interval_seconds,
        stop_event=stop_event,
        observe=observe,
        record_failure=_ignore_call,
        record_gap=lambda reason, count: gaps.append((reason, count)),
        record_timing=lambda phase, seconds: timings[phase].observe(seconds),
        monotonic=clock.monotonic,
    )

    assert observed == expected_samples
    assert sampler.attempts == expected_samples
    assert sampler.first_started_at == 0
    assert sampler.last_started_at == pytest.approx(86_400 - interval_seconds)
    assert gaps == []
    assert all(item.snapshot().count == expected_samples for item in timings.values())
    assert all(item.snapshot().limit_violations == 0 for item in timings.values())


def test_strict_timing_limit_uses_exact_float_boundary_for_every_phase() -> None:
    sender, receiver = socket.socketpair(type=socket.SOCK_DGRAM)
    accumulator = _SamplerChildAccumulator(sender)
    try:
        for phase in ("wake_lateness", "collection", "handoff"):
            accumulator.record_timing(
                phase,  # type: ignore[arg-type]
                math.nextafter(SAMPLER_STRICT_TIMING_LIMIT_SECONDS, 0.0),
            )
            accumulator.record_timing(phase, SAMPLER_STRICT_TIMING_LIMIT_SECONDS)  # type: ignore[arg-type]
            accumulator.record_timing(
                phase,  # type: ignore[arg-type]
                math.nextafter(SAMPLER_STRICT_TIMING_LIMIT_SECONDS, math.inf),
            )
        snapshot = accumulator.snapshot()
    finally:
        sender.close()
        receiver.close()

    for histogram in (
        snapshot.wake_lateness,
        snapshot.collection_duration,
        snapshot.handoff_duration,
    ):
        assert histogram.count == 3
        assert histogram.limit_violations == 2


def test_timing_violation_counts_add_and_reject_regression() -> None:
    earlier_histogram = _TimingHistogram()
    earlier_histogram.observe(SAMPLER_STRICT_TIMING_LIMIT_SECONDS)
    earlier = earlier_histogram.snapshot()
    later_histogram = _TimingHistogram()
    later_histogram.observe(SAMPLER_STRICT_TIMING_LIMIT_SECONDS)
    later_histogram.observe(SAMPLER_STRICT_TIMING_LIMIT_SECONDS)
    later = later_histogram.snapshot()

    combined = earlier + later

    assert combined.limit_violations == 3
    assert combined.count == 3
    assert later.contains(earlier)
    assert not replace(later, limit_violations=0).contains(earlier)


def _child_snapshot(sequence: int = 1) -> _SamplerChildSnapshot:
    empty = TimingHistogramSnapshot.empty()
    return _SamplerChildSnapshot(
        emitted_monotonic_seconds=10.0,
        sample=_sample(sequence, 10.0, 0.5),
        successes=sequence,
        failures=0,
        scheduler_late_gaps=0,
        collection_overrun_gaps=0,
        wake_lateness=empty,
        collection_duration=empty,
        handoff_duration=empty,
    )


def test_sampler_ipc_rejects_partial_frame_without_replacing_complete_snapshot() -> None:
    expected = _child_snapshot()
    encoded = _encode_sampler_snapshot(expected)

    assert _decode_sampler_snapshot(encoded) == expected
    for truncated in (encoded[:1], encoded[:-1], encoded[: len(encoded) // 2]):
        with pytest.raises(ValueError, match="complete JSON"):
            _decode_sampler_snapshot(truncated)
    assert _decode_sampler_snapshot(encoded) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"", "size is invalid"),
        (b"x" * (64 * 1024 + 1), "size is invalid"),
        (b"[]", "must be an object"),
        (b'{"version":1}', "fields are invalid"),
        (
            lambda: json.dumps(
                {
                    **json.loads(_encode_sampler_snapshot(_child_snapshot())),
                    "version": 1,
                }
            ).encode(),
            "version is unsupported",
        ),
        (
            lambda: json.dumps(
                {
                    **json.loads(_encode_sampler_snapshot(_child_snapshot())),
                    "successes": "1",
                }
            ).encode(),
            "must be a non-negative integer",
        ),
    ],
)
def test_sampler_ipc_rejects_malformed_frame_matrix(
    raw: bytes | Callable[[], bytes],
    message: str,
) -> None:
    payload = raw() if callable(raw) else raw

    with pytest.raises(ValueError, match=message):
        _decode_sampler_snapshot(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda histogram: histogram.pop("limit_violations"), "fields are invalid"),
        (lambda histogram: histogram.update(extra=0), "fields are invalid"),
        (
            lambda histogram: histogram.update(limit_violations=-1),
            "must be a non-negative integer",
        ),
        (
            lambda histogram: histogram.update(limit_violations=0.5),
            "must be a non-negative integer",
        ),
    ],
)
def test_sampler_ipc_rejects_malformed_timing_violation_field(
    mutation: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    payload = json.loads(_encode_sampler_snapshot(_child_snapshot()))
    mutation(payload["timings"]["collection_duration"])

    with pytest.raises(ValueError, match=message):
        _decode_sampler_snapshot(json.dumps(payload).encode())


def test_sampler_ipc_rejects_inconsistent_and_regressing_sample_times() -> None:
    earlier = _child_snapshot()

    with pytest.raises(ValueError, match="cannot be observed after"):
        replace(earlier, emitted_monotonic_seconds=9.0)

    regressing = replace(
        _child_snapshot(sequence=2),
        emitted_monotonic_seconds=10.5,
        sample=_sample(2, 9.5, 0.5),
    )
    assert not regressing.contains(earlier)

    violated = _TimingHistogram()
    violated.observe(SAMPLER_STRICT_TIMING_LIMIT_SECONDS)
    assert not replace(
        _child_snapshot(sequence=2),
        emitted_monotonic_seconds=10.5,
        wake_lateness=replace(violated.snapshot(), limit_violations=0),
    ).contains(replace(earlier, wake_lateness=violated.snapshot()))


def test_sampler_child_rollover_preserves_timing_violation_offsets() -> None:
    violated = _TimingHistogram()
    violated.observe(SAMPLER_STRICT_TIMING_LIMIT_SECONDS)
    histogram = violated.snapshot()
    child = _SamplerChild(
        process=_AlwaysAliveProcess(),  # type: ignore[arg-type]
        receiver=None,  # type: ignore[arg-type]
        stop_sender=None,  # type: ignore[arg-type]
        started_monotonic_seconds=0.0,
        snapshot=replace(
            _child_snapshot(),
            wake_lateness=histogram,
            collection_duration=histogram,
            handoff_duration=histogram,
        ),
    )
    supervisor = ProcessTreeSamplerProcess.__new__(ProcessTreeSamplerProcess)
    supervisor._success_offset = 0
    supervisor._failure_offset = 0
    supervisor._scheduler_late_offset = 0
    supervisor._collection_overrun_offset = 0
    supervisor._wake_lateness_offset = TimingHistogramSnapshot.empty()
    supervisor._collection_duration_offset = TimingHistogramSnapshot.empty()
    supervisor._handoff_duration_offset = TimingHistogramSnapshot.empty()

    supervisor._roll_child_into_offsets(child)

    assert supervisor._wake_lateness_offset.limit_violations == 1
    assert supervisor._collection_duration_offset.limit_violations == 1
    assert supervisor._handoff_duration_offset.limit_violations == 1


class _AlwaysAliveProcess:
    def is_alive(self) -> bool:
        return True


class _FrameReceiver:
    def __init__(self, frames: list[tuple[bytes, int]]) -> None:
        self._frames = iter(frames)

    def recvmsg(self, _max_bytes: int):
        try:
            raw, flags = next(self._frames)
        except StopIteration as exc:
            raise BlockingIOError from exc
        return raw, [], flags, None


def test_parent_rejects_future_and_regressing_frames_without_postponing_stale_recovery() -> None:
    clock = _VirtualClock()
    clock.advance(10.0)
    valid = _child_snapshot()
    future = replace(
        _child_snapshot(sequence=2),
        emitted_monotonic_seconds=1_000.0,
        sample=_sample(2, 10.5, 0.5),
    )
    regressing = replace(
        _child_snapshot(sequence=2),
        emitted_monotonic_seconds=10.5,
        sample=_sample(2, 9.5, 0.5),
    )
    child = _SamplerChild(
        process=_AlwaysAliveProcess(),  # type: ignore[arg-type]
        receiver=_FrameReceiver(  # type: ignore[arg-type]
            [
                (_encode_sampler_snapshot(valid), 0),
                (b"truncated", socket.MSG_TRUNC),
                (_encode_sampler_snapshot(future), 0),
                (_encode_sampler_snapshot(regressing), 0),
            ]
        ),
        stop_sender=None,  # type: ignore[arg-type]
        started_monotonic_seconds=clock.monotonic(),
    )
    supervisor = ProcessTreeSamplerProcess.__new__(ProcessTreeSamplerProcess)
    supervisor.interval_seconds = 0.5
    supervisor._monotonic = clock.monotonic
    supervisor._local_failures = 0
    supervisor._last_sample = None
    supervisor._stale_reported = False
    supervisor._stale_after_seconds = 5.0

    supervisor._drain_frames(child)

    assert supervisor._local_failures == 3
    assert child.snapshot == valid
    assert supervisor._last_sample == valid.sample
    assert child.last_valid_frame_received_monotonic_seconds == 10.0

    clock.advance(5.5)
    restarted: list[_SamplerChild] = []
    supervisor._restart_child = lambda stale_child: restarted.append(stale_child)  # type: ignore[method-assign]
    supervisor._restart_if_unhealthy(child, clock.monotonic())

    assert restarted == [child]
    assert supervisor._local_failures == 4


def _wait_for_process_snapshot(
    sampler_process: ProcessTreeSamplerProcess,
    predicate,
    *,
    timeout: float = 8.0,
):
    deadline = time.monotonic() + timeout
    latest = sampler_process.snapshot()
    while not predicate(latest) and time.monotonic() < deadline:
        time.sleep(0.02)
        latest = sampler_process.snapshot()
    assert predicate(latest), latest
    return latest


def _fake_process_tree_paths(tmp_path: Path) -> tuple[Path, Path]:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _snapshot(proc_root, {20: _stat(20, 1, user_ticks=10, rss_pages=2)})
    return proc_root, _cgroup_cpu(tmp_path, 1_000_000)


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process and libc behavior")
def test_sampler_process_keeps_half_second_cadence_during_parent_gil_hold(
    tmp_path: Path,
) -> None:
    proc_root, cgroup_cpu_path = _fake_process_tree_paths(tmp_path)
    sampler_process = ProcessTreeSamplerProcess(
        root_pid=20,
        proc_root=proc_root,
        cgroup_cpu_stat_path=cgroup_cpu_path,
        interval_seconds=0.5,
        process_context=multiprocessing.get_context("spawn"),
    )
    thread_stop = threading.Event()
    thread_started = threading.Event()
    thread_gaps: list[tuple[str, int]] = []
    thread_sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=proc_root,
        cgroup_cpu_stat_path=cgroup_cpu_path,
        interval_seconds=0.5,
    )

    def observe_thread(_sample: ProcessTreeSample) -> None:
        if thread_started.is_set():
            thread_stop.set()
        thread_started.set()

    old_thread = threading.Thread(
        target=run_process_tree_sampler,
        kwargs={
            "sampler": thread_sampler,
            "interval_seconds": 0.5,
            "stop_event": thread_stop,
            "observe": observe_thread,
            "record_failure": _ignore_call,
            "record_gap": lambda reason, count: thread_gaps.append((reason, count)),
        },
    )
    old_thread_started = False
    try:
        initial = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes >= 1,
        )
        old_thread.start()
        old_thread_started = True
        assert thread_started.wait(timeout=2)

        libc = ctypes.PyDLL(None)
        try:
            usleep = libc.usleep
        except AttributeError:
            pytest.skip("libc does not expose usleep")
        usleep.argtypes = [ctypes.c_uint]
        usleep.restype = ctypes.c_int
        assert usleep(1_100_000) == 0

        process_after_hold = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes >= initial.successes + 2,
        )
        old_thread.join(timeout=2)

        assert process_after_hold.scheduler_late_gaps == 0
        assert process_after_hold.collection_overrun_gaps == 0
        assert not old_thread.is_alive()
        assert sum(count for reason, count in thread_gaps if reason == "scheduler_late") >= 1
    finally:
        thread_stop.set()
        if old_thread_started:
            old_thread.join(timeout=2)
        sampler_process.close()


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process signals")
def test_sampler_process_death_fails_closed_and_restarts_with_monotonic_counters(
    tmp_path: Path,
) -> None:
    proc_root, cgroup_cpu_path = _fake_process_tree_paths(tmp_path)
    sampler_process = ProcessTreeSamplerProcess(
        root_pid=20,
        proc_root=proc_root,
        cgroup_cpu_stat_path=cgroup_cpu_path,
        interval_seconds=0.5,
        process_context=multiprocessing.get_context("spawn"),
    )
    try:
        before = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes >= 1,
        )
        old_pid = sampler_process.child_pid
        assert old_pid is not None
        os.kill(old_pid, signal.SIGKILL)

        restarted = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.starts >= 2 and snapshot.failures >= 1,
        )
        new_pid = sampler_process.child_pid
        assert new_pid is not None and new_pid != old_pid
        recovered = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes > before.successes,
        )

        assert restarted.starts == 2
        assert recovered.starts == 2
        assert recovered.successes > before.successes
        assert recovered.failures >= 1
        assert recovered.sample is not None
        assert recovered.sample.process_tree_cpu_seconds >= recovered.sample.root_cpu_seconds
        assert recovered.sample.process_tree_rss_bytes >= recovered.sample.root_rss_bytes

        second_pid = sampler_process.child_pid
        assert second_pid is not None
        os.kill(second_pid, signal.SIGKILL)
        restarted_again = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.starts >= 3 and snapshot.failures > recovered.failures,
        )
        recovered_again = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes > recovered.successes,
        )

        assert restarted_again.starts == 3
        assert recovered_again.starts == 3
        assert recovered_again.successes > recovered.successes
        assert recovered_again.failures > recovered.failures
        assert recovered_again.scheduler_late_gaps >= recovered.scheduler_late_gaps
        assert recovered_again.collection_overrun_gaps >= recovered.collection_overrun_gaps
        assert recovered_again.wake_lateness.contains(recovered.wake_lateness)
        assert recovered_again.collection_duration.contains(recovered.collection_duration)
        assert recovered_again.handoff_duration.contains(recovered.handoff_duration)
    finally:
        sampler_process.close()

    assert sampler_process.child_pid is None


@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX process signals")
def test_sampler_process_stale_output_fails_closed_and_restarts(tmp_path: Path) -> None:
    proc_root, cgroup_cpu_path = _fake_process_tree_paths(tmp_path)
    sampler_process = ProcessTreeSamplerProcess(
        root_pid=20,
        proc_root=proc_root,
        cgroup_cpu_stat_path=cgroup_cpu_path,
        interval_seconds=0.1,
        stale_after_seconds=0.2,
        process_context=multiprocessing.get_context("spawn"),
    )
    try:
        before = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.successes >= 1,
        )
        old_pid = sampler_process.child_pid
        assert old_pid is not None
        os.kill(old_pid, signal.SIGSTOP)
        time.sleep(0.3)

        restarted = _wait_for_process_snapshot(
            sampler_process,
            lambda snapshot: snapshot.starts == 2 and snapshot.failures >= 1,
        )

        assert restarted.successes >= before.successes
        assert sampler_process.child_pid not in (None, old_pid)
    finally:
        sampler_process.close()


@pytest.mark.parametrize("interval_seconds", [0, -0.5, 1.01])
def test_sampler_rejects_out_of_contract_interval(interval_seconds: float) -> None:
    with pytest.raises(ValueError, match="positive and no greater than one"):
        ProcessTreeSampler(interval_seconds=interval_seconds)


def test_loop_rejects_interval_different_from_sample_metadata() -> None:
    sampler = ProcessTreeSampler(interval_seconds=0.5)

    with pytest.raises(ValueError, match="must equal the sampler interval"):
        run_process_tree_sampler(
            sampler,
            interval_seconds=1,
            stop_event=_NeverStartedEvent(),  # type: ignore[arg-type]
            observe=_ignore_sample,
            record_failure=_ignore_call,
            record_gap=_ignore_gap,
        )


class _NeverStartedEvent:
    def is_set(self) -> bool:
        return True

    def wait(self, _seconds: float) -> bool:
        return True


def _ignore_sample(_sample: ProcessTreeSample) -> None:
    pass


def _ignore_call() -> None:
    pass


def _ignore_gap(_reason: str, _gaps: int) -> None:
    pass
