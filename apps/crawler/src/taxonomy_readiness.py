"""Fail-closed local-Postgres -> Typesense taxonomy contract evidence.

The registry sync is an eventually consistent producer and intentionally logs
downstream failures. This module is the operator gate used after a protected
sync/backfill: it compares every static consumer-facing taxonomy field against
one read-only local PostgreSQL snapshot, verifies the live Typesense schema,
and returns a non-zero status for any unverifiable or divergent state.

Posting-derived active counts are deliberately excluded from this static
projection and remain governed by ``refresh-typesense``. The protected
maintenance operation separately proves the posting corpus with a full
backfill followed by a fresh 256-partition reconciliation under the same host
mutation lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import asyncpg

from src.sync import _LOCATION_MACRO_ALIASES

# Retained for compatibility with operator/tests that referred to the old
# ten-document sample floor. The verifier now compares every document, while
# still refusing an implausibly small taxonomy snapshot.
SAMPLE_SIZE = 10
DOCUMENT_PAGE_SIZE = 250
MAX_MISMATCH_DETAILS = 20
_SAFE_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")

_LOCATION_ROWS_SQL = """
    SELECT id, type, lat, lng, slug, population, parent_id
    FROM location
    ORDER BY id
"""
_LOCATION_NAMES_SQL = """
    SELECT location_id, locale, name, is_display
    FROM location_name
    WHERE locale IN ('en', 'de', 'fr', 'it')
    ORDER BY location_id, locale, name
"""
_LOCATION_MACROS_SQL = """
    SELECT macro_id, country_id
    FROM location_macro_member
    ORDER BY macro_id, country_id
"""
_OCCUPATION_ROWS_SQL = """
    SELECT o.id, o.slug, o.parent_id, o.domain_id,
           on2.locale, on2.name, on2.is_display,
           d.slug AS domain_slug
    FROM occupation o
    JOIN occupation_name on2 ON on2.occupation_id = o.id
    LEFT JOIN occupation_domain d ON d.id = o.domain_id
    ORDER BY o.id, on2.locale, on2.name
"""
_OCCUPATION_DOMAIN_NAMES_SQL = """
    SELECT domain_id, locale, name
    FROM occupation_domain_name
    WHERE is_display
    ORDER BY domain_id, locale, name
"""
_SENIORITY_ROWS_SQL = """
    SELECT s.id, s.slug, sn.locale, sn.name, sn.is_display
    FROM seniority s
    JOIN seniority_name sn ON sn.seniority_id = s.id
    ORDER BY s.id, sn.locale, sn.name
"""
_TECHNOLOGY_ROWS_SQL = """
    SELECT id, slug, name, category
    FROM technology
    ORDER BY id
"""
_COMPANY_ROWS_SQL = """
    SELECT c.id, c.industry, i.name AS industry_name
    FROM company c
    LEFT JOIN industry i ON i.id = c.industry
    ORDER BY c.id
"""
_INDUSTRY_NAMES_SQL = """
    SELECT industry_id, locale, name
    FROM industry_name
    WHERE is_display AND locale IN ('de', 'fr', 'it')
    ORDER BY industry_id, locale, name
