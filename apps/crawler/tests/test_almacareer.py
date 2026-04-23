from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.almacareer import (
    GRAPHQL_URL,
    _detail_url,
    _extract_widget_config,
    _flatten_groups,
    _host_from_board,
    _match_country,
    _parse_date,
    _parse_employment_type,
    _parse_job,
    _parse_location,
    _parse_locations,
    _parse_salary,
    can_handle,
    discover,
)


class TestMatchCountry:
    def test_cz(self):
        assert _match_country("mcdonalds.jobs.cz") == ("cz", "mcdonalds")

    def test_sk(self):
        assert _match_country("mcdonalds.topjobs.sk") == ("sk", "mcdonalds")

    def test_www_prefix_stripped(self):
        assert _match_country("www.acme.jobs.cz") == ("cz", "acme")

    def test_uppercase_host(self):
        # Hosts can arrive with mixed-case (e.g. from Redirect headers).
        assert _match_country("ACME.Jobs.CZ") == ("cz", "acme")
        assert _match_country("ACME.TopJobs.SK") == ("sk", "acme")

    def test_multilevel_slug(self):
        # Slug can contain hyphens or numbers.
        assert _match_country("my-company-123.jobs.cz") == ("cz", "my-company-123")

    def test_ignored_slugs(self):
        for host in ("www.jobs.cz", "api.jobs.cz", "cdn.jobs.cz"):
            assert _match_country(host) is None

    def test_non_alma(self):
        assert _match_country("stripe.com") is None
        assert _match_country("example.jobs.de") is None


class TestHostFromBoard:
    def test_from_url(self):
        assert _host_from_board("https://acme.jobs.cz/volna-mista/", {}) == "acme.jobs.cz"

    def test_from_metadata_host(self):
        assert _host_from_board("ignored", {"host": "ACME.jobs.cz"}) == "acme.jobs.cz"

    def test_from_slug_country_cz(self):
        assert _host_from_board("", {"slug": "acme", "country": "cz"}) == "acme.jobs.cz"

    def test_from_slug_country_sk(self):
        assert _host_from_board("", {"slug": "acme", "country": "sk"}) == "acme.topjobs.sk"

    def test_unknown_returns_none(self):
        assert _host_from_board("https://stripe.com", {}) is None


class TestDetailUrl:
    def test_cz(self):
        assert (
            _detail_url("acme.jobs.cz", "detail-pozice", "123")
            == "https://acme.jobs.cz/detail-pozice?r=detail&id=123"
        )

    def test_sk(self):
        assert (
            _detail_url("acme.topjobs.sk", "detail-pozicie", "42")
            == "https://acme.topjobs.sk/detail-pozicie?r=detail&id=42"
        )


