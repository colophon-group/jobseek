"""SQLite candidate-creation ledger with startup GitHub reconciliation."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from src.ats_inventory.candidates import hash_text, parse_candidate_markers
from src.ats_inventory.github import GitHubWorkItem


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    source_key: str
    normalized_url: str | None
    state: str
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LedgerReconciliation:
    remote_markers: int
    recovered_sources: int
    missing_remote_sources: tuple[str, ...]


class CandidateLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()
        self._sources: dict[str, LedgerRecord] = {}
        self._urls: dict[str, tuple[LedgerRecord, ...]] = {}
        self._refresh_index()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates (
                    source_key TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL UNIQUE,
                    normalized_url TEXT,
                    normalized_url_hash TEXT,
                    family TEXT,
                    tenant TEXT,
                    state TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS candidates_url_hash
                    ON candidates(normalized_url_hash);
                CREATE TABLE IF NOT EXISTS remote_items (
                    kind TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    source_key TEXT NOT NULL REFERENCES candidates(source_key),
                    state TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    board_url_hash TEXT NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    PRIMARY KEY(kind, number)
                );
                CREATE INDEX IF NOT EXISTS remote_items_source
                    ON remote_items(source_key);
                """
            )

    def reconcile_remote(self, items: Iterable[GitHubWorkItem]) -> LedgerReconciliation:
        now = int(time.time())
        marked: list[tuple[GitHubWorkItem, str, str]] = []
        for item in items:
            # Active PRs remain an in-memory hard stop, but contributor-owned
            # PR markers must never become permanent fail-closed ledger rows.
            if item.kind != "issue":
                continue
            for source_key, board_hash in parse_candidate_markers(item.body):
                marked.append((item, source_key, board_hash))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = {
                str(row["source_key"])
                for row in connection.execute("SELECT source_key FROM candidates")
            }
            connection.execute("DELETE FROM remote_items")
            observed: set[str] = set()
            for item, source_key, board_hash in marked:
                observed.add(source_key)
                state = f"remote_{item.kind}_{item.state.casefold()}"
                connection.execute(
                    """
                    INSERT INTO candidates (
                        source_key, source_hash, normalized_url, normalized_url_hash,
                        family, tenant, state, created_at, updated_at
                    ) VALUES (?, ?, NULL, ?, NULL, NULL, ?, ?, ?)
                    ON CONFLICT(source_key) DO UPDATE SET
                        normalized_url_hash = COALESCE(
                            candidates.normalized_url_hash, excluded.normalized_url_hash
                        ),
                        state = excluded.state,
                        updated_at = excluded.updated_at
                    """,
                    (source_key, hash_text(source_key), board_hash, state, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO remote_items (
                        kind, number, source_key, state, url, title,
                        board_url_hash, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kind, number) DO UPDATE SET
                        source_key = excluded.source_key,
                        state = excluded.state,
                        url = excluded.url,
                        title = excluded.title,
                        board_url_hash = excluded.board_url_hash,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        item.kind,
                        item.number,
                        source_key,
                        item.state,
                        item.url,
                        item.title,
                        board_hash,
                        now,
                    ),
                )
            missing = before - observed
            if missing:
                placeholders = ",".join("?" for _ in missing)
                connection.execute(
                    f"""UPDATE candidates
                        SET state = 'remote_missing', updated_at = ?
                        WHERE source_key IN ({placeholders})""",
                    (now, *sorted(missing)),
                )
            connection.commit()
        self._refresh_index()
        return LedgerReconciliation(
            remote_markers=len(marked),
            recovered_sources=len(observed - before),
            missing_remote_sources=tuple(sorted(missing)),
        )

    def record_created(
        self,
        *,
        source_key: str,
        normalized_url: str,
        family: str,
        tenant: str,
        item: GitHubWorkItem,
    ) -> None:
        """Commit immediately after a successful or reconciled GitHub create."""

        if item.kind != "issue":
            raise ValueError("only created GitHub issues may enter the durable ledger")

        now = int(time.time())
        markers = parse_candidate_markers(item.body)
        board_hash = next(
            (marker_hash for marker_source, marker_hash in markers if marker_source == source_key),
            hash_text(normalized_url),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO candidates (
                    source_key, source_hash, normalized_url, normalized_url_hash,
                    family, tenant, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    normalized_url = excluded.normalized_url,
                    normalized_url_hash = excluded.normalized_url_hash,
                    family = excluded.family,
                    tenant = excluded.tenant,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (
                    source_key,
                    hash_text(source_key),
                    normalized_url,
                    hash_text(normalized_url),
                    family,
                    tenant,
                    f"remote_{item.kind}_{item.state.casefold()}",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO remote_items (
                    kind, number, source_key, state, url, title,
                    board_url_hash, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, number) DO UPDATE SET
                    source_key = excluded.source_key,
                    state = excluded.state,
                    url = excluded.url,
                    title = excluded.title,
                    board_url_hash = excluded.board_url_hash,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item.kind,
                    item.number,
                    source_key,
                    item.state,
                    item.url,
                    item.title,
                    board_hash,
                    now,
                ),
            )
            connection.commit()
        self._refresh_index()

    def find_source(self, source_key: str) -> LedgerRecord | None:
        return self._sources.get(source_key)

    def find_url(self, normalized_url: str) -> tuple[LedgerRecord, ...]:
        return self._urls.get(hash_text(normalized_url), ())

    def _refresh_index(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM candidates ORDER BY source_key").fetchall()
            remote_rows = connection.execute(
                """SELECT source_key, kind, number, state, url
                   FROM remote_items ORDER BY source_key, kind, number"""
            ).fetchall()
        references: dict[str, list[str]] = {}
        for item in remote_rows:
            references.setdefault(str(item["source_key"]), []).append(
                f"{item['kind']}:{item['number']} [{item['state']}] {item['url']}"
            )
        sources: dict[str, LedgerRecord] = {}
        urls: dict[str, list[LedgerRecord]] = {}
        for row in rows:
            source_key = str(row["source_key"])
            record = LedgerRecord(
                source_key=source_key,
                normalized_url=(
                    str(row["normalized_url"]) if row["normalized_url"] is not None else None
                ),
                state=str(row["state"]),
                references=tuple(references.get(source_key, ())),
            )
            sources[source_key] = record
            url_hash = row["normalized_url_hash"]
            if url_hash is not None:
                urls.setdefault(str(url_hash), []).append(record)
        self._sources = sources
        self._urls = {
            key: tuple(sorted(values, key=lambda value: value.source_key))
            for key, values in urls.items()
        }