"""


@dataclass(frozen=True, slots=True)
class FieldRequirement:
    name: str
    field_type: str
    index: bool | None = None
    facet: bool | None = None


@dataclass(frozen=True, slots=True)
class CollectionSpec:
    collection: str
    query_by: str
    compared_fields: tuple[str, ...]
    unordered_fields: frozenset[str]
    minimum_documents: int
    schema_fields: tuple[FieldRequirement, ...]


@dataclass(frozen=True, slots=True)
class AuthoritativeCollection:
    documents: tuple[dict[str, Any], ...]

    @property
    def document_count(self) -> int:
        return len(self.documents)


_LOCATION_SPEC = CollectionSpec(
    collection="location",
    query_by="name_en",
    compared_fields=(
        "id",
        "location_id",
        "slug",
        "name_en",
        "name_de",
        "name_fr",
        "name_it",
        "aliases",
        "parent_name",
        "parent_id",
        "ancestor_ids",
        "member_country_ids",
        "type",
        "coordinates",
        "population",
    ),
    unordered_fields=frozenset({"aliases", "ancestor_ids", "member_country_ids"}),
    minimum_documents=SAMPLE_SIZE,
    schema_fields=(
        FieldRequirement("location_id", "int32"),
        FieldRequirement("slug", "string", index=True),
        FieldRequirement("name_en", "string", index=True),
        FieldRequirement("name_de", "string", index=True),
        FieldRequirement("name_fr", "string", index=True),
        FieldRequirement("name_it", "string", index=True),
        FieldRequirement("aliases", "string[]", index=True),
        FieldRequirement("parent_name", "string", index=True),
        FieldRequirement("parent_id", "int32", index=True),
        FieldRequirement("ancestor_ids", "int32[]", index=True, facet=True),
        FieldRequirement("member_country_ids", "int32[]", index=False),
        FieldRequirement("type", "string", index=True, facet=True),
        FieldRequirement("coordinates", "geopoint", index=True),
        FieldRequirement("population", "int32", index=True),
    ),
)
_OCCUPATION_SPEC = CollectionSpec(
    collection="occupation",
    query_by="name",
    compared_fields=(
        "id",
        "occupation_id",
        "slug",
        "name",
        "aliases",
        "parent_id",
        "domain_id",
        "domain_slug",
        "domain_name",
        "locale",
    ),
    unordered_fields=frozenset({"aliases"}),
    minimum_documents=SAMPLE_SIZE,
    schema_fields=(
        FieldRequirement("occupation_id", "int32"),
        FieldRequirement("slug", "string", index=True),
        FieldRequirement("name", "string", index=True),
        FieldRequirement("aliases", "string[]", index=True),
        FieldRequirement("parent_id", "int32", index=True),
        FieldRequirement("domain_id", "int32", index=True),
        FieldRequirement("domain_slug", "string", index=False),
        FieldRequirement("domain_name", "string", index=True, facet=True),
        FieldRequirement("locale", "string", index=True, facet=True),
    ),
)
_SENIORITY_SPEC = CollectionSpec(
    collection="seniority",
    query_by="name",
    compared_fields=("id", "seniority_id", "slug", "name", "aliases", "locale"),
    unordered_fields=frozenset({"aliases"}),
    minimum_documents=SAMPLE_SIZE,
    schema_fields=(
        FieldRequirement("seniority_id", "int32"),
        FieldRequirement("slug", "string", index=True),
        FieldRequirement("name", "string", index=True),
        FieldRequirement("aliases", "string[]", index=True),
        FieldRequirement("locale", "string", index=True, facet=True),
    ),
)
_TECHNOLOGY_SPEC = CollectionSpec(
    collection="technology",
    query_by="name",
    compared_fields=("id", "technology_id", "slug", "name", "category"),
    unordered_fields=frozenset(),
    minimum_documents=SAMPLE_SIZE,
    schema_fields=(
        FieldRequirement("technology_id", "int32"),
        FieldRequirement("slug", "string", index=True),
        FieldRequirement("name", "string", index=True),
        FieldRequirement("category", "string", index=True, facet=True),
    ),
)
_COMPANY_SPEC = CollectionSpec(
    collection="company",
    query_by="name",
    compared_fields=(
        "id",
        "industry_id",
        "industry_name",
        "industry_name_de",
        "industry_name_fr",
        "industry_name_it",
    ),
    unordered_fields=frozenset(),
    minimum_documents=1,
    schema_fields=(
        FieldRequirement("name", "string", index=True),
        FieldRequirement("industry_id", "int32", index=True, facet=True),
        FieldRequirement("industry_name", "string", index=True, facet=True),
        FieldRequirement("industry_name_de", "string", index=True),
        FieldRequirement("industry_name_fr", "string", index=True),
        FieldRequirement("industry_name_it", "string", index=True),
    ),
)
_SPECS = (
    _LOCATION_SPEC,
    _OCCUPATION_SPEC,
    _SENIORITY_SPEC,
    _TECHNOLOGY_SPEC,
    _COMPANY_SPEC,
)
_JOB_POSTING_SCHEMA_FIELDS = (
    FieldRequirement("location_direct_ids", "int32[]", index=True, facet=True),
)


def _set_unique_display(
    values: dict[Any, dict[str, str]],
    entity_id: Any,
    locale: str,
    value: str,
    *,
    entity: str,
) -> None:
    localized = values.setdefault(entity_id, {})
    current = localized.get(locale)
    if current is not None and current != value:
        raise RuntimeError(f"authoritative {entity} has multiple display names for one locale")
    localized[locale] = value


def _require_slug(value: Any, *, entity: str) -> str:
    slug = str(value or "").strip()
    if not slug:
        raise RuntimeError(f"authoritative {entity} has a blank slug")
    return slug


async def _load_location_documents(conn: asyncpg.Connection) -> tuple[dict[str, Any], ...]:
    rows = await conn.fetch(_LOCATION_ROWS_SQL)
    name_rows = await conn.fetch(_LOCATION_NAMES_SQL)
    macro_rows = await conn.fetch(_LOCATION_MACROS_SQL)

    names_by_id: dict[int, dict[str, str]] = {}
    aliases_by_id: dict[int, set[str]] = {}
    for row in name_rows:
        location_id = int(row["location_id"])
        locale = str(row["locale"])
        name = str(row["name"])
        if row["is_display"]:
            _set_unique_display(
                names_by_id,
                location_id,
                locale,
                name,
                entity="location",
            )
        else:
            aliases_by_id.setdefault(location_id, set()).add(name)

    parent_by_id = {int(row["id"]): row["parent_id"] for row in rows}
    type_by_id = {int(row["id"]): str(row["type"] or "city") for row in rows}
    macros_by_country: dict[int, set[int]] = {}
    members_by_macro: dict[int, set[int]] = {}
    for row in macro_rows:
        macro_id = int(row["macro_id"])
        country_id = int(row["country_id"])
        macros_by_country.setdefault(country_id, set()).add(macro_id)
        members_by_macro.setdefault(macro_id, set()).add(country_id)

    ancestors_by_id: dict[int, list[int]] = {}
    for location_id in parent_by_id:
        ancestors: set[int] = set()
        geographic_path: set[int] = set()
        current: int | None = location_id
        while current is not None:
            if current in geographic_path:
                raise RuntimeError("authoritative location hierarchy contains a cycle")
            if current not in parent_by_id:
                raise RuntimeError("authoritative location hierarchy references a missing parent")
            geographic_path.add(current)
            ancestors.add(current)
            if type_by_id[current] == "country":
                ancestors.update(macros_by_country.get(current, set()))
            parent = parent_by_id[current]
            current = int(parent) if parent is not None else None
        ancestors_by_id[location_id] = [location_id, *sorted(ancestors - {location_id})]

    documents: list[dict[str, Any]] = []
    for row in rows:
        location_id = int(row["id"])
        localized = names_by_id.get(location_id, {})
        name_en = localized.get("en", "").strip()
        if not name_en:
            raise RuntimeError("authoritative location has no English display name")
        document: dict[str, Any] = {
            "id": str(location_id),
            "location_id": location_id,
            "slug": _require_slug(row["slug"], entity="location"),
            "name_en": name_en,
            "type": type_by_id[location_id],
            "ancestor_ids": ancestors_by_id[location_id],
        }
        for locale in ("de", "fr", "it"):
            if localized.get(locale):
                document[f"name_{locale}"] = localized[locale]
        if row["lat"] is not None and row["lng"] is not None:
            document["coordinates"] = [float(row["lat"]), float(row["lng"])]
        parent = row["parent_id"]
        if parent is not None:
            parent_id = int(parent)
            document["parent_id"] = parent_id
            parent_name = names_by_id.get(parent_id, {}).get("en")
            if parent_name:
                document["parent_name"] = parent_name
        member_country_ids = members_by_macro.get(location_id)
        if member_country_ids:
            document["member_country_ids"] = sorted(member_country_ids)
        if row["population"] is not None:
            document["population"] = int(row["population"])
        aliases = set(aliases_by_id.get(location_id, set()))
        if type_by_id[location_id] == "macro":
            aliases.update(_LOCATION_MACRO_ALIASES.get(document["slug"], []))
        if aliases:
            document["aliases"] = sorted(aliases)
        documents.append(document)
    return tuple(documents)


async def _load_occupation_documents(conn: asyncpg.Connection) -> tuple[dict[str, Any], ...]:
    rows = await conn.fetch(_OCCUPATION_ROWS_SQL)
    domain_rows = await conn.fetch(_OCCUPATION_DOMAIN_NAMES_SQL)
    domain_names: dict[int, dict[str, str]] = {}
    for row in domain_rows:
        _set_unique_display(
            domain_names,
            int(row["domain_id"]),
            str(row["locale"]),
            str(row["name"]),
            entity="occupation domain",
        )

    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    wildcard_aliases: dict[int, list[str]] = {}
    for row in rows:
        occupation_id = int(row["id"])
        locale = str(row["locale"])
        if locale == "*":
            wildcard_aliases.setdefault(occupation_id, []).append(str(row["name"]))
        key = (occupation_id, locale)
        data = grouped.setdefault(
            key,
            {
                "slug": _require_slug(row["slug"], entity="occupation"),
                "parent_id": row["parent_id"],
                "domain_id": row["domain_id"],
                "domain_slug": row["domain_slug"],
                "name": None,
                "aliases": [],
            },
        )
        if row["is_display"]:
            existing = data["name"]
            if existing is not None and existing != row["name"]:
                raise RuntimeError(
                    "authoritative occupation has multiple display names for one locale"
                )
            data["name"] = str(row["name"])
        else:
            data["aliases"].append(str(row["name"]))

    documents: list[dict[str, Any]] = []
    for (occupation_id, locale), data in sorted(grouped.items()):
        if locale == "*" or not data["name"]:
            continue
        document: dict[str, Any] = {
            "id": f"{occupation_id}-{locale}",
            "occupation_id": occupation_id,
            "slug": data["slug"],
            "name": data["name"],
            "aliases": sorted(data["aliases"] + wildcard_aliases.get(occupation_id, [])),
            "locale": locale,
        }
        if data["parent_id"] is not None:
            document["parent_id"] = int(data["parent_id"])
        if data["domain_id"] is not None:
            domain_id = int(data["domain_id"])
            document["domain_id"] = domain_id
            if data["domain_slug"]:
                document["domain_slug"] = str(data["domain_slug"])
            domain_name_map = domain_names.get(domain_id, {})
            domain_name = domain_name_map.get(locale) or domain_name_map.get("en")
            if domain_name:
                document["domain_name"] = domain_name
        documents.append(document)
    return tuple(documents)


async def _load_seniority_documents(conn: asyncpg.Connection) -> tuple[dict[str, Any], ...]:
    rows = await conn.fetch(_SENIORITY_ROWS_SQL)
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    wildcard_aliases: dict[int, list[str]] = {}
    for row in rows:
        seniority_id = int(row["id"])
        locale = str(row["locale"])
        if locale == "*":
            wildcard_aliases.setdefault(seniority_id, []).append(str(row["name"]))
        data = grouped.setdefault(
            (seniority_id, locale),
            {
                "slug": _require_slug(row["slug"], entity="seniority"),
                "name": None,
                "aliases": [],
            },
        )
        if row["is_display"]:
            existing = data["name"]
            if existing is not None and existing != row["name"]:
                raise RuntimeError(
                    "authoritative seniority has multiple display names for one locale"
                )
            data["name"] = str(row["name"])
        else:
            data["aliases"].append(str(row["name"]))

    documents: list[dict[str, Any]] = []
    for (seniority_id, locale), data in sorted(grouped.items()):
        if locale == "*" or not data["name"]:
            continue
        documents.append(
            {
                "id": f"{seniority_id}-{locale}",
                "seniority_id": seniority_id,
                "slug": data["slug"],
                "name": data["name"],
                "aliases": sorted(data["aliases"] + wildcard_aliases.get(seniority_id, [])),
                "locale": locale,
            }
        )
    return tuple(documents)


async def _load_technology_documents(conn: asyncpg.Connection) -> tuple[dict[str, Any], ...]:
    rows = await conn.fetch(_TECHNOLOGY_ROWS_SQL)
    documents: list[dict[str, Any]] = []
    for row in rows:
        technology_id = int(row["id"])
        slug = _require_slug(row["slug"], entity="technology")
        document: dict[str, Any] = {
            "id": str(technology_id),
            "technology_id": technology_id,
            "slug": slug,
            "name": str(row["name"] or slug),
        }
        if row["category"]:
            document["category"] = str(row["category"])
        documents.append(document)
    return tuple(documents)


async def _load_company_documents(conn: asyncpg.Connection) -> tuple[dict[str, Any], ...]:
    rows = await conn.fetch(_COMPANY_ROWS_SQL)
    industry_rows = await conn.fetch(_INDUSTRY_NAMES_SQL)
    localized_names: dict[int, dict[str, str]] = {}
    for row in industry_rows:
        _set_unique_display(
            localized_names,
            int(row["industry_id"]),
            str(row["locale"]),
            str(row["name"]),
            entity="industry",
        )

    documents: list[dict[str, Any]] = []
    for row in rows:
        document: dict[str, Any] = {"id": str(row["id"])}
        industry = row["industry"]
        if industry is not None:
            industry_id = int(industry)
            document["industry_id"] = industry_id
            if row["industry_name"]:
                document["industry_name"] = str(row["industry_name"])
            for locale in ("de", "fr", "it"):
                name = localized_names.get(industry_id, {}).get(locale)
                if name:
                    document[f"industry_name_{locale}"] = name
        documents.append(document)
    return tuple(documents)


async def _load_authoritative_snapshot(
    local_pool: asyncpg.Pool,
) -> dict[str, AuthoritativeCollection]:
    """Load every static contract field from one immutable local snapshot."""

    async with local_pool.acquire() as acquired_conn:
        conn = cast(asyncpg.Connection, acquired_conn)
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            return {
                "location": AuthoritativeCollection(await _load_location_documents(conn)),
                "occupation": AuthoritativeCollection(await _load_occupation_documents(conn)),
                "seniority": AuthoritativeCollection(await _load_seniority_documents(conn)),
                "technology": AuthoritativeCollection(await _load_technology_documents(conn)),
                "company": AuthoritativeCollection(await _load_company_documents(conn)),
            }


def _validate_document_id(document_id: str) -> None:
    if not _SAFE_DOCUMENT_ID.fullmatch(document_id):
        raise RuntimeError("authoritative taxonomy produced an unsafe document id")


def _normalized_projection(
    spec: CollectionSpec,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for field in spec.compared_fields:
        if field not in document:
            continue
        value = document[field]
        if field in spec.unordered_fields:
            if not isinstance(value, list):
                raise RuntimeError("Typesense taxonomy returned an invalid array field")
            value = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
        projected[field] = value
    document_id = str(projected.get("id", ""))
    _validate_document_id(document_id)
    projected["id"] = document_id
    return projected


def _canonical_document_hash(document: Mapping[str, Any]) -> bytes:
    canonical = json.dumps(
        dict(document),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).digest()


def _digest_document_hashes(hashes: Sequence[bytes]) -> str:
    return hashlib.sha256(b"".join(sorted(hashes))).hexdigest()


def _redacted_key(document_id: str) -> str:
    return hashlib.sha256(document_id.encode()).hexdigest()


def _append_mismatch(
    details: list[dict[str, Any]],
    mismatch: dict[str, Any],
) -> None:
    if len(details) < MAX_MISMATCH_DETAILS:
        details.append(mismatch)


def _schema_evidence(
    metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    requirements = {
        "job_posting": _JOB_POSTING_SCHEMA_FIELDS,
        **{spec.collection: spec.schema_fields for spec in _SPECS},
    }
    collections: dict[str, Any] = {}
    ready = True
    for collection, required_fields in requirements.items():
        live_fields = metadata[collection].get("fields")
        if not isinstance(live_fields, list):
            raise RuntimeError("Typesense collection returned invalid schema metadata")
        live_by_name = {
            str(field.get("name")): field for field in live_fields if isinstance(field, dict)
        }
        mismatches: list[dict[str, Any]] = []
        for required in required_fields:
            live = live_by_name.get(required.name)
            if live is None:
                mismatches.append({"field": required.name, "attributes": ["missing"]})
                continue
            attributes: list[str] = []
            if live.get("type") != required.field_type:
                attributes.append("type")
            if required.index is not None and live.get("index", True) is not required.index:
                attributes.append("index")
            if required.facet is not None and live.get("facet", False) is not required.facet:
                attributes.append("facet")
            if attributes:
                mismatches.append({"field": required.name, "attributes": attributes})
        status = "ready" if not mismatches else "not_ready"
        ready = ready and status == "ready"
        collections[collection] = {"status": status, "mismatches": mismatches}
    return {"status": "ready" if ready else "not_ready", "collections": collections}


def _collection_metadata(client: Any, collection: str) -> Mapping[str, Any]:
    value = client.collections[collection].retrieve()
    if not isinstance(value, dict):
        raise RuntimeError("Typesense collection returned invalid metadata")
    return value


def _compare_remote_collection(
    client: Any,
    spec: CollectionSpec,
    expected: AuthoritativeCollection,
    metadata: Mapping[str, Any],
    *,
    page_size: int,
) -> tuple[dict[str, Any], int]:
    count = metadata.get("num_documents")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise RuntimeError("Typesense collection returned an invalid document count")

    expected_by_id: dict[str, dict[str, Any]] = {}
    expected_hashes: list[bytes] = []
    for document in expected.documents:
        projection = _normalized_projection(spec, document)
        document_id = projection["id"]
        if document_id in expected_by_id:
            raise RuntimeError("authoritative taxonomy returned duplicate document ids")
        expected_by_id[document_id] = projection
        expected_hashes.append(_canonical_document_hash(projection))

    seen_ids: set[str] = set()
    actual_hashes: list[bytes] = []
    mismatch_details: list[dict[str, Any]] = []
    mismatch_count = 0
    found: int | None = None
    search_calls = 0
    page = 1
    while True:
        response = client.collections[spec.collection].documents.search(
            {
                "q": "*",
                "query_by": spec.query_by,
                "include_fields": ",".join(spec.compared_fields),
                "page": page,
                "per_page": page_size,
            }
        )
        search_calls += 1
        if not isinstance(response, dict):
            raise RuntimeError("Typesense taxonomy search returned an invalid response")
        response_found = response.get("found")
        hits = response.get("hits")
        if (
            not isinstance(response_found, int)
            or isinstance(response_found, bool)
            or response_found < 0
            or not isinstance(hits, list)
        ):
            raise RuntimeError("Typesense taxonomy search returned invalid pagination metadata")
        if found is None:
            found = response_found
        elif response_found != found:
            raise RuntimeError("Typesense taxonomy count changed during exact verification")

        for hit in hits:
            if not isinstance(hit, dict) or not isinstance(hit.get("document"), dict):
                raise RuntimeError("Typesense taxonomy search returned an invalid hit")
            projection = _normalized_projection(spec, hit["document"])
            document_id = projection["id"]
            actual_hashes.append(_canonical_document_hash(projection))
            if document_id in seen_ids:
                mismatch_count += 1
                _append_mismatch(
                    mismatch_details,
                    {"kind": "duplicate", "document_key_sha256": _redacted_key(document_id)},
                )
                continue
            seen_ids.add(document_id)
            expected_document = expected_by_id.get(document_id)
            if expected_document is None:
                mismatch_count += 1
                _append_mismatch(
                    mismatch_details,
                    {"kind": "unexpected", "document_key_sha256": _redacted_key(document_id)},
                )
                continue
            fields = [
                field
                for field in spec.compared_fields
                if projection.get(field) != expected_document.get(field)
                or (field in projection) != (field in expected_document)
            ]
            if fields:
                mismatch_count += 1
                _append_mismatch(
                    mismatch_details,
                    {
                        "kind": "field_mismatch",
                        "document_key_sha256": _redacted_key(document_id),
                        "fields": fields,
                    },
                )

        if len(seen_ids) >= response_found:
            break
        if not hits:
            raise RuntimeError("Typesense taxonomy pagination ended before the reported count")
        page += 1

    for document_id in sorted(expected_by_id.keys() - seen_ids):
        mismatch_count += 1
        _append_mismatch(
            mismatch_details,
            {"kind": "missing", "document_key_sha256": _redacted_key(document_id)},
        )

    expected_count = expected.document_count
    count_matches = count == expected_count and found == expected_count
    minimum_satisfied = expected_count >= spec.minimum_documents
    ready = count_matches and minimum_satisfied and mismatch_count == 0
    return (
        {
            "status": "ready" if ready else "not_ready",
            "authoritative_document_count": expected_count,
            "typesense_document_count": count,
            "typesense_search_found": found,
            "count_matches": count_matches,
            "minimum_document_count": spec.minimum_documents,
            "minimum_satisfied": minimum_satisfied,
            "compared_document_count": len(seen_ids),
            "compared_fields": list(spec.compared_fields),
            "expected_projection_sha256": _digest_document_hashes(expected_hashes),
            "typesense_projection_sha256": _digest_document_hashes(actual_hashes),
            "mismatch_count": mismatch_count,
            "mismatch_details": mismatch_details,
            "mismatch_details_truncated": mismatch_count > len(mismatch_details),
        },
        search_calls,
    )


def _fetch_typesense_evidence(
    client: Any,
    authoritative: Mapping[str, AuthoritativeCollection],
    *,
    page_size: int,
) -> tuple[dict[str, Any], dict[str, Any], int, int]:
    metadata = {
        collection: _collection_metadata(client, collection)
        for collection in ("job_posting", *(spec.collection for spec in _SPECS))
    }
    schema = _schema_evidence(metadata)
    comparisons: dict[str, Any] = {}
    search_calls = 0
    for spec in _SPECS:
        comparison, calls = _compare_remote_collection(
            client,
            spec,
            authoritative[spec.collection],
            metadata[spec.collection],
            page_size=page_size,
        )
        comparisons[spec.collection] = comparison
        search_calls += calls
    return schema, comparisons, len(metadata), search_calls


async def verify_taxonomy_readiness(
    local_pool: asyncpg.Pool,
    typesense_client: Any,
    *,
    page_size: int = DOCUMENT_PAGE_SIZE,
) -> dict[str, Any]:
    """Return exact, redacted taxonomy contract evidence."""

    if typesense_client is None:
        raise RuntimeError("Typesense operations client is not configured")
    if page_size < 1 or page_size > DOCUMENT_PAGE_SIZE:
        raise ValueError(f"page_size must be in [1, {DOCUMENT_PAGE_SIZE}]")

    authoritative = await _load_authoritative_snapshot(local_pool)
    schema, comparisons, metadata_calls, search_calls = await asyncio.to_thread(
        _fetch_typesense_evidence,
        typesense_client,
        authoritative,
        page_size=page_size,
    )
    ready = schema["status"] == "ready" and all(
        item["status"] == "ready" for item in comparisons.values()
    )
    return {
        "command": "verify-typesense-taxonomies",
        "status": "ready" if ready else "not_ready",
        "authority": "local_postgres",
        "coverage": {
            "document_counts": "exact",
            "documents": "full",
            "fields": "static_consumer_contract",
            "collections": [spec.collection for spec in _SPECS],
            "excluded_dynamic_fields": ["active_posting_count", "has_active_postings"],
        },
        "snapshot": {"isolation": "repeatable_read", "read_only": True},
        "pagination": {
            "maximum_documents_per_search": DOCUMENT_PAGE_SIZE,
            "configured_documents_per_search": page_size,
            "retained_remote_state": "document_ids_and_sha256_only",
        },
        "typesense_calls": {
            "collection_metadata": metadata_calls,
            "document_searches": search_calls,
            "total": metadata_calls + search_calls,
        },
        "schema": schema,
        "collections": comparisons,
    }


def emit_evidence(evidence: Mapping[str, Any]) -> None:
    """Write exactly one machine-readable evidence record."""

    json.dump(evidence, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()


async def run_cli(local_pool: asyncpg.Pool, typesense_client: Any) -> int:
    """Run the fail-closed exact gate and emit redacted JSON."""

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
    "DOCUMENT_PAGE_SIZE",
    "MAX_MISMATCH_DETAILS",
    "SAMPLE_SIZE",
    "emit_evidence",
    "run_cli",
    "verify_taxonomy_readiness",
]
