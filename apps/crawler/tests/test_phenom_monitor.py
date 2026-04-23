from __future__ import annotations

import httpx
import pytest

from src.core.monitors.phenom import (
    _DEFAULT_KEEP_LANGS,
    _PHENOM_CHILD_RE,
    _child_language,
    _is_phenom_job_url,
    _keep_langs_from_metadata,
    _select_children,
    can_handle,
    discover,
)

# ── URL classification ──────────────────────────────────────────────────


class TestIsPhenomJobUrl:
    def test_canonical_job_path(self):
        assert _is_phenom_job_url(
            "https://careers.marriott.com/guest-expert/job/00017DEF7EFC4C7F187FB719FA88F08D"
        )

    def test_per_language_job_path(self):
        assert _is_phenom_job_url("https://careers.nike.com/de/lead-marketing/job/R-79637")

    def test_mchire_query_param(self):
        assert _is_phenom_job_url(
            "https://www.mchire.com/co/McDonalds5045/Job?job_id=PDX_WVL_3288DCA2_106041"
        )

    def test_mchire_query_param_ampersand(self):
        assert _is_phenom_job_url(
            "https://www.mchire.com/co/McDonalds5045/Job?lang=en&job_id=PDX_123"
        )

    def test_excludes_site_root(self):
        assert not _is_phenom_job_url("https://careers.nationwide.com/")

    def test_excludes_jobs_index(self):
        assert not _is_phenom_job_url("https://careers.nationwide.com/jobs")

    def test_excludes_search_page(self):
        assert not _is_phenom_job_url("https://careers.marriott.com/search-jobs?country=US")


class TestPhenomChildFingerprint:
    def test_per_language_english(self):
        assert _PHENOM_CHILD_RE.search("https://careers.marriott.com/sitemap-0a80f330-en.xml")

    def test_per_language_arabic(self):
        assert _PHENOM_CHILD_RE.search("https://careers.marriott.com/sitemap-0a80f330-ar.xml")

    def test_hyphenated_locale(self):
        assert _PHENOM_CHILD_RE.search(
            "https://hourlyjobs-us.mondelezinternational.com/sitemap-a985dd49-zh-cn.xml"
        )

    def test_non_phenom_wordpress_sitemap(self):
        assert not _PHENOM_CHILD_RE.search("https://example.com/post-sitemap.xml")

    def test_non_phenom_numeric_shard(self):
        assert not _PHENOM_CHILD_RE.search("https://example.com/sitemap-1.xml")


class TestChildLanguage:
    def test_simple_language(self):
        assert _child_language("https://x/sitemap-abcd-en.xml") == "en"

    def test_hyphenated_language(self):
        assert _child_language("https://x/sitemap-abcd-en-us.xml") == "en-us"

    def test_uppercase_normalized(self):
        assert _child_language("https://x/sitemap-abcd-EN.xml") == "en"

    def test_no_language_suffix(self):
        # Nationwide's content-only sitemap has just two dash-segments and
        # no language token — returned as None so _select_children keeps it
        # (the URLs inside are filtered later by _is_phenom_job_url).
        assert _child_language("https://careers.nationwide.com/sitemap-content.xml") is None


class TestSelectChildren:
    def test_per_language_keeps_only_english(self):
        children = [
            "https://x/sitemap-abcd-ar.xml",
            "https://x/sitemap-abcd-de.xml",
            "https://x/sitemap-abcd-en.xml",
            "https://x/sitemap-abcd-es.xml",
            "https://x/sitemap-abcd-fr.xml",
        ]
        kept = _select_children(children)
        assert kept == ["https://x/sitemap-abcd-en.xml"]

    def test_per_language_keeps_en_us_variant(self):
        children = [
            "https://x/sitemap-abcd-ar.xml",
            "https://x/sitemap-abcd-en-us.xml",
        ]
        kept = _select_children(children)
        assert kept == ["https://x/sitemap-abcd-en-us.xml"]

    def test_sharded_same_language_pass_through(self):
        children = [
            "https://x/sitemap-0001-en.xml",
            "https://x/sitemap-0002-en.xml",
            "https://x/sitemap-0003-en.xml",
        ]
        kept = _select_children(children)
        assert kept == children

    def test_empty_unchanged(self):
        assert _select_children([]) == []

    def test_custom_keep_langs_picks_spanish(self):
        # mchire use case: opt into Spanish shards via per-board override.
        children = [
            "https://x/sitemap-abcd-en.xml",
            "https://x/sitemap-abcd-es-es.xml",
            "https://x/sitemap-abcd-es-mx.xml",
            "https://x/sitemap-abcd-fr.xml",
        ]
        kept = _select_children(children, frozenset({"en", "en-us", "es-es", "es-mx"}))
        assert kept == [
            "https://x/sitemap-abcd-en.xml",
            "https://x/sitemap-abcd-es-es.xml",
            "https://x/sitemap-abcd-es-mx.xml",
        ]


