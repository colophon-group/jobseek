"""Bounded, read-only Justup feed contract checks with synthetic source rows."""

from __future__ import annotations

import asyncio
import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from src.justup_feed import __main__ as feed

COMPANY = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
POSTING = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 11, 1, tzinfo=UTC)
BODY = "<p>Synthetic public job.</p>"


def source_row(**changes):
    digest = hashlib.sha256(BODY.encode()).digest()
    row = {
        "id": POSTING,
        "company_id": COMPANY,
        "company_name": "Example",
        "company_slug": "example",
        "company_website": "https://example.org/",
        "titles": ["Engineer"],
        "source_url": "https://example.org/jobs/1",
        "is_active": True,
        "tdm_reserved": False,
        "first_seen_at": START,
        "description_r2_hash": 123456789,
        "last_seen_at": None,
        "updated_at": START + timedelta(seconds=1),
        "locales": ["en"],
        "location_ids": [2657896],
        "location_names": ["Zurich"],
        "location_types": ["onsite"],
        "location_geo_types": ["city"],
        "employment_type": "full_time",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "experience_min": None,
        "experience_max": None,
        "occupation_id": None,
        "occupation_name": None,
        "seniority_id": None,
        "seniority_name": None,
        "body_locale": "en",
        "body_hash": struct.unpack(">q", digest[:8])[0],
        "body_bytes": len(BODY),
        "body_html": BODY,
        "private_trace": "must-never-export",
    }
    row.update(changes)
    return row


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.connection.events.append("snapshot")
        self.connection.visible = self.connection.writer_committed

    async def __aexit__(self, *_):
        return False


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, row=None, *, committed=False):
        self.row = row
        self.writer_committed = committed
        self.visible = False
        self.events = []

    def transaction(self, **options):
        assert options == {"isolation": "repeatable_read", "readonly": True}
        return FakeTransaction(self)

    async def fetchval(self, sql, *args):
        assert sql == feed.SOURCE_COUNT_SQL
        return int(
            self.visible
            and self.row is not None
            and self.row["is_active"]
            and self.row["updated_at"] < args[1]
        )

    async def fetch(self, sql, *args):
        if self.row is None or not self.visible:
            return []
        if sql == feed.SNAPSHOT_SQL:
            return [self.row] if self.row["is_active"] and self.row["updated_at"] < args[1] else []
        if sql == feed.CHANGES_SQL:
            after = (args[0], str(args[1]))
            key = (self.row["updated_at"], self.row["id"])
            return [self.row] if after < key and key[0] < args[2] else []
        raise AssertionError("Unexpected query")


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeManyConnection(FakeConnection):
    def __init__(self, rows):
        super().__init__(None, committed=True)
        self.rows = rows

    async def fetchval(self, sql, *args):
        assert sql == feed.SOURCE_COUNT_SQL
        return len(self.rows)

    async def fetch(self, sql, *args):
        if sql == feed.CHANGES_SQL:
            after = (args[0], str(args[1]))
            return [
                row
                for row in self.rows
                if after < (row["updated_at"], row["id"]) and row["updated_at"] < args[2]
            ][: args[4]]
        raise AssertionError("Unexpected query")


