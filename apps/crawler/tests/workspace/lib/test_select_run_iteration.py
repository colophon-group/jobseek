"""Multi-config iteration test (mirrors §3.1 ``configure-board`` instructions).

Sequence:

1. ``select_monitor(name='cfg-1', config=A)``
2. ``run_monitor(name='cfg-1')`` — unsatisfactory (stub returns 0 URLs)
3. ``select_monitor(name='cfg-2', config=B)``
4. ``run_monitor(name='cfg-2')`` — satisfactory (stub returns rich data)
5. ``feedback(verdict='good')``
6. assert active config is ``cfg-2`` and feedback is recorded against it,
   while ``cfg-1`` remains untouched.

The test exercises the composition pattern documented in
``apps/crawler/src/workspace/lib/README.md``: select stores into the KV,
the caller reads back to build a ``BoardConfigState``, and ``run_monitor``
operates on the snapshot.

``monitor_one`` and ``async_playwright`` are stubbed so the test runs
hermetically without network access.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workspace.lib import (
    BoardConfigState,
    InMemoryClaimKV,
    feedback,
    run_monitor,
    select_monitor,
)


@dataclass
class _FakeMonitorResult:
    urls: set[str] = field(default_factory=set)
    jobs_by_url: dict | None = None
    filtered_count: int = 0


@dataclass
class _Job:
    title: str | None
    description: str | None
    locations: list[str] | None = None


@contextmanager
def _patched_run_with(monitor_result: _FakeMonitorResult):
    """Patch the lazy imports inside ``run_monitor`` so the body executes."""
    with (
        patch("playwright.async_api.async_playwright") as pw_factory,
        patch("src.shared.http.create_logging_http_client") as http_factory,
        patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as monitor_one,
    ):
        pw_ctx = AsyncMock()
        pw_ctx.__aenter__.return_value = MagicMock(name="pw")
        pw_ctx.__aexit__.return_value = False
        pw_factory.return_value = pw_ctx

        http_client = AsyncMock()
        http_client.aclose = AsyncMock()
        http_factory.return_value = (http_client, [])

        monitor_one.return_value = monitor_result
        yield monitor_one


def _state_for(slot: dict) -> BoardConfigState:
    return BoardConfigState(
        board_url="https://example.com",
        monitor_type=slot["monitor_type"],
        monitor_config=dict(slot["monitor_config"]),
    )


@pytest.mark.asyncio
async def test_multi_config_iteration_select_run_feedback():
    kv = InMemoryClaimKV()

    # 1. Select cfg-1.
    await select_monitor(kv, "sitemap", "cfg-1", {"depth": 1})
    assert await kv.get_active() == "cfg-1"

    # 2. Run monitor for cfg-1 — stubbed to return zero URLs (unsatisfactory).
    cfg1_slot = await kv.get("cfg-1")
    cfg1_state = _state_for(cfg1_slot)
    with _patched_run_with(_FakeMonitorResult(urls=set(), jobs_by_url=None)) as mo:
        cfg1_result = await run_monitor(cfg1_state, config_name="cfg-1")
        # Verify the lookup arguments came from cfg-1.
        assert mo.call_args.args[1] == "sitemap"
        assert mo.call_args.args[2] == {"depth": 1}
    assert cfg1_result.urls == []
    # The agent inspects this and decides the config is unsatisfactory.

    # 3. Select cfg-2.
    await select_monitor(kv, "greenhouse", "cfg-2", {"slug": "acme"})
    assert await kv.get_active() == "cfg-2"

    # 4. Run monitor for cfg-2 — stubbed to return rich data (satisfactory).
    cfg2_slot = await kv.get("cfg-2")
    cfg2_state = _state_for(cfg2_slot)
    rich = {
        "https://example.com/jobs/a": _Job(title="A", description="desc-a"),
        "https://example.com/jobs/b": _Job(title="B", description="desc-b"),
    }
    with _patched_run_with(_FakeMonitorResult(urls=set(rich.keys()), jobs_by_url=rich)) as mo:
        cfg2_result = await run_monitor(cfg2_state, config_name="cfg-2")
        assert mo.call_args.args[1] == "greenhouse"
        assert mo.call_args.args[2] == {"slug": "acme"}
    assert len(cfg2_result.urls) == 2
    assert cfg2_result.has_rich_data is True

    # 5. feedback(verdict='good') — recorded against cfg-2 because it's active.
    fb_result = await feedback(
        kv,
        verdict="good",
        per_field={
            "title": {"quality": "clean"},
            "description": {"quality": "clean"},
        },
    )
    assert fb_result.name == "cfg-2"
    assert fb_result.verdict == "good"

    # 6. Assertions.
    assert await kv.get_active() == "cfg-2"

    cfg1_after = await kv.get("cfg-1")
    cfg2_after = await kv.get("cfg-2")
    assert "feedback" not in cfg1_after
    assert cfg2_after["feedback"]["verdict"] == "good"

    # Sanity: cfg-1's monitor config is unchanged.
    assert cfg1_after["monitor_type"] == "sitemap"
    assert cfg1_after["monitor_config"] == {"depth": 1}
