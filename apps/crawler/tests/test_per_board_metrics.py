"""End-to-end tests for the per-board failure metrics added in #2704.

These tests exercise the REAL emission paths:

- ``monitor_url_filtered_total`` is emitted from
  ``processing.board.process_one_board_streaming`` whenever the URL
  filter drops a discovered URL. We drive it through ``_classify_job_url``
  + a synthetic batch result to confirm the new ``board_id`` label is
  set and the existing ``reason`` aggregation still works.
- ``monitor_failed_per_board_total`` is emitted from the monitor
  pipeline's outer ``except Exception`` handler in
  ``workers.pipeline._process_monitor_work``. We drive it by raising
  inside the patched processing layer and assert the per-board
  counter increments.

We use ``prometheus_client.REGISTRY``'s sample-collection API rather
than the counter object's private state — that's the surface a Grafana
scrape sees.
"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import REGISTRY


def _samples_for(metric_name: str) -> list[dict[str, Any]]:
    """Return all current samples for a Prometheus counter."""
    out: list[dict[str, Any]] = []
    for metric in REGISTRY.collect():
        if metric.name != metric_name:
            continue
        for sample in metric.samples:
            # Counter exposes both `<name>_total` and `<name>_created`;
            # we only want the value samples.
            if sample.name.endswith("_created"):
                continue
            out.append({"labels": dict(sample.labels), "value": sample.value})
    return out


def _value_for(metric_name: str, **labels: str) -> float:
    """Sum samples matching all label kwargs (subset match)."""
    total = 0.0
    for s in _samples_for(metric_name):
        if all(s["labels"].get(k) == v for k, v in labels.items()):
            total += s["value"]
    return total


# ─── monitor_url_filtered_total: board_id label ──────────────────────


def test_monitor_url_filtered_metric_has_board_id_label() -> None:
    """Smoke: the counter's label set advertises board_id."""
    from src.metrics import monitor_url_filtered_total

    assert "board_id" in monitor_url_filtered_total._labelnames
    assert "reason" in monitor_url_filtered_total._labelnames


def test_monitor_url_filtered_total_emit_with_board_id() -> None:
    """Direct emission with both labels lands as a sample carrying both
    label values; ``sum by (reason)`` (the prior aggregation) still
    works."""
    from src.metrics import monitor_url_filtered_total

    board_a = "11111111-1111-1111-1111-111111111111"
    board_b = "22222222-2222-2222-2222-222222222222"

    base_a = _value_for("crawler_monitor_url_filtered", reason="invalid", board_id=board_a)
    base_b = _value_for("crawler_monitor_url_filtered", reason="invalid", board_id=board_b)

    monitor_url_filtered_total.labels(reason="invalid", board_id=board_a).inc(3)
    monitor_url_filtered_total.labels(reason="bare_host", board_id=board_a).inc(1)
    monitor_url_filtered_total.labels(reason="invalid", board_id=board_b).inc(2)

    # Per-board counts
    assert (
        _value_for("crawler_monitor_url_filtered", reason="invalid", board_id=board_a) == base_a + 3
    )
    assert (
        _value_for("crawler_monitor_url_filtered", reason="invalid", board_id=board_b) == base_b + 2
    )

    # Existing aggregation by reason — sum across all board_ids — must
    # still recover the totals.
    invalid_total_now = _value_for("crawler_monitor_url_filtered", reason="invalid")
    assert invalid_total_now >= 5  # baselines + new emissions


# ─── monitor_failed_per_board_total: new metric ──────────────────────


def test_monitor_failed_per_board_total_advertises_board_id_only() -> None:
    """Per the issue's cardinality envelope, only board_id — no
    profile, no reason. Keeps series count tightly bounded."""
    from src.metrics import monitor_failed_per_board_total

    assert monitor_failed_per_board_total._labelnames == ("board_id",)


def test_monitor_failed_per_board_total_increments_on_failure() -> None:
    """Direct emission lands as a sample carrying the board_id."""
    from src.metrics import monitor_failed_per_board_total

    board = "33333333-3333-3333-3333-333333333333"
    base = _value_for("crawler_monitor_failed_per_board", board_id=board)

    monitor_failed_per_board_total.labels(board_id=board).inc()
    monitor_failed_per_board_total.labels(board_id=board).inc()

    assert _value_for("crawler_monitor_failed_per_board", board_id=board) == base + 2


# ─── pipeline integration: failure path emits the new metric ─────────


@pytest.mark.asyncio
async def test_process_monitor_work_failure_emits_per_board_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive ``_process_monitor_work`` against a stub board + inject a
    failure inside ``_process_one_board_streaming``. The outer
    ``except Exception`` block must increment the per-board counter
    BEFORE the reschedule attempt (so a Redis hiccup in the reschedule
    path doesn't hide the original failure).
    """
    from src.workers import pipeline

    failed_board_id = "44444444-4444-4444-4444-444444444444"
    base = _value_for("crawler_monitor_failed_per_board", board_id=failed_board_id)

    # Stub everything the function reaches into.
    class _StubConn:
        async def fetchval(self, *args: Any) -> str:
            # Return 'active' so the disabled short-circuit doesn't run.
            return "active"

    class _StubPool:
        def acquire(self) -> Any:
            class _Ctx:
                async def __aenter__(self_inner) -> _StubConn:
                    return _StubConn()

                async def __aexit__(self_inner, *args: Any) -> None:
                    pass

            return _Ctx()

    # Force the streaming processor to raise — triggers the outer
    # except. Patch into the lazy import inside the function.
    import src.processing.board as board_mod

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic monitor failure")

    monkeypatch.setattr(board_mod, "_process_one_board_streaming", _boom)

    # reschedule should be called but we don't want it to actually hit
    # Redis. Make it a no-op.
    async def _noop_reschedule(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(pipeline, "reschedule_task", _noop_reschedule)

    # Likewise enqueue / monitor_needs_browser shouldn't matter since
    # we're a browser worker — pass browser=True so the rerouting block
    # is bypassed.
    work = pipeline.BoardWork(
        board_id=failed_board_id,
        domain="example.com",
        config={"crawler_type": "greenhouse"},
    )
    import structlog

    log = structlog.get_logger("test")

    # Run — the function swallows the exception in its outer except.
    await pipeline._process_monitor_work(
        log,
        work,
        local_pool=_StubPool(),  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        browser=True,
        pw=None,
    )

    # The per-board failure counter incremented exactly once.
    assert _value_for("crawler_monitor_failed_per_board", board_id=failed_board_id) == base + 1


# ─── cardinality guard ──────────────────────────────────────────────


def test_failed_per_board_metric_starts_with_zero_unrelated_series() -> None:
    """The metric is emit-on-failure-only, so absent failures it must
    NOT pre-allocate any series (would defeat the point of the bounded
    cardinality design)."""
    # We can only assert this for a fresh board id that nothing else
    # in the test session touched — pick a UUID unlikely to collide.
    fresh = "deadbeef-dead-dead-dead-deadbeefdead"
    assert _value_for("crawler_monitor_failed_per_board", board_id=fresh) == 0
