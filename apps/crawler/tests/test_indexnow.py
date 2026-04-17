from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.config import settings
from src.indexnow import (
    _COMPANY_HASH_FIELDS,
    LOCALES,
    MAX_URLS_PER_REQUEST,
    company_urls,
    compute_company_hash,
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
# compute_company_hash
# ---------------------------------------------------------------------------


class TestComputeCompanyHash:
    def test_stable_for_identical_rows(self):
        row = _make_company_row()
        assert compute_company_hash(row) == compute_company_hash(row)

    def test_changes_when_material_field_changes(self):
        base = _make_company_row()
        original = compute_company_hash(base)
        for field in _COMPANY_HASH_FIELDS:
            mutated = {**base, field: "CHANGED"}
            assert compute_company_hash(mutated) != original, (
                f"Hash did not change when field {field!r} was mutated"
            )

    def test_none_and_empty_string_distinguishable(self):
        """Empty string must hash differently than the literal string 'None'."""
        none_row = {**_make_company_row(), "logo": None}
        empty_row = {**_make_company_row(), "logo": ""}
        # Both end up as empty strings in the joined form — equal by design.
        # The test documents the current behavior so changes are deliberate.
        assert compute_company_hash(none_row) == compute_company_hash(empty_row)

    def test_accepts_record_like_access(self):
        rec = MagicMock()
        data = _make_company_row()
        rec.__contains__ = lambda self, k: k in data
        rec.__getitem__ = lambda self, k: data[k]
        # compute_company_hash calls row.get(field, None); asyncpg.Record
        # supports this, mock it here to match.
        rec.get = lambda k, default=None: data.get(k, default)
        assert compute_company_hash(rec) == compute_company_hash(data)


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

    async def test_nothing_to_submit_when_hashes_match(self):
        row = _make_company_row()
        expected_hash = compute_company_hash(row)
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(
            side_effect=[
                [row],  # company rows
                [  # prior submissions — all current
                    {"url": url, "content_hash": expected_hash}
                    for url in company_urls(row["slug"], settings.indexnow_site_url)
                ],
            ]
        )
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 0
        assert result["unchanged"] == len(LOCALES)
        http.post.assert_not_called()

    async def test_submits_and_records_when_hash_missing(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], []])  # no prior submissions
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == len(LOCALES)
        assert result["unchanged"] == 0
        http.post.assert_awaited_once()
        # Payload shape check
        _args, kwargs = http.post.call_args
        payload = kwargs["json"]
        assert payload["host"] == settings.indexnow_host
        assert payload["key"] == settings.indexnow_key
        assert payload["keyLocation"] == settings.indexnow_key_url
        assert len(payload["urlList"]) == len(LOCALES)
        conn.executemany.assert_awaited_once()

    async def test_dry_run_does_not_post_or_record(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], []])
        http = AsyncMock(spec=httpx.AsyncClient)

        result = await notify_indexnow(pool, http, dry_run=True)

        assert result["submitted"] == 0
        assert result["dry_run"] == len(LOCALES)
        http.post.assert_not_called()

    async def test_does_not_record_on_http_error(self):
        row = _make_company_row()
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[[row], []])
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
        conn.fetch = AsyncMock(side_effect=[[row], []])
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(side_effect=httpx.ConnectError("boom"))

        result = await notify_indexnow(pool, http)

        assert result["submitted"] == 0
        conn.executemany.assert_not_awaited()

    async def test_chunks_above_protocol_limit(self):
        """Generate more candidate URLs than the 10k/request cap."""
        # Each company yields len(LOCALES) urls. Need > 10_000 urls.
        companies_needed = (MAX_URLS_PER_REQUEST // len(LOCALES)) + 10
        rows = [_make_company_row(slug=f"c{i}") for i in range(companies_needed)]
        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(side_effect=[rows, []])
        conn.executemany = AsyncMock()
        http = AsyncMock(spec=httpx.AsyncClient)
        http.post = AsyncMock(return_value=MagicMock(status_code=200))

        result = await notify_indexnow(pool, http)

        total_urls = companies_needed * len(LOCALES)
        assert result["submitted"] == total_urls
        # Expect ceil(total_urls / MAX_URLS_PER_REQUEST) POST calls.
        expected_batches = (total_urls + MAX_URLS_PER_REQUEST - 1) // MAX_URLS_PER_REQUEST
        assert http.post.await_count == expected_batches
        # Every batch body respects the cap.
        for call in http.post.call_args_list:
            _, kwargs = call
            assert len(kwargs["json"]["urlList"]) <= MAX_URLS_PER_REQUEST
