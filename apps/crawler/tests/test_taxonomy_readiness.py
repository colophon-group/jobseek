"""Tests for the exact retained-taxonomy readiness gate."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from contextlib import AbstractAsyncContextManager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src import cli, taxonomy_readiness
from src.cli import parse_args
from src.taxonomy_readiness import SAMPLE_SIZE, run_cli, verify_taxonomy_readiness
from src.typesense_schema import COLLECTIONS


def _location_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, SAMPLE_SIZE + 1):
        rows.append(
            {
                "id": index,
                "type": "macro" if index == 1 else "country" if index == 2 else "city",
                "lat": 47.0 + index / 100 if index == 3 else None,
                "lng": 8.0 + index / 100 if index == 3 else None,
                "slug": "eu" if index == 1 else f"location-{index}",
                "population": index * 1_000 if index >= 2 else None,
                "parent_id": 2 if index == 3 else None,
            }
        )
    return rows


def _localized_rows(
    id_field: str,
    prefix: str,
    *,
    include_aliases: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, SAMPLE_SIZE + 1):
        rows.extend(
            [
                {
                    id_field: index,
                    "locale": "en",
                    "name": f"{prefix} {index}",
                    "is_display": True,
                },
                {
                    id_field: index,
                    "locale": "de",
                    "name": f"{prefix} DE {index}",
                    "is_display": True,
                },
            ]
        )
        if include_aliases:
            rows.append(
                {
                    id_field: index,
                    "locale": "en",
                    "name": f"{prefix} alias {index}",
                    "is_display": False,
                }
            )
    return rows


def _occupation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, SAMPLE_SIZE + 1):
        common = {
            "id": index,
            "slug": f"occupation-{index}",
            "parent_id": 1 if index > 1 else None,
            "domain_id": 1,
            "domain_slug": "engineering",
            "locale": "en",
        }
        rows.extend(
            [
                {**common, "name": f"Occupation {index}", "is_display": True},
                {**common, "name": f"Occupation alias {index}", "is_display": False},
            ]
        )
    return rows


def _seniority_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, SAMPLE_SIZE + 1):
        common = {
            "id": index,
            "slug": f"seniority-{index}",
            "locale": "en",
        }
        rows.extend(
            [
                {**common, "name": f"Seniority {index}", "is_display": True},
                {**common, "name": f"Seniority alias {index}", "is_display": False},
            ]
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
        self.location_rows = _location_rows()
        self.location_name_rows = _localized_rows("location_id", "Location", include_aliases=True)
        self.location_macro_rows = [{"macro_id": 1, "country_id": 2}]
        self.occupation_rows = _occupation_rows()
        self.occupation_domain_name_rows = [
            {"domain_id": 1, "locale": "en", "name": "Engineering"},
            {"domain_id": 1, "locale": "de", "name": "Entwicklung"},
        ]
        self.seniority_rows = _seniority_rows()
        self.technology_rows = [
            {
                "id": index,
                "slug": f"technology-{index}",
                "name": f"Technology {index}",
                "category": "language" if index % 2 else None,
            }
            for index in range(1, SAMPLE_SIZE + 1)
        ]
        self.company_rows = [
            {
                "id": f"00000000-0000-0000-0000-{index:012d}",
                "industry": index,
                "industry_name": f"Industry {index}",
            }
            for index in range(1, SAMPLE_SIZE + 1)
        ]
        self.industry_name_rows = [
            {"industry_id": index, "locale": locale, "name": f"Industry {locale} {index}"}
            for index in range(1, SAMPLE_SIZE + 1)
            for locale in ("de", "fr", "it")
        ]

    def transaction(self, **kwargs: Any) -> _Transaction:
        self.transaction_kwargs = kwargs
        return _Transaction(self, kwargs)

    async def fetch(self, sql: str) -> list[dict[str, Any]]:
        if "FROM location_macro_member" in sql:
            name, rows = "location_macro", self.location_macro_rows
        elif "FROM location_name" in sql:
            name, rows = "location_name", self.location_name_rows
        elif "FROM location" in sql:
            name, rows = "location", self.location_rows
        elif "FROM occupation_domain_name" in sql:
            name, rows = "occupation_domain_name", self.occupation_domain_name_rows
        elif "FROM occupation o" in sql:
            name, rows = "occupation", self.occupation_rows
        elif "FROM seniority s" in sql:
            name, rows = "seniority", self.seniority_rows
        elif "FROM technology" in sql:
            name, rows = "technology", self.technology_rows
        elif "FROM industry_name" in sql:
            name, rows = "industry_name", self.industry_name_rows
        elif "FROM company c" in sql:
            name, rows = "company", self.company_rows
        else:
            raise AssertionError(f"unexpected SQL: {sql}")
        self.events.append(f"fetch:{name}")
        return copy.deepcopy(rows)


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


class _Documents:
    def __init__(self, client: _Typesense, name: str) -> None:
        self.client = client
        self.name = name

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        self.client.search_calls.append((self.name, copy.deepcopy(params)))
        documents = self.client.documents[self.name]
        page = params["page"]
        per_page = params["per_page"]
        offset = (page - 1) * per_page
        include_fields = params["include_fields"].split(",")
        hits = [
            {"document": {field: document[field] for field in include_fields if field in document}}
            for document in documents[offset : offset + per_page]
        ]
        return {"found": len(documents), "hits": hits}


class _Collection:
    def __init__(self, client: _Typesense, name: str) -> None:
        self.client = client
        self.name = name
        self.documents = _Documents(client, name)

    def retrieve(self) -> dict[str, Any]:
        self.client.metadata_calls.append(self.name)
        if self.client.retrieve_error_for == self.name:
            raise RuntimeError("secret operations key should not be printed")
        return copy.deepcopy(self.client.metadata[self.name])


class _Collections:
    def __init__(self, client: _Typesense) -> None:
        self.client = client

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(self.client, name)


class _Typesense:
    def __init__(
        self,
        authoritative: dict[str, taxonomy_readiness.AuthoritativeCollection],
    ) -> None:
        schemas = {collection["name"]: collection for collection in COLLECTIONS}
        self.documents = {
            name: copy.deepcopy(list(collection.documents))
            for name, collection in authoritative.items()
        }
        self.metadata = {
            "job_posting": {
                "num_documents": 0,
                "fields": copy.deepcopy(schemas["job_posting"]["fields"]),
            },
            **{
                name: {
                    "num_documents": len(documents),
                    "fields": copy.deepcopy(schemas[name]["fields"]),
                }
                for name, documents in self.documents.items()
            },
        }
        self.metadata_calls: list[str] = []
        self.search_calls: list[tuple[str, dict[str, Any]]] = []
        self.retrieve_error_for: str | None = None
        self.collections = _Collections(self)


async def _authoritative(
    connection: _Connection,
) -> dict[str, taxonomy_readiness.AuthoritativeCollection]:
    connection.events.clear()
    return await taxonomy_readiness._load_authoritative_snapshot(  # pyright: ignore[reportPrivateUsage]
        _Pool(connection)  # type: ignore[arg-type]
    )


async def test_ready_evidence_compares_every_static_field_in_one_snapshot() -> None:
    connection = _Connection()
    typesense = _Typesense(await _authoritative(connection))
    connection.events.clear()

    evidence = await verify_taxonomy_readiness(
        _Pool(connection),  # type: ignore[arg-type]
        typesense,
        page_size=3,
    )

    assert evidence["status"] == "ready"
    assert evidence["coverage"] == {
        "document_counts": "exact",
        "documents": "full",
        "fields": "static_consumer_contract",
        "collections": ["location", "occupation", "seniority", "technology", "company"],
        "excluded_dynamic_fields": ["active_posting_count", "has_active_postings"],
    }
    assert connection.transaction_kwargs == {
        "isolation": "repeatable_read",
        "readonly": True,
    }
    assert connection.events[0] == "snapshot_enter"
    assert connection.events[-1] == "snapshot_exit"
    assert typesense.metadata_calls == [
        "job_posting",
        "location",
        "occupation",
        "seniority",
        "technology",
        "company",
    ]
    assert len(typesense.search_calls) == 20
    for collection in evidence["collections"].values():
        assert collection["authoritative_document_count"] == SAMPLE_SIZE
        assert collection["compared_document_count"] == SAMPLE_SIZE
        assert collection["expected_projection_sha256"] == collection["typesense_projection_sha256"]
        assert collection["mismatch_details"] == []


async def test_hierarchy_and_localized_industry_drift_fail_with_redacted_evidence(
    capsys,
) -> None:
    connection = _Connection()
    typesense = _Typesense(await _authoritative(connection))
    typesense.documents["location"][2]["ancestor_ids"] = [3]
    typesense.documents["company"][0]["industry_name_de"] = "private stale value"

    exit_code = await run_cli(_Pool(connection), typesense)  # type: ignore[arg-type]
    evidence = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert evidence["status"] == "not_ready"
    location_mismatch = evidence["collections"]["location"]["mismatch_details"][0]
    company_mismatch = evidence["collections"]["company"]["mismatch_details"][0]
    assert location_mismatch["kind"] == "field_mismatch"
    assert location_mismatch["fields"] == ["ancestor_ids"]
    assert company_mismatch["fields"] == ["industry_name_de"]
    assert len(company_mismatch["document_key_sha256"]) == 64
    assert "private stale value" not in json.dumps(evidence)


async def test_nonindexed_localized_industry_schema_fails_the_gate() -> None:
    connection = _Connection()
    typesense = _Typesense(await _authoritative(connection))
    company_fields = typesense.metadata["company"]["fields"]
    next(field for field in company_fields if field["name"] == "industry_name_de")["index"] = False

    evidence = await verify_taxonomy_readiness(_Pool(connection), typesense)  # type: ignore[arg-type]

    assert evidence["status"] == "not_ready"
    assert evidence["schema"]["status"] == "not_ready"
    assert evidence["schema"]["collections"]["company"]["mismatches"] == [
        {"field": "industry_name_de", "attributes": ["index"]}
    ]


async def test_count_drift_fails_even_when_search_documents_match() -> None:
    connection = _Connection()
    typesense = _Typesense(await _authoritative(connection))
    typesense.metadata["occupation"]["num_documents"] += 1

    evidence = await verify_taxonomy_readiness(_Pool(connection), typesense)  # type: ignore[arg-type]

    assert evidence["status"] == "not_ready"
    assert evidence["collections"]["occupation"]["count_matches"] is False
    assert evidence["collections"]["occupation"]["mismatch_count"] == 0


async def test_typesense_error_is_nonzero_and_redacted(capsys) -> None:
    connection = _Connection()
    typesense = _Typesense(await _authoritative(connection))
    typesense.retrieve_error_for = "location"

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
    connection.seniority_rows = connection.seniority_rows[:-2]
    typesense = _Typesense(await _authoritative(connection))

    evidence = await verify_taxonomy_readiness(_Pool(connection), typesense)  # type: ignore[arg-type]

    seniority = evidence["collections"]["seniority"]
    assert evidence["status"] == "not_ready"
    assert seniority["count_matches"] is True
    assert seniority["minimum_satisfied"] is False
    assert seniority["authoritative_document_count"] == SAMPLE_SIZE - 1


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
