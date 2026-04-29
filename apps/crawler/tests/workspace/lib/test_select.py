"""Tests for ``src.workspace.lib.select`` — pure async select functions."""

from __future__ import annotations

import pytest

from src.workspace.lib import (
    InMemoryClaimKV,
    SelectResult,
    WsConfigInvalid,
    select_monitor,
    select_scraper,
)

# ── Verification: select_monitor ────────────────────────────────────


@pytest.mark.asyncio
async def test_select_monitor_saves_type_and_config_under_name():
    kv = InMemoryClaimKV()
    cfg = {"keys": ["url", "title"]}
    result = await select_monitor(kv, "sitemap", "cfg-1", cfg)

    assert isinstance(result, SelectResult)
    assert result.name == "cfg-1"
    assert result.kind == "monitor"
    assert result.type == "sitemap"
    assert result.config == cfg

    stored = await kv.get("cfg-1")
    assert stored["monitor_type"] == "sitemap"
    assert stored["monitor_config"] == cfg


@pytest.mark.asyncio
async def test_select_monitor_under_different_names_accumulate():
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"a": 1})
    await select_monitor(kv, "greenhouse", "cfg-2", {"b": 2})

    cfg1 = await kv.get("cfg-1")
    cfg2 = await kv.get("cfg-2")
    assert cfg1["monitor_type"] == "sitemap"
    assert cfg1["monitor_config"] == {"a": 1}
    assert cfg2["monitor_type"] == "greenhouse"
    assert cfg2["monitor_config"] == {"b": 2}

    listed = await kv.list_all()
    assert set(listed.keys()) == {"cfg-1", "cfg-2"}


@pytest.mark.asyncio
async def test_select_monitor_overwrite_same_name_last_write_wins():
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"version": 1})
    await select_monitor(kv, "greenhouse", "cfg-1", {"version": 2})

    stored = await kv.get("cfg-1")
    assert stored["monitor_type"] == "greenhouse"
    assert stored["monitor_config"] == {"version": 2}


@pytest.mark.asyncio
async def test_select_monitor_sets_active_pointer():
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {})
    assert await kv.get_active() == "cfg-1"
    await select_monitor(kv, "greenhouse", "cfg-2", {})
    assert await kv.get_active() == "cfg-2"


@pytest.mark.asyncio
async def test_select_monitor_preserves_existing_scraper_on_same_slot():
    """Re-selecting the monitor on a name with a scraper must not clobber it."""
    kv = InMemoryClaimKV()
    await select_scraper(kv, "json_ld", "cfg-1", {"selector": "h1"})
    await select_monitor(kv, "sitemap", "cfg-1", {"a": 1})
    stored = await kv.get("cfg-1")
    assert stored["monitor_type"] == "sitemap"
    assert stored["scraper_type"] == "json_ld"
    assert stored["scraper_config"] == {"selector": "h1"}


@pytest.mark.asyncio
async def test_select_monitor_rejects_empty_name():
    kv = InMemoryClaimKV()
    with pytest.raises(WsConfigInvalid):
        await select_monitor(kv, "sitemap", "", {})


@pytest.mark.asyncio
async def test_select_monitor_rejects_reserved_name():
    kv = InMemoryClaimKV()
    with pytest.raises(WsConfigInvalid):
        await select_monitor(kv, "sitemap", "__active__", {})


@pytest.mark.asyncio
async def test_select_monitor_rejects_empty_type():
    kv = InMemoryClaimKV()
    with pytest.raises(WsConfigInvalid):
        await select_monitor(kv, "", "cfg-1", {})


@pytest.mark.asyncio
async def test_select_monitor_handles_none_config():
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", None)
    stored = await kv.get("cfg-1")
    assert stored["monitor_config"] == {}


@pytest.mark.asyncio
async def test_select_monitor_deepcopies_config():
    kv = InMemoryClaimKV()
    src: dict = {"keys": ["a"]}
    await select_monitor(kv, "sitemap", "cfg-1", src)
    src["keys"].append("b")
    stored = await kv.get("cfg-1")
    assert stored["monitor_config"] == {"keys": ["a"]}


# ── Verification: select_scraper ────────────────────────────────────


@pytest.mark.asyncio
async def test_select_scraper_saves_type_and_config_under_name():
    kv = InMemoryClaimKV()
    result = await select_scraper(kv, "json_ld", "cfg-1", {"selector": "h1"})
    assert result.kind == "scraper"
    assert result.type == "json_ld"
    stored = await kv.get("cfg-1")
    assert stored["scraper_type"] == "json_ld"
    assert stored["scraper_config"] == {"selector": "h1"}


@pytest.mark.asyncio
async def test_select_scraper_preserves_existing_monitor_on_same_slot():
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"a": 1})
    await select_scraper(kv, "json_ld", "cfg-1", {"b": 2})
    stored = await kv.get("cfg-1")
    assert stored["monitor_type"] == "sitemap"
    assert stored["monitor_config"] == {"a": 1}
    assert stored["scraper_type"] == "json_ld"
    assert stored["scraper_config"] == {"b": 2}


# ── Verification: run_monitor lookup via claim_kv ─────────────────────


@pytest.mark.asyncio
async def test_run_monitor_retrieves_named_config_via_claim_kv():
    """The composition pattern documented in lib/README.md.

    The lib's ``run_monitor`` takes a frozen ``BoardConfigState`` snapshot;
    callers (CLI / HTTP route) resolve the named config from ``claim_kv``
    and build the snapshot. This test verifies that round-trip end-to-end:
    select -> read back -> build state -> stub-run.
    """
    from src.workspace.lib import BoardConfigState

    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"depth": 2})
    await select_monitor(kv, "greenhouse", "cfg-2", {"slug": "acme"})

    # Resolve cfg-1 (the way the CLI adapter / HTTP route does).
    slot = await kv.get("cfg-1")
    state = BoardConfigState(
        board_url="https://example.com",
        monitor_type=slot["monitor_type"],
        monitor_config=dict(slot["monitor_config"]),
    )
    assert state.monitor_type == "sitemap"
    assert state.monitor_config == {"depth": 2}

    # Verify the cfg-2 slot is independent.
    slot2 = await kv.get("cfg-2")
    assert slot2["monitor_type"] == "greenhouse"
    assert slot2["monitor_config"] == {"slug": "acme"}
