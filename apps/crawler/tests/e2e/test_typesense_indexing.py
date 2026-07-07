"""E2E tests for Typesense indexing pipeline.

These tests verify collection schemas, data integrity, search behaviour,
and special-character handling against a **local** Typesense instance.

Local development runs are skipped when Typesense is unreachable at
localhost:8108. CI/REQUIRE_TYPESENSE_E2E runs fail instead of skipping.
The suite seeds a small synthetic dataset on startup and cleans it up
after every run.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid

import pytest
import typesense
from typesense.exceptions import ObjectNotFound

# ---------------------------------------------------------------------------
# Typesense connectivity check
# ---------------------------------------------------------------------------

TYPESENSE_HOST = os.environ.get("TYPESENSE_HOST", "localhost")
TYPESENSE_PORT = os.environ.get("TYPESENSE_PORT", "8108")
TYPESENSE_PROTOCOL = os.environ.get("TYPESENSE_PROTOCOL", "http")
TYPESENSE_API_KEY = os.environ.get(
    "TYPESENSE_ADMIN_KEY",
    os.environ.get("TYPESENSE_API_KEY", "local_dev_typesense_key"),
)

# Alias -> versioned collection name mapping.  The test suite operates on
# versioned names (required for document CRUD) but verifies aliases exist.
ALIAS_NAMES = [
    "job_posting",
    "location",
    "occupation",
    "seniority",
    "technology",
    "company",
    "watchlist",
]


def _make_client() -> typesense.Client:
    return typesense.Client(
        {
            "nodes": [
                {
                    "host": TYPESENSE_HOST,
                    "port": TYPESENSE_PORT,
                    "protocol": TYPESENSE_PROTOCOL,
                }
            ],
            "api_key": TYPESENSE_API_KEY,
            "connection_timeout_seconds": 5,
        }
    )


def _typesense_is_reachable() -> bool:
    try:
        client = _make_client()
        return bool(client.operations.is_healthy())
    except Exception:
        return False


def _typesense_e2e_required() -> bool:
    return os.environ.get("CI") == "true" or os.environ.get("REQUIRE_TYPESENSE_E2E") == "true"


def _skip_or_fail_when_unreachable() -> None:
    if _typesense_is_reachable():
        return

    message = (
        f"Typesense is not reachable at {TYPESENSE_PROTOCOL}://{TYPESENSE_HOST}:{TYPESENSE_PORT}"
    )
    if _typesense_e2e_required():
        pytest.exit(
            f"{message}; refusing to skip Typesense E2E suite when CI/REQUIRE_TYPESENSE_E2E is set",
            returncode=1,
        )
    pytest.skip(message)


def _resolve_aliases(client: typesense.Client) -> dict[str, str]:
    """Return {alias_name: versioned_collection_name} for every expected alias.

    Falls back to ``{alias}_v1`` when an alias is not yet configured, so
    that seeding/cleanup still works even if aliases are partially set up.
    """
    mapping: dict[str, str] = {}
    aliases_resp = client.aliases.retrieve()
    for alias in aliases_resp.get("aliases", []):
        mapping[alias["name"]] = alias["collection_name"]
    # Fill in any missing aliases with the default _v1 naming convention
    for alias in ALIAS_NAMES:
        if alias not in mapping:
            mapping[alias] = f"{alias}_v1"
    return mapping


# ---------------------------------------------------------------------------
# Seed data constants
# ---------------------------------------------------------------------------

# Unique prefix to avoid collisions with any real data
_PREFIX = f"e2e_{uuid.uuid4().hex[:8]}"

NOW_TS = int(time.time())
PAST_TS = NOW_TS - 86_400 * 30  # 30 days ago

COMPANIES = [
    {
        "id": f"{_PREFIX}_company_{i}",
        "name": name,
        "slug": slug,
        "active_posting_count": 0,  # will be updated after postings
        "year_posting_count": 0,
    }
    for i, (name, slug) in enumerate(
        [
            ("Acme Corp", "acme-corp"),
            ("Globex Inc", "globex-inc"),
            ("Initech", "initech"),
            ("Umbrella Corp", "umbrella-corp"),
            ("Stark Industries", "stark-industries"),
        ]
    )
]

TECHNOLOGIES = [
    {
        "id": f"{_PREFIX}_tech_{i}",
        "technology_id": 9000 + i,
        "slug": slug,
        "name": name,
        "category": category,
        "has_active_postings": True,
        "active_posting_count": 1,
    }
    for i, (name, slug, category) in enumerate(
        [
            ("Python", "python", "Language"),
            ("TypeScript", "typescript", "Language"),
            ("React", "react", "Framework"),
            ("PostgreSQL", "postgresql", "Database"),
            ("Docker", "docker", "DevOps"),
            ("Kubernetes", "kubernetes", "DevOps"),
            ("C++", "cpp", "Language"),
            ("C#", "csharp", "Language"),
            (".NET", "dotnet", "Framework"),
            ("Go", "go", "Language"),
        ]
    )
]

LOCATIONS = [
    {
        "id": f"{_PREFIX}_loc_{i}",
        "location_id": 8000 + i,
        "slug": slug,
        "name_en": name_en,
        "name_de": name_de,
        "name_fr": name_fr,
        "name_it": name_it,
        "type": loc_type,
        "has_active_postings": True,
        "active_posting_count": 1,
        **({"coordinates": coords} if coords else {}),
        **({"parent_name": parent} if parent else {}),
        **({"population": pop} if pop else {}),
    }
    for i, (name_en, name_de, name_fr, name_it, slug, loc_type, coords, parent, pop) in enumerate(
        [
            (
                "Zurich",
                "Zuerich",
                "Zurich",
                "Zurigo",
                "zurich",
                "city",
                [47.3769, 8.5417],
                "Switzerland",
                434_008,
            ),
            (
                "Berlin",
                "Berlin",
                "Berlin",
                "Berlino",
                "berlin",
                "city",
                [52.5200, 13.4050],
                "Germany",
                3_748_148,
            ),
            (
                "London",
                "London",
                "Londres",
                "Londra",
                "london",
                "city",
                [51.5074, -0.1278],
                "United Kingdom",
                8_982_000,
            ),
            (
                "Paris",
                "Paris",
                "Paris",
                "Parigi",
                "paris",
                "city",
                [48.8566, 2.3522],
                "France",
                2_161_000,
            ),
            ("Remote", "Remote", "Remote", "Remote", "remote", "macro", None, None, None),
            (
                "Switzerland",
                "Schweiz",
                "Suisse",
                "Svizzera",
                "switzerland",
                "country",
                [46.8182, 8.2275],
                None,
                8_776_000,
            ),
            (
                "Germany",
                "Deutschland",
                "Allemagne",
                "Germania",
                "germany",
                "country",
                [51.1657, 10.4515],
                None,
                83_200_000,
            ),
            (
                "New York",
                "New York",
                "New York",
                "New York",
                "new-york",
                "city",
                [40.7128, -74.0060],
                "United States",
                8_336_817,
            ),
            (
                "Bavaria",
                "Bayern",
                "Baviere",
                "Baviera",
                "bavaria",
                "region",
                [48.7904, 11.4979],
                "Germany",
                13_140_000,
            ),
            (
                "Tokyo",
                "Tokio",
                "Tokyo",
                "Tokyo",
                "tokyo",
                "city",
                [35.6762, 139.6503],
                "Japan",
                13_960_000,
            ),
        ]
    )
]

OCCUPATIONS = [
    {
        "id": f"{_PREFIX}_occ_{locale}_{i}",
        "occupation_id": 7000 + i,
        "slug": slug,
        "name": name if locale == "en" else f"{name} ({locale})",
        "aliases": aliases,
        "locale": locale,
        "has_active_postings": True,
        "active_posting_count": 1,
    }
    for i, (name, slug, aliases) in enumerate(
        [
            ("Software Engineer", "software-engineer", ["Developer", "Programmer"]),
            ("Data Scientist", "data-scientist", ["ML Engineer", "Data Analyst"]),
            ("Product Manager", "product-manager", ["PM", "Product Owner"]),
            ("DevOps Engineer", "devops-engineer", ["SRE", "Platform Engineer"]),
            ("Designer", "designer", ["UX Designer", "UI Designer"]),
        ]
    )
    for locale in ("en", "de")
]

SENIORITIES = [
    {
        "id": f"{_PREFIX}_sen_{locale}_{i}",
        "seniority_id": 6000 + i,
        "slug": slug,
        "name": name if locale == "en" else f"{name} ({locale})",
        "aliases": aliases,
        "locale": locale,
        "has_active_postings": True,
        "active_posting_count": 1,
    }
    for i, (name, slug, aliases) in enumerate(
        [
            ("Junior", "junior", ["Entry Level", "Graduate"]),
            ("Mid-Level", "mid-level", ["Intermediate"]),
            ("Senior", "senior", ["Experienced", "Sr."]),
        ]
    )
    for locale in ("en", "de")
]


def _make_postings() -> list[dict]:
    """Build 20 synthetic job postings with deterministic properties."""
    postings = []
    titles = [
        "Senior Software Engineer",
        "Junior Data Scientist",
        "Staff React Developer",
        "DevOps Engineer",
        "Product Manager",
        "Backend Python Developer",
        "Frontend TypeScript Developer",
        "Machine Learning Engineer",
        "Senior Designer",
        "Platform Engineer",
        "C++ Systems Developer",
        "Full Stack Engineer",
        ".NET Developer",
        "Go Backend Engineer",
        "Senior C# Developer",
        "Cloud Infrastructure Engineer",
        "Data Platform Engineer",
        "Mobile Developer",
        "Security Engineer",
        "QA Automation Engineer",
    ]
    for i, title in enumerate(titles):
        company = COMPANIES[i % len(COMPANIES)]
        loc = LOCATIONS[i % len(LOCATIONS)]
        occ = OCCUPATIONS[(i * 2) % len(OCCUPATIONS)]  # en locale entries
        sen = SENIORITIES[(i * 2) % len(SENIORITIES)]
        tech_start = i % len(TECHNOLOGIES)
        techs = [TECHNOLOGIES[tech_start], TECHNOLOGIES[(tech_start + 1) % len(TECHNOLOGIES)]]

        experience_min = -1 if i % 3 == 0 else (i + 1)
        experience_max = -1 if experience_min == -1 else 99
        posting: dict = {
            "id": f"{_PREFIX}_posting_{i}",
            "company_id": company["id"],
            "company_name": company["name"],
            "company_slug": company["slug"],
            "title": title,
            "is_active": i < 16,  # 16 active, 4 inactive
            "location_ids": [loc["location_id"]],
            "location_names": [loc["name_en"]],
            "location_types": [loc["type"]],
            "location_geo_types": [loc["type"]],
            "occupation_id": occ["occupation_id"],
            "occupation_name": occ["name"],
            "seniority_id": sen["seniority_id"],
            "seniority_name": sen["name"],
            "technology_ids": [t["technology_id"] for t in techs],
            "technology_names": [t["name"] for t in techs],
            "experience_min": experience_min,
            "experience_max": experience_max,
            "experience_min_years": float(experience_min),
            "experience_max_years": float(experience_max),
            "locales": ["_none"] if i % 4 == 0 else ["en", "de"],
            "first_seen_at": PAST_TS + i * 3600,
            "last_seen_at": NOW_TS - i * 600,
        }

        # Some postings have salary, some don't
        if i % 2 == 0:
            posting["salary_eur"] = 60_000 + i * 5_000

        # Some have source_url, some don't
        if i % 3 != 2:
            posting["source_url"] = f"https://careers.example.com/jobs/{i}"

        # Some have employment_type
        if i % 2 == 0:
            posting["employment_type"] = "full-time"

        postings.append(posting)

    return postings


POSTINGS = _make_postings()

WATCHLISTS = [
    {
        "id": f"{_PREFIX}_wl_0",
        "slug": "my-tech-watchlist",
        "title": "Top Tech Companies",
        "description": "A curated watchlist of tech companies",
        "owner_name": "Test User",
        "filters_json": '{"keywords":["python"]}',
        "company_count": 3,
        "active_job_count": 10,
        "mirror_count": 2,
        "created_at": PAST_TS,
        "is_public": True,
    },
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Track seeded document IDs for cleanup  {versioned_collection_name: [doc_ids]}
_SEEDED: dict[str, list[str]] = {}


@pytest.fixture(scope="module")
def ts_client():
    """Return a Typesense client, seeding data before all tests in the module
    and cleaning up after.

    The fixture resolves aliases to versioned collection names (e.g.
    ``company`` -> ``company_v1``) because Typesense's ``/collections/``
    endpoint requires the real collection name, not the alias.
    """
    _skip_or_fail_when_unreachable()
    client = _make_client()
    alias_map = _resolve_aliases(client)

    # -- Seed data -----------------------------------------------------------
    _seed_collection(client, alias_map["company"], COMPANIES)
    _seed_collection(client, alias_map["technology"], TECHNOLOGIES)
    _seed_collection(client, alias_map["location"], LOCATIONS)
    _seed_collection(client, alias_map["occupation"], OCCUPATIONS)
    _seed_collection(client, alias_map["seniority"], SENIORITIES)
    _seed_collection(client, alias_map["job_posting"], POSTINGS)
    _seed_collection(client, alias_map["watchlist"], WATCHLISTS)

    # Update company active_posting_count to reflect seeded postings
    company_col = alias_map["company"]
    for company in COMPANIES:
        active_count = sum(
            1 for p in POSTINGS if p["company_id"] == company["id"] and p["is_active"]
        )
        total_count = sum(1 for p in POSTINGS if p["company_id"] == company["id"])
        client.collections[company_col].documents[company["id"]].update(
            {"active_posting_count": active_count, "year_posting_count": total_count}
        )

    yield client

    # -- Cleanup -------------------------------------------------------------
    for collection_name, doc_ids in _SEEDED.items():
        for doc_id in doc_ids:
            with contextlib.suppress(ObjectNotFound):
                client.collections[collection_name].documents[doc_id].delete()
    _SEEDED.clear()


@pytest.fixture(scope="module")
def alias_map(ts_client: typesense.Client) -> dict[str, str]:
    """Return {alias_name: versioned_collection_name} mapping."""
    return _resolve_aliases(ts_client)


def _seed_collection(client: typesense.Client, versioned_name: str, docs: list[dict]) -> None:
    """Import documents into a collection, tracking IDs for cleanup."""
    _SEEDED.setdefault(versioned_name, [])
    for doc in docs:
        client.collections[versioned_name].documents.upsert(doc)
        _SEEDED[versioned_name].append(doc["id"])


# ---------------------------------------------------------------------------
# Helpers — shorthand for accessing collections via alias map
# ---------------------------------------------------------------------------


def _col(client: typesense.Client, alias_map: dict[str, str], alias: str):
    """Return the collection object for a given alias name."""
    return client.collections[alias_map[alias]]


# ============================================================================
# Schema tests
# ============================================================================


class TestSchemas:
    """Verify all 7 collections exist with correct schemas."""

    def test_all_collections_exist(self, ts_client: typesense.Client, alias_map: dict):
        """All 7 collections (job_posting, location, occupation, seniority,
        technology, company, watchlist) are accessible via their aliases."""
        for alias in ALIAS_NAMES:
            assert alias in alias_map, f"Alias '{alias}' not found in Typesense"
            versioned = alias_map[alias]
            info = ts_client.collections[versioned].retrieve()
            assert info["name"] == versioned

    def test_job_posting_schema_fields(self, ts_client: typesense.Client, alias_map: dict):
        """job_posting collection has all expected fields with correct types.
        Specifically: title (string), is_active (bool), location_ids (int32[]),
        salary_eur (int32, optional), coordinates NOT present (only on location)."""
        info = _col(ts_client, alias_map, "job_posting").retrieve()
        fields_by_name = {f["name"]: f for f in info["fields"]}

        assert fields_by_name["title"]["type"] == "string"
        assert fields_by_name["is_active"]["type"] == "bool"
        assert fields_by_name["location_ids"]["type"] == "int32[]"
        assert fields_by_name["salary_eur"]["type"] == "int32"
        assert fields_by_name["salary_eur"].get("optional") is True
        assert fields_by_name["first_seen_at"]["type"] == "int64"
        assert fields_by_name["company_id"]["type"] == "string"
        assert fields_by_name["company_name"]["type"] == "string"
        assert fields_by_name["experience_min_years"]["type"] == "float"
        assert fields_by_name["experience_max_years"]["type"] == "float"
        assert fields_by_name["experience_min"]["type"] == "int32"
        assert fields_by_name["experience_max"]["type"] == "int32"
        assert "coordinates" not in fields_by_name, (
            "coordinates should only be on location collection, not job_posting"
        )

    def test_job_posting_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """job_posting has the expected number of fields (28)."""
        info = _col(ts_client, alias_map, "job_posting").retrieve()
        assert len(info["fields"]) == 28

    def test_location_schema_has_geopoint(self, ts_client: typesense.Client, alias_map: dict):
        """location collection has 'coordinates' field of type 'geopoint'."""
        info = _col(ts_client, alias_map, "location").retrieve()
        fields_by_name = {f["name"]: f for f in info["fields"]}
        assert "coordinates" in fields_by_name
        assert fields_by_name["coordinates"]["type"] == "geopoint"

    def test_technology_schema_has_symbols(self, ts_client: typesense.Client, alias_map: dict):
        """technology collection has token_separators and symbols_to_index
        configured for +, #, . characters."""
        info = _col(ts_client, alias_map, "technology").retrieve()
        assert set(info.get("token_separators", [])) == {"+", "#", "."}
        assert set(info.get("symbols_to_index", [])) == {"+", "#", "."}

    def test_location_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """location collection has 12 fields."""
        info = _col(ts_client, alias_map, "location").retrieve()
        assert len(info["fields"]) == 12

    def test_occupation_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """occupation collection has 8 fields."""
        info = _col(ts_client, alias_map, "occupation").retrieve()
        assert len(info["fields"]) == 8

    def test_seniority_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """seniority collection has 7 fields."""
        info = _col(ts_client, alias_map, "seniority").retrieve()
        assert len(info["fields"]) == 7

    def test_technology_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """technology collection has 6 fields."""
        info = _col(ts_client, alias_map, "technology").retrieve()
        assert len(info["fields"]) == 6

    def test_company_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """company collection has 8 fields (id is implicit)."""
        info = _col(ts_client, alias_map, "company").retrieve()
        assert len(info["fields"]) == 8

    def test_watchlist_field_count(self, ts_client: typesense.Client, alias_map: dict):
        """watchlist collection has 11 fields (id is implicit)."""
        info = _col(ts_client, alias_map, "watchlist").retrieve()
        assert len(info["fields"]) == 11


