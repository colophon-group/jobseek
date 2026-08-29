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
            10: _stat(10, 1, rss_pages=10),
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
    )

    first = sampler.sample()
    assert first.descendant_count == 2
    assert first.process_tree_cpu_delta_seconds == 0
    assert first.process_tree_rss_bytes == 60 * 4096

    # PID 12 exits and PID 13 appears. Cgroup accounting retains all CPU used
    # by the exited process instead of depending on a final /proc observation.
    _snapshot(
        tmp_path,
        {
            10: _stat(10, 1, rss_pages=10),
            11: _stat(11, 10, user_ticks=125, start_time_ticks=11, rss_pages=20),
            13: _stat(13, 11, user_ticks=10, start_time_ticks=13, rss_pages=5),
        },
    )
    _cgroup_cpu(tmp_path, 1_850_000)
    second = sampler.sample()
    assert second.process_tree_cpu_delta_seconds == pytest.approx(0.35)
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
    assert sampler.sample().process_tree_cpu_delta_seconds == 0

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


def test_sampler_fails_closed_when_root_is_missing(tmp_path: Path) -> None:
    _snapshot(tmp_path, {30: _stat(30, 1)})
    sampler = ProcessTreeSampler(root_pid=31, proc_root=tmp_path)

    with pytest.raises(RuntimeError, match="root process is absent"):
        sampler.sample()


def test_sampling_loop_records_missed_intervals() -> None:
    class FakeSampler:
        def sample(self) -> ProcessTreeSample:
            return ProcessTreeSample(0, 1, 0)

    class ThreeIterations:
        def __init__(self) -> None:
            self.checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            return self.checks > 3

        def wait(self, _seconds: float) -> None:
            pass

    gaps: list[int] = []
    moments = iter((0.0, 0.5, 2.0))
    run_process_tree_sampler(
        FakeSampler(),  # type: ignore[arg-type]
        interval_seconds=0.5,
        stop_event=ThreeIterations(),  # type: ignore[arg-type]
        observe=lambda _sample: None,
        record_failure=lambda: None,
        record_gap=gaps.append,
        monotonic=lambda: next(moments),
    )

    assert gaps == [2]
