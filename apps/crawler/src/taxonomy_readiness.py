"""Fail-closed local-Postgres -> Typesense taxonomy readiness verification.

This is intentionally separate from :mod:`src.sync`: sync is an eventually
consistent producer and logs downstream failures, while this module is an
operator gate that must return a non-zero status for every unverifiable or
divergent state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

SAMPLE_SIZE = 10
_SAFE_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")

_LOCATION_COUNT_SQL = "SELECT COUNT(*)::int FROM location"
_LOCATION_SAMPLE_SQL = """
    SELECT l.id,
           COALESCE(l.slug, '') AS slug,
           COALESCE(MAX(ln.name) FILTER (
               WHERE ln.locale = 'en' AND ln.is_display
           ), '') AS name_en,
           MAX(ln.name) FILTER (
               WHERE ln.locale = 'de' AND ln.is_display
           ) AS name_de,
           MAX(ln.name) FILTER (
               WHERE ln.locale = 'fr' AND ln.is_display
           ) AS name_fr,
           MAX(ln.name) FILTER (
               WHERE ln.locale = 'it' AND ln.is_display
           ) AS name_it
    FROM location l
    LEFT JOIN location_name ln
      ON ln.location_id = l.id
     AND ln.locale IN ('en', 'de', 'fr', 'it')
    GROUP BY l.id, l.slug
    ORDER BY md5('location:' || l.id::text), l.id
    LIMIT $1
"""

_OCCUPATION_COUNT_SQL = """
    SELECT COUNT(*)::int
    FROM (
        SELECT on2.occupation_id, on2.locale
        FROM occupation_name on2
        WHERE on2.locale <> '*'
        GROUP BY on2.occupation_id, on2.locale
        HAVING BOOL_OR(on2.is_display)
    ) documents
"""
_OCCUPATION_SAMPLE_SQL = """
    SELECT o.id,
           o.slug,
           on2.locale,
           MIN(on2.name) FILTER (WHERE on2.is_display) AS name
    FROM occupation o
    JOIN occupation_name on2 ON on2.occupation_id = o.id
    WHERE on2.locale <> '*'
    GROUP BY o.id, o.slug, on2.locale
    HAVING BOOL_OR(on2.is_display)
    ORDER BY md5('occupation:' || o.id::text || '-' || on2.locale), o.id, on2.locale
    LIMIT $1
"""

_SENIORITY_COUNT_SQL = """
    SELECT COUNT(*)::int
    FROM (
        SELECT sn.seniority_id, sn.locale
        FROM seniority_name sn
        WHERE sn.locale <> '*'
        GROUP BY sn.seniority_id, sn.locale
        HAVING BOOL_OR(sn.is_display)
    ) documents
"""
_SENIORITY_SAMPLE_SQL = """
    SELECT s.id,
           s.slug,
           sn.locale,
           MIN(sn.name) FILTER (WHERE sn.is_display) AS name
    FROM seniority s
    JOIN seniority_name sn ON sn.seniority_id = s.id
    WHERE sn.locale <> '*'
    GROUP BY s.id, s.slug, sn.locale
    HAVING BOOL_OR(sn.is_display)
    ORDER BY md5('seniority:' || s.id::text || '-' || sn.locale), s.id, sn.locale
    LIMIT $1
"""

_TECHNOLOGY_COUNT_SQL = "SELECT COUNT(*)::int FROM technology"
_TECHNOLOGY_SAMPLE_SQL = """
    SELECT id, slug, COALESCE(NULLIF(name, ''), slug) AS name
    FROM technology
    ORDER BY md5('technology:' || id::text), id
    LIMIT $1