# ============================================================================
# Data integrity tests
# ============================================================================


class TestDataIntegrity:
    """Verify seeded documents have correct sentinel values, denormalized
    names, timestamps, and structural invariants."""

    def test_job_posting_denormalized_fields(self, ts_client: typesense.Client, alias_map: dict):
        """For each seeded posting: title is non-empty, company_name matches,
        location_names length == location_ids length, technology_names length
        == technology_ids length, first_seen_at > 0."""
        col = _col(ts_client, alias_map, "job_posting")
        for posting_data in POSTINGS:
            doc = col.documents[posting_data["id"]].retrieve()
            assert doc["title"], "title should be non-empty"
            assert doc["company_name"] == posting_data["company_name"]
            assert len(doc["location_names"]) == len(doc["location_ids"])
            assert len(doc["technology_names"]) == len(doc["technology_ids"])
            assert doc["first_seen_at"] > 0

    def test_job_posting_timestamps_are_unix(self, ts_client: typesense.Client, alias_map: dict):
        """first_seen_at and last_seen_at are integers (unix timestamps),
        not ISO strings. Values should be > 1600000000 (post-2020)."""
        col = _col(ts_client, alias_map, "job_posting")
        for posting_data in POSTINGS[:5]:
            doc = col.documents[posting_data["id"]].retrieve()
            assert isinstance(doc["first_seen_at"], int)
            assert doc["first_seen_at"] > 1_600_000_000
            if "last_seen_at" in doc and doc.get("last_seen_at"):
                assert isinstance(doc["last_seen_at"], int)
                assert doc["last_seen_at"] > 1_600_000_000

    def test_job_posting_sentinel_experience(self, ts_client: typesense.Client, alias_map: dict):
        """Postings with experience_min = -1 in seed data should have
        experience_min = -1 in Typesense (sentinel value for unset)."""
        col = _col(ts_client, alias_map, "job_posting")
        sentinel_postings = [p for p in POSTINGS if p["experience_min"] == -1]
        assert len(sentinel_postings) > 0, "Should have at least one sentinel posting"
        for posting_data in sentinel_postings:
            doc = col.documents[posting_data["id"]].retrieve()
            assert doc["experience_min"] == -1, (
                f"Expected sentinel -1 for experience_min, got {doc['experience_min']}"
            )
            assert doc["experience_min_years"] == -1.0
            assert doc["experience_max_years"] == -1.0

    def test_job_posting_sentinel_locales(self, ts_client: typesense.Client, alias_map: dict):
        """Postings with locales = ['_none'] in seed data should have
        locales = ['_none'] in Typesense (sentinel value for empty)."""
        col = _col(ts_client, alias_map, "job_posting")
        sentinel_postings = [p for p in POSTINGS if p["locales"] == ["_none"]]
        assert len(sentinel_postings) > 0, "Should have at least one _none locale posting"
        for posting_data in sentinel_postings:
            doc = col.documents[posting_data["id"]].retrieve()
            assert doc["locales"] == ["_none"], (
                f"Expected ['_none'] for locales, got {doc['locales']}"
            )

    def test_job_posting_location_geo_types(self, ts_client: typesense.Client, alias_map: dict):
        """Postings with location_ids should have location_geo_types array
        of same length, with values in ['city', 'region', 'country', 'macro']."""
        col = _col(ts_client, alias_map, "job_posting")
        valid_types = {"city", "region", "country", "macro"}
        for posting_data in POSTINGS[:10]:
            doc = col.documents[posting_data["id"]].retrieve()
            if doc["location_ids"]:
                assert len(doc["location_geo_types"]) == len(doc["location_ids"])
                for geo_type in doc["location_geo_types"]:
                    assert geo_type in valid_types, (
                        f"Unexpected geo_type '{geo_type}', expected one of {valid_types}"
                    )

    def test_job_posting_has_source_url(self, ts_client: typesense.Client, alias_map: dict):
        """Postings should have source_url field (string or absent when optional)."""
        col = _col(ts_client, alias_map, "job_posting")
        postings_with_url = [p for p in POSTINGS if "source_url" in p]
        assert len(postings_with_url) > 0
        for posting_data in postings_with_url[:5]:
            doc = col.documents[posting_data["id"]].retrieve()
            assert "source_url" in doc
            assert isinstance(doc["source_url"], str)
            assert doc["source_url"].startswith("https://")

    def test_job_posting_salary_values(self, ts_client: typesense.Client, alias_map: dict):
        """salary_eur is either absent/None or > 0."""
        col = _col(ts_client, alias_map, "job_posting")
        for posting_data in POSTINGS:
            doc = col.documents[posting_data["id"]].retrieve()
            if "salary_eur" in doc and doc["salary_eur"] is not None:
                assert doc["salary_eur"] > 0

    def test_location_collection_has_coordinates(
        self, ts_client: typesense.Client, alias_map: dict
    ):
        """City locations should have a 'coordinates' field that is a
        [lat, lng] array with lat in [-90, 90] and lng in [-180, 180]."""
        col = _col(ts_client, alias_map, "location")
        city_locations = [loc for loc in LOCATIONS if loc["type"] == "city"]
        assert len(city_locations) >= 5
        for loc_data in city_locations:
            doc = col.documents[loc_data["id"]].retrieve()
            assert "coordinates" in doc, f"City '{loc_data['name_en']}' should have coordinates"
            coords = doc["coordinates"]
            assert isinstance(coords, list) and len(coords) == 2
            lat, lng = coords
            assert -90 <= lat <= 90, f"lat {lat} out of range for {loc_data['name_en']}"
            assert -180 <= lng <= 180, f"lng {lng} out of range for {loc_data['name_en']}"

    def test_location_collection_multilingual(self, ts_client: typesense.Client, alias_map: dict):
        """Locations should have name_en populated. At least some should
        have name_de, name_fr, name_it populated."""
        col = _col(ts_client, alias_map, "location")
        has_de = 0
        has_fr = 0
        has_it = 0
        for loc_data in LOCATIONS:
            doc = col.documents[loc_data["id"]].retrieve()
            assert doc.get("name_en"), f"name_en missing for location {loc_data['id']}"
            if doc.get("name_de"):
                has_de += 1
            if doc.get("name_fr"):
                has_fr += 1
            if doc.get("name_it"):
                has_it += 1
        assert has_de > 0, "At least one location should have name_de"
        assert has_fr > 0, "At least one location should have name_fr"
        assert has_it > 0, "At least one location should have name_it"

    def test_occupation_collection_per_locale(self, ts_client: typesense.Client, alias_map: dict):
        """Occupation docs have a 'locale' field. For a known occupation slug,
        verify docs exist for at least 'en' and 'de' locales."""
        col = _col(ts_client, alias_map, "occupation")
        results = col.documents.search(
            {
                "q": "Software Engineer",
                "query_by": "name",
                "filter_by": "occupation_id:=7000",
            }
        )
        locales_found = {hit["document"]["locale"] for hit in results["hits"]}
        assert "en" in locales_found, "Should have 'en' locale for Software Engineer"
        assert "de" in locales_found, "Should have 'de' locale for Software Engineer"