class TestExtractWidgetConfig:
    def test_mcdonalds_cz(self):
        script = (
            'var x = { "widgets":{"main":{"id":"dcc74a07-bcb5-444e-a185-2bf060a49aab",'
            '"apiKey":"6b57c70d41a5aff5522c9e4f93a30414ed360b1567930270515de4047bf0b5c3",'
            '"pagePath":"volna-mista","version":3,"themes":[],"detailPath":"detail-pozice"}'
        )
        cfg = _extract_widget_config(script)
        assert cfg is not None
        assert cfg["id"] == "dcc74a07-bcb5-444e-a185-2bf060a49aab"
        assert cfg["apiKey"].startswith("6b57c70d41a5")
        assert cfg["detail_path"] == "detail-pozice"

    def test_mcdonalds_sk(self):
        script = (
            '"widgets":{"main":{"id":"075c7246-bbd1-4670-a7b6-347e02e35dd2",'
            '"apiKey":"a4c5b243a41cfda762c8068c73cdc89bf357f6284fc80f59c7bbbfaddb8cd18e",'
            '"pagePath":"volne-miesta","detailPath":"detail-pozicie"}'
        )
        cfg = _extract_widget_config(script)
        assert cfg is not None
        assert cfg["id"] == "075c7246-bbd1-4670-a7b6-347e02e35dd2"
        assert cfg["detail_path"] == "detail-pozicie"

    def test_missing_returns_none(self):
        assert _extract_widget_config("unrelated content") is None

    def test_missing_api_key(self):
        script = '"widgets":{"main":{"id":"dcc74a07-bcb5-444e-a185-2bf060a49aab"}}'
        assert _extract_widget_config(script) is None

    def test_fallback_detail_path(self):
        # When detailPath missing, fallback to default (detail-pozice).
        script = (
            '"widgets":{"main":{"id":"dcc74a07-bcb5-444e-a185-2bf060a49aab",'
            '"apiKey":"6b57c70d41a5aff5522c9e4f93a30414ed360b1567930270515de4047bf0b5c3"}'
        )
        cfg = _extract_widget_config(script)
        assert cfg is not None
        assert cfg["detail_path"] == "detail-pozice"

    def test_nested_object_before_api_key(self):
        # Real minified bundles frequently nest objects (``themes``, ``filters``)
        # between ``id`` and ``apiKey`` inside the ``main`` body.  A naive
        # balanced-brace match would cut the body short.
        script = (
            '"widgets":{"main":{'
            '"id":"dcc74a07-bcb5-444e-a185-2bf060a49aab",'
            '"themes":[{"name":"default","variables":{"primary":"#ff0000"}}],'
            '"filters":{"employment":{"enabled":true,"default":[]}},'
            '"apiKey":"6b57c70d41a5aff5522c9e4f93a30414ed360b1567930270515de4047bf0b5c3",'
            '"detailPath":"detail-pozice"}}'
        )
        cfg = _extract_widget_config(script)
        assert cfg is not None
        assert cfg["id"] == "dcc74a07-bcb5-444e-a185-2bf060a49aab"
        assert cfg["apiKey"].startswith("6b57c70d41a5")
        assert cfg["detail_path"] == "detail-pozice"


class TestFlattenGroups:
    def test_nested(self):
        group = {
            "jobAds": [{"id": "1"}, {"id": "2"}],
            "groups": [
                {
                    "jobAds": [{"id": "3"}],
                    "groups": [{"jobAds": [{"id": "4"}]}],
                }
            ],
        }
        ads = _flatten_groups(group)
        assert [a["id"] for a in ads] == ["1", "2", "3", "4"]

    def test_empty(self):
        assert _flatten_groups(None) == []
        assert _flatten_groups({}) == []


class TestParseLocation:
    def test_full(self):
        # District duplicates city → dropped; cityPart/region/country kept.
        loc = {
            "country": "Slovensko",
            "region": "Košický",
            "district": "Košice",
            "city": "Košice",
            "cityPart": "Staré Mesto",
        }
        assert _parse_location(loc) == "Staré Mesto, Košice, Košický, Slovensko"

    def test_five_distinct_parts(self):
        # When every level is populated with a distinct value, keep all five.
        loc = {
            "country": "Česká republika",
            "region": "Středočeský kraj",
            "district": "Praha-východ",
            "city": "Říčany",
            "cityPart": "Strašín",
        }
        assert (
            _parse_location(loc)
            == "Strašín, Říčany, Praha-východ, Středočeský kraj, Česká republika"
        )

    def test_partial(self):
        loc = {"country": "Česká republika", "city": "Praha"}
        assert _parse_location(loc) == "Praha, Česká republika"

    def test_duplicate_city_district(self):
        loc = {"city": "Pardubice", "district": "Pardubice", "country": "Česká republika"}
        assert _parse_location(loc) == "Pardubice, Česká republika"

    def test_empty(self):
        assert _parse_location(None) is None
        assert _parse_location({}) is None