class TestKeepLangsFromMetadata:
    def test_default_when_missing(self):
        assert _keep_langs_from_metadata({}) == _DEFAULT_KEEP_LANGS

    def test_default_when_empty_list(self):
        assert _keep_langs_from_metadata({"keep_languages": []}) == _DEFAULT_KEEP_LANGS

    def test_default_when_not_a_list(self):
        # Defensive: malformed config doesn't crash the monitor.
        assert _keep_langs_from_metadata({"keep_languages": "en,es-es"}) == _DEFAULT_KEEP_LANGS

    def test_parses_list(self):
        got = _keep_langs_from_metadata({"keep_languages": ["en", "en-us", "es-es", "es-mx"]})
        assert got == frozenset({"en", "en-us", "es-es", "es-mx"})

    def test_lowercases_values(self):
        got = _keep_langs_from_metadata({"keep_languages": ["EN", "EN-US"]})
        assert got == frozenset({"en", "en-us"})

    def test_drops_falsy_entries(self):
        got = _keep_langs_from_metadata({"keep_languages": ["en", "", None, "es-es"]})
        assert got == frozenset({"en", "es-es"})


# ── discover() ───────────────────────────────────────────────────────────


_PHENOM_INDEX_PER_LANGUAGE = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://careers.example.com/sitemap-aaaa1111-ar.xml</loc></sitemap>
  <sitemap><loc>https://careers.example.com/sitemap-aaaa1111-en.xml</loc></sitemap>
  <sitemap><loc>https://careers.example.com/sitemap-aaaa1111-es.xml</loc></sitemap>
</sitemapindex>
"""

_CHILD_EN = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/software-engineer/job/ABC123</loc></url>
  <url><loc>https://careers.example.com/product-manager/job/DEF456</loc></url>
  <url><loc>https://careers.example.com/</loc></url>
</urlset>
"""

_CHILD_AR = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/ar/engineer/job/ABC123__ar</loc></url>
</urlset>
"""

_CHILD_ES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/es/ingeniero/job/ABC123__es</loc></url>
</urlset>
"""


def _transport(mapping: dict[str, tuple[int, str]]):
    def handler(request):
        url = str(request.url)
        if url in mapping:
            status, content = mapping[url]
            return httpx.Response(
                status,
                content=content,
                headers={"content-type": "application/xml"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_discover_per_language_keeps_only_english():
    """Marriott-shaped index: 3 per-language children → only English children fetched."""
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_PER_LANGUAGE),
            "https://careers.example.com/sitemap-aaaa1111-en.xml": (200, _CHILD_EN),
            "https://careers.example.com/sitemap-aaaa1111-ar.xml": (200, _CHILD_AR),
            "https://careers.example.com/sitemap-aaaa1111-es.xml": (200, _CHILD_ES),
        }
    )
    board = {
        "id": "b1",
        "board_url": "https://careers.example.com",
        "metadata": {},
    }
    async with httpx.AsyncClient(transport=transport) as client:
        urls, new_sitemap_url = await discover(board, client)
    # Only EN URLs; AR and ES excluded by the language filter.
    assert urls == {
        "https://careers.example.com/software-engineer/job/ABC123",
        "https://careers.example.com/product-manager/job/DEF456",
    }
    assert new_sitemap_url == "https://careers.example.com/sitemap.xml"


_PHENOM_INDEX_SHARDED = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://careers.example.com/sitemap-0001-en.xml</loc></sitemap>
  <sitemap><loc>https://careers.example.com/sitemap-0002-en.xml</loc></sitemap>
</sitemapindex>
"""

_SHARD_1 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/crew-member/job/P8-1</loc></url>
</urlset>
"""