# ============================================================================
# Search tests
# ============================================================================


class TestSearch:
    """Verify search, faceting, and filtering work correctly."""

    def _company_filter(self) -> str:
        """Return a filter_by clause that restricts to seeded companies only."""
        ids = ",".join(c["id"] for c in COMPANIES)
        return f"company_id:[{ids}]"

    def test_search_by_keyword_returns_matches(self, ts_client: typesense.Client, alias_map: dict):
        """Searching for 'Engineer' returns matching job postings."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "Engineer",
                "query_by": "title",
                "filter_by": self._company_filter(),
            }
        )
        assert results["found"] > 0, "Search for 'Engineer' should return results"
        for hit in results["hits"]:
            assert "engineer" in hit["document"]["title"].lower()

    def test_search_by_keyword_developer(self, ts_client: typesense.Client, alias_map: dict):
        """Searching for 'Developer' returns matching postings."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "Developer",
                "query_by": "title",
                "filter_by": self._company_filter(),
            }
        )
        assert results["found"] > 0, "Search for 'Developer' should return results"
        for hit in results["hits"]:
            assert "developer" in hit["document"]["title"].lower()

    def test_facet_by_company_id(self, ts_client: typesense.Client, alias_map: dict):
        """Faceting by company_id returns counts per company."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "facet_by": "company_id",
                "filter_by": self._company_filter(),
            }
        )
        facet_counts = results["facet_counts"]
        assert len(facet_counts) > 0, "Should have facet results"
        company_facet = facet_counts[0]
        assert company_facet["field_name"] == "company_id"
        assert len(company_facet["counts"]) > 0, "Should have at least one company facet"

        # Verify counts sum to total
        total_from_facets = sum(c["count"] for c in company_facet["counts"])
        assert total_from_facets == results["found"]

    def test_filter_is_active(self, ts_client: typesense.Client, alias_map: dict):
        """Filtering by is_active:true returns only active postings."""
        col = _col(ts_client, alias_map, "job_posting")
        active_results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"is_active:true && {self._company_filter()}",
            }
        )
        expected_active = sum(1 for p in POSTINGS if p["is_active"])
        assert active_results["found"] == expected_active

        inactive_results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": f"is_active:false && {self._company_filter()}",
            }
        )
        expected_inactive = sum(1 for p in POSTINGS if not p["is_active"])
        assert inactive_results["found"] == expected_inactive

    def test_filter_by_location_ids(self, ts_client: typesense.Client, alias_map: dict):
        """Filtering by location_ids returns only postings in those locations."""
        col = _col(ts_client, alias_map, "job_posting")
        target_loc_id = LOCATIONS[0]["location_id"]  # Zurich
        results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": (f"location_ids:=[{target_loc_id}] && {self._company_filter()}"),
            }
        )
        expected = sum(1 for p in POSTINGS if target_loc_id in p["location_ids"])
        assert results["found"] == expected
        for hit in results["hits"]:
            assert target_loc_id in hit["document"]["location_ids"]

    def test_facet_by_is_active(self, ts_client: typesense.Client, alias_map: dict):
        """Faceting by is_active returns true/false counts."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "facet_by": "is_active",
                "filter_by": self._company_filter(),
            }
        )
        facet = next(f for f in results["facet_counts"] if f["field_name"] == "is_active")
        values = {c["value"]: c["count"] for c in facet["counts"]}
        assert "true" in values
        assert "false" in values
        assert values["true"] == sum(1 for p in POSTINGS if p["is_active"])
        assert values["false"] == sum(1 for p in POSTINGS if not p["is_active"])

    def test_search_no_results(self, ts_client: typesense.Client, alias_map: dict):
        """Searching for a nonsense keyword returns zero results."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "xyznonexistentkeyword12345",
                "query_by": "title",
            }
        )
        assert results["found"] == 0
        assert results["hits"] == []

    def test_search_pagination(self, ts_client: typesense.Client, alias_map: dict):
        """Pagination returns different documents on different pages."""
        col = _col(ts_client, alias_map, "job_posting")
        page1 = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": self._company_filter(),
                "page": 1,
                "per_page": 5,
            }
        )
        page2 = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": self._company_filter(),
                "page": 2,
                "per_page": 5,
            }
        )
        ids_page1 = {h["document"]["id"] for h in page1["hits"]}
        ids_page2 = {h["document"]["id"] for h in page2["hits"]}
        assert len(ids_page1) == 5
        assert len(ids_page2) == 5
        assert ids_page1.isdisjoint(ids_page2), "Pages should not overlap"

    def test_search_with_combined_filters(self, ts_client: typesense.Client, alias_map: dict):
        """Combining multiple filters (is_active + location + occupation) works."""
        col = _col(ts_client, alias_map, "job_posting")
        target_loc_id = LOCATIONS[0]["location_id"]
        target_occ_id = OCCUPATIONS[0]["occupation_id"]  # Software Engineer, en
        results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "filter_by": (
                    f"is_active:true && "
                    f"location_ids:=[{target_loc_id}] && "
                    f"occupation_id:={target_occ_id} && "
                    f"{self._company_filter()}"
                ),
            }
        )
        # Verify all results satisfy all filters
        for hit in results["hits"]:
            doc = hit["document"]
            assert doc["is_active"] is True
            assert target_loc_id in doc["location_ids"]
            assert doc["occupation_id"] == target_occ_id

    def test_facet_by_company_name(self, ts_client: typesense.Client, alias_map: dict):
        """Faceting by company_name returns human-readable names."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "*",
                "query_by": "title",
                "facet_by": "company_name",
                "filter_by": self._company_filter(),
            }
        )
        facet = next(f for f in results["facet_counts"] if f["field_name"] == "company_name")
        facet_names = {c["value"] for c in facet["counts"]}
        expected_names = {c["name"] for c in COMPANIES}
        assert facet_names == expected_names

    def test_company_search(self, ts_client: typesense.Client, alias_map: dict):
        """Searching the company collection by name works."""
        col = _col(ts_client, alias_map, "company")
        results = col.documents.search(
            {
                "q": "Acme",
                "query_by": "name",
            }
        )
        assert results["found"] >= 1
        assert any("Acme" in h["document"]["name"] for h in results["hits"])

    def test_company_posting_counts(self, ts_client: typesense.Client, alias_map: dict):
        """Companies have active_posting_count reflecting seeded postings."""
        col = _col(ts_client, alias_map, "company")
        for company in COMPANIES:
            doc = col.documents[company["id"]].retrieve()
            expected_active = sum(
                1 for p in POSTINGS if p["company_id"] == company["id"] and p["is_active"]
            )
            assert doc["active_posting_count"] == expected_active, (
                f"Company {company['name']}: expected {expected_active} active postings, "
                f"got {doc['active_posting_count']}"
            )


