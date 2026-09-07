"""Byte-authoritative description staging and UPSERT regression tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import structlog

import src.processing.scrape as scrape
from src.core.description_store import content_hash


def _compact(sql: str) -> str:
    return " ".join(sql.split())


def test_equal_html_skips_the_update_that_could_disturb_an_inflight_claim() -> None:
    sql = _compact(scrape._UPSERT_DESCRIPTION)

    assert "WHERE posting_id = $1 AND locale = $2 FOR UPDATE" in sql
    assert "ON CONFLICT (posting_id, locale) DO UPDATE" in sql
    assert (
        "WHERE convert_to(descriptions.html, 'UTF8') IS DISTINCT FROM "
        "convert_to(EXCLUDED.html, 'UTF8')"
    ) in sql
    assert "descriptions.hash = $4" not in sql
    assert "descriptions.hash = $5" not in sql
    assert "CASE WHEN sample.keep THEN (SELECT md5(html) FROM prior) END" in sql
    assert "get_byte(uuid_send($1::uuid), 15) = 0" in sql


def test_changed_html_stores_one_byte_hash_and_resets_retry_state() -> None:
    sql = _compact(scrape._UPSERT_DESCRIPTION)

    assert "hash = EXCLUDED.hash" in sql
    assert "r2_uploaded = false" in sql
    assert "r2_upload_failures = 0" in sql
    assert "r2_next_attempt_at = '-infinity'::timestamptz" in sql
    assert "EXISTS (SELECT 1 FROM upserted) AS upload_scheduled" in sql


async def test_sampled_diagnostic_records_board_checksums_and_state() -> None:
    html = "<p>Same normalized bytes</p>"
    byte_hash = content_hash(html)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "diagnostic_sampled": True,
            "row_existed": True,
            "old_html_checksum": "old-checksum",
            "new_html_checksum": "new-checksum",
            "upload_scheduled": False,
            "upload_state_changed": False,
        }
    )
    with structlog.testing.capture_logs() as logs:
        await scrape._upsert_staged_description(
            conn,
            posting_id="11111111-2222-3333-4444-555555555555",
            board_id="dupont-careers-phenom",
            staged=(html, "en", byte_hash, byte_hash),
            source="scrape",
        )

    conn.fetchrow.assert_awaited_once_with(
        scrape._UPSERT_DESCRIPTION,
        "11111111-2222-3333-4444-555555555555",
        "en",
        html,
        byte_hash,
        byte_hash,
    )
    [event] = [entry for entry in logs if entry["event"] == "r2.description_dedupe_sample"]
    assert event == {
        "posting_id": "11111111-2222-3333-4444-555555555555",
        "board_id": "dupont-careers-phenom",
        "locale": "en",
        "source": "scrape",
        "old_html_checksum": "old-checksum",
        "new_html_checksum": "new-checksum",
        "row_existed": True,
        "upload_scheduled": False,
        "upload_state_changed": False,
        "sample_modulus": 256,
        "event": "r2.description_dedupe_sample",
        "log_level": "info",
    }