class TestParseLocations:
    def test_dedup(self):
        locs = [{"city": "Praha"}, {"city": "Praha"}]
        assert _parse_locations(locs) == ["Praha"]

    def test_multiple(self):
        locs = [{"city": "Praha"}, {"city": "Brno"}]
        assert _parse_locations(locs) == ["Praha", "Brno"]

    def test_empty(self):
        assert _parse_locations([]) is None
        assert _parse_locations(None) is None


class TestParseSalary:
    def test_full_monthly_czk(self):
        salary = {"min": 30000, "max": 40000, "period": "měsíc", "currency": "Kč"}
        assert _parse_salary(salary) == {
            "currency": "Kč",
            "min": 30000.0,
            "max": 40000.0,
            "unit": "month",
        }

    def test_eur_monthly_sk(self):
        salary = {"min": 1000, "max": 1500, "period": "mesiac", "currency": "EUR"}
        assert _parse_salary(salary) == {
            "currency": "EUR",
            "min": 1000.0,
            "max": 1500.0,
            "unit": "month",
        }

    def test_hourly(self):
        salary = {"min": 150, "max": None, "period": "hodina", "currency": "Kč"}
        result = _parse_salary(salary)
        assert result is not None
        assert result["unit"] == "hour"
        assert result["max"] is None

    def test_yearly(self):
        salary = {"min": 600000, "max": 800000, "period": "rok", "currency": "Kč"}
        assert _parse_salary(salary)["unit"] == "year"

    def test_unknown_period_defaults_month(self):
        salary = {"min": 100, "period": "week", "currency": "EUR"}
        assert _parse_salary(salary)["unit"] == "month"

    def test_no_currency(self):
        assert _parse_salary({"min": 100}) is None

    def test_no_min_or_max(self):
        assert _parse_salary({"currency": "EUR"}) is None

    def test_none(self):
        assert _parse_salary(None) is None


class TestParseEmploymentType:
    def test_by_id_full_time(self):
        params = {"employmentTypesObjects": [{"id": "201300001", "label": "ignored"}]}
        assert _parse_employment_type(params) == "full-time"

    def test_by_id_part_time(self):
        params = {"employmentTypesObjects": [{"id": "201300002", "label": "ignored"}]}
        assert _parse_employment_type(params) == "part-time"

    def test_by_id_internship(self):
        params = {"employmentTypesObjects": [{"id": "201300005", "label": "x"}]}
        assert _parse_employment_type(params) == "internship"

    def test_cz_label_fallback(self):
        params = {"employmentTypesObjects": [{"id": None, "label": "Práce na plný úvazek"}]}
        assert _parse_employment_type(params) == "full-time"

    def test_sk_label_fallback(self):
        params = {"employmentTypesObjects": [{"id": None, "label": "Práca na plný úväzok"}]}
        assert _parse_employment_type(params) == "full-time"

    def test_unknown(self):
        params = {"employmentTypesObjects": [{"id": "999", "label": "Mystery"}]}
        assert _parse_employment_type(params) is None

    def test_empty(self):
        assert _parse_employment_type({}) is None
        assert _parse_employment_type(None) is None


class TestParseDate:
    def test_iso_with_tz(self):
        assert _parse_date("2026-04-22T09:10:22+02:00") == "2026-04-22"

    def test_iso_utc(self):
        assert _parse_date("2026-04-22T09:10:22+00:00") == "2026-04-22"

    def test_empty(self):
        assert _parse_date(None) is None
        assert _parse_date("") is None

    def test_non_string(self):
        assert _parse_date(12345) is None  # type: ignore[arg-type]

    def test_too_short(self):
        assert _parse_date("2026") is None


