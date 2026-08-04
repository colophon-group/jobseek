"""Tests for the strict retained-taxonomy readiness gate."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import AbstractAsyncContextManager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src import cli
from src.cli import parse_args
from src.taxonomy_readiness import SAMPLE_SIZE, run_cli, verify_taxonomy_readiness


def _sample_rows(kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, SAMPLE_SIZE + 1):
        if kind == "location":
            rows.append(
                {
                    "id": index,
                    "slug": f"location-{index}",
                    "name_en": f"Location {index}",
                    "name_de": f"Ort {index}",
                    "name_fr": None,
                    "name_it": None,
                }
            )
        elif kind in {"occupation", "seniority"}:
            rows.append(
                {
                    "id": index,
                    "slug": f"{kind}-{index}",
                    "locale": "en",
                    "name": f"{kind.title()} {index}",
                }
            )
        else:
            rows.append(
                {
                    "id": index,
                    "slug": f"technology-{index}",
                    "name": f"Technology {index}",
                }
            )
    return rows


class _Transaction(AbstractAsyncContextManager[None]):
    def __init__(self, connection: _Connection, kwargs: dict[str, Any]) -> None:
        self.connection = connection
        self.kwargs = kwargs

    async def __aenter__(self) -> None:
        self.connection.events.append("snapshot_enter")

    async def __aexit__(self, *_args: object) -> None:
        self.connection.events.append("snapshot_exit")


class _Connection:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.transaction_kwargs: dict[str, Any] | None = None
        self.rows = {
            "location": _sample_rows("location"),
            "occupation": _sample_rows("occupation"),
            "seniority": _sample_rows("seniority"),
            "technology": _sample_rows("technology"),
        }

    def transaction(self, **kwargs: Any) -> _Transaction:
        self.transaction_kwargs = kwargs
        return _Transaction(self, kwargs)

    @staticmethod
    def _taxonomy(sql: str) -> str:
        if "FROM location" in sql:
            return "location"
        if "occupation_name" in sql:
            return "occupation"
        if "seniority_name" in sql:
            return "seniority"
        if "FROM technology" in sql:
            return "technology"
        raise AssertionError(f"unexpected SQL: {sql}")

    async def fetchval(self, sql: str) -> int:
        taxonomy = self._taxonomy(sql)
        self.events.append(f"count:{taxonomy}")
        return len(self.rows[taxonomy])

    async def fetch(self, sql: str, limit: int) -> list[dict[str, Any]]:
        taxonomy = self._taxonomy(sql)
        assert "ORDER BY md5(" in sql
        self.events.append(f"sample:{taxonomy}")
        return self.rows[taxonomy][:limit]


class _Acquire(AbstractAsyncContextManager[_Connection]):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


class _Collection:
    def __init__(self, client: _Typesense, name: str) -> None:
        self.client = client
        self.name = name

    def retrieve(self) -> dict[str, int]:
        self.client.metadata_calls.append(self.name)
        return {"num_documents": self.client.counts[self.name]}


class _Collections:
    def __init__(self, client: _Typesense) -> None:
        self.client = client

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(self.client, name)


class _MultiSearch:
    def __init__(self, client: _Typesense) -> None:
        self.client = client

    def perform(self, request: dict[str, Any]) -> dict[str, Any]:
        self.client.multi_search_calls.append(request)
        results = []
        for search in request["searches"]:
            collection = search["collection"]
            documents = self.client.documents[collection]
            results.append({"hits": [{"document": document} for document in documents]})
        return {"results": results}


class _Typesense:
    def __init__(self, connection: _Connection) -> None:
        self.counts = {name: len(rows) for name, rows in connection.rows.items()}
        self.documents = {
            "location": [
                {
                    "id": str(row["id"]),
                    "location_id": row["id"],
                    "slug": row["slug"],
                    "name_en": row["name_en"],
                    "name_de": row["name_de"],
                }
                for row in connection.rows["location"]
            ],
            "occupation": [
                {
                    "id": f"{row['id']}-{row['locale']}",
                    "occupation_id": row["id"],
                    "slug": row["slug"],
                    "name": row["name"],
                    "locale": row["locale"],
                }
                for row in connection.rows["occupation"]
            ],
            "seniority": [
                {
                    "id": f"{row['id']}-{row['locale']}",
                    "seniority_id": row["id"],
                    "slug": row["slug"],
                    "name": row["name"],
                    "locale": row["locale"],
                }
                for row in connection.rows["seniority"]
            ],
            "technology": [
                {
                    "id": str(row["id"]),
                    "technology_id": row["id"],
                    "slug": row["slug"],
                    "name": row["name"],
                }
                for row in connection.rows["technology"]
            ],
        }
        self.metadata_calls: list[str] = []
        self.multi_search_calls: list[dict[str, Any]] = []
        self.collections = _Collections(self)
        self.multi_search = _MultiSearch(self)


async def test_ready_evidence_uses_one_read_only_snapshot_and_five_typesense_calls() -> None:
    connection = _Connection()
    typesense = _Typesense(connection)

    evidence = await verify_taxonomy_readiness(_Pool(connection), typesense)  # type: ignore[arg-type]

    assert evidence["status"] == "ready"
    assert connection.transaction_kwargs == {
        "isolation": "repeatable_read",
        "readonly": True,
    }
    assert connection.events[0] == "snapshot_enter"
    assert connection.events[-1] == "snapshot_exit"
    assert typesense.metadata_calls == ["location", "occupation", "seniority", "technology"]
    assert len(typesense.multi_search_calls) == 1
    for taxonomy in evidence["taxonomies"].values():
        assert taxonomy["authoritative_sample_size"] == SAMPLE_SIZE
        assert taxonomy["typesense_sample_size"] == SAMPLE_SIZE
        assert taxonomy["expected_sample_sha256"] == taxonomy["typesense_sample_sha256"]
        assert taxonomy["mismatches"] == []


async def test_count_and_display_drift_fail_with_redacted_evidence(capsys) -> None:
    connection = _Connection()
    typesense = _Typesense(connection)
    typesense.counts["occupation"] += 1
    typesense.documents["technology"][0]["name"] = "private stale display value"

    exit_code = await run_cli(_Pool(connection), typesense)  # type: ignore[arg-type]
    evidence = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert evidence["status"] == "not_ready"
    assert evidence["taxonomies"]["occupation"]["count_matches"] is False
    mismatch = evidence["taxonomies"]["technology"]["mismatches"][0]
    assert mismatch["kind"] == "field_mismatch"
    assert mismatch["fields"] == ["name"]
    assert len(mismatch["sample_key_sha256"]) == 64
    assert "private stale display value" not in json.dumps(evidence)


async def test_typesense_error_is_nonzero_and_redacted(capsys) -> None:
    connection = _Connection()
    typesense = _Typesense(connection)

    def fail_retrieve() -> dict[str, int]:
        raise RuntimeError("secret operations key should not be printed")

    with patch.object(_Collection, "retrieve", side_effect=fail_retrieve):
        exit_code = await run_cli(_Pool(connection), typesense)  # type: ignore[arg-type]

    evidence = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert evidence == {
        "authority": "local_postgres",
        "command": "verify-typesense-taxonomies",
        "error_class": "RuntimeError",
        "status": "error",
    }


async def test_fewer_than_ten_authoritative_documents_fails_the_gate() -> None:
    connection = _Connection()
    connection.rows["seniority"] = connection.rows["seniority"][: SAMPLE_SIZE - 1]
    typesense = _Typesense(connection)

    evidence = await verify_taxonomy_readiness(_Pool(connection), typesense)  # type: ignore[arg-type]

    seniority = evidence["taxonomies"]["seniority"]
    assert evidence["status"] == "not_ready"
    assert seniority["count_matches"] is True
    assert seniority["sample_size_sufficient"] is False
    assert seniority["authoritative_sample_size"] == SAMPLE_SIZE - 1


def test_crawler_cli_exposes_taxonomy_readiness_gate(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "verify-typesense-taxonomies"])

    args = parse_args()

    assert args.command == "verify-typesense-taxonomies"


async def test_cli_dispatch_uses_local_pool_and_fails_closed(monkeypatch) -> None:
    local_pool = object()
    typesense_client = object()
    verify = AsyncMock(return_value=1)
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(command="verify-typesense-taxonomies"),
    )
    monkeypatch.setattr(cli, "create_local_pool", AsyncMock(return_value=local_pool))
    monkeypatch.setattr(cli, "close_all_pools", AsyncMock())

    with (
        patch("src.taxonomy_readiness.run_cli", new=verify),
        patch("src.typesense_client.get_typesense_client", return_value=typesense_client),
        pytest.raises(SystemExit) as exc_info,
    ):
        await cli.run()

    assert exc_info.value.code == 1
    verify.assert_awaited_once_with(local_pool, typesense_client)
