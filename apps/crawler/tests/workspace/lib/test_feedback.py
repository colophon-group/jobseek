"""Tests for ``src.workspace.lib.feedback`` — pure async feedback fn."""

from __future__ import annotations

import pytest

from src.workspace.lib import (
    FeedbackResult,
    InMemoryClaimKV,
    WsConfigInvalid,
    WsFeedbackIncomplete,
    feedback,
    select_monitor,
    select_scraper,
)

# ── Helpers ─────────────────────────────────────────────────────────


async def _seed_active_config(name: str = "cfg-1") -> InMemoryClaimKV:
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", name, {"depth": 1})
    await select_scraper(kv, "json_ld", name, {})
    return kv


def _all_clean(*fields: str) -> dict[str, dict[str, str]]:
    return {f: {"quality": "clean"} for f in fields}


# ── Verification: feedback records against the active named config ─


@pytest.mark.asyncio
async def test_feedback_records_against_active_named_config():
    kv = await _seed_active_config("cfg-1")
    result = await feedback(
        kv,
        verdict="good",
        per_field=_all_clean("title", "description"),
        verdict_notes="all good",
    )
    assert isinstance(result, FeedbackResult)
    assert result.name == "cfg-1"
    assert result.verdict == "good"
    assert result.verdict_notes == "all good"

    slot = await kv.get("cfg-1")
    assert "feedback" in slot
    assert slot["feedback"]["verdict"] == "good"
    assert slot["feedback"]["verdict_notes"] == "all good"
    assert slot["feedback"]["fields"]["title"]["quality"] == "clean"


@pytest.mark.asyncio
async def test_feedback_does_not_corrupt_other_named_configs():
    """A feedback call against the active slot must NOT touch siblings."""
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"a": 1})
    await select_monitor(kv, "greenhouse", "cfg-2", {"b": 2})
    # cfg-2 is now active (set_active is called on every select).
    assert await kv.get_active() == "cfg-2"

    cfg1_before = await kv.get("cfg-1")

    await feedback(
        kv,
        verdict="poor",
        per_field=_all_clean("title", "description"),
    )

    cfg1_after = await kv.get("cfg-1")
    assert cfg1_after == cfg1_before
    # cfg-1 has no "feedback" key
    assert "feedback" not in cfg1_after

    # cfg-2 has the feedback recorded
    cfg2_after = await kv.get("cfg-2")
    assert cfg2_after["feedback"]["verdict"] == "poor"


@pytest.mark.asyncio
async def test_feedback_with_unusable_verdict_does_not_corrupt_others():
    """The issue calls this out specifically as ``verdict='bad'``;
    the closest CLI verdict is ``unusable``."""
    kv = InMemoryClaimKV()
    await select_monitor(kv, "sitemap", "cfg-1", {"a": 1})
    await select_monitor(kv, "greenhouse", "cfg-2", {"b": 2})

    await feedback(
        kv,
        verdict="unusable",
        per_field=_all_clean("title", "description"),
    )

    cfg1 = await kv.get("cfg-1")
    cfg2 = await kv.get("cfg-2")
    assert "feedback" not in cfg1
    assert cfg2["feedback"]["verdict"] == "unusable"


# ── Verification: validation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_verdict():
    kv = await _seed_active_config()
    with pytest.raises(WsConfigInvalid):
        await feedback(kv, verdict="terrible", per_field=_all_clean("title", "description"))


@pytest.mark.asyncio
async def test_feedback_requires_active_config():
    kv = InMemoryClaimKV()
    with pytest.raises(WsConfigInvalid):
        await feedback(kv, verdict="good", per_field=_all_clean("title", "description"))


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_per_field_quality():
    kv = await _seed_active_config()
    with pytest.raises(WsConfigInvalid):
        await feedback(
            kv,
            verdict="good",
            per_field={"title": {"quality": "ugly"}},
        )


@pytest.mark.asyncio
async def test_feedback_requires_explicit_rating_for_required_fields():
    kv = await _seed_active_config()
    with pytest.raises(WsFeedbackIncomplete):
        await feedback(kv, verdict="good", per_field={"title": {"quality": "clean"}})


@pytest.mark.asyncio
async def test_feedback_requires_rating_when_field_has_coverage():
    kv = await _seed_active_config()
    with pytest.raises(WsFeedbackIncomplete):
        await feedback(
            kv,
            verdict="good",
            per_field=_all_clean("title", "description"),
            monitor_run={"jobs": 10, "quality": {"locations": 8}},
        )


# ── Verification: shape / coverage / tier summaries ──────────────────


@pytest.mark.asyncio
async def test_feedback_computes_coverage_strings_from_run_data():
    kv = await _seed_active_config()
    per_field = _all_clean("title", "description", "locations")
    result = await feedback(
        kv,
        verdict="good",
        per_field=per_field,
        monitor_run={"jobs": 10, "quality": {"title": 10, "description": 9, "locations": 7}},
    )
    assert result.fields["title"]["coverage"] == "10/10"
    assert result.fields["description"]["coverage"] == "9/10"
    assert result.fields["locations"]["coverage"] == "7/10"


@pytest.mark.asyncio
async def test_feedback_required_tier_summary():
    kv = await _seed_active_config()
    per_field = {
        "title": {"quality": "clean"},
        "description": {"quality": "noisy"},
    }
    result = await feedback(
        kv,
        verdict="acceptable",
        per_field=per_field,
        monitor_run={"jobs": 10, "quality": {"title": 10, "description": 8}},
    )
    assert result.required["coverage"] == "18/20"
    assert result.required["quality"] == "noisy"  # worst across tier


@pytest.mark.asyncio
async def test_feedback_auto_absent_for_zero_coverage_field():
    kv = await _seed_active_config()
    result = await feedback(
        kv,
        verdict="acceptable",
        per_field=_all_clean("title", "description"),
        monitor_run={"jobs": 10, "quality": {"title": 10, "description": 10, "skills": 0}},
    )
    # ``skills`` had coverage=0 with field-data present → auto-absent.
    assert result.fields["skills"]["quality"] == "absent"
    assert result.fields["skills"]["coverage"] == "0/10"


@pytest.mark.asyncio
async def test_feedback_omits_coverage_when_no_field_data():
    kv = await _seed_active_config()
    result = await feedback(
        kv,
        verdict="good",
        per_field=_all_clean("title", "description"),
    )
    assert "coverage" not in result.fields["title"]
    assert "coverage" not in result.fields["description"]


@pytest.mark.asyncio
async def test_feedback_does_not_change_active_pointer():
    kv = await _seed_active_config("cfg-1")
    await feedback(kv, verdict="good", per_field=_all_clean("title", "description"))
    assert await kv.get_active() == "cfg-1"


@pytest.mark.asyncio
async def test_feedback_records_notes_when_provided():
    kv = await _seed_active_config()
    per_field = {
        "title": {"quality": "clean"},
        "description": {"quality": "noisy", "notes": "trailing whitespace"},
    }
    result = await feedback(kv, verdict="acceptable", per_field=per_field)
    assert result.fields["description"]["notes"] == "trailing whitespace"