class TestParseJob:
    def _raw(self, **overrides):
        base = {
            "id": "1001",
            "title": "Cook",
            "validFrom": "2026-04-22T09:10:22+02:00",
            "languageIso": "cs",
            "teaser": "<p>Brief</p>",
            "locations": [
                {"city": "Praha", "country": "Česká republika"},
            ],
            "salary": {"min": 30000, "max": 40000, "period": "měsíc", "currency": "Kč"},
            "employer": {"companyName": "Acme"},
            "parameters": {"employmentTypesObjects": [{"id": "201300001"}]},
            "fieldsObjects": [{"id": "f1", "label": "Gastronomy"}],
            "professionsObjects": [{"id": "p1", "label": "Cook"}],
        }
        base.update(overrides)
        return base

    def test_full(self):
        job = _parse_job(
            self._raw(), host="acme.jobs.cz", detail_path="detail-pozice", country="cz"
        )
        assert job is not None
        assert job.url == "https://acme.jobs.cz/detail-pozice?r=detail&id=1001"
        assert job.title == "Cook"
        assert job.locations == ["Praha, Česká republika"]
        assert job.employment_type == "full-time"
        assert job.date_posted == "2026-04-22"
        assert job.base_salary == {
            "currency": "Kč",
            "min": 30000.0,
            "max": 40000.0,
            "unit": "month",
        }
        assert job.language == "cs"
        assert job.description == "<p>Brief</p>"
        assert job.metadata == {
            "id": "1001",
            "country": "cz",
            "company_name": "Acme",
            "fields": ["Gastronomy"],
            "professions": ["Cook"],
        }

    def test_missing_id(self):
        raw = self._raw()
        del raw["id"]
        assert _parse_job(raw, host="acme.jobs.cz", detail_path="x", country="cz") is None

    def test_missing_title(self):
        raw = self._raw()
        del raw["title"]
        assert _parse_job(raw, host="acme.jobs.cz", detail_path="x", country="cz") is None

    def test_minimal(self):
        raw = {"id": "1", "title": "Job"}
        job = _parse_job(raw, host="acme.jobs.cz", detail_path="detail-pozice", country="cz")
        assert job is not None
        assert job.url == "https://acme.jobs.cz/detail-pozice?r=detail&id=1"
        assert job.metadata == {"id": "1", "country": "cz"}
        assert job.description is None


