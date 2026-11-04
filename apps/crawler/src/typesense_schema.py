"""Retained schema snapshot for offline/native parity; setup runs in Go."""

from __future__ import annotations

COLLECTIONS: list[dict] = [
    {
        "name": "job_posting",
        "fields": [
            # Stable UUID high-byte partition for bounded bidirectional
            # reconciliation. Optional only for the in-place rollout; every
            # new/upserted document carries it, and the first complete repair
            # cycle removes legacy documents that still lack it.
            {
                "name": "reconciliation_bucket",
                "type": "string",
                "facet": True,
                "optional": True,
            },
            {"name": "company_id", "type": "string", "facet": True},
            {"name": "company_name", "type": "string", "facet": True},
            {"name": "company_slug", "type": "string", "index": False},
            {"name": "company_icon", "type": "string", "index": False, "optional": True},
            {"name": "title", "type": "string"},
            {"name": "is_active", "type": "bool", "facet": True},
            # `has_content` is True iff the posting has both a non-empty title
            # AND a description blob in R2 (description_r2_hash IS NOT NULL).
            # Web search surfaces filter on `has_content:!=false` so postings
            # without title or description are hidden (issue #2917). Optional
            # so existing docs stay visible until backfill replays the field;
            # `!=false` matches `true` and absent values, only excluding docs
            # the exporter has explicitly stamped as `false`.
            {"name": "has_content", "type": "bool", "facet": True, "optional": True},
            # Mining eligibility only: ordinary search continues to show the posting.
            {"name": "tdm_reserved", "type": "bool", "optional": True},
            {"name": "location_ids", "type": "int32[]", "facet": True},
            # Unexpanded source IDs as stored on the crawler row. Company
            # location views need direct-tag counts, while ``location_ids``
            # intentionally includes ancestors for search filtering.
            {
                "name": "location_direct_ids",
                "type": "int32[]",
                "facet": True,
                "optional": True,
            },
            {"name": "location_names", "type": "string[]", "facet": True},
            {"name": "location_types", "type": "string[]", "facet": True},
            {"name": "location_geo_types", "type": "string[]", "index": False},
            # The leaf occupation scalar and its display name remain stored for
            # exporter/reconciliation compatibility, but every production web
            # filter and facet uses the ancestor-expanded ``occupation_ids``.
            # Keeping these two fields out of the in-memory index reduces the
            # collection footprint without changing returned documents.
            {
                "name": "occupation_id",
                "type": "int32",
                "index": False,
                "optional": True,
            },
            {"name": "occupation_ids", "type": "int32[]", "facet": True, "optional": True},
            {
                "name": "occupation_name",
                "type": "string",
                "index": False,
                "optional": True,
            },
            {"name": "seniority_id", "type": "int32", "facet": True, "optional": True},
            {"name": "seniority_name", "type": "string", "facet": True, "optional": True},
            {"name": "technology_ids", "type": "int32[]", "facet": True},
            {"name": "technology_names", "type": "string[]", "facet": True},
            {"name": "employment_type", "type": "string", "facet": True, "optional": True},
            {"name": "salary_eur", "type": "int32", "facet": True, "optional": True},
            # Original salary fields are required by posting detail and saved-job
            # snapshots. ``salary_eur`` remains the normalized search/facet value;
            # these fields preserve the source currency and period for display.
            {"name": "salary_min", "type": "int32", "index": False, "optional": True},
            {"name": "salary_max", "type": "int32", "index": False, "optional": True},
            {"name": "salary_currency", "type": "string", "index": False, "optional": True},
            {"name": "salary_period", "type": "string", "index": False, "optional": True},
            # Precise decimal-year experience fields. Added alongside the
            # legacy integer fields below so production can add them in-place
            # without rebuilding the existing job_posting collection first.
            {"name": "experience_min_years", "type": "float", "facet": True, "optional": True},
            {"name": "experience_max_years", "type": "float", "facet": True, "optional": True},
            # Legacy whole-year compatibility fields. New decimal rows are
            # encoded conservatively by exporter.py (min ceil, max floor) so
            # fallback filters cannot broaden precise float matches.
            {"name": "experience_min", "type": "int32", "facet": True},
            # `experience_max` is stamped alongside `experience_min` so the web
            # filter can do range-overlap matching (e.g. user wants "exactly 6
            # years"; a row stored as 5-10 years must match). Sentinels:
            # `-1` when `experience_min` is also `-1` (no info from the
            # extractor) and `99` for open-ended ("5+ years") rows where the
            # extractor returns `max=None` — see exporter.py. Optional so
            # docs that haven't been backfilled yet stay valid; the filter
            # treats absent `experience_max` as part of the "no required
            # experience" sentinel bucket. See #3217.
            {"name": "experience_max", "type": "int32", "facet": True, "optional": True},
            {"name": "locales", "type": "string[]", "facet": True},
            {"name": "source_url", "type": "string", "index": False, "optional": True},
            {"name": "first_seen_at", "type": "int64"},
            # An unindexed exact key validates returned hits. Signed int64
            # halves provide the UUID tie-break without a string sort index.
            # Optional only for the in-place transition; readiness requires a
            # measured full backfill and reconciliation proof.
            {
                "name": "candidate_order_key",
                "type": "string",
                "index": False,
                "optional": True,
            },
            {"name": "candidate_order_hi", "type": "int64", "sort": True, "optional": True},
            {"name": "candidate_order_lo", "type": "int64", "sort": True, "optional": True},
            # Emitted for compatibility and diagnostics, but no search, filter,
            # facet, sort, web response, or reconciliation path consumes it.
            # The value remains stored on disk and returned on direct retrieval.
            {
                "name": "last_seen_at",
                "type": "int64",
                "index": False,
                "optional": True,
            },
        ],
        "default_sorting_field": "first_seen_at",
        "token_separators": ["-", "/"],
    },
    {
        "name": "location",
        "fields": [
            {"name": "location_id", "type": "int32"},
            # Exact slug filters back the web filter-chip resolver. Unlike the
            # old Postgres mirror lookup, this field must remain indexed.
            {"name": "slug", "type": "string"},
            {"name": "name_en", "type": "string", "locale": "en"},
            {"name": "name_de", "type": "string", "locale": "de", "optional": True},
            {"name": "name_fr", "type": "string", "locale": "fr", "optional": True},
            {"name": "name_it", "type": "string", "locale": "it", "optional": True},
            # Natural-language synonyms queried alongside ``name_*`` so users
            # who type "Europe" or "European Union" surface the EU macro row
            # whose canonical ``name_en`` is just "EU". Currently populated
            # for macro regions only (sync.py — ``_LOCATION_MACRO_ALIASES``);
            # countries fall through to ``name_*``. See #2939.
            {"name": "aliases", "type": "string[]", "optional": True},
            {"name": "parent_name", "type": "string", "optional": True},
            {"name": "parent_id", "type": "int32", "optional": True},
            # Self + geographic parents + macro regions. This supports the
            # descendant expansion contract without a recursive SQL CTE.
            {
                "name": "ancestor_ids",
                "type": "int32[]",
                "facet": True,
                "optional": True,
            },
            # Populated only on macro documents; used by the location modal's
            # tooltip/disable semantics and the company multi-country gate.
            {
                "name": "member_country_ids",
                "type": "int32[]",
                "index": False,
                "optional": True,
            },
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
            {"name": "slug", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "aliases", "type": "string[]"},
            {"name": "parent_id", "type": "int32", "optional": True},
            {"name": "domain_id", "type": "int32", "optional": True},
            {"name": "domain_slug", "type": "string", "index": False, "optional": True},
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
            {"name": "slug", "type": "string"},
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
            # `slug` MUST be indexed: web/_fetchCompanyBySlugFromTypesense uses
            # `filter_by: slug:=<slug>` for every company detail page. With
            # `index: false`, Typesense rejects the filter ("Cannot filter on
            # non-indexed field"), so every lookup falls through to Postgres
            # — the OG-image prerender (4400 × 4 locales) caused #2918 (build
            # ECONNRESET) by hitting Supabase 17,600 times per build. See
            # #2931.
            {"name": "slug", "type": "string"},
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
            # Industry suggestions query all localized names, not just the
            # English display value. These fields must therefore stay indexed;
            # ``index: false`` makes Typesense reject the entire ``query_by``
            # list even for a wildcard browse request.
            {"name": "industry_name_de", "type": "string", "optional": True},
            {"name": "industry_name_fr", "type": "string", "optional": True},
            {"name": "industry_name_it", "type": "string", "optional": True},
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
            {"name": "filters_json", "type": "string", "index": False, "optional": True},
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
