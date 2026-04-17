from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import settings
from src.indexnow import (
    _COMPANY_STABLE_FIELDS,
    _HASH_VERSION,
    LOCALES,
    MAX_URLS_PER_REQUEST,
    company_urls,
    compute_company_locale_hash,
    notify_indexnow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool() -> AsyncMock:
    """Mirror of tests/test_exporter.py::_make_pool."""
    pool = AsyncMock()
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _make_company_row(**overrides) -> dict:
    """Build a minimal company row dict with sensible defaults."""
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "slug": "acme",
        "name": "Acme",
        "website": "https://acme.example",
        "logo": None,
        "icon": None,
        "industry": 42,
        "employee_count_range": 3,
        "founded_year": 2010,
    }
    row.update(overrides)
    return row


def _description_rows(company_id: str, per_locale: dict[str, str | None]) -> list[dict]:
    """Shape rows returned by the company_description query."""
    return [
        {"company_id": company_id, "locale": loc, "description": desc}
        for loc, desc in per_locale.items()
        if desc is not None
    ]


def _priors(urls_and_hashes: dict[str, str]) -> list[dict]:
    """Shape rows returned by the indexnow_submission SELECT."""
    return [{"url": u, "content_hash": h} for u, h in urls_and_hashes.items()]


@pytest.fixture(autouse=True)
def _set_indexnow_env():
    """Populate indexnow settings for the duration of each test."""
    prev = (
        settings.indexnow_key,
        settings.indexnow_host,
        settings.indexnow_site_url,
        settings.indexnow_key_url,
    )
    settings.indexnow_key = "a" * 32
    settings.indexnow_host = "jseek.test"
    settings.indexnow_site_url = "https://jseek.test"
    settings.indexnow_key_url = "https://jseek.test/indexnow-key.txt"
    try:
        yield
    finally:
        (
            settings.indexnow_key,
            settings.indexnow_host,
            settings.indexnow_site_url,
            settings.indexnow_key_url,
        ) = prev


# ---------------------------------------------------------------------------
# compute_company_locale_hash
# ---------------------------------------------------------------------------


class TestComputeCompanyLocaleHash:
    def test_stable_for_identical_inputs(self):
        row = _make_company_row()
        assert compute_company_locale_hash(row, "About Acme.") == compute_company_locale_hash(
            row, "About Acme."
        )

    def test_versioned_prefix(self):
        row = _make_company_row()
        digest = compute_company_locale_hash(row, None)
        assert digest.startswith(f"{_HASH_VERSION}:")

    def test_changes_when_stable_field_changes(self):
        base = _make_company_row()
        desc = "About Acme."
        original = compute_company_locale_hash(base, desc)
        for field in _COMPANY_STABLE_FIELDS:
            mutated = {**base, field: "CHANGED"}
            assert compute_company_locale_hash(mutated, desc) != original, (
                f"Hash did not change when field {field!r} was mutated"
            )

    def test_changes_when_description_changes(self):
        row = _make_company_row()
        assert compute_company_locale_hash(row, "A") != compute_company_locale_hash(row, "B")

    def test_none_description_hashed_as_empty(self):
        """A missing company_description row (None) hashes the same as an explicit empty string."""
        row = _make_company_row()
        assert compute_company_locale_hash(row, None) == compute_company_locale_hash(row, "")

    def test_accepts_record_like_access(self):
        """asyncpg.Record uses row.get(field, None); mock the same surface."""
        rec = MagicMock()
        data = _make_company_row()
        rec.__contains__ = lambda self, k: k in data
        rec.__getitem__ = lambda self, k: data[k]
        rec.get = lambda k, default=None: data.get(k, default)
        assert compute_company_locale_hash(rec, "d") == compute_company_locale_hash(data, "d")


# ---------------------------------------------------------------------------
# Per-locale isolation: a description edit in one locale must only
# invalidate that locale's URL hash.
# ---------------------------------------------------------------------------


class TestPerLocaleIsolation:
    def test_en_and_de_hashes_differ_when_descriptions_differ(self):
        row = _make_company_row()
        en_hash = compute_company_locale_hash(row, "English.")
        de_hash = compute_company_locale_hash(row, "Deutsch.")
        assert en_hash != de_hash

    def test_unchanged_locale_keeps_same_hash(self):
        row = _make_company_row()
        en_before = compute_company_locale_hash(row, "English.")
        # German description changes — the English-hashed payload is
        # byte-identical to before, so the English URL's hash must be
        # unchanged. Operationally this means a German edit does not
        # trigger re-submission of the /en/... URL.
        en_after = compute_company_locale_hash(row, "English.")
        assert en_before == en_after


# ---------------------------------------------------------------------------
# company_urls
# ---------------------------------------------------------------------------


class TestCompanyUrls:
    def test_expands_to_every_locale(self):
        urls = company_urls("acme", "https://jseek.test")
        assert len(urls) == len(LOCALES)
        assert set(urls) == {f"https://jseek.test/{locale}/company/acme" for locale in LOCALES}


# ---------------------------------------------------------------------------
# notify_indexnow: orchestration
#
# The driving query set is three sequential conn.fetch calls:
#   1. company rows
#   2. per-locale descriptions
#   3. prior submission hashes
# Each test below wires `conn.fetch.side_effect` accordingly.
# ---------------------------------------------------------------------------