class TestDiscover:
    @staticmethod
    def _build_script(widget_id: str, api_key: str, detail_path: str) -> str:
        return (
            '"widgets":{"main":{"id":"'
            + widget_id
            + '","apiKey":"'
            + api_key
            + '","detailPath":"'
            + detail_path
            + '"}'
        )

    @staticmethod
    def _listing_response(ads: list[dict], *, last_page: int = 1) -> dict:
        return {
            "data": {
                "widget": {
                    "config": {"languageIso": "cs"},
                    "jobAdList": {
                        "paginator": {
                            "currentPage": 1,
                            "lastPage": last_page,
                            "totalNumberOfItems": len(ads),
                            "numberOfItemsPerPage": 10,
                        },
                        "groupedJobAds": {"jobAds": ads, "groups": []},
                    },
                }
            }
        }

    @staticmethod
    def _detail_response(job_id: str, html: str) -> dict:
        return {
            "data": {
                "widget": {
                    "jobAd": {
                        "id": job_id,
                        "languageIso": "cs",
                        "content": {"htmlContent": html},
                    }
                }
            }
        }

    async def test_cz_full_flow(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")

        ad = {
            "id": "42",
            "title": "Barista",
            "validFrom": "2026-04-01T00:00:00+02:00",
            "languageIso": "cs",
            "teaser": "teaser html",
            "locations": [{"city": "Praha", "country": "Česká republika"}],
            "salary": {"min": 28000, "max": None, "period": "měsíc", "currency": "Kč"},
            "employer": {"companyName": "Acme"},
            "parameters": {"employmentTypesObjects": [{"id": "201300001"}]},
            "fieldsObjects": [],
            "professionsObjects": [],
        }

        call_counter = {"list": 0, "detail": 0, "script": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                call_counter["script"] += 1
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                body = json.loads(request.content)
                if "jobAd(id:" in body["query"] or "JOB_DETAIL" in body["query"]:
                    call_counter["detail"] += 1
                    return httpx.Response(200, json=self._detail_response("42", "<p>Full HTML</p>"))
                call_counter["list"] += 1
                return httpx.Response(200, json=self._listing_response([ad]))
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.cz/volna-mista/",
                "metadata": {},
            }
            jobs = await discover(board, client)

        assert len(jobs) == 1
        job = jobs[0]
        assert isinstance(job, DiscoveredJob)
        assert job.url == "https://acme.jobs.cz/detail-pozice?r=detail&id=42"
        assert job.title == "Barista"
        assert job.description == "<p>Full HTML</p>"
        assert job.employment_type == "full-time"
        assert job.metadata["country"] == "cz"
        assert job.metadata["id"] == "42"
        assert call_counter["script"] == 1
        assert call_counter["list"] == 1
        assert call_counter["detail"] == 1

    async def test_pagination(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")
        pages_seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                body = json.loads(request.content)
                query = body["query"]
                if "JOB_DETAIL" in query:
                    jid = body["variables"]["jobId"]
                    return httpx.Response(200, json=self._detail_response(jid, f"<p>{jid}</p>"))
                page = body["variables"]["page"]
                pages_seen.append(page)
                ads = [{"id": f"{page}-{i}", "title": f"Job {page}-{i}"} for i in range(2)]
                return httpx.Response(200, json=self._listing_response(ads, last_page=3))
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            jobs = await discover(board, client)

        assert pages_seen == [1, 2, 3]
        assert len(jobs) == 6

    async def test_dedup_across_groups(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            body = json.loads(request.content)
            if "JOB_DETAIL" in body["query"]:
                return httpx.Response(
                    200, json=self._detail_response(body["variables"]["jobId"], "<p>x</p>")
                )
            duplicate_ad = {"id": "42", "title": "Barista"}
            payload = {
                "data": {
                    "widget": {
                        "config": {"languageIso": "cs"},
                        "jobAdList": {
                            "paginator": {
                                "currentPage": 1,
                                "lastPage": 1,
                                "totalNumberOfItems": 2,
                                "numberOfItemsPerPage": 10,
                            },
                            "groupedJobAds": {
                                "jobAds": [duplicate_ad],
                                "groups": [{"jobAds": [duplicate_ad], "groups": []}],
                            },
                        },
                    }
                }
            }
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            jobs = await discover(board, client)
        assert len(jobs) == 1

    async def test_metadata_override_skips_script_fetch(self):
        widget_id = "dcc74a07-bcb5-444e-a185-2bf060a49aab"
        api_key = "6b57c70d41a5aff5522c9e4f93a30414ed360b1567930270515de4047bf0b5c3"

        call_counter = {"script": 0, "list": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                call_counter["script"] += 1
                return httpx.Response(200, text="")
            if str(request.url) == GRAPHQL_URL:
                body = json.loads(request.content)
                if "JOB_DETAIL" in body["query"]:
                    return httpx.Response(
                        200, json={"data": {"widget": {"jobAd": {"id": "1", "content": None}}}}
                    )
                call_counter["list"] += 1
                assert body["variables"]["widgetId"] == widget_id
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "widget": {
                                "config": {"languageIso": "cs"},
                                "jobAdList": {
                                    "paginator": {
                                        "currentPage": 1,
                                        "lastPage": 1,
                                        "totalNumberOfItems": 1,
                                        "numberOfItemsPerPage": 10,
                                    },
                                    "groupedJobAds": {"jobAds": [{"id": "1", "title": "Job"}]},
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://acme.jobs.cz/x/",
                "metadata": {
                    "widget_id": widget_id,
                    "api_key": api_key,
                    "detail_path": "detail-pozice",
                },
            }
            jobs = await discover(board, client)

        assert len(jobs) == 1
        assert call_counter["script"] == 0
        assert call_counter["list"] == 1

    async def test_missing_host_raises(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(404))
        ) as client:
            board = {"board_url": "https://stripe.com/jobs", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive AlmaCareer host"):
                await discover(board, client)

    async def test_script_404_raises_board_gone(self):
        """A 404 on the tenant's ``script.min.js`` means the tenant no
        longer exists — surface as ``BoardGoneError`` so the board is
        auto-disabled in one cycle rather than looping failures."""
        from src.core.monitors import BoardGoneError

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(404)
            return httpx.Response(200, json={"data": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            with pytest.raises(BoardGoneError):
                await discover(board, client)

    async def test_malformed_script_raises_value_error(self):
        """A 200 with a script that has no widget config block is a
        bug (or AlmaCareer changed their bundle format), not a gone
        board — keep the ``ValueError`` path so the worker retries."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text="// no widgets here")
            return httpx.Response(200, json={"data": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            with pytest.raises(ValueError, match="widget config not found"):
                await discover(board, client)

    async def test_graphql_error_surfaces(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            return httpx.Response(200, json={"errors": [{"message": "Boom"}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            with pytest.raises(RuntimeError, match="AlmaCareer GraphQL error: Boom"):
                await discover(board, client)

    async def test_empty_listing(self):
        # Tenant exists and widget config resolves, but no ads are currently
        # posted — should return an empty list without raising or making
        # any detail fetches.
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")

        call_counter = {"list": 0, "detail": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                body = json.loads(request.content)
                if "JOB_DETAIL" in body["query"]:
                    call_counter["detail"] += 1
                    return httpx.Response(200, json={"data": {"widget": {"jobAd": None}}})
                call_counter["list"] += 1
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "widget": {
                                "config": {"languageIso": "cs"},
                                "jobAdList": {
                                    "paginator": {
                                        "currentPage": 1,
                                        "lastPage": 1,
                                        "totalNumberOfItems": 0,
                                        "numberOfItemsPerPage": 10,
                                    },
                                    "groupedJobAds": {"jobAds": [], "groups": []},
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            jobs = await discover(board, client)

        assert jobs == []
        assert call_counter["list"] == 1
        assert call_counter["detail"] == 0

    async def test_detail_fetch_returns_none_preserves_teaser(self):
        # When the detail GraphQL returns no htmlContent, the teaser that was
        # populated from the listing should remain as the description.
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = self._build_script(widget_id, api_key, "detail-pozice")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                body = json.loads(request.content)
                if "JOB_DETAIL" in body["query"]:
                    # htmlContent missing → _fetch_job_html returns None.
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "widget": {
                                    "jobAd": {
                                        "id": "99",
                                        "languageIso": "cs",
                                        "content": {"htmlContent": ""},
                                    }
                                }
                            }
                        },
                    )
                ad = {
                    "id": "99",
                    "title": "Greeter",
                    "teaser": "teaser-fallback",
                }
                return httpx.Response(200, json=self._listing_response([ad]))
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://acme.jobs.cz/x/", "metadata": {}}
            jobs = await discover(board, client)

        assert len(jobs) == 1
        # description left at the teaser value from the listing payload.
        assert jobs[0].description == "teaser-fallback"


class TestCanHandle:
    async def test_url_match_triggers_probe(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = (
            '"widgets":{"main":{"id":"'
            + widget_id
            + '","apiKey":"'
            + api_key
            + '","detailPath":"detail-pozice"}'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "widget": {
                                "config": {"languageIso": "cs"},
                                "jobAdList": {
                                    "paginator": {
                                        "currentPage": 1,
                                        "lastPage": 1,
                                        "totalNumberOfItems": 42,
                                        "numberOfItemsPerPage": 10,
                                    },
                                    "groupedJobAds": {"jobAds": []},
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.jobs.cz/volna-mista/", client)
        assert result is not None
        assert result["slug"] == "acme"
        assert result["country"] == "cz"
        assert result["host"] == "acme.jobs.cz"
        assert result["widget_id"] == widget_id
        assert result["jobs"] == 42

    async def test_sk_host(self):
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = (
            '"widgets":{"main":{"id":"'
            + widget_id
            + '","apiKey":"'
            + api_key
            + '","detailPath":"detail-pozicie"}'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "widget": {
                            "config": {"languageIso": "sk"},
                            "jobAdList": {
                                "paginator": {
                                    "currentPage": 1,
                                    "lastPage": 1,
                                    "totalNumberOfItems": 7,
                                    "numberOfItemsPerPage": 10,
                                },
                                "groupedJobAds": {"jobAds": []},
                            },
                        }
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.topjobs.sk/volne-miesta/", client)
        assert result is not None
        assert result["country"] == "sk"
        assert result["detail_path"] == "detail-pozicie"

    async def test_non_almacareer(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://stripe.com/careers", client) is None

    async def test_host_matched_but_no_capybara_script(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/assets/js/script.min.js":
                return httpx.Response(404)
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://acme.jobs.cz/careers/", client) is None

    async def test_no_client_returns_minimal_match(self):
        # When no client is provided we can still match the host suffix.
        result = await can_handle("https://acme.jobs.cz/volna-mista/", None)
        assert result is not None
        assert result["slug"] == "acme"
        assert result["country"] == "cz"
        # No client => no widget config probe.
        assert "widget_id" not in result

    async def test_page_html_marker_fallback(self):
        # Custom-domain portal: URL host is ``careers.example.com`` but the
        # embedded iframe / template sets ``data-host="acme.jobs.cz"`` and
        # includes the ``cdn.capybara.lmc.cz`` marker.  can_handle should
        # follow those markers to identify the tenant.
        widget_id = "11111111-2222-3333-4444-555555555555"
        api_key = "a" * 64
        script = (
            '"widgets":{"main":{"id":"'
            + widget_id
            + '","apiKey":"'
            + api_key
            + '","detailPath":"detail-pozice"}'
        )
        page_html = (
            '<html data-host="acme.jobs.cz">'
            '<script src="https://cdn.capybara.lmc.cz/bundle.js"></script>'
            "</html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            if host == "careers.example.com":
                return httpx.Response(200, text=page_html)
            if host == "acme.jobs.cz" and path == "/assets/js/script.min.js":
                return httpx.Response(200, text=script)
            if str(request.url) == GRAPHQL_URL:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "widget": {
                                "config": {"languageIso": "cs"},
                                "jobAdList": {
                                    "paginator": {
                                        "currentPage": 1,
                                        "lastPage": 1,
                                        "totalNumberOfItems": 5,
                                        "numberOfItemsPerPage": 10,
                                    },
                                    "groupedJobAds": {"jobAds": []},
                                },
                            }
                        }
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.example.com/jobs", client)

        assert result is not None
        assert result["host"] == "acme.jobs.cz"
        assert result["slug"] == "acme"
        assert result["country"] == "cz"
        assert result["widget_id"] == widget_id
        assert result["jobs"] == 5


class TestRegistry:
    def test_registered_in_main_registry(self):
        from src.core.monitors import _REGISTRY

        names = [m.name for m in _REGISTRY]
        assert "almacareer" in names

    def test_get_discoverer(self):
        from src.core.monitors import get_discoverer

        fn = get_discoverer("almacareer")
        assert callable(fn)

    def test_probe_detects_cz_url_without_client(self):
        import asyncio

        from src.core.monitors import detect_monitor_type

        # With no client, can_handle returns a minimal dict so detection fires.
        result = asyncio.run(detect_monitor_type("https://acme.jobs.cz/volna-mista/", None))
        assert result is not None
        name, meta = result
        assert name == "almacareer"
        assert meta["slug"] == "acme"
