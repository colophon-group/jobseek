"""Tests for ``src.workspace.lib.claim_kv``: protocol + InMemoryClaimKV."""

from __future__ import annotations

import pytest

from src.workspace.lib import ACTIVE_KEY, ClaimKV, InMemoryClaimKV


@pytest.mark.asyncio
async def test_in_memory_claim_kv_implements_protocol():
    kv = InMemoryClaimKV()
    assert isinstance(kv, ClaimKV)


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    kv = InMemoryClaimKV()
    assert await kv.get("missing") is None


@pytest.mark.asyncio
async def test_set_and_get_roundtrips():
    kv = InMemoryClaimKV()
    await kv.set("cfg-1", {"monitor_type": "sitemap", "monitor_config": {"k": 1}})
    got = await kv.get("cfg-1")
    assert got == {"monitor_type": "sitemap", "monitor_config": {"k": 1}}


@pytest.mark.asyncio
async def test_set_deepcopies_input():
    """Mutating the source dict must NOT mutate the stored value."""
    kv = InMemoryClaimKV()
    src: dict = {"monitor_config": {"keys": ["a"]}}
    await kv.set("cfg-1", src)
    src["monitor_config"]["keys"].append("b")
    src["new_key"] = "x"
    got = await kv.get("cfg-1")
    assert got == {"monitor_config": {"keys": ["a"]}}


@pytest.mark.asyncio
async def test_get_returns_independent_copy():
    """Mutating a get() result must not mutate the stored slot."""
    kv = InMemoryClaimKV()
    await kv.set("cfg-1", {"items": [1, 2]})
    got = await kv.get("cfg-1")
    got["items"].append(3)
    again = await kv.get("cfg-1")
    assert again == {"items": [1, 2]}


@pytest.mark.asyncio
async def test_list_all_excludes_active_sentinel():
    kv = InMemoryClaimKV()
    await kv.set("cfg-1", {"a": 1})
    await kv.set("cfg-2", {"b": 2})
    await kv.set_active("cfg-2")
    result = await kv.list_all()
    assert set(result.keys()) == {"cfg-1", "cfg-2"}
    assert ACTIVE_KEY not in result


@pytest.mark.asyncio
async def test_clear_removes_everything_including_active():
    kv = InMemoryClaimKV()
    await kv.set("cfg-1", {"a": 1})
    await kv.set_active("cfg-1")
    await kv.clear()
    assert await kv.list_all() == {}
    assert await kv.get_active() is None


@pytest.mark.asyncio
async def test_get_active_when_unset_returns_none():
    kv = InMemoryClaimKV()
    assert await kv.get_active() is None


@pytest.mark.asyncio
async def test_set_active_then_get_active():
    kv = InMemoryClaimKV()
    await kv.set_active("cfg-2")
    assert await kv.get_active() == "cfg-2"


@pytest.mark.asyncio
async def test_initial_seed_is_deepcopied():
    seed: dict = {"cfg-1": {"items": [1]}}
    kv = InMemoryClaimKV(initial=seed)
    seed["cfg-1"]["items"].append(2)
    got = await kv.get("cfg-1")
    assert got == {"items": [1]}
