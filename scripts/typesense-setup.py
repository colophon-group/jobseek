"""Create Typesense collections and aliases for jobseek.

Run from the crawler directory so that ``src.config`` resolves:

    cd apps/crawler && uv run python ../../scripts/typesense-setup.py

Flags:
    --force   Drop existing collections and recreate from scratch.
"""
from __future__ import annotations

import argparse
import sys

import typesense
from typesense.exceptions import ObjectAlreadyExists, ObjectNotFound

from src.config import settings

# ---------------------------------------------------------------------------
# Collection schemas
# ---------------------------------------------------------------------------

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
            {"name": "description", "type": "string", "index": False, "optional": True},
            {"name": "industry_id", "type": "int32", "facet": True, "optional": True},
            {"name": "industry_name", "type": "string", "facet": True, "optional": True},
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alias_exists(client: typesense.Client, alias_name: str) -> str | None:
    """Return the collection name behind *alias_name*, or None."""
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


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def setup_collections(client: typesense.Client, *, force: bool = False) -> None:
    for schema in COLLECTIONS:
        alias_name = schema["name"]
        versioned_name = f"{alias_name}_v1"

        print(f"\n--- {alias_name} ---")

        if force:
            _drop_alias(client, alias_name)
            _drop_collection(client, versioned_name)

        # Check if alias already points to a collection (idempotent)
        existing_target = _alias_exists(client, alias_name)
        if existing_target:
            print(f"  alias '{alias_name}' already exists -> {existing_target}, skipping")
            continue

        # Create the versioned collection
        versioned_schema = {**schema, "name": versioned_name}
        if _collection_exists(client, versioned_name):
            print(f"  collection '{versioned_name}' already exists (no alias), creating alias")
        else:
            try:
                client.collections.create(versioned_schema)
                print(f"  created collection {versioned_name}")
            except ObjectAlreadyExists:
                print(f"  collection '{versioned_name}' already exists")

        # Create alias
        try:
            client.aliases.upsert(alias_name, {"collection_name": versioned_name})
            print(f"  created alias {alias_name} -> {versioned_name}")
        except Exception as exc:
            print(f"  ERROR creating alias {alias_name}: {exc}", file=sys.stderr)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up Typesense collections for jobseek")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop existing collections and recreate from scratch",
    )
    args = parser.parse_args()

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

    # Verify connectivity
    try:
        health = client.operations.is_healthy()
        if not health:
            print("ERROR: Typesense reports unhealthy", file=sys.stderr)
            sys.exit(1)
        print("Typesense is healthy")
    except Exception as exc:
        print(f"ERROR: Cannot connect to Typesense: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_collections(client, force=args.force)
    print("\nDone.")


if __name__ == "__main__":
    main()
