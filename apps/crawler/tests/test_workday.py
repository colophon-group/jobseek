from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.workday import (
    PAGE_SIZE,
    _api_base,
    _api_list_stream,
    _api_list_url,
    _cross_site_path_key,
    _discover_sites,
    _fetch_job_count,
    _group_split_facet_values,
    _job_url,
    _list_all_sites,
    _list_all_sites_stream,
    _materially_below_advertised_total,
    _paginate_query,
    _parse_components,
    _pick_split_facet,
    _post_page_with_retry,
    can_handle,
    discover,
)
from src.core.scrapers.workday import (
    WorkdayDetailPayloadError,
    _detail_url,
    _normalize_workday_location,
    _parse_detail,
    _parse_job_url,
    _parse_location_type,
    scrape,
)
from src.shared.http import (
    WORKDAY_LIST_303_INCIDENT,
    RequestHostTrackingTransport,
    track_request_hosts,
)
from src.shared.http_retry import PaginationFetchError


class TestParseComponents:
    def test_standard_url(self):
        result = _parse_components("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_with_locale_prefix(self):
        result = _parse_components(
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        )
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_hyphenated_company(self):
        result = _parse_components("https://my-company.wd1.myworkdayjobs.com/External")
        assert result == ("my-company", "wd1", "External")

    def test_non_matching_url(self):
        assert _parse_components("https://example.com/careers") is None

    def test_with_trailing_slash(self):
        result = _parse_components("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/")
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")


class TestApiBase:
    def test_basic(self):
        result = _api_base("nvidia", "wd5")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia"


class TestApiListUrl:
    def test_basic(self):
        result = _api_list_url("nvidia", "wd5", "ExtSite")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/jobs"


class TestCrossSitePathKey:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (
                "/job/USA-WI-Marinette/Engineer_885928-2",
                "/job/USA-WI-Marinette/Engineer_885928",
            ),
            (
                "/job/USA-WI-Marinette/Engineer_R-258265-2",
                "/job/USA-WI-Marinette/Engineer_R-258265",
            ),
        ],
    )
    def test_strips_workday_distribution_copy_suffix(self, path, expected):
        assert _cross_site_path_key(path) == expected

    @pytest.mark.parametrize(
        "path",
        [
            "/job/USA-WI-Marinette/JR-2",
            "/job/US-Gaithersburg/Capital-Projects-Director_R-258265",
        ],
    )
    def test_preserves_paths_without_copy_suffix(self, path):
        assert _cross_site_path_key(path) == path


class TestDetailUrl:
    def test_basic(self):
        result = _detail_url("nvidia", "wd5", "ExtSite", "/job/Senior-Engineer/JR001")
        assert (
            result
            == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/job/Senior-Engineer/JR001"
        )


class TestJobUrl:
    def test_basic(self):
        result = _job_url("nvidia", "wd5", "ExtSite", "/Senior-Engineer/JR001")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/ExtSite/Senior-Engineer/JR001"


class TestParseLocationtype:
    def test_remote(self):
        assert _parse_location_type("Remote") == "remote"

    def test_flexible(self):
        assert _parse_location_type("Flexible") == "hybrid"

    def test_hybrid(self):
        assert _parse_location_type("Hybrid") == "hybrid"

    def test_none(self):
        assert _parse_location_type(None) is None

    def test_onsite(self):
        # Centralized via #2992 — Workday's ``remoteType`` enum does emit
        # ``onsite`` per the public API, and the central map handles
        # ``On-Site`` / ``OnSite``.  Pre-#2992 the local
        # ``_parse_location_type`` only knew ``remote``/``flexible``/
        # ``hybrid`` and silently dropped ``On-Site`` to ``None`` — that
        # was a bug, not an invariant.
        assert _parse_location_type("On-Site") == "onsite"
        assert _parse_location_type("OnSite") == "onsite"

    def test_case_insensitive(self):
        assert _parse_location_type("REMOTE") == "remote"

    def test_unknown_falls_back_to_none(self):
        # Local fallback is ``None`` (not the central default of
        # ``onsite``) so unknown upstream values surface as ``None`` in
        # ``JobContent.job_location_type`` — matches pre-#2992
        # behaviour.
        assert _parse_location_type("Mystery type") is None


