from __future__ import annotations

from src.typesense_schema import COLLECTIONS


def test_job_posting_schema_carries_original_salary_for_detail_views() -> None:
    job_posting = next(c for c in COLLECTIONS if c["name"] == "job_posting")
    fields = {field["name"]: field for field in job_posting["fields"]}

    for name in ("salary_min", "salary_max", "salary_currency", "salary_period"):
        assert fields[name]["optional"] is True
        assert fields[name]["index"] is False


def test_job_posting_schema_keeps_unused_compatibility_fields_stored_only() -> None:
    job_posting = next(c for c in COLLECTIONS if c["name"] == "job_posting")
    fields = {field["name"]: field for field in job_posting["fields"]}

    for name in ("occupation_id", "occupation_name", "last_seen_at"):
        assert fields[name]["index"] is False
        assert fields[name]["optional"] is True


def test_job_posting_schema_has_numeric_candidate_order() -> None:
    job_posting = next(c for c in COLLECTIONS if c["name"] == "job_posting")
    fields = {f["name"]: f for f in job_posting["fields"]}

    assert fields["candidate_order_key"] == {
        "name": "candidate_order_key",
        "type": "string",
        "index": False,
        "optional": True,
    }
    for name in ("candidate_order_hi", "candidate_order_lo"):
        assert fields[name] == {
            "name": name,
            "type": "int64",
            "sort": True,
            "optional": True,
        }


def test_taxonomy_schema_carries_web_hierarchy_contract() -> None:
    schemas = {collection["name"]: collection for collection in COLLECTIONS}
    posting_fields = {field["name"]: field for field in schemas["job_posting"]["fields"]}
    location_fields = {field["name"]: field for field in schemas["location"]["fields"]}
    occupation_fields = {field["name"]: field for field in schemas["occupation"]["fields"]}

    assert posting_fields["location_direct_ids"]["facet"] is True
    assert location_fields["slug"].get("index", True) is True
    assert location_fields["ancestor_ids"]["facet"] is True
    assert {"parent_id", "member_country_ids"} <= location_fields.keys()
    assert occupation_fields["slug"].get("index", True) is True
    assert {"parent_id", "domain_id", "domain_slug"} <= occupation_fields.keys()


def test_company_schema_indexes_localized_industry_search_fields() -> None:
    company = next(c for c in COLLECTIONS if c["name"] == "company")
    fields = {field["name"]: field for field in company["fields"]}

    for name in (
        "industry_name",
        "industry_name_de",
        "industry_name_fr",
        "industry_name_it",
    ):
        assert fields[name]["type"] == "string"
        assert fields[name].get("index", True) is True


def _company_field(name: str) -> dict:
    company = next(c for c in COLLECTIONS if c["name"] == "company")
    return next(f for f in company["fields"] if f["name"] == name)


def test_company_slug_is_indexed() -> None:
    """`apps/web/src/lib/actions/company.ts::_fetchCompanyBySlugFromTypesense`
    issues `filter_by: slug:=<slug>` for every company-detail page render.
    Typesense rejects filter clauses on non-indexed fields with
    "Cannot filter on non-indexed field", so flipping this back to
    `index: false` would re-introduce #2931 (every company lookup falls
    through to Postgres)."""
    field = _company_field("slug")
    # `index: true` is Typesense's default — accept either explicit True or
    # the omitted-key form. Reject explicit False.
    assert field.get("index", True) is True


def test_company_slug_field_type_is_string() -> None:
    """Belt + braces: the filter `slug:=<slug>` only works against a string
    field. Cheap sanity to catch a clumsy refactor that retypes it."""
    assert _company_field("slug")["type"] == "string"