_SHARD_2 = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/crew-member/job/P8-2</loc></url>
</urlset>
"""


@pytest.mark.asyncio
async def test_discover_sharded_fetches_all_shards():
    """mcdonalds-*-shaped index: all shards share language → all fetched."""
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_SHARDED),
            "https://careers.example.com/sitemap-0001-en.xml": (200, _SHARD_1),
            "https://careers.example.com/sitemap-0002-en.xml": (200, _SHARD_2),
        }
    )
    board = {"id": "b1", "board_url": "https://careers.example.com", "metadata": {}}
    async with httpx.AsyncClient(transport=transport) as client:
        urls, _ = await discover(board, client)
    assert urls == {
        "https://careers.example.com/crew-member/job/P8-1",
        "https://careers.example.com/crew-member/job/P8-2",
    }


@pytest.mark.asyncio
async def test_discover_filters_non_job_urls():
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_PER_LANGUAGE),
            "https://careers.example.com/sitemap-aaaa1111-en.xml": (200, _CHILD_EN),
            "https://careers.example.com/sitemap-aaaa1111-ar.xml": (200, _CHILD_AR),
            "https://careers.example.com/sitemap-aaaa1111-es.xml": (200, _CHILD_ES),
        }
    )
    board = {"id": "b1", "board_url": "https://careers.example.com", "metadata": {}}
    async with httpx.AsyncClient(transport=transport) as client:
        urls, _ = await discover(board, client)
    # "https://careers.example.com/" (site root, in _CHILD_EN) filtered out.
    assert "https://careers.example.com/" not in urls


@pytest.mark.asyncio
async def test_discover_returns_new_sitemap_url_when_not_cached():
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_SHARDED),
            "https://careers.example.com/sitemap-0001-en.xml": (200, _SHARD_1),
            "https://careers.example.com/sitemap-0002-en.xml": (200, _SHARD_2),
        }
    )
    board = {"id": "b1", "board_url": "https://careers.example.com", "metadata": {}}
    async with httpx.AsyncClient(transport=transport) as client:
        _, new_sitemap_url = await discover(board, client)
    assert new_sitemap_url == "https://careers.example.com/sitemap.xml"


@pytest.mark.asyncio
async def test_discover_skips_metadata_write_when_cached():
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_SHARDED),
            "https://careers.example.com/sitemap-0001-en.xml": (200, _SHARD_1),
            "https://careers.example.com/sitemap-0002-en.xml": (200, _SHARD_2),
        }
    )
    board = {
        "id": "b1",
        "board_url": "https://careers.example.com/jobs",
        "metadata": {"sitemap_url": "https://careers.example.com/sitemap.xml"},
    }
    async with httpx.AsyncClient(transport=transport) as client:
        _, new_sitemap_url = await discover(board, client)
    assert new_sitemap_url is None


_PHENOM_INDEX_MCHIRE = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://careers.example.com/sitemap-0001-en.xml</loc></sitemap>
  <sitemap><loc>https://careers.example.com/sitemap-0002-es-es.xml</loc></sitemap>
  <sitemap><loc>https://careers.example.com/sitemap-0003-fr.xml</loc></sitemap>
</sitemapindex>
"""

_MCHIRE_EN = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/crew-member/job/EN-1</loc></url>
</urlset>
"""

_MCHIRE_ES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/crew-member/job/ES-1</loc></url>
</urlset>
"""

_MCHIRE_FR = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.example.com/crew-member/job/FR-1</loc></url>
</urlset>
"""


@pytest.mark.asyncio
async def test_discover_keep_languages_override_picks_up_spanish():
    """mchire-style override: opt in to Spanish shards, skip French."""
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_MCHIRE),
            "https://careers.example.com/sitemap-0001-en.xml": (200, _MCHIRE_EN),
            "https://careers.example.com/sitemap-0002-es-es.xml": (200, _MCHIRE_ES),
            "https://careers.example.com/sitemap-0003-fr.xml": (200, _MCHIRE_FR),
        }
    )
    board = {
        "id": "b1",
        "board_url": "https://careers.example.com",
        "metadata": {"keep_languages": ["en", "en-us", "es-es", "es-mx"]},
    }
    async with httpx.AsyncClient(transport=transport) as client:
        urls, _ = await discover(board, client)
    assert urls == {
        "https://careers.example.com/crew-member/job/EN-1",
        "https://careers.example.com/crew-member/job/ES-1",
    }


@pytest.mark.asyncio
async def test_discover_default_excludes_spanish():
    """Without override, only English kept — regression guard for the default."""
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_MCHIRE),
            "https://careers.example.com/sitemap-0001-en.xml": (200, _MCHIRE_EN),
            "https://careers.example.com/sitemap-0002-es-es.xml": (200, _MCHIRE_ES),
            "https://careers.example.com/sitemap-0003-fr.xml": (200, _MCHIRE_FR),
        }
    )
    board = {
        "id": "b1",
        "board_url": "https://careers.example.com",
        "metadata": {},
    }
    async with httpx.AsyncClient(transport=transport) as client:
        urls, _ = await discover(board, client)
    assert urls == {"https://careers.example.com/crew-member/job/EN-1"}


# ── can_handle() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_can_handle_detects_phenom():
    transport = _transport(
        {
            "https://careers.example.com/sitemap.xml": (200, _PHENOM_INDEX_PER_LANGUAGE),
            "https://careers.example.com/sitemap-aaaa1111-en.xml": (200, _CHILD_EN),
            "https://careers.example.com/sitemap-aaaa1111-ar.xml": (200, _CHILD_AR),
            "https://careers.example.com/sitemap-aaaa1111-es.xml": (200, _CHILD_ES),
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await can_handle("https://careers.example.com/", client)
    assert meta is not None
    assert meta["sitemap_url"] == "https://careers.example.com/sitemap.xml"
    # English-only URLs after language filter: 3 total, 2 are job URLs.
    assert meta["urls"] == 3
    assert meta["jobs"] == 2


@pytest.mark.asyncio
async def test_can_handle_rejects_non_phenom_sitemap():
    generic_index = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/post-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://example.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>
"""
    transport = _transport({"https://example.com/sitemap.xml": (200, generic_index)})
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await can_handle("https://example.com/", client)
    assert meta is None


@pytest.mark.asyncio
async def test_can_handle_rejects_missing_sitemap():
    transport = _transport({})
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await can_handle("https://example.com/", client)
    assert meta is None
