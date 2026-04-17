# apps/crawler/tests/test_enrich_local.py
"""Tests for mark_candidates and run_sync_enrich in src/core/enrich/local.py."""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.enrich.local import (
    FilterConfig,
    OutputConfig,
    RequireConfig,
    _build_exclude_regex,
    fetch_html_local,
    mark_candidates_from_yaml,
    run_sync_enrich,
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

    # Second call (filter UPDATE): positional args are (sql, exclude_regex, experience_max)
    second_call_args = pool.execute.call_args_list[1]
    assert second_call_args[0][2] == 3  # $2 param is experience_max


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


# ── run_sync_enrich ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_sync_enrich_happy_path():
    """Two postings with HTML: both get enriched, _persist_results called once."""
    import uuid
    from unittest.mock import patch

    fake_id_1 = str(uuid.uuid4())
    fake_id_2 = str(uuid.uuid4())

    # pool.fetch returns 2 rows on first call, empty on second (terminates loop)
    row1 = {"id": fake_id_1, "title": "Engineer", "locale": "en", "employment_type": "full_time"}
    row2 = {"id": fake_id_2, "title": "Analyst", "locale": "en", "employment_type": "full_time"}
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[[row1, row2], []])
    pool.fetchval = AsyncMock(return_value="<p>Job HTML</p>")
    pool.execute = AsyncMock(return_value=None)

    provider = MagicMock()
    provider.generate = AsyncMock(
        return_value=({"work_permit_support": "yes", "seniority": "entry"}, MagicMock())
    )

    with patch("src.core.enrich.batch._persist_results", new_callable=AsyncMock) as mock_persist:
        result = await run_sync_enrich(pool, provider, batch_size=2, rate_limit_rpm=60)

    assert result["enriched"] == 2
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert provider.generate.call_count == 2
    assert mock_persist.call_count == 1


@pytest.mark.asyncio
async def test_run_sync_enrich_breaks_when_all_skipped():
    """If all claimed postings lack HTML, the loop breaks (no infinite loop)."""
    import uuid
    from unittest.mock import patch

    fake_id = str(uuid.uuid4())
    row = {"id": fake_id, "title": "Engineer", "locale": "en", "employment_type": "full_time"}
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[row])  # always returns same row
    pool.fetchval = AsyncMock(return_value=None)  # no HTML
    pool.execute = AsyncMock(return_value=None)

    provider = MagicMock()
    provider.generate = AsyncMock()

    with patch("src.core.enrich.batch._persist_results", new_callable=AsyncMock):
        result = await run_sync_enrich(pool, provider, batch_size=1, rate_limit_rpm=60)

    # Loop must exit; provider.generate must never be called
    assert result["skipped"] == 1
    assert result["enriched"] == 0
    provider.generate.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_enrich_all_errors_exits():
    """If all claimed postings have HTML but provider always raises, loop exits cleanly."""
    import uuid

    fake_id = str(uuid.uuid4())
    row = {"id": fake_id, "title": "Engineer", "locale": "en", "employment_type": "full_time"}
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[row])  # always returns same row
    pool.fetchval = AsyncMock(return_value="<p>Job HTML</p>")  # HTML present
    pool.execute = AsyncMock(return_value=None)

    provider = MagicMock()
    provider.generate = AsyncMock(side_effect=Exception("API error"))

    with patch("src.core.enrich.batch._persist_results", new_callable=AsyncMock):
        result = await run_sync_enrich(pool, provider, batch_size=1, rate_limit_rpm=60)

    assert result["enriched"] == 0
    assert result["failed"] == 1
    provider.generate.assert_called_once()
