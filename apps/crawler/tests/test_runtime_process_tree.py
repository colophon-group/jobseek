"""Deterministic process-tree resource sampler tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime_cost.process_tree import ProcessTreeSampler, parse_proc_stat


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


def test_sampler_accumulates_nested_descendants_and_preserves_peak(tmp_path: Path) -> None:
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
        clock_ticks_per_second=100,
        page_size_bytes=4096,
    )

    first = sampler.sample()
    assert first.descendant_count == 2
    assert first.descendant_cpu_delta_seconds == pytest.approx(1.5)
    assert first.process_tree_rss_bytes == 60 * 4096
    assert first.process_tree_peak_rss_bytes == 60 * 4096

    # PID 12 exits and PID 13 appears. The CPU already observed for PID 12 is
    # retained by the Prometheus counter; only new deltas are returned.
    _snapshot(
        tmp_path,
        {
            10: _stat(10, 1, rss_pages=10),
            11: _stat(11, 10, user_ticks=125, start_time_ticks=11, rss_pages=20),
            13: _stat(13, 11, user_ticks=10, start_time_ticks=13, rss_pages=5),
        },
    )
    second = sampler.sample()
    assert second.descendant_cpu_delta_seconds == pytest.approx(0.35)
    assert second.process_tree_rss_bytes == 35 * 4096
    assert second.process_tree_peak_rss_bytes == 60 * 4096


def test_sampler_treats_reused_pid_as_new_start_time_identity(tmp_path: Path) -> None:
    _snapshot(
        tmp_path,
        {
            20: _stat(20, 1, rss_pages=1),
            21: _stat(21, 20, user_ticks=80, start_time_ticks=100, rss_pages=1),
        },
    )
    sampler = ProcessTreeSampler(
        root_pid=20,
        proc_root=tmp_path,
        clock_ticks_per_second=100,
        page_size_bytes=4096,
    )
    assert sampler.sample().descendant_cpu_delta_seconds == pytest.approx(0.8)

    _snapshot(
        tmp_path,
        {
            20: _stat(20, 1, rss_pages=1),
            21: _stat(21, 20, user_ticks=10, start_time_ticks=200, rss_pages=1),
        },
    )
    assert sampler.sample().descendant_cpu_delta_seconds == pytest.approx(0.1)


def test_sampler_fails_closed_when_root_is_missing(tmp_path: Path) -> None:
    _snapshot(tmp_path, {30: _stat(30, 1)})
    sampler = ProcessTreeSampler(root_pid=31, proc_root=tmp_path)

    with pytest.raises(RuntimeError, match="root process is absent"):
        sampler.sample()