class TestNormalizeWorkdayLocation:
    """Test _normalize_workday_location for Workday location formats."""

    # Code format: US-STATE-CITY with building/address after ~
    def test_code_format_with_tilde(self):
        assert (
            _normalize_workday_location("US-AR-SPRINGDALE-BLDG 1 ~ 275 E Robinson Ave ~ BLDG 1")
            == "Springdale, AR, US"
        )

    def test_code_format_with_building(self):
        assert (
            _normalize_workday_location("US-MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG")
            == "Tewksbury, MA, US"
        )

    def test_code_format_remote(self):
        assert _normalize_workday_location("US-CT-REMOTE") == "Remote, CT, US"

    def test_code_format_au(self):
        assert (
            _normalize_workday_location("AU-NSW-NOWRA-039 ~ 39 Wugan St ~ WUGAN Lot 10 Yerriyong")
            == "Nowra, NSW, AU"
        )

    def test_code_format_gb(self):
        assert _normalize_workday_location("GB-LND-LONDON") == "London, LND, GB"

    # Display format: space-separated without commas
    def test_display_format_double_space(self):
        # Citi returns "Sg  Singapore" (double space)
        assert _normalize_workday_location("Sg  Singapore") == "Sg, Singapore"

    def test_display_format_triple_part(self):
        assert _normalize_workday_location("Heredia  Costa Rica") == "Heredia, Costa Rica"

    # Already comma-separated (pass through)
    def test_already_comma_separated(self):
        assert (
            _normalize_workday_location("New York, NY, United States")
            == "New York, NY, United States"
        )

    def test_maurices_us_facility_uses_upstream_country(self):
        assert (
            _normalize_workday_location(
                "Store 1272-South Franklin-maurices-Colby, KS 67701",
                country="United States of America",
                tenant="maurices",
            )
            == "Colby, KS, United States of America"
        )

    def test_maurices_canada_facility_strips_postal_code(self):
        assert (
            _normalize_workday_location(
                "Store 4148-Uptown Centre-Fredericton, NB E3B 3C1",
                country="Canada",
                tenant="maurices",
            )
            == "Fredericton, NB, Canada"
        )

    def test_maurices_corporate_facility(self):
        assert (
            _normalize_workday_location(
                "Corporate Office-maurices-Duluth, MN 55802",
                country="United States of America",
                tenant="maurices",
            )
            == "Duluth, MN, United States of America"
        )

    def test_tenant_separator_preserves_hyphenated_city(self):
        assert (
            _normalize_workday_location(
                "Store 9999-Test Mall-maurices-Winston-Salem, NC 27101",
                country="United States of America",
                tenant="maurices",
            )
            == "Winston-Salem, NC, United States of America"
        )

    def test_configured_tenant_alias_handles_live_maurices_typo(self):
        raw = "Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660"
        assert (
            _normalize_workday_location(
                raw,
                country="United States of America",
                tenant="maurices",
                tenant_aliases=("maurice",),
            )
            == "Pflugerville, TX, United States of America"
        )

    def test_unconfigured_tenant_alias_fails_closed(self):
        raw = "Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660"
        assert (
            _normalize_workday_location(
                raw,
                country="United States of America",
                tenant="maurices",
            )
            == raw
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "M2327-Omache Shopping Center-Omak, WA 98841",
                "Omak, WA, United States of America",
            ),
            (
                "M2340-Largo Plaza-maurices-Largo, FL 33771",
                "Largo, FL, United States of America",
            ),
            (
                "Store 2319 - Forum Plaza Shopping Center - Rolla, MO 65401",
                "Rolla, MO, United States of America",
            ),
            (
                "Store M2320-Park West Place-Stockton, CA 95219",
                "Stockton, CA, United States of America",
            ),
            (
                "Store M2323-The Uptown-Jonesboro, AR 72401",
                "Jonesboro, AR, United States of America",
            ),
            (
                "Store M2324-Creekside Town Center-Roseville, CA 95678",
                "Roseville, CA, United States of America",
            ),
            (
                "Store 2321-Dimond Center-Anchorage AK 99515",
                "Anchorage, AK, United States of America",
            ),
            (
                "Store 2333-Chesterfield Comns E-Chesterfield, MO  63005",
                "Chesterfield, MO, United States of America",
            ),
            (
                "Store 2326-Stone Creek Crossing-San Marcos, TX 78666",
                "San Marcos, TX, United States of America",
            ),
        ],
    )
    def test_live_maurices_facility_variants(self, raw, expected):
        assert (
            _normalize_workday_location(
                raw,
                country="United States of America",
                tenant="maurices",
            )
            == expected
        )

    def test_ambiguous_facility_boundary_fails_closed(self):
        raw = "Store 9999-Unknown-Winston-Salem, NC 27101"
        assert (
            _normalize_workday_location(
                raw,
                country="United States of America",
                tenant="maurices",
            )
            == raw
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "Field Mgmt-District 426-maurices",
            "Store 2325-Village at Allen-maurices",
            "Store 2328-Lebanon Marketplace-maurices",
            "M4145 – Westgate Home Centre –maurices",
            "Store 4146-Emerald Hills Centre-maurices",
        ],
    )
    def test_facility_without_city_evidence_fails_closed(self, raw):
        assert (
            _normalize_workday_location(
                raw,
                country="United States of America",
                tenant="maurices",
            )
            == raw
        )

    def test_does_not_rewrite_ordinary_hyphenated_location(self):
        assert (
            _normalize_workday_location(
                "Winston-Salem, NC 27101", country="United States of America"
            )
            == "Winston-Salem, NC 27101"
        )

    # Plain city name (unchanged)
    def test_plain_city(self):
        assert _normalize_workday_location("Singapore") == "Singapore"

    def test_empty_string(self):
        assert _normalize_workday_location("") == ""