"""


@dataclass(frozen=True)
class TaxonomySpec:
    collection: str
    id_field: str
    display_fields: tuple[str, ...]
    count_sql: str
    sample_sql: str

    @property
    def compared_fields(self) -> tuple[str, ...]:
        return ("id", self.id_field, "slug", *self.display_fields)


@dataclass(frozen=True)
class AuthoritativeTaxonomy:
    document_count: int
    samples: tuple[dict[str, Any], ...]


_SPECS = (
    TaxonomySpec(
        collection="location",
        id_field="location_id",
        display_fields=("name_en", "name_de", "name_fr", "name_it"),
        count_sql=_LOCATION_COUNT_SQL,
        sample_sql=_LOCATION_SAMPLE_SQL,
    ),
    TaxonomySpec(
        collection="occupation",
        id_field="occupation_id",
        display_fields=("name", "locale"),
        count_sql=_OCCUPATION_COUNT_SQL,
        sample_sql=_OCCUPATION_SAMPLE_SQL,
    ),
    TaxonomySpec(
        collection="seniority",
        id_field="seniority_id",
        display_fields=("name", "locale"),
        count_sql=_SENIORITY_COUNT_SQL,
        sample_sql=_SENIORITY_SAMPLE_SQL,
    ),
    TaxonomySpec(
        collection="technology",
        id_field="technology_id",
        display_fields=("name",),
        count_sql=_TECHNOLOGY_COUNT_SQL,
        sample_sql=_TECHNOLOGY_SAMPLE_SQL,
    ),
)


def _document_from_row(spec: TaxonomySpec, row: Mapping[str, Any]) -> dict[str, Any]:
    entity_id = int(row["id"])
    locale = row.get("locale")
    document_id = f"{entity_id}-{locale}" if locale is not None else str(entity_id)
    document: dict[str, Any] = {
        "id": document_id,
        spec.id_field: entity_id,
        "slug": row["slug"],
    }
    for field in spec.display_fields:
        value = row.get(field)
        # Location's non-English display fields are optional in the producer.
        if field in {"name_de", "name_fr", "name_it"} and not value:
            continue
        document[field] = value
    return document


async def _load_authoritative_snapshot(
    local_pool: asyncpg.Pool,
    *,
    sample_size: int = SAMPLE_SIZE,
) -> dict[str, AuthoritativeTaxonomy]:
    """Read all four authoritative views from one immutable local snapshot."""

    if sample_size < SAMPLE_SIZE:
        raise ValueError(f"sample_size must be at least {SAMPLE_SIZE}")

    authoritative: dict[str, AuthoritativeTaxonomy] = {}
    async with (
        local_pool.acquire() as conn,
        conn.transaction(isolation="repeatable_read", readonly=True),
    ):
        for spec in _SPECS:
            count = int(await conn.fetchval(spec.count_sql))
            rows = await conn.fetch(spec.sample_sql, sample_size)
            authoritative[spec.collection] = AuthoritativeTaxonomy(
                document_count=count,
                samples=tuple(_document_from_row(spec, row) for row in rows),
            )
    return authoritative


def _validate_document_ids(documents: Sequence[Mapping[str, Any]]) -> None:
    for document in documents:
        document_id = str(document["id"])
        if not _SAFE_DOCUMENT_ID.fullmatch(document_id):
            raise RuntimeError("authoritative taxonomy produced an unsafe document id")


def _fetch_typesense_state(
    client: Any,
    authoritative: Mapping[str, AuthoritativeTaxonomy],
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    """Fetch counts and all deterministic samples in five Typesense calls."""

    counts: dict[str, int] = {}
    searches: list[dict[str, Any]] = []
    for spec in _SPECS:
        metadata = client.collections[spec.collection].retrieve()
        count = metadata.get("num_documents")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise RuntimeError("Typesense collection returned an invalid document count")
        counts[spec.collection] = count

        samples = authoritative[spec.collection].samples
        _validate_document_ids(samples)
        ids = ",".join(str(document["id"]) for document in samples)
        filter_by = f"id:=[{ids}]" if ids else "id:=__taxonomy_readiness_empty__"
        searches.append(
            {
                "collection": spec.collection,
                "q": "*",
                "query_by": spec.display_fields[0],
                "filter_by": filter_by,
                "include_fields": ",".join(spec.compared_fields),
                "per_page": max(1, len(samples)),
            }
        )

    response = client.multi_search.perform({"searches": searches})
    results = response.get("results")
    if not isinstance(results, list) or len(results) != len(_SPECS):
        raise RuntimeError("Typesense multi-search returned an invalid result set")

    sampled_documents: dict[str, list[dict[str, Any]]] = {}
    for spec, result in zip(_SPECS, results, strict=True):
        if not isinstance(result, dict):
            raise RuntimeError("Typesense multi-search returned an invalid taxonomy result")
        hits = result.get("hits")
        if not isinstance(hits, list):
            raise RuntimeError("Typesense multi-search taxonomy result has no hits")
        documents: list[dict[str, Any]] = []
        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("document"), dict):
                raise RuntimeError("Typesense multi-search returned an invalid taxonomy hit")
            documents.append(hit["document"])
        sampled_documents[spec.collection] = documents

    return counts, sampled_documents


def _digest(documents: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        sorted((dict(document) for document in documents), key=lambda item: str(item.get("id"))),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _redacted_key(document_id: str) -> str:
    return hashlib.sha256(document_id.encode()).hexdigest()


def _compare_taxonomy(
    spec: TaxonomySpec,
    expected: AuthoritativeTaxonomy,
    actual_count: int,
    actual_documents: Sequence[Mapping[str, Any]],
    *,
    minimum_sample_size: int,
) -> dict[str, Any]:
    expected_by_id = {str(document["id"]): document for document in expected.samples}
    actual_by_id: dict[str, Mapping[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for document in actual_documents:
        document_id = str(document.get("id", ""))
        if document_id in actual_by_id:
            duplicate_ids.add(document_id)
        actual_by_id[document_id] = document

    mismatches: list[dict[str, Any]] = []
    for document_id, expected_document in expected_by_id.items():
        actual_document = actual_by_id.get(document_id)
        if actual_document is None:
            mismatches.append({"kind": "missing", "sample_key_sha256": _redacted_key(document_id)})
            continue
        fields = [
            field
            for field in spec.compared_fields
            if actual_document.get(field) != expected_document.get(field)
            or (field in actual_document) != (field in expected_document)
        ]
        if fields:
            mismatches.append(
                {
                    "kind": "field_mismatch",
                    "sample_key_sha256": _redacted_key(document_id),
                    "fields": fields,
                }
            )

    for document_id in sorted(duplicate_ids):
        mismatches.append({"kind": "duplicate", "sample_key_sha256": _redacted_key(document_id)})
    for document_id in sorted(actual_by_id.keys() - expected_by_id.keys()):
        mismatches.append({"kind": "unexpected", "sample_key_sha256": _redacted_key(document_id)})

    actual_projection = [
        {field: document[field] for field in spec.compared_fields if field in document}
        for document in actual_documents
    ]
    sufficient_sample = len(expected.samples) >= minimum_sample_size
    count_matches = actual_count == expected.document_count
    ready = count_matches and sufficient_sample and not mismatches
    return {
        "status": "ready" if ready else "not_ready",
        "authoritative_document_count": expected.document_count,
        "typesense_document_count": actual_count,
        "count_matches": count_matches,
        "required_sample_size": minimum_sample_size,
        "authoritative_sample_size": len(expected.samples),
        "typesense_sample_size": len(actual_documents),
        "sample_size_sufficient": sufficient_sample,
        "expected_sample_sha256": _digest(expected.samples),
        "typesense_sample_sha256": _digest(actual_projection),
        "mismatches": mismatches,
    }


async def verify_taxonomy_readiness(
    local_pool: asyncpg.Pool,
    typesense_client: Any,
    *,
    sample_size: int = SAMPLE_SIZE,
) -> dict[str, Any]:
    """Return redacted evidence and never downgrade verification failures."""

    if typesense_client is None:
        raise RuntimeError("Typesense operations client is not configured")

    authoritative = await _load_authoritative_snapshot(local_pool, sample_size=sample_size)
    typesense_counts, typesense_samples = await asyncio.to_thread(
        _fetch_typesense_state,
        typesense_client,
        authoritative,
    )
    taxonomy_evidence = {
        spec.collection: _compare_taxonomy(
            spec,
            authoritative[spec.collection],
            typesense_counts[spec.collection],
            typesense_samples[spec.collection],
            minimum_sample_size=sample_size,
        )
        for spec in _SPECS
    }
    ready = all(item["status"] == "ready" for item in taxonomy_evidence.values())
    return {
        "command": "verify-typesense-taxonomies",
        "status": "ready" if ready else "not_ready",
        "authority": "local_postgres",
        "snapshot": {"isolation": "repeatable_read", "read_only": True},
        "typesense_calls": {"collection_metadata": 4, "multi_search": 1, "total": 5},
        "taxonomies": taxonomy_evidence,
    }


def emit_evidence(evidence: Mapping[str, Any]) -> None:
    """Write exactly one machine-readable evidence record."""

    json.dump(evidence, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()


async def run_cli(local_pool: asyncpg.Pool, typesense_client: Any) -> int:
    """Run the strict gate, emitting redacted JSON for success and failure."""

    try:
        evidence = await verify_taxonomy_readiness(local_pool, typesense_client)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as exc:
        evidence = {
            "command": "verify-typesense-taxonomies",
            "status": "error",
            "authority": "local_postgres",
            "error_class": type(exc).__name__,
        }
        emit_evidence(evidence)
        return 1

    emit_evidence(evidence)
    return 0 if evidence["status"] == "ready" else 1


__all__ = [
    "SAMPLE_SIZE",
    "emit_evidence",
    "run_cli",
    "verify_taxonomy_readiness",
]
