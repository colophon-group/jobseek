"""Regression tests for monitor allocator reclamation (#5102)."""

from __future__ import annotations

from src.workers.monitor_memory import reclaim_process_memory


def test_reclaim_process_memory_measures_collection_and_trim(monkeypatch):
    import src.workers.monitor_memory as memory

    rss_values = iter([500, 300])
    cgroup_values = iter([700, 450])
    monkeypatch.setattr(memory, "process_rss_bytes", lambda: next(rss_values))
    monkeypatch.setattr(memory, "cgroup_memory_bytes", lambda: next(cgroup_values))
    monkeypatch.setattr(memory.gc, "collect", lambda: 7)
    monkeypatch.setattr(memory, "_malloc_trim", lambda: True)

    result = reclaim_process_memory()

    assert result.collected_objects == 7
    assert result.malloc_trimmed is True
    assert result.rss_before_bytes == 500
    assert result.rss_after_bytes == 300
    assert result.rss_reclaimed_bytes == 200
    assert result.cgroup_before_bytes == 700
    assert result.cgroup_after_bytes == 450