def read_rows(directory: Path, pending: dict):
    manifest = json.loads((directory / pending["manifest"]).read_text())
    rows = []
    for part in manifest["parts"]:
        data = (directory / part["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == part["sha256"]
        rows.extend(json.loads(data)["rows"])
    return manifest, rows


def test_public_allowlist_verifies_per_locale_hash_and_full_transport_digest():
    exported = feed.public_row(source_row())
    assert exported["body"] == {
        "locale": "en",
        "html": BODY,
        "sha256": hashlib.sha256(BODY.encode()).hexdigest(),
    }
    assert exported["hasContent"] is True
    assert exported["locationNames"] == ["Zurich"]
    assert "private_trace" not in json.dumps(exported)
    assert feed.public_row(source_row(description_r2_hash=None))["hasContent"] is True
    bad = feed.public_row(source_row(body_hash=0))
    assert bad["body"] is None
    assert bad["bodyStatus"] == "hash_mismatch"
    assert bad["hasContent"] is True
    too_large = feed.public_row(source_row(body_bytes=feed.BODY_LIMIT + 1, body_html=None))
    assert too_large["body"] is None
    assert too_large["bodyStatus"] == "too_large"


def test_scope_selector_is_exact_bounded_and_searchable_without_ids_in_manifest(tmp_path):
    selector = tmp_path / "companies.json"
    selector.write_text(json.dumps({"schemaVersion": 1, "companyIds": [COMPANY]}))
    companies, scope = feed.selected_companies(selector)
    assert companies == [UUID(COMPANY)]
    assert scope["requestedCount"] == 1
    assert COMPANY not in json.dumps(scope)
    for value in ([], [COMPANY, COMPANY], [COMPANY.upper()], ["bad"]):
        selector.write_text(json.dumps({"schemaVersion": 1, "companyIds": value}))
        with pytest.raises(feed.FeedError):
            feed.selected_companies(selector)
    selector.write_text('{"schemaVersion":1,"companyIds":[],"companyIds":["' + COMPANY + '"]}')
    with pytest.raises(feed.FeedError):
        feed.selected_companies(selector)


def test_cutoff_precedes_snapshot_so_a_writer_committing_during_capture_is_visible(tmp_path):
    connection = FakeConnection(source_row(), committed=False)
    pool = FakePool(connection)
    cutoff = START + timedelta(seconds=2)

    async def capture(_):
        connection.events.append("cutoff")
        connection.writer_committed = True
        return cutoff

    state = {"schemaVersion": 1, "cursor": None, "scope": None, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        pending = asyncio.run(
            feed.build_batch(pool, tmp_path, state, False, None, {"kind": "all_eligible"})
        )
    manifest, rows = read_rows(tmp_path, pending)
    assert connection.events == ["cutoff", "snapshot"]
    assert manifest["sourceScopeRowCount"] == 1
    assert len(rows) == 1
    assert manifest["toCursor"] == feed.cursor(cutoff)


def test_pre_cutoff_active_writer_is_recovered_by_next_changes_batch(tmp_path):
    row = source_row(updated_at=START + timedelta(seconds=1))
    connection = FakeConnection(row, committed=False)
    pool = FakePool(connection)
    first_cutoff = START
    second_cutoff = START + timedelta(seconds=2)

    async def capture_first(_):
        return first_cutoff

    initial = {"schemaVersion": 1, "cursor": None, "scope": None, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture_first):
        pending = asyncio.run(
            feed.build_batch(pool, tmp_path, initial, False, None, {"kind": "all_eligible"})
        )
    manifest, rows = read_rows(tmp_path, pending)
    assert rows == []
    assert manifest["toCursor"] == feed.cursor(first_cutoff)

    connection.writer_committed = True
    state = {
        "schemaVersion": 1,
        "cursor": manifest["toCursor"],
        "scope": {"kind": "all_eligible"},
        "pending": None,
    }

    async def capture_second(_):
        return second_cutoff

    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture_second):
        next_pending = asyncio.run(
            feed.build_batch(pool, tmp_path, state, False, None, {"kind": "all_eligible"})
        )
    next_manifest, rows = read_rows(tmp_path, next_pending)
    assert next_manifest["kind"] == "changes"
    assert [item["id"] for item in rows] == [POSTING]
    assert next_manifest["toCursor"] == feed.cursor(second_cutoff)


def test_changed_scope_requires_reconciliation_and_resets_batch_boundary(tmp_path):
    connection = FakeConnection(source_row(), committed=True)
    pool = FakePool(connection)
    old_scope = {"kind": "all_eligible"}
    new_scope = {"kind": "company_ids", "requestedCount": 1, "requestedSetSha256": "a" * 64}
    state = {"schemaVersion": 1, "cursor": feed.cursor(START), "scope": old_scope, "pending": None}

    async def capture(_):
        return START + timedelta(seconds=2)

    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        with pytest.raises(feed.FeedError, match="scope requires"):
            asyncio.run(feed.build_batch(pool, tmp_path, state, False, [UUID(COMPANY)], new_scope))
        pending = asyncio.run(
            feed.build_batch(pool, tmp_path, state, True, [UUID(COMPANY)], new_scope)
        )
    manifest, rows = read_rows(tmp_path, pending)
    assert manifest["kind"] == "snapshot"
    assert manifest["fromCursor"] is None
    assert manifest["scope"] == new_scope
    assert [item["id"] for item in rows] == [POSTING]


def test_inactive_change_is_explicit_and_never_inferred_from_snapshot_absence(tmp_path):
    connection = FakeConnection(source_row(is_active=False), committed=True)
    pool = FakePool(connection)
    scope = {"kind": "all_eligible"}

    async def capture(_):
        return START + timedelta(seconds=2)

    state = {"schemaVersion": 1, "cursor": None, "scope": None, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        initial = asyncio.run(feed.build_batch(pool, tmp_path, state, False, None, scope))
    manifest, rows = read_rows(tmp_path, initial)
    assert rows == []
    assert manifest["sourceScopeRowCount"] == 0

    state = {"schemaVersion": 1, "cursor": feed.cursor(START), "scope": scope, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        changed = asyncio.run(feed.build_batch(pool, tmp_path, state, False, None, scope))
    manifest, rows = read_rows(tmp_path, changed)
    assert manifest["kind"] == "changes"
    assert rows[0]["active"] is False


def test_partial_change_batch_requires_following_page_before_complete(tmp_path):
    rows = [
        source_row(
            id=str(UUID(int=index + 1)), updated_at=START + timedelta(microseconds=index + 1)
        )
        for index in range(feed.CHANGE_ROW_LIMIT + 1)
    ]
    pool = FakePool(FakeManyConnection(rows))
    scope = {"kind": "all_eligible"}

    async def capture(_):
        return START + timedelta(seconds=2)

    state = {"schemaVersion": 1, "cursor": feed.cursor(START), "scope": scope, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        first = asyncio.run(feed.build_batch(pool, tmp_path, state, False, None, scope))
    manifest, exported = read_rows(tmp_path, first)
    assert manifest["complete"] is False
    assert len(exported) == feed.CHANGE_ROW_LIMIT
    assert manifest["toCursor"]["id"] == rows[feed.CHANGE_ROW_LIMIT - 1]["id"]

    state = {"schemaVersion": 1, "cursor": manifest["toCursor"], "scope": scope, "pending": None}
    with patch.object(feed, "capture_cdc_snapshot_cutoff", capture):
        second = asyncio.run(feed.build_batch(pool, tmp_path, state, False, None, scope))
    next_manifest, next_rows = read_rows(tmp_path, second)
    assert next_manifest["complete"] is True
    assert [row["id"] for row in next_rows] == [rows[-1]["id"]]
    assert next_manifest["toCursor"] == feed.cursor(START + timedelta(seconds=2))


def test_ack_requires_exact_receipt_and_preserves_pending_replay(tmp_path):
    target = tmp_path / "feed"
    target.mkdir(mode=0o700)
    result = feed.public_row(source_row())
    parts = feed.Parts(target, "a" * 32)
    parts.add(result)
    parts.flush()
    manifest = {
        "schemaVersion": 1,
        "parts": parts.parts,
        "rows": 1,
        "toCursor": feed.cursor(START),
        "scope": {"kind": "all_eligible"},
    }
    data = feed.encode(manifest)
    name = "batch-" + "a" * 32 + "-manifest.json"
    feed.write_atomic(target / name, data)
    digest = hashlib.sha256(data).hexdigest()
    state = {
        "schemaVersion": 1,
        "cursor": None,
        "scope": None,
        "pending": {
            "manifest": name,
            "sha256": digest,
            "toCursor": feed.cursor(START),
            "scope": {"kind": "all_eligible"},
        },
    }
    feed.write_atomic(target / "state.json", feed.encode(state))
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schemaVersion": 1, "manifestSha256": "0" * 64}))
    with pytest.raises(feed.FeedError):
        feed.acknowledge(target, receipt)
    assert feed.load_state(target)["pending"] is not None
    receipt.write_text(json.dumps({"schemaVersion": 1, "manifestSha256": digest}))
    part_path = target / parts.parts[0]["file"]
    original = part_path.read_bytes()
    part_path.write_bytes(original + b" ")
    with pytest.raises(feed.FeedError, match="Pending part changed"):
        feed.acknowledge(target, receipt)
    part_path.write_bytes(original)
    assert feed.acknowledge(target, receipt) == {"cursor": feed.cursor(START)}
    assert feed.load_state(target)["pending"] is None
    assert feed.load_state(target)["scope"] == {"kind": "all_eligible"}