# ============================================================================
# Special character tests
# ============================================================================


class TestSpecialCharacters:
    """Verify that technology names with special characters (+, #, .) are
    searchable thanks to symbols_to_index configuration."""

    def test_search_cpp(self, ts_client: typesense.Client, alias_map: dict):
        """Search for 'C++' in technology collection returns a result."""
        col = _col(ts_client, alias_map, "technology")
        results = col.documents.search(
            {
                "q": "C++",
                "query_by": "name",
            }
        )
        assert results["found"] >= 1, "Search for 'C++' should return at least 1 result"
        names = [h["document"]["name"] for h in results["hits"]]
        assert any("C++" in n for n in names), f"Expected 'C++' in results, got {names}"

    def test_search_csharp(self, ts_client: typesense.Client, alias_map: dict):
        """Search for 'C#' in technology collection returns a result."""
        col = _col(ts_client, alias_map, "technology")
        results = col.documents.search(
            {
                "q": "C#",
                "query_by": "name",
            }
        )
        assert results["found"] >= 1, "Search for 'C#' should return at least 1 result"
        names = [h["document"]["name"] for h in results["hits"]]
        assert any("C#" in n for n in names), f"Expected 'C#' in results, got {names}"

    def test_search_dotnet(self, ts_client: typesense.Client, alias_map: dict):
        """Search for '.NET' in technology collection returns a result."""
        col = _col(ts_client, alias_map, "technology")
        results = col.documents.search(
            {
                "q": ".NET",
                "query_by": "name",
            }
        )
        assert results["found"] >= 1, "Search for '.NET' should return at least 1 result"
        names = [h["document"]["name"] for h in results["hits"]]
        assert any(".NET" in n for n in names), f"Expected '.NET' in results, got {names}"

    def test_search_cpp_in_job_postings(self, ts_client: typesense.Client, alias_map: dict):
        """Searching job postings for 'C++' matches postings with C++ in title."""
        col = _col(ts_client, alias_map, "job_posting")
        results = col.documents.search(
            {
                "q": "C++",
                "query_by": "title,technology_names",
                "filter_by": (f"company_id:[{','.join(c['id'] for c in COMPANIES)}]"),
            }
        )
        # We have a posting titled "C++ Systems Developer"
        assert results["found"] >= 1, "Should find postings matching C++"
