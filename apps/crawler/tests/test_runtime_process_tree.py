"""Deterministic process-tree resource sampler tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_cost.process_tree import (
    ProcessTreeSample,
    ProcessTreeSampler,
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
    def __init__(self, clock: _VirtualClock) -> None:
        self.clock = clock
        self.waits: list[float] = []
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.clock.advance(seconds)
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
) -> tuple[list[ProcessTreeSample], list[int], int, _VirtualStopEvent]:
    stop_event = _VirtualStopEvent(sampler.clock)
    observed: list[ProcessTreeSample] = []
    gaps: list[int] = []
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
        record_gap=gaps.append,
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
    assert gaps == [2]
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
    assert gaps == [3]


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
    assert gaps == [2]
    assert failures == 1


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


def _ignore_gap(_gaps: int) -> None:
    pass
