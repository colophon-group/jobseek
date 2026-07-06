"""Tests for the root Typesense taxonomy seed script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT_DIR / "scripts" / "typesense-seed-taxonomy.py"


def _load_seed_module() -> Any:
    spec = importlib.util.spec_from_file_location("typesense_seed_taxonomy", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDocuments:
    def __init__(self, client: FakeTypesenseClient, collection: str) -> None:
        self.client = client
        self.collection = collection

    def import_(self, docs: list[dict[str, Any]], options: dict[str, str]) -> list[dict[str, bool]]:
        self.client.imports.append((self.collection, docs, options))
        return [{"success": True} for _ in docs]


class FakeCollection:
    def __init__(self, client: FakeTypesenseClient, collection: str) -> None:
        self.documents = FakeDocuments(client, collection)


class FakeCollections:
    def __init__(self, client: FakeTypesenseClient) -> None:
        self.client = client

    def __getitem__(self, collection: str) -> FakeCollection:
        return FakeCollection(self.client, collection)


class FakeTypesenseClient:
    def __init__(self) -> None:
        self.collections = FakeCollections(self)
        self.imports: list[tuple[str, list[dict[str, Any]], dict[str, str]]] = []


class FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        normalized = " ".join(query.split())

        if "unnest(location_ids) AS lid" in normalized:
            return [{"lid": 1, "cnt": 3}]
        if "SELECT occupation_id, count(*)::int AS cnt" in normalized:
            return []
        if "SELECT seniority_id, count(*)::int AS cnt" in normalized:
            return []
        if "unnest(technology_ids) AS tid" in normalized:
            return []
        if "SELECT company_id, count(*)::int AS active" in normalized:
            return [{"company_id": 42, "active": 2, "year_cnt": 1}]
        if "FROM location l LEFT JOIN location lo" in normalized:
            return [
                {
                    "id": 999,
                    "type": "city",
                    "population": None,
                    "lat": None,
                    "lng": None,
                    "parent_id": None,
                    "slug": "obsolete",
                }
            ]
        if (
            normalized
            == "SELECT id, type::text AS type, population, lat, lng, parent_id, slug FROM location"
        ):
            return [
                {
                    "id": 1,
                    "type": "city",
                    "population": 434008,
                    "lat": 47.3769,
                    "lng": 8.5417,
                    "parent_id": None,
                    "slug": "zurich",
                }
            ]
        if normalized == "SELECT location_id, locale, name, is_display FROM location_name":
            return [{"location_id": 1, "locale": "en", "name": "Zurich", "is_display": True}]
        if normalized == "SELECT id, slug FROM occupation":
            return []
        if normalized == "SELECT occupation_id, locale, name, is_display FROM occupation_name":
            return []
        if normalized == "SELECT id, slug FROM seniority":
            return []
        if normalized == "SELECT seniority_id, locale, name, is_display FROM seniority_name":
            return []
        if normalized == "SELECT id, slug, name, category FROM technology":
            return []
        if normalized == "SELECT DISTINCT company_id, board_slug FROM job_board":
            return [{"company_id": 42, "board_slug": "acme-careers"}]

        raise AssertionError(f"Unexpected query: {query}")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_seed_locations_uses_current_location_schema_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_seed_module()
    conn = FakeConnection()
    client = FakeTypesenseClient()

    async def fake_connect(db_url: str, ssl: str) -> FakeConnection:
        assert db_url == "postgresql://example/crawler"
        assert ssl == "disable"
        return conn

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "companies.csv").write_text(
        "slug,name,icon_url,industry\nacme,Acme,,7\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://example/crawler")
    monkeypatch.setattr(module, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(module, "_ts", lambda: client)
    monkeypatch.setattr(module.asyncpg, "connect", fake_connect)

    await module.seed()

    joined_queries = "\n".join(conn.queries)
    assert "FROM location l LEFT JOIN location lo" not in joined_queries
    assert conn.closed is True

    location_import = next(item for item in client.imports if item[0] == "location")
    assert location_import[1] == [
        {
            "id": "1",
            "location_id": 1,
            "slug": "zurich",
            "name_en": "Zurich",
            "type": "city",
            "has_active_postings": True,
            "active_posting_count": 3,
            "coordinates": [47.3769, 8.5417],
            "population": 434008,
        }
    ]
