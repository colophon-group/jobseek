# apps/crawler/tests/test_enrich_local.py
"""Tests for mark_candidates and run_sync_enrich in src/core/enrich/local.py."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.core.enrich.local import (
    FilterConfig,
    OutputConfig,
    RequireConfig,
    _build_exclude_regex,
    fetch_html_local,
    mark_candidates_from_yaml,
)


# ── _build_exclude_regex ──────────────────────────────────────────────


def test_build_exclude_regex_joins_patterns():
    import re
    regex = _build_exclude_regex(["senior", "lead", "director"])
    assert re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert re.search(regex, "Tech Lead", re.IGNORECASE)
    assert not re.search(regex, "Software Engineer", re.IGNORECASE)


def test_build_exclude_regex_empty_returns_no_match_pattern():
    import re
    regex = _build_exclude_regex([])
    # Empty pattern should match nothing
    assert not re.search(regex, "Senior Engineer", re.IGNORECASE)
    assert not re.search(regex, "any job title", re.IGNORECASE)


# ── mark_candidates_from_yaml ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_candidates_calls_two_updates(tmp_path):
    """mark_candidates runs a reset UPDATE then a filter UPDATE."""
    config_path = tmp_path / "filters.yaml"
    config_path.write_text(textwrap.dedent("""
        exclude_title_patterns:
          - senior
        require:
          work_permit_support: "yes"
          experience_max: 2
    """))

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=["UPDATE 100", "UPDATE 30"])

    result = await mark_candidates_from_yaml(pool, str(config_path))

    assert pool.execute.call_count == 2
    assert result["marked"] == 100
    assert result["cleared"] == 30


@pytest.mark.asyncio
async def test_mark_candidates_uses_experience_max_from_config(tmp_path):
    config_path = tmp_path / "filters.yaml"
    config_path.write_text(textwrap.dedent("""
        exclude_title_patterns: []
        require:
          work_permit_support: "yes"
          experience_max: 3
    """))

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=["UPDATE 50", "UPDATE 5"])

    await mark_candidates_from_yaml(pool, str(config_path))

    # Second call (filter UPDATE) should have experience_max=3 as parameter
    second_call_args = pool.execute.call_args_list[1]
    assert 3 in second_call_args[0]  # experience_max=3 in positional args


# ── fetch_html_local ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_html_local_returns_html():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="<p>Job description</p>")

    html = await fetch_html_local(pool, "some-uuid", "en")
    assert html == "<p>Job description</p>"


@pytest.mark.asyncio
async def test_fetch_html_local_returns_none_when_missing():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)

    html = await fetch_html_local(pool, "some-uuid", "en")
    assert html is None