class TestParseDetail:
    def test_full_detail(self):
        detail = {
            "jobPostingInfo": {
                "title": "Senior Engineer",
                "externalPath": "/Senior-Engineer/JR001",
                "jobDescription": "<p>Build software</p>",
                "location": "Santa Clara, CA",
                "additionalLocations": ["Austin, TX", "Remote"],
                "timeType": "Full-time",
                "remoteType": "Hybrid",
                "startDate": "2024-01-15",
                "jobReqId": "JR001",
            }
        }
        result = _parse_detail(detail)
        assert result.title == "Senior Engineer"
        assert result.description == "<p>Build software</p>"
        assert result.locations == ["Santa Clara, CA", "Austin, TX", "Remote"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2024-01-15"
        assert result.metadata == {"jobReqId": "JR001"}

    def test_missing_job_posting_info(self):
        result = _parse_detail({})
        assert result.title is None

    def test_locations_dedup(self):
        detail = {
            "jobPostingInfo": {
                "location": "NYC",
                "additionalLocations": ["NYC", "LA"],
            }
        }
        result = _parse_detail(detail)
        assert result.locations == ["NYC", "LA"]

    def test_no_locations(self):
        detail = {"jobPostingInfo": {}}
        result = _parse_detail(detail)
        assert result.locations is None

    def test_facility_location_uses_country_descriptor(self):
        detail = {
            "jobPostingInfo": {
                "location": "Store 4133-Leamington Pwr Ctr-maurices-Leamington, ON N8H 3C5",
                "country": {"descriptor": "Canada", "id": "country-id"},
            }
        }
        result = _parse_detail(detail, tenant="maurices")
        assert result.locations == ["Leamington, ON, Canada"]

    def test_facility_location_uses_configured_tenant_alias(self):
        detail = {
            "jobPostingInfo": {
                "location": ("Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660"),
                "country": {"descriptor": "United States of America"},
            }
        }
        result = _parse_detail(
            detail,
            tenant="maurices",
            tenant_aliases=("maurice",),
        )
        assert result.locations == ["Pflugerville, TX, United States of America"]

    def test_no_metadata(self):
        detail = {"jobPostingInfo": {}}
        result = _parse_detail(detail)
        assert result.metadata is None


class TestPickSplitFacet:
    def test_picks_facet_with_most_values(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 500},
                    {"id": "cat2", "count": 300},
                    {"id": "cat3", "count": 200},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                    {"id": "loc2", "count": 100},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "category"
        assert ids == ["cat1", "cat2", "cat3"]

    def test_skips_facet_with_value_at_cap(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 2000},  # At cap
                    {"id": "cat2", "count": 100},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "location"

    def test_no_valid_facets(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [{"id": "cat1", "count": 2000}],
            }
        ]
        assert _pick_split_facet(facets) is None

    def test_empty_facets(self):
        assert _pick_split_facet([]) is None

    def test_facet_without_values(self):
        facets = [{"facetParameter": "category", "values": []}]
        assert _pick_split_facet(facets) is None

    def test_picks_nested_facet(self):
        facets = [
            {
                "facetParameter": "locationMainGroup",
                "values": [
                    {
                        "facetParameter": "locations",
                        "values": [
                            {"id": "loc1", "count": 900},
                            {"id": "loc2", "count": 800},
                        ],
                    }
                ],
            }
        ]

        assert _pick_split_facet(facets) == ("locations", ["loc1", "loc2"])

    def test_preferred_facet_overrides_automatic_value_count_choice(self):
        facets = [
            {
                "facetParameter": "state",
                "values": [
                    {"id": "state-1", "count": 300},
                    {"id": "state-2", "count": 200},
                    {"id": "state-3", "count": 100},
                ],
            },
            {
                "facetParameter": "country",
                "values": [
                    {"id": "country-1", "count": 500},
                    {"id": "country-2", "count": 100},
                ],
            },
        ]

        assert _pick_split_facet(facets, preferred="country") == (
            "country",
            ["country-1", "country-2"],
        )

    def test_preferred_facet_must_remain_advertised(self):
        with pytest.raises(ValueError, match="split_facet 'country'.*not advertised"):
            _pick_split_facet([], preferred="country")

    def test_preferred_facet_rejects_a_capped_value(self):
        facets = [
            {
                "facetParameter": "country",
                "values": [{"id": "country-1", "count": 2000}],
            }
        ]

        with pytest.raises(ValueError, match="unsafe value count"):
            _pick_split_facet(facets, preferred="country")

    @pytest.mark.parametrize(
        "value",
        [
            {"count": 1},
            {"id": "", "count": 1},
            {"id": " country-1 ", "count": 1},
            {"id": "country-1", "count": -1},
            {"id": "country-1", "count": True},
            {"id": "country-1"},
        ],
    )
    def test_preferred_facet_rejects_malformed_values(self, value):
        facets = [{"facetParameter": "country", "values": [value]}]

        with pytest.raises(ValueError, match="invalid value id|unsafe value count"):
            _pick_split_facet(facets, preferred="country")

    def test_preferred_facet_rejects_duplicate_value_ids(self):
        facets = [
            {
                "facetParameter": "country",
                "values": [
                    {"id": "country-1", "count": 2},
                    {"id": "country-1", "count": 1},
                ],
            }
        ]

        with pytest.raises(ValueError, match="duplicate value id"):
            _pick_split_facet(facets, preferred="country")