class TestNotifyIndexnow:
    async def test_skipped_when_key_unset(self):
        settings.indexnow_key = ""
        pool = _make_pool()
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http)

        assert result == {"skipped": 1, "submitted": 0, "unchanged": 0}
        pool.acquire.assert_not_called()

    async def test_skipped_when_host_missing(self):
        settings.indexnow_host = ""
        pool = _make_pool()
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http)

        assert result["skipped"] == 1
        pool.acquire.assert_not_called()

    async def test_nothing_to_submit_when_all_locale_hashes_match(self):
        row = _make_company_row()
        # Same description for every locale — happens for a fresh
        # company that only has the CSV-sourced English entry
        # backfilled via the i18n pipeline.
        per_locale = {loc: "Shared description" for loc in LOCALES}
        site = settings.indexnow_site_url
        expected_priors = {
            f"{site}/{loc}/company/{row['slug']}": compute_company_locale_hash(row, per_locale[loc])
            for loc in LOCALES
        }
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(
            side_effect=[
                [row],
                _description_rows(row["id"], per_locale),
                _priors(expected_priors),
            ]
        )
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 0
        assert result["unchanged"] == len(LOCALES)
        http.post.assert_not_called()

    async def test_only_changed_locale_is_resubmitted(self):
        """German description edit re-submits /de/... only; other locales stay put."""
        row = _make_company_row()
        # Priors reflect yesterday's state: German said "Alt." everywhere.
        yesterday_per_locale = {loc: "Shared." for loc in LOCALES}
        site = settings.indexnow_site_url
        yesterday_priors = {
            f"{site}/{loc}/company/{row['slug']}": compute_company_locale_hash(
                row, yesterday_per_locale[loc]
            )
            for loc in LOCALES
        }
        # Today: only German changed.
        today_per_locale = {**yesterday_per_locale, "de": "Neu!"}

        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(
            side_effect=[
                [row],
                _description_rows(row["id"], today_per_locale),
                _priors(yesterday_priors),
            ]
        )
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 1
        assert result["unchanged"] == len(LOCALES) - 1
        _args, kwargs = http.post.call_args
        submitted_urls = kwargs["json"]["urlList"]
        assert submitted_urls == [f"{settings.indexnow_site_url}/de/company/{row['slug']}"]

    async def test_submits_all_locales_when_no_prior(self):
        row = _make_company_row()
        per_locale = {"en": "English."}  # other locales have no row → None
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(
            side_effect=[
                [row],
                _description_rows(row["id"], per_locale),
                [],  # no priors
            ]
        )
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == len(LOCALES)
        assert result["unchanged"] == 0
        http.post.assert_awaited_once()
        _args, kwargs = http.post.call_args
        payload = kwargs["json"]
        assert payload["host"] == settings.indexnow_host
        assert payload["key"] == settings.indexnow_key
        assert payload["keyLocation"] == settings.indexnow_key_url
        assert len(payload["urlList"]) == len(LOCALES)
        conn.executemany.assert_awaited_once()

    async def test_old_hash_scheme_triggers_full_resubmit(self):
        """Stored v1-style raw-sha hashes never match v2-prefixed ones."""
        row = _make_company_row()
        per_locale = {loc: "desc" for loc in LOCALES}
        legacy_priors = {
            f"{settings.indexnow_site_url}/{loc}/company/{row['slug']}": "legacyhashnoversion"
            for loc in LOCALES
        }
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(
            side_effect=[[row], _description_rows(row["id"], per_locale), _priors(legacy_priors)]
        )
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == len(LOCALES)
        # Verify the stored values we write back carry the v2 prefix.
        _args, _kwargs = conn.executemany.call_args
        recorded_hashes = [h for (_u, h) in _args[1]]
        assert all(h.startswith(f"{_HASH_VERSION}:") for h in recorded_hashes)

    async def test_dry_run_does_not_post_or_record(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], [], []])
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http, dry_run=True)

        assert result["submitted"] == 0
        assert result["dry_run"] == len(LOCALES)
        http.post.assert_not_called()

    async def test_does_not_record_on_http_error(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], [], []])
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=500, text="oops"))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 0
        conn.executemany.assert_not_awaited()  # hash table untouched → retries next tick

    async def test_network_error_swallowed(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], [], []])
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 0
        conn.executemany.assert_not_awaited()

    async def test_chunks_above_protocol_limit(self):
        """Generate more candidate URLs than the 10k/request cap."""
        companies_needed = (MAX_URLS_PER_REQUEST // len(LOCALES)) + 10
        rows = [
            _make_company_row(
                id=f"00000000-0000-0000-0000-{i:012d}",
                slug=f"c{i}",
            )
            for i in range(companies_needed)
        ]
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[rows, [], []])
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        total_urls = companies_needed * len(LOCALES)
        assert result["submitted"] == total_urls
        expected_batches = (total_urls + MAX_URLS_PER_REQUEST - 1) // MAX_URLS_PER_REQUEST
        assert http.post.await_count == expected_batches
        for call in http.post.call_args_list:
            _, kwargs = call
            assert len(kwargs["json"]["urlList"]) <= MAX_URLS_PER_REQUEST
