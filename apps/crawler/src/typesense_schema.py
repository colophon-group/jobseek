"""Typesense collection schemas + idempotent setup logic.

Single source of truth for the collection definitions. Used by:

- ``crawler setup-typesense`` CLI subcommand (called by deploy.sh)
- ``scripts/typesense-setup.py`` (operator-facing wrapper)

The ``setup_collections`` function is idempotent: it creates missing
collections + aliases, and PATCHes existing collections to add any
fields that are present in the schema but absent on the live cluster.
Field removals are intentionally manual to avoid accidental data loss.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from typesense.exceptions import ObjectAlreadyExists, ObjectNotFound

if TYPE_CHECKING:
    import typesense


COLLECTIONS: list[dict] = [
    {
        "name": "job_posting",
        "fields": [
            {"name": "company_id", "type": "string", "facet": True},
            {"name": "company_name", "type": "string", "facet": True},
            {"name": "company_slug", "type": "string", "index": False},
            {"name": "company_icon", "type": "string", "index": False, "optional": True},
            {"name": "title", "type": "string"},
            {"name": "is_active", "type": "bool", "facet": True},
            {"name": "location_ids", "type": "int32[]", "facet": True},
            {"name": "location_names", "type": "string[]", "facet": True},
            {"name": "location_types", "type": "string[]", "facet": True},
            {"name": "location_geo_types", "type": "string[]", "index": False},
            {"name": "occupation_id", "type": "int32", "facet": True, "optional": True},
            {"name": "occupation_ids", "type": "int32[]", "facet": True, "optional": True},
            {"name": "occupation_name", "type": "string", "facet": True, "optional": True},
            {"name": "seniority_id", "type": "int32", "facet": True, "optional": True},
            {"name": "seniority_name", "type": "string", "facet": True, "optional": True},
            {"name": "technology_ids", "type": "int32[]", "facet": True},
            {"name": "technology_names", "type": "string[]", "facet": True},
            {"name": "employment_type", "type": "string", "facet": True, "optional": True},
            {"name": "salary_eur", "type": "int32", "facet": True, "optional": True},
            {"name": "experience_min", "type": "int32", "facet": True},
            {"name": "locales", "type": "string[]", "facet": True},
            {"name": "source_url", "type": "string", "index": False, "optional": True},
            {"name": "first_seen_at", "type": "int64"},
            {"name": "last_seen_at", "type": "int64", "optional": True},
        ],
        "default_sorting_field": "first_seen_at",
        "token_separators": ["-", "/"],
    },
    {
        "name": "location",
        "fields": [
            {"name": "location_id", "type": "int32"},
            {"name": "slug", "type": "string", "index": False},
            {"name": "name_en", "type": "string", "locale": "en"},
            {"name": "name_de", "type": "string", "locale": "de", "optional": True},
            {"name": "name_fr", "type": "string", "locale": "fr", "optional": True},
            {"name": "name_it", "type": "string", "locale": "it", "optional": True},
            {"name": "parent_name", "type": "string", "optional": True},
            {"name": "type", "type": "string", "facet": True},
            {"name": "coordinates", "type": "geopoint", "optional": True},
            {"name": "population", "type": "int32", "optional": True},
            {"name": "has_active_postings", "type": "bool", "facet": True},
            {"name": "active_posting_count", "type": "int32"},
        ],
        "default_sorting_field": "active_posting_count",
    },
    {
        "name": "occupation",
        "fields": [
            {"name": "occupation_id", "type": "int32"},
            {"name": "slug", "type": "string", "index": False},
            {"name": "name", "type": "string"},
            {"name": "aliases", "type": "string[]"},
            {"name": "domain_name", "type": "string", "facet": True, "optional": True},
            {"name": "locale", "type": "string", "facet": True},
            {"name": "has_active_postings", "type": "bool", "facet": True},
            {"name": "active_posting_count", "type": "int32"},
        ],
        "default_sorting_field": "active_posting_count",
    },
    {
        "name": "seniority",
        "fields": [
            {"name": "seniority_id", "type": "int32"},
            {"name": "slug", "type": "string", "index": False},
            {"name": "name", "type": "string"},
            {"name": "aliases", "type": "string[]"},
            {"name": "locale", "type": "string", "facet": True},
            {"name": "has_active_postings", "type": "bool", "facet": True},
            {"name": "active_posting_count", "type": "int32"},
        ],
        "default_sorting_field": "active_posting_count",
    },
    {
        "name": "technology",
        "fields": [
            {"name": "technology_id", "type": "int32"},
            {"name": "slug", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "category", "type": "string", "facet": True, "optional": True},
            {"name": "has_active_postings", "type": "bool", "facet": True},
            {"name": "active_posting_count", "type": "int32"},
        ],
        "default_sorting_field": "active_posting_count",
        "token_separators": ["+", "#", "."],
        "symbols_to_index": ["+", "#", "."],
    },
    {
        "name": "company",
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "slug", "type": "string", "index": False},
            {"name": "icon", "type": "string", "index": False, "optional": True},
            {"name": "logo", "type": "string", "index": False, "optional": True},
            {"name": "website", "type": "string", "index": False, "optional": True},
            {"name": "description", "type": "string", "index": False, "optional": True},
            # Locale variants for the company detail page (en is in
            # `description`/`industry_name` above). Readers fall back to the
            # English field when the locale variant is missing.
            {"name": "description_de", "type": "string", "index": False, "optional": True},
            {"name": "description_fr", "type": "string", "index": False, "optional": True},
            {"name": "description_it", "type": "string", "index": False, "optional": True},
            {"name": "industry_id", "type": "int32", "facet": True, "optional": True},
            {"name": "industry_name", "type": "string", "facet": True, "optional": True},
            {"name": "industry_name_de", "type": "string", "index": False, "optional": True},
            {"name": "industry_name_fr", "type": "string", "index": False, "optional": True},
            {"name": "industry_name_it", "type": "string", "index": False, "optional": True},
            {"name": "employee_count_range", "type": "int32", "optional": True},
            {"name": "founded_year", "type": "int32", "optional": True},
            {"name": "active_posting_count", "type": "int32"},
            {"name": "year_posting_count", "type": "int32"},
        ],
        "default_sorting_field": "active_posting_count",
    },
    {
        "name": "watchlist",
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "slug", "type": "string", "index": False},
            {"name": "title", "type": "string"},
            {"name": "description", "type": "string", "optional": True},
            {"name": "owner_name", "type": "string"},
            {"name": "owner_username", "type": "string", "index": False, "optional": True},
            {"name": "company_count", "type": "int32"},
            {"name": "active_job_count", "type": "int32"},
            {"name": "mirror_count", "type": "int32"},
            {"name": "is_featured", "type": "bool", "facet": True},
            {"name": "has_description", "type": "bool", "facet": True},
            {"name": "created_at", "type": "int64"},
            {"name": "is_public", "type": "bool", "facet": True},
        ],
        "default_sorting_field": "created_at",
    },
]


def _alias_exists(client: typesense.Client, alias_name: str) -> str | None:
    try:
        result = client.aliases[alias_name].retrieve()
        return result.get("collection_name")
    except ObjectNotFound:
        return None


def _collection_exists(client: typesense.Client, name: str) -> bool:
    try:
        client.collections[name].retrieve()
        return True
    except ObjectNotFound:
        return False


def _drop_collection(client: typesense.Client, name: str) -> None:
    try:
        client.collections[name].delete()
        print(f"  dropped collection {name}")
    except ObjectNotFound:
        pass


def _drop_alias(client: typesense.Client, name: str) -> None:
    try:
        client.aliases[name].delete()
        print(f"  dropped alias {name}")
    except ObjectNotFound:
        pass


def _patch_missing_fields(
    client: typesense.Client, collection_name: str, desired_fields: list[dict]
) -> None:
    """Add fields present in the desired schema but absent from the live one.

    Typesense supports adding/removing fields in-place via PATCH on a
    collection. We only ever ADD here — removals are intentionally manual to
    avoid accidental data loss. ``id`` and existing fields are left alone.
    """
    try:
        live = client.collections[collection_name].retrieve()
    except ObjectNotFound:
        return

    existing_names = {f["name"] for f in live.get("fields", [])}
    missing = [f for f in desired_fields if f["name"] not in existing_names]
    if not missing:
        print(f"  schema up to date for {collection_name}")
        return

    print(
        f"  adding {len(missing)} field(s) to {collection_name}: "
        f"{', '.join(f['name'] for f in missing)}"
    )
    try:
        client.collections[collection_name].update({"fields": missing})
    except Exception as exc:
        print(f"  ERROR patching {collection_name}: {exc}", file=sys.stderr)
        raise


def setup_collections(client: typesense.Client, *, force: bool = False) -> None:
    for schema in COLLECTIONS:
        alias_name = schema["name"]
        versioned_name = f"{alias_name}_v1"

        print(f"\n--- {alias_name} ---")

        if force:
            _drop_alias(client, alias_name)
            _drop_collection(client, versioned_name)

        existing_target = _alias_exists(client, alias_name)
        if existing_target:
            print(f"  alias '{alias_name}' already exists -> {existing_target}")
            _patch_missing_fields(client, existing_target, schema["fields"])
            continue

        versioned_schema = {**schema, "name": versioned_name}
        if _collection_exists(client, versioned_name):
            print(f"  collection '{versioned_name}' already exists (no alias), creating alias")
            _patch_missing_fields(client, versioned_name, schema["fields"])
        else:
            try:
                client.collections.create(versioned_schema)
                print(f"  created collection {versioned_name}")
            except ObjectAlreadyExists:
                print(f"  collection '{versioned_name}' already exists")

        try:
            client.aliases.upsert(alias_name, {"collection_name": versioned_name})
            print(f"  created alias {alias_name} -> {versioned_name}")
        except Exception as exc:
            print(f"  ERROR creating alias {alias_name}: {exc}", file=sys.stderr)
            raise


def run_setup(*, force: bool = False) -> None:
    """Connect using project settings and run setup_collections.

    Caller for both the ``crawler setup-typesense`` CLI subcommand and
    the standalone ``scripts/typesense-setup.py`` wrapper.
    """
    import typesense

    from src.config import settings

    if not settings.typesense_admin_key:
        print("ERROR: TYPESENSE_ADMIN_KEY not set. Cannot proceed.", file=sys.stderr)
        sys.exit(1)
    if not settings.typesense_host:
        print("ERROR: TYPESENSE_HOST not set. Cannot proceed.", file=sys.stderr)
        sys.exit(1)

    client = typesense.Client(
        {
            "nodes": [
                {
                    "host": settings.typesense_host,
                    "port": str(settings.typesense_port),
                    "protocol": settings.typesense_protocol,
                }
            ],
            "api_key": settings.typesense_admin_key,
            "connection_timeout_seconds": 10,
        }
    )

    try:
        if not client.operations.is_healthy():
            print("ERROR: Typesense reports unhealthy", file=sys.stderr)
            sys.exit(1)
        print("Typesense is healthy")
    except Exception as exc:
        print(f"ERROR: Cannot connect to Typesense: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_collections(client, force=force)
    print("\nDone.")