class TestFetchJobCount:
    async def test_derives_capped_total_from_nested_facet(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "total": 2000,
                    "jobPostings": [],
                    "facets": [
                        {
                            "facetParameter": "locationMainGroup",
                            "values": [
                                {
                                    "facetParameter": "locations",
                                    "values": [
                                        {"id": "loc1", "count": 1800},
                                        {"id": "loc2", "count": 1200},
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            count = await _fetch_job_count("co", "wd1", "Site", client)

        assert count == 3000


class TestGroupSplitFacetValues:
    def test_groups_nested_values_below_result_cap(self):
        facets = [
            {
                "facetParameter": "locationMainGroup",
                "values": [
                    {
                        "facetParameter": "locations",
                        "values": [
                            {"id": "loc1", "count": 900},
                            {"id": "loc2", "count": 800},
                            {"id": "loc3", "count": 500},
                        ],
                    }
                ],
            }
        ]

        assert _group_split_facet_values(facets, "locations", ["loc1", "loc2", "loc3"]) == [
            ["loc1", "loc2"],
            ["loc3"],
        ]

    def test_unknown_counts_are_queried_separately(self):
        facets = [{"facetParameter": "category", "values": [{"id": "known", "count": 1}]}]

        assert _group_split_facet_values(facets, "category", ["unknown", "known"]) == [
            ["unknown"],
            ["known"],
        ]

    def test_limits_values_per_query(self):
        values = [{"id": f"loc-{i}", "count": 1} for i in range(101)]
        facets = [{"facetParameter": "locations", "values": values}]

        assert _group_split_facet_values(
            facets, "locations", [value["id"] for value in values]
        ) == [
            [f"loc-{i}" for i in range(100)],
            ["loc-100"],
        ]


class TestInventoryCompleteness:
    def test_allows_only_small_live_inventory_drift(self):
        assert not _materially_below_advertised_total(990, 1000)
        assert _materially_below_advertised_total(989, 1000)

    async def test_preferred_facet_rejects_idless_partition_before_false_complete(
        self, monkeypatch
    ):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 3)
        queried_partitions: list[str] = []

        def handler(request):
            payload = json.loads(request.read())
            if "appliedFacets" not in payload:
                return httpx.Response(
                    200,
                    json={
                        "total": 3,
                        "jobPostings": [{"externalPath": "/first"}],
                        "facets": [
                            {
                                "facetParameter": "country",
                                "values": [
                                    {"id": "c1", "count": 2},
                                    {"id": "c2", "count": 2},
                                    {"descriptor": "Unclassified", "count": 1},
                                ],
                            }
                        ],
                    },
                )

            facet_id = payload["appliedFacets"]["country"][0]
            queried_partitions.append(facet_id)
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "jobPostings": [
                        {"externalPath": f"/{facet_id}/1"},
                        {"externalPath": f"/{facet_id}/2"},
                    ],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="invalid value id"):
                _ = [
                    batch
                    async for batch in _api_list_stream(
                        "co",
                        "wd1",
                        "Site",
                        client,
                        split_facet="country",
                    )
                ]

        assert queried_partitions == []

    async def test_direct_pagination_reaches_beyond_tenant_cap(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 40)
        offsets: list[int] = []

        def handler(request):
            payload = json.loads(request.read())
            assert payload["searchText"] == "Dollar Tree"
            offset = payload["offset"]
            offsets.append(offset)
            paths = [f"/job/{i}" for i in range(offset, min(offset + PAGE_SIZE, 45))]
            return httpx.Response(
                200,
                json={"total": 45, "jobPostings": [{"externalPath": p} for p in paths]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [
                batch
                async for batch in _api_list_stream(
                    "co",
                    "wd1",
                    "Site",
                    client,
                    search_text="Dollar Tree",
                )
            ]

        assert [path for batch in batches for path in batch] == [f"/job/{i}" for i in range(45)]
        assert offsets == [0, 0, 20, 40]

    async def test_direct_pagination_fails_when_offsets_remain_capped(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 40)

        def handler(request):
            payload = json.loads(request.read())
            offset = payload["offset"]
            paths = [f"/job/{i}" for i in range(offset, min(offset + PAGE_SIZE, 40))]
            return httpx.Response(
                200,
                json={"total": 45, "jobPostings": [{"externalPath": p} for p in paths]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="40 of 45 advertised unique jobs"):
                _ = [batch async for batch in _api_list_stream("co", "wd1", "Site", client)]

    async def test_under_cap_pagination_recovers_from_a_second_snapshot(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        first_paths = [f"/job/{i}" for i in range(8)]

        async def incomplete_first_pass(list_url, body, client, *, cap_abort=0):
            return first_paths, 12, []

        async def recovering_direct(*args, known_paths=None, **kwargs):
            assert known_paths == set(first_paths)
            yield [f"/job/{i}" for i in range(8, 12)]

        monkeypatch.setattr(wd_module, "_paginate_query", incomplete_first_pass)
        monkeypatch.setattr(wd_module, "_direct_pagination_stream", recovering_direct)

        async with httpx.AsyncClient() as client:
            batches = [batch async for batch in _api_list_stream("co", "wd1", "Site", client)]

        assert batches == [[f"/job/{i}" for i in range(12)]]

    async def test_under_cap_pagination_fails_when_recovery_is_incomplete(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        async def incomplete_first_pass(list_url, body, client, *, cap_abort=0):
            return [f"/job/{i}" for i in range(8)], 12, []

        async def incomplete_direct(*args, **kwargs):
            yield ["/job/8"]
            raise RuntimeError(
                "Workday direct pagination returned 9 of 12 advertised unique jobs for co/Site"
            )

        monkeypatch.setattr(wd_module, "_paginate_query", incomplete_first_pass)
        monkeypatch.setattr(wd_module, "_direct_pagination_stream", incomplete_direct)

        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="direct pagination returned 9 of 12"):
                _ = [batch async for batch in _api_list_stream("co", "wd1", "Site", client)]

    async def test_faceted_pagination_recovers_material_inventory_gap(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 10)

        async def fake_paginate(list_url, body, client, *, cap_abort=0):
            if not body:
                return (
                    ["/first-page"],
                    12,
                    [
                        {
                            "facetParameter": "location",
                            "values": [
                                {"id": "a", "count": 6},
                                {"id": "b", "count": 6},
                            ],
                        }
                    ],
                )
            facet_id = body["appliedFacets"]["location"][0]
            return [f"/{facet_id}/{i}" for i in range(4)], 4, []

        monkeypatch.setattr(wd_module, "_paginate_query", fake_paginate)

        async def fake_direct(*args, **kwargs):
            yield [f"/{i}" for i in range(12)]

        monkeypatch.setattr(wd_module, "_direct_pagination_stream", fake_direct)

        async with httpx.AsyncClient() as client:
            batches = [batch async for batch in _api_list_stream("co", "wd1", "Site", client)]

        paths = [path for batch in batches for path in batch]
        assert set(paths) == {
            *(f"/a/{i}" for i in range(4)),
            *(f"/b/{i}" for i in range(4)),
            *(f"/{i}" for i in range(12)),
        }
        assert len(paths) == 20

    async def test_faceted_pagination_fails_when_direct_recovery_is_incomplete(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 10)

        async def fake_paginate(list_url, body, client, *, cap_abort=0):
            if not body:
                return (
                    ["/first-page"],
                    12,
                    [
                        {
                            "facetParameter": "location",
                            "values": [
                                {"id": "a", "count": 6},
                                {"id": "b", "count": 6},
                            ],
                        }
                    ],
                )
            facet_id = body["appliedFacets"]["location"][0]
            return [f"/{facet_id}/{i}" for i in range(4)], 4, []

        async def incomplete_direct(*args, **kwargs):
            yield [f"/{i}" for i in range(8)]
            raise RuntimeError(
                "Workday direct pagination returned 8 of 12 advertised unique jobs for co/Site"
            )

        monkeypatch.setattr(wd_module, "_paginate_query", fake_paginate)
        monkeypatch.setattr(wd_module, "_direct_pagination_stream", incomplete_direct)

        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="direct pagination returned 8 of 12"):
                _ = [batch async for batch in _api_list_stream("co", "wd1", "Site", client)]

    async def test_query_concurrency_is_shared_across_sites(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 10)
        active = 0
        max_active = 0

        async def fake_paginate(list_url, body, client, *, cap_abort=0):
            nonlocal active, max_active
            site = list_url.rsplit("/", 2)[-2]
            if not body:
                return (
                    [f"/{site}/first"],
                    12,
                    [
                        {
                            "facetParameter": "location",
                            "values": [{"id": f"{site}-{i}", "count": 9} for i in range(12)],
                        }
                    ],
                )

            facet_id = body["appliedFacets"]["location"][0]
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.001)
            active -= 1
            return [f"/job/{facet_id}"], 1, []

        monkeypatch.setattr(wd_module, "_paginate_query", fake_paginate)

        async with httpx.AsyncClient() as client:
            site_paths, truncated = await _list_all_sites("co", "wd1", ["SiteA", "SiteB"], client)

        assert len(site_paths) == 24
        assert truncated is False
        assert max_active == wd_module._QUERY_CONCURRENCY

    async def test_cross_site_mirrors_are_deduplicated_non_streaming(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        async def fake_api_list(company, wd_instance, site, client, *, query_sem=None):
            copy_suffix = "-1" if site == "SiteA" else ""
            paths = [
                f"/job/shared/Engineer_123456{copy_suffix}",
                f"/job/{site}/JR002",
            ]
            return paths, False

        monkeypatch.setattr(wd_module, "_api_list", fake_api_list)

        async with httpx.AsyncClient() as client:
            site_paths, truncated = await _list_all_sites("co", "wd1", ["SiteA", "SiteB"], client)

        assert site_paths == [
            ("SiteA", "/job/shared/Engineer_123456-1"),
            ("SiteA", "/job/SiteA/JR002"),
            ("SiteB", "/job/SiteB/JR002"),
        ]
        assert truncated is False

    async def test_cross_site_mirrors_are_deduplicated_streaming(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        async def fake_api_list_stream(company, wd_instance, site, client, *, query_sem=None):
            copy_suffix = "-1" if site == "SiteA" else ""
            yield [
                f"/job/shared/Engineer_123456{copy_suffix}",
                f"/job/{site}/JR002",
            ]

        monkeypatch.setattr(wd_module, "_api_list_stream", fake_api_list_stream)

        async with httpx.AsyncClient() as client:
            batches = [
                batch
                async for batch in _list_all_sites_stream("co", "wd1", ["SiteA", "SiteB"], client)
            ]

        assert batches == [
            [
                ("SiteA", "/job/shared/Engineer_123456-1"),
                ("SiteA", "/job/SiteA/JR002"),
            ],
            [("SiteB", "/job/SiteB/JR002")],
        ]

    async def test_site_failure_propagates_in_non_streaming_discovery(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        async def fake_api_list(company, wd_instance, site, client, *, query_sem=None):
            if site == "SiteB":
                raise RuntimeError("site unavailable")
            return ["/job/1"], False

        monkeypatch.setattr(wd_module, "_api_list", fake_api_list)

        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="site unavailable"):
                await _list_all_sites("co", "wd1", ["SiteA", "SiteB"], client)

    async def test_site_failure_propagates_in_streaming_discovery(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        async def fake_api_list_stream(company, wd_instance, site, client, *, query_sem=None):
            if site == "SiteB":
                raise RuntimeError("site unavailable")
            yield ["/job/1"]

        monkeypatch.setattr(wd_module, "_api_list_stream", fake_api_list_stream)

        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="site unavailable"):
                _ = [
                    batch
                    async for batch in _list_all_sites_stream(
                        "co", "wd1", ["SiteA", "SiteB"], client
                    )
                ]


class TestDiscover:
    async def test_returns_urls(self):
        def handler(request):
            url = str(request.url)
            if request.method == "POST" and "/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "jobPostings": [
                            {"externalPath": "/Engineer/JR001"},
                            {"externalPath": "/Designer/JR002"},
                        ],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {
                    "company": "nvidia",
                    "wd_instance": "wd5",
                    "site": "ExtSite",
                },
            }
            urls = await discover(board, client)
            assert isinstance(urls, set)
            assert len(urls) == 2
            assert all(isinstance(u, str) for u in urls)
            assert any("JR001" in u for u in urls)
            assert any("JR002" in u for u in urls)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                },
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_no_components_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot parse Workday"):
                await discover(board, client)

    async def test_components_from_url(self):
        def handler(request):
            url = str(request.url)
            assert "nvidia" in url
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {},
            }
            urls = await discover(board, client)
            assert len(urls) == 0

    async def test_search_text_is_preserved_across_facet_queries(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 2)
        request_bodies: list[dict] = []

        def handler(request):
            payload = json.loads(request.read())
            request_bodies.append(payload)
            assert payload["searchText"] == "Dollar Tree"
            if "appliedFacets" not in payload:
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "jobPostings": [{"externalPath": "/first"}],
                        "facets": [
                            {
                                "facetParameter": "category",
                                "values": [
                                    {"id": "stores", "count": 1},
                                    {"id": "corporate", "count": 1},
                                ],
                            }
                        ],
                    },
                )

            category = payload["appliedFacets"]["category"][0]
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "jobPostings": [{"externalPath": f"/{category}"}],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                    "all_sites": False,
                    "search_text": "Dollar Tree",
                },
            }
            urls = await discover(board, client)

        assert len(urls) == 2
        assert len(request_bodies) == 3

    async def test_search_text_requires_single_site(self):
        async with httpx.AsyncClient() as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                    "search_text": "Dollar Tree",
                },
            }
            with pytest.raises(ValueError, match="requires all_sites=false"):
                await discover(board, client)

    @pytest.mark.parametrize("search_text", ["", "   ", 42])
    async def test_search_text_must_be_a_non_empty_string(self, search_text):
        async with httpx.AsyncClient() as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                    "all_sites": False,
                    "search_text": search_text,
                },
            }
            with pytest.raises(ValueError, match="must be a non-empty string"):
                await discover(board, client)

    async def test_split_facet_is_preserved_for_multi_site_discovery(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module, "_API_RESULT_CAP", 3)
        facet_queries: list[dict] = []

        def handler(request):
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text="Sitemap: https://co.wd1.myworkdayjobs.com/Site/siteMap.xml\n",
                )
            payload = json.loads(request.read())
            if "appliedFacets" not in payload:
                return httpx.Response(
                    200,
                    json={
                        "total": 3,
                        "jobPostings": [{"externalPath": "/first"}],
                        "facets": [
                            {
                                "facetParameter": "state",
                                "values": [
                                    {"id": "s1", "count": 1},
                                    {"id": "s2", "count": 1},
                                    {"id": "s3", "count": 1},
                                ],
                            },
                            {
                                "facetParameter": "country",
                                "values": [
                                    {"id": "c1", "count": 1},
                                    {"id": "c2", "count": 2},
                                ],
                            },
                        ],
                    },
                )
            facet_queries.append(payload["appliedFacets"])
            country = payload["appliedFacets"]["country"][0]
            total = 1 if country == "c1" else 2
            return httpx.Response(
                200,
                json={
                    "total": total,
                    "jobPostings": [
                        {"externalPath": f"/{country}/{index}"} for index in range(total)
                    ],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            urls = await discover(
                {
                    "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                    "metadata": {
                        "company": "co",
                        "wd_instance": "wd1",
                        "site": "Site",
                        "split_facet": "country",
                    },
                },
                client,
            )

        assert len(urls) == 3
        assert sorted(query["country"][0] for query in facet_queries) == ["c1", "c2"]

    @pytest.mark.parametrize("split_facet", ["", "bad facet", 42])
    async def test_split_facet_must_be_a_provider_facet_name(self, split_facet):
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="provider facet name"):
                await discover(
                    {
                        "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                        "metadata": {
                            "company": "co",
                            "wd_instance": "wd1",
                            "site": "Site",
                            "split_facet": split_facet,
                        },
                    },
                    client,
                )


