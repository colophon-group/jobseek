"""Tests for enrich.taxonomy — lazy-cached taxonomy resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.enrich import taxonomy


@pytest.fixture(autouse=True)
def _reset_caches():
    """Reset module-level caches between tests."""
    taxonomy._occupation_ids = None
    taxonomy._seniority_ids = None
    taxonomy._technology_ids = None
    taxonomy._tech_name_to_slug = None
    taxonomy._warned_empty = False
    yield
    taxonomy._occupation_ids = None
    taxonomy._seniority_ids = None
    taxonomy._technology_ids = None
    taxonomy._tech_name_to_slug = None
    taxonomy._warned_empty = False


def _make_pool(
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
    tech_ids: dict[str, int] | None = None,
) -> AsyncMock:
    """Create a mock pool that returns the given ID maps."""
    pool = AsyncMock()
    _tech_ids = tech_ids or {}

    async def mock_fetch(query: str):
        if "occupation" in query:
            return [{"slug": slug, "id": id_} for slug, id_ in occ_ids.items()]
        if "seniority" in query:
            return [{"slug": slug, "id": id_} for slug, id_ in sen_ids.items()]
        if "technology" in query:
            return [{"slug": slug, "id": id_} for slug, id_ in _tech_ids.items()]
        return []

    pool.fetch = mock_fetch
    return pool


class TestResolveTaxonomy:
    @pytest.mark.asyncio
    async def test_seniority_direct_lookup(self):
        """Seniority slug from enrichment maps directly to an ID."""
        pool = _make_pool({}, {"senior": 10, "entry": 20})
        parsed = {"seniority": "senior", "occupation": None}

        result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id is None
        assert result.seniority_id == 10

    @pytest.mark.asyncio
    async def test_occupation_fuzzy_match(self):
        """Occupation string is fuzzy-matched via match_occupation."""
        pool = _make_pool({"software-engineer": 42}, {})
        parsed = {"occupation": "Software Engineer", "seniority": None}

        with patch(
            "src.core.enrich.taxonomy.match_occupation",
            return_value="software-engineer",
        ):
            result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id == 42
        assert result.seniority_id is None

    @pytest.mark.asyncio
    async def test_both_resolved(self):
        """Both occupation and seniority resolve together."""
        pool = _make_pool(
            {"data-analyst": 5},
            {"mid": 3},
        )
        parsed = {"occupation": "Data Analyst", "seniority": "mid"}

        with patch(
            "src.core.enrich.taxonomy.match_occupation",
            return_value="data-analyst",
        ):
            result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id == 5
        assert result.seniority_id == 3

    @pytest.mark.asyncio
    async def test_none_when_no_match(self):
        """Returns None for fields that don't match."""
        pool = _make_pool({"software-engineer": 1}, {"senior": 2})
        parsed = {"occupation": "Obscure Role XYZ", "seniority": "nonexistent"}

        with patch(
            "src.core.enrich.taxonomy.match_occupation",
            return_value=None,
        ):
            result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id is None
        assert result.seniority_id is None

    @pytest.mark.asyncio
    async def test_none_when_fields_missing(self):
        """Returns None, None when parsed has no occupation/seniority."""
        pool = _make_pool({"software-engineer": 1}, {"senior": 2})
        parsed = {"technologies": ["Python"]}

        result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id is None
        assert result.seniority_id is None

    @pytest.mark.asyncio
    async def test_caches_loaded_once(self):
        """ID maps are fetched from DB only once, then cached."""
        pool = _make_pool({"software-engineer": 1}, {"senior": 2})

        parsed = {"occupation": None, "seniority": "senior"}
        await taxonomy.resolve_taxonomy(pool, parsed)
        await taxonomy.resolve_taxonomy(pool, parsed)

        # Cache should be populated after first call
        assert taxonomy._occupation_ids == {"software-engineer": 1}
        assert taxonomy._seniority_ids == {"senior": 2}

    @pytest.mark.asyncio
    async def test_exception_returns_empty_result(self):
        """Any exception in resolve returns empty TaxonomyResult without raising."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("DB down"))
        parsed = {"occupation": "Software Engineer", "seniority": "senior"}

        result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id is None
        assert result.seniority_id is None
        assert result.technology_ids == []
        assert result.misses == []

    @pytest.mark.asyncio
    async def test_empty_tables_warns_once(self):
        """Warns once when taxonomy tables are empty, not on every call."""
        pool = _make_pool({}, {})
        parsed = {"occupation": None, "seniority": None}

        await taxonomy.resolve_taxonomy(pool, parsed)
        assert taxonomy._warned_empty is True

        # Second call should not re-warn (flag stays True)
        await taxonomy.resolve_taxonomy(pool, parsed)
        assert taxonomy._warned_empty is True

    @pytest.mark.asyncio
    async def test_occupation_miss_recorded(self):
        """Unmatched occupation is recorded as a miss."""
        pool = _make_pool({"software-engineer": 1}, {})
        parsed = {"occupation": "Obscure Role XYZ", "seniority": None}

        with patch(
            "src.core.enrich.taxonomy.match_occupation",
            return_value=None,
        ):
            result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.occupation_id is None
        assert len(result.misses) == 1
        miss = result.misses[0]
        assert miss.taxonomy == "occupation"
        assert miss.raw_value == "obscure role xyz"
        assert miss.sample_value == "Obscure Role XYZ"

    @pytest.mark.asyncio
    async def test_technology_resolution(self):
        """Known technologies are resolved to IDs."""
        pool = _make_pool({}, {}, {"python": 10, "react": 20})
        # Pre-set the name->slug map
        taxonomy._tech_name_to_slug = {"python": "python", "react": "react"}

        parsed = {"technologies": ["Python", "React"]}

        result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert sorted(result.technology_ids) == [10, 20]
        assert result.misses == []

    @pytest.mark.asyncio
    async def test_technology_miss_recorded(self):
        """Unknown technologies are recorded as misses."""
        pool = _make_pool({}, {}, {"python": 10})
        taxonomy._tech_name_to_slug = {"python": "python"}

        parsed = {"technologies": ["Python", "SomeObscureFramework"]}

        result = await taxonomy.resolve_taxonomy(pool, parsed)

        assert result.technology_ids == [10]
        assert len(result.misses) == 1
        miss = result.misses[0]
        assert miss.taxonomy == "technology"
        assert miss.raw_value == "someobscureframework"
        assert miss.sample_value == "SomeObscureFramework"