class TestCanHandle:
    async def test_workday_url_match(self):
        result = await can_handle("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        assert result is not None
        assert result["company"] == "nvidia"
        assert result["wd_instance"] == "wd5"
        assert result["site"] == "NVIDIAExternalCareerSite"

    async def test_non_matching_url(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 42, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://nvidia.wd5.myworkdayjobs.com/ExtSite", client)
            assert result is not None
            assert result["jobs"] == 42

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "myworkdayjobs.com" in url and "wday/cxs" in url:
                return httpx.Response(
                    200,
                    json={"total": 10, "jobPostings": [], "facets": []},
                )
            # Place the Workday URL at the end of the text so the regex's $ anchor works
            return httpx.Response(
                200,
                text="<html>Apply at https://acme.wd1.myworkdayjobs.com/Careers",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.acme.com/careers", client)
            assert result is not None
            assert result["company"] == "acme"
            assert result["site"] == "Careers"

    async def test_no_match_with_client(self):
        def handler(request):
            return httpx.Response(200, text="<html>no workday</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None


class TestDiscoverSites:
    async def test_parses_robots_txt(self):
        robots = (
            "User-agent: *\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteA/siteMap.xml\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteB/siteMap.xml\n"
        )

        def handler(request):
            if "/robots.txt" in str(request.url):
                return httpx.Response(200, text=robots)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sites = await _discover_sites("co", "wd1", client)
            assert sites == ["SiteA", "SiteB"]

    async def test_robots_not_found_returns_empty(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            sites = await _discover_sites("co", "wd1", client)
            assert sites == []


class TestMultiSiteDiscover:
    async def test_aggregates_urls_from_all_sites(self):
        robots = (
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteA/siteMap.xml\n"
            "Sitemap: https://co.wd1.myworkdayjobs.com/SiteB/siteMap.xml\n"
        )

        def handler(request):
            url = str(request.url)
            if "/robots.txt" in url:
                return httpx.Response(200, text=robots)
            if request.method == "POST" and "SiteA/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Eng/JR001"}],
                        "facets": [],
                    },
                )
            if request.method == "POST" and "SiteB/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Design/JR002"}],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/SiteA",
                "metadata": {"company": "co", "wd_instance": "wd1", "site": "SiteA"},
            }
            urls = await discover(board, client)
            assert len(urls) == 2
            assert any("SiteA" in u for u in urls)
            assert any("SiteB" in u for u in urls)

    async def test_all_sites_false_uses_single_site(self):
        def handler(request):
            url = str(request.url)
            if request.method == "POST" and "SiteA/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 1,
                        "jobPostings": [{"externalPath": "/Eng/JR001"}],
                        "facets": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/SiteA",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "SiteA",
                    "all_sites": False,
                },
            }
            urls = await discover(board, client)
            assert len(urls) == 1
            assert any("JR001" in u for u in urls)


class TestParseJobUrl:
    def test_standard_job_url(self):
        result = _parse_job_url(
            "https://nvidia.wd5.myworkdayjobs.com/ExtSite/job/Senior-Engineer/JR001"
        )
        assert result == ("nvidia", "wd5", "ExtSite", "/job/Senior-Engineer/JR001")

    def test_with_locale_prefix(self):
        result = _parse_job_url("https://nvidia.wd5.myworkdayjobs.com/en-US/ExtSite/job/Eng/JR001")
        assert result == ("nvidia", "wd5", "ExtSite", "/job/Eng/JR001")

    def test_non_matching_url(self):
        assert _parse_job_url("https://example.com/careers/123") is None

    def test_board_url_without_job_path(self):
        assert _parse_job_url("https://nvidia.wd5.myworkdayjobs.com/ExtSite") is None


class TestScrape:
    async def test_passes_configured_tenant_aliases_to_detail_parser(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Assistant Manager",
                        "location": (
                            "Store 1738-Stone Hill Town Ctr-maurice-Pflugerville, TX 78660"
                        ),
                        "country": {"descriptor": "United States of America"},
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://maurices.wd5.myworkdayjobs.com/us_retail_jobs/job/Test/JR001",
                {"facility_tenant_aliases": ["maurice"]},
                client,
            )

        assert result.locations == ["Pflugerville, TX, United States of America"]

    async def test_fetches_detail(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Engineer",
                        "jobDescription": "<p>Build</p>",
                        "location": "NYC",
                        "timeType": "Full-time",
                        "remoteType": "Remote",
                        "startDate": "2024-06-01",
                        "jobReqId": "JR001",
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://nvidia.wd5.myworkdayjobs.com/ExtSite/job/Eng/JR001",
                {},
                client,
            )
            assert result.title == "Engineer"
            assert result.description == "<p>Build</p>"
            assert result.locations == ["NYC"]
            assert result.employment_type == "Full-time"
            assert result.job_location_type == "remote"

    async def test_retries_invalid_success_payload_and_requests_json(self):
        """A transient empty 200 must not fail thousands of detail scrapes at once."""
        requests = []
        responses = iter(
            [
                httpx.Response(200, content=b"", headers={"content-type": "text/html"}),
                httpx.Response(
                    200,
                    json={"jobPostingInfo": {"title": "Recovered engineer"}},
                ),
            ]
        )

        def handler(request):
            requests.append(request)
            return next(responses)

        sleep = AsyncMock()

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                result = await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                    sleep=sleep,
                )

        assert result.title == "Recovered engineer"
        assert len(requests) == 2
        assert all(request.headers["accept"] == "application/json" for request in requests)
        assert all("content-type" not in request.headers for request in requests)
        sleep.assert_awaited_once()
        assert tracker.last_status_code == 200
        assert tracker.last_application_error is None
        assert tracker.transient_failure_host is None

    async def test_invalid_success_payload_exhaustion_is_classified_and_redacted(self):
        """Final errors carry safe diagnostics without logging response content."""
        secret_body = b"<html>upstream challenge with sensitive request echo</html>"
        transport = RequestHostTrackingTransport(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=secret_body,
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
        )
        sleep = AsyncMock()

        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                with pytest.raises(WorkdayDetailPayloadError) as raised:
                    await scrape(
                        "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                        {},
                        client,
                        sleep=sleep,
                    )

        error = raised.value
        assert error.attempts == 3
        assert error.reason == "json_decode"
        assert error.content_type == "text/html; charset=utf-8"
        assert error.body_length == len(secret_body)
        assert "sensitive request echo" not in str(error)
        assert len(error.body_sha256) == 16
        assert sleep.await_count == 2
        assert tracker.last_status_code == 200
        assert tracker.last_application_error == "workday_invalid_detail_payload"
        assert tracker.transient_failure_host == "co.wd1.myworkdayjobs.com"

    async def test_unparseable_url_returns_empty(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape("https://example.com/job/123", {}, client)
            assert result.title is None

    async def test_404_returns_empty(self):
        """Posting removed between list + detail fetches — soft-fail."""
        transport = httpx.MockTransport(lambda r: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await scrape(
                "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                {},
                client,
            )
            assert result.title is None

    async def test_403_raises(self):
        """Bare 403 (real WAF block / auth failure) surfaces as error so it's retried."""
        transport = httpx.MockTransport(lambda r: httpx.Response(403))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_403_s22_returns_empty(self):
        """Workday's 'closed requisition' response: 403 + {errorCode: S22}.

        Verified 2026-04-19 against 15 consecutive 403 URLs from Loki —
        0/15 were in the current LIST output. Treat as soft-fail (same as
        the documented 404) so delisted jobs drain from the scrape queue
        without flooding batch.scrape.error.
        """

        def handler(request):
            return httpx.Response(
                403,
                json={
                    "errorCode": "S22",
                    "errorCaseId": "test-case",
                    "httpStatus": 403,
                    "message": "permission denied",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/x/JR001",
                {},
                client,
            )
            assert result.title is None  # empty JobContent, no exception

    async def test_403_other_code_raises(self):
        """403 with a different errorCode shape is NOT treated as gone —
        it could be a real auth failure or rate limit, so let it surface."""

        def handler(request):
            return httpx.Response(403, json={"errorCode": "OTHER", "message": "rate limited"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_403_non_json_body_raises(self):
        """403 with a non-JSON body (HTML WAF page) still raises."""
        transport = httpx.MockTransport(lambda r: httpx.Response(403, text="<html>blocked</html>"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )

    async def test_500_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape(
                    "https://co.wd1.myworkdayjobs.com/Site/job/X/JR001",
                    {},
                    client,
                )


# ---------------------------------------------------------------------------
# Pagination retry semantics (#2748)
# ---------------------------------------------------------------------------


_LIST_URL = "https://co.wd1.myworkdayjobs.com/wday/cxs/co/Site/jobs"


class TestPostPageWithRetry:
    """``_post_page_with_retry`` mirrors ``fetch_with_retry``'s contract on
    Workday's POST list endpoint: 5xx / 408 / 425 / 429 / network errors
    are retried, non-retryable 4xx fail fast, and persistent failures
    raise :class:`PaginationFetchError` so a single broken pagination
    page doesn't silently truncate the run (#2748).
    """

    async def test_returns_on_success(self):
        def handler(request):
            return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(client, _LIST_URL, {"limit": 20, "offset": 0})
            assert data == {"total": 0, "jobPostings": [], "facets": []}

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(
                200, json={"total": 1, "jobPostings": [{"externalPath": "/x"}], "facets": []}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data["jobPostings"] == [{"externalPath": "/x"}]
            assert calls["n"] == 3

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Issue #2748's load-bearing case: pre-fix, a non-429 retryable
        status (503, etc.) was not retried — ``raise_for_status`` raised
        out and the run was recorded as a scrape-level failure rather
        than retried. Now 503 is retried like every other transient.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503, text="service unavailable")
            return httpx.Response(
                200, json={"total": 1, "jobPostings": [{"externalPath": "/x"}], "facets": []}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data["jobPostings"] == [{"externalPath": "/x"}]
            assert calls["n"] == 3

    async def test_retries_transient_303_without_changing_post_to_get(self, monkeypatch):
        """Reproduce #5715's Workday incident.

        The provider returned 303 without a usable canonical redirect across
        many tenants. Following it would change this list API's POST into GET;
        retry the original request after backoff instead.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        methods: list[str] = []

        def handler(request):
            methods.append(request.method)
            if len(methods) == 1:
                return httpx.Response(303, headers={"Location": ""})
            return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                data = await _post_page_with_retry(
                    client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
                )

        assert data == {"total": 0, "jobPostings": [], "facets": []}
        assert methods == ["POST", "POST"]
        assert tracker.last_provider_incident is None

    async def test_marks_only_an_exhausted_303_provider_incident(self, monkeypatch):
        """Three terminal POST 303s retain distinct-host circuit evidence."""
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        methods: list[str] = []

        def handler(request):
            methods.append(request.method)
            return httpx.Response(303, headers={"Location": ""})

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                with pytest.raises(PaginationFetchError) as exc_info:
                    await _post_page_with_retry(
                        client,
                        _LIST_URL,
                        {"limit": 20, "offset": 0},
                        base_delay=0.001,
                    )

        assert exc_info.value.last_status == 303
        assert methods == ["POST", "POST", "POST"]
        assert tracker.last_provider_incident == WORKDAY_LIST_303_INCIDENT
        assert tracker.last_provider_incident_host == "co.wd1.myworkdayjobs.com"

    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        """Cloudflare origin codes 520-526/530 are retried (parity with
        dom + accenture + PCSX). Pinned for one representative code; the
        full set is exercised by ``test_http_retry``."""
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(520, text="cf origin error")
            return httpx.Response(200, json={"total": 0, "jobPostings": [], "facets": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            data = await _post_page_with_retry(
                client, _LIST_URL, {"limit": 20, "offset": 0}, base_delay=0.001
            )
            assert data == {"total": 0, "jobPostings": [], "facets": []}
            assert calls["n"] == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Issue #2748 acceptance: persistent 5xx exhausts the retry budget
        and raises ``PaginationFetchError`` — no silent truncation.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(500, text="internal")

        transport = RequestHostTrackingTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            with track_request_hosts() as tracker:
                with pytest.raises(PaginationFetchError) as exc_info:
                    await _post_page_with_retry(
                        client,
                        _LIST_URL,
                        {"limit": 20, "offset": 0},
                        retries=3,
                        base_delay=0.001,
                    )
        assert exc_info.value.last_status == 500
        assert exc_info.value.attempts == 3
        assert calls["n"] == 3
        assert tracker.last_provider_incident is None

    async def test_raises_on_non_retryable_4xx_immediately(self, monkeypatch):
        """A 401 / 403 / 400 indicates a hard error — no point retrying.
        Raise ``PaginationFetchError`` on the first attempt."""
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, text="unauthorized")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=3,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status == 401
            # Exactly one attempt — no retry on non-retryable 4xx.
            assert calls["n"] == 1

    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            raise httpx.ConnectError("conn refused")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )
            assert exc_info.value.last_status is None
            assert exc_info.value.last_error == "ConnectError"

    async def test_raises_on_empty_200_body(self, monkeypatch):
        """Per the issue, a 200 with a body that decodes to ``null`` (or
        any non-dict shape) used to leave ``data is None`` and silently
        ``break`` the pagination loop. Now the helper treats it as a
        transient failure (retry, then raise) so the run surfaces.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            # JSON ``null`` decodes to Python ``None``.
            return httpx.Response(
                200, content=b"null", headers={"content-type": "application/json"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _post_page_with_retry(
                    client,
                    _LIST_URL,
                    {"limit": 20, "offset": 0},
                    retries=2,
                    base_delay=0.001,
                )


class TestPaginateQueryRetry:
    """Issue #2748 acceptance: the inner pagination loop propagates the
    new retry-then-raise contract end-to-end. Pre-fix, a 5xx on page N>0
    raised ``HTTPStatusError`` straight out of the page fetch — caller
    treated it as a scrape-level failure but ``data is None`` could also
    silently break the loop on any future change. Now both transients
    are retried and persistent failures raise ``PaginationFetchError``.
    """

    async def test_503_then_200_pagination_continues(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        # Total 30 (PAGE_SIZE=20 → two pages). First page succeeds with
        # 20 postings; second page returns 503 once then 200 with 10.
        page2_calls = {"n": 0}

        def handler(request):
            body = request.read().decode()
            offset = 0
            if '"offset": 20' in body or '"offset":20' in body:
                offset = 20
            if offset == 0:
                return httpx.Response(
                    200,
                    json={
                        "total": 30,
                        "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                        "facets": [],
                    },
                )
            page2_calls["n"] += 1
            if page2_calls["n"] < 2:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{20 + i}"} for i in range(10)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            paths, total, _ = await _paginate_query(_LIST_URL, {}, client)
            assert total == 30
            assert len(paths) == 30
            # Page 2 was retried once before succeeding.
            assert page2_calls["n"] == 2

    async def test_persistent_500_raises_not_silent_break(self, monkeypatch):
        """Pre-fix, ``data is None`` after the retry loop hit a silent
        ``break`` and returned the partial ``paths`` list. Now the helper
        raises ``PaginationFetchError`` instead.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                return httpx.Response(500, text="internal")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _paginate_query(_LIST_URL, {}, client)
            assert exc_info.value.last_status == 500

    async def test_persistent_connection_error_raises(self, monkeypatch):
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                raise httpx.ConnectError("conn reset")
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _paginate_query(_LIST_URL, {}, client)
            assert exc_info.value.last_error == "ConnectError"

    async def test_empty_200_body_raises(self, monkeypatch):
        """Per the issue: ``data is None`` from a 200 with a ``null`` /
        non-dict body must raise rather than silently break the loop.
        """
        from src.core.monitors import workday as wd_module

        monkeypatch.setattr(wd_module.asyncio, "sleep", AsyncMock())

        def handler(request):
            body = request.read().decode()
            if '"offset": 20' in body or '"offset":20' in body:
                return httpx.Response(
                    200, content=b"null", headers={"content-type": "application/json"}
                )
            return httpx.Response(
                200,
                json={
                    "total": 30,
                    "jobPostings": [{"externalPath": f"/job/{i}"} for i in range(PAGE_SIZE)],
                    "facets": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await _paginate_query(_LIST_URL, {}, client)
