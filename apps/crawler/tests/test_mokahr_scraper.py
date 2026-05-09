"""Tests for the Mokahr monitor + scraper structured-field extraction.

The scraper covers Rule 16 (extract every structured field the upstream
exposes). These tests pin every field individually against synthetic
detail / listing payloads modelled on real ZTE responses, plus a
roundtrip through ``scrape`` with stubbed transport.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from src.core.monitors.mokahr import (
    _COMMITMENT_MAP,
    _SALARY_UNIT,
    _build_city_name_map,
    _decrypt,
    _lookup_city_name,
    _parse_experience,
    _parse_job,
    _parse_locations,
    _parse_metadata,
    _parse_salary,
)
from src.core.scrapers import JobContent
from src.core.scrapers.mokahr import _parse_detail, _parse_url, scrape

# ── _COMMITMENT_MAP ──────────────────────────────────────────────────


class TestCommitmentMap:
    def test_chinese_full_time(self):
        # The actual Mokahr detail API returns Chinese labels.
        assert _COMMITMENT_MAP["全职"] == "Full-time"

    def test_chinese_part_time(self):
        assert _COMMITMENT_MAP["兼职"] == "Part-time"

    def test_chinese_intern(self):
        assert _COMMITMENT_MAP["实习"] == "Intern"

    def test_chinese_other_unmapped(self):
        # ``其它`` (other) is intentionally absent so the field stays None.
        assert "其它" not in _COMMITMENT_MAP

    def test_legacy_english_keys_preserved(self):
        # Forward-compat in case Mokahr ever switches its locale.
        assert _COMMITMENT_MAP["fullTime"] == "Full-time"
        assert _COMMITMENT_MAP["partTime"] == "Part-time"
        assert _COMMITMENT_MAP["intern"] == "Intern"
        assert _COMMITMENT_MAP["contract"] == "Contract"


# ── _parse_salary ────────────────────────────────────────────────────


class TestParseSalary:
    def test_k_month_unit_multiplies_by_1000(self):
        # salaryUnit=0 (K_MONTH) is the most common ZTE shape.
        result = _parse_salary({"minSalary": 40, "maxSalary": 80, "salaryUnit": 0})
        assert result == {
            "currency": "CNY",
            "min": 40000,
            "max": 80000,
            "unit": "monthly",
        }

    def test_yuan_month_unit_no_multiplier(self):
        result = _parse_salary({"minSalary": 8000, "maxSalary": 12000, "salaryUnit": 1})
        assert result == {
            "currency": "CNY",
            "min": 8000,
            "max": 12000,
            "unit": "monthly",
        }

    def test_yearly_unit(self):
        result = _parse_salary({"minSalary": 200000, "maxSalary": 500000, "salaryUnit": 11})
        assert result == {
            "currency": "CNY",
            "min": 200000,
            "max": 500000,
            "unit": "yearly",
        }

    def test_hourly_unit(self):
        result = _parse_salary({"minSalary": 50, "maxSalary": 80, "salaryUnit": 4})
        assert result == {
            "currency": "CNY",
            "min": 50,
            "max": 80,
            "unit": "hourly",
        }

    def test_min_only_max_open_ended(self):
        result = _parse_salary({"minSalary": 30, "maxSalary": 0, "salaryUnit": 0})
        assert result == {
            "currency": "CNY",
            "min": 30000,
            "max": None,
            "unit": "monthly",
        }

    def test_max_only_min_zero(self):
        result = _parse_salary({"minSalary": 0, "maxSalary": 60, "salaryUnit": 0})
        assert result == {
            "currency": "CNY",
            "min": None,
            "max": 60000,
            "unit": "monthly",
        }

    def test_returns_none_when_both_zero(self):
        # ZTE campus jobs default to 0/0 — treat as "salary not disclosed".
        assert _parse_salary({"minSalary": 0, "maxSalary": 0, "salaryUnit": 0}) is None

    def test_returns_none_when_keys_missing(self):
        assert _parse_salary({}) is None

    def test_unknown_unit_falls_back_to_no_period(self):
        # An out-of-band unit code shouldn't kill the extraction —
        # propagate the raw values with unit=None so a label step
        # downstream can still inspect them.
        result = _parse_salary({"minSalary": 40, "maxSalary": 80, "salaryUnit": 999})
        assert result == {
            "currency": "CNY",
            "min": 40,
            "max": 80,
            "unit": None,
        }

    def test_salary_unit_enum_complete(self):
        # Pin the full enum so an upstream change is visible in tests.
        assert _SALARY_UNIT[0] == ("monthly", 1000)
        assert _SALARY_UNIT[1] == ("monthly", 1)
        assert _SALARY_UNIT[11] == ("yearly", 1)
        assert _SALARY_UNIT[4] == ("hourly", 1)


# ── _parse_experience ────────────────────────────────────────────────


class TestParseExperience:
    def test_min_and_max(self):
        assert _parse_experience({"minExperience": 5, "maxExperience": 10}) == {
            "min_years": 5,
            "max_years": 10,
        }

    def test_min_only_open_ended(self):
        assert _parse_experience({"minExperience": 10, "maxExperience": None}) == {
            "min_years": 10,
        }

    def test_max_only(self):
        assert _parse_experience({"maxExperience": 3}) == {"max_years": 3}

    def test_returns_none_when_absent(self):
        assert _parse_experience({}) is None

    def test_returns_none_when_both_null(self):
        assert _parse_experience({"minExperience": None, "maxExperience": None}) is None

    def test_coerces_floats_to_ints(self):
        # Mokahr always returns ints in practice but be defensive.
        assert _parse_experience({"minExperience": 5.0, "maxExperience": 10.0}) == {
            "min_years": 5,
            "max_years": 10,
        }


# ── _parse_metadata ──────────────────────────────────────────────────


class TestParseMetadata:
    def test_full_metadata(self):
        d = {
            "department": {"id": 430278, "name": "中兴通讯股份有限公司"},
            "education": "硕士",
            "zhineng": {"id": 72363, "name": "研发类"},
        }
        assert _parse_metadata(d) == {
            "department": "中兴通讯股份有限公司",
            "education": "硕士",
            "job_function": "研发类",
        }

    def test_string_department(self):
        assert _parse_metadata({"department": "engineering"}) == {"department": "engineering"}

    def test_string_zhineng(self):
        assert _parse_metadata({"zhineng": "研发类"}) == {"job_function": "研发类"}

    def test_skips_empty_dept(self):
        # Empty dept shouldn't pollute metadata.
        assert _parse_metadata({"department": {"id": 0}}) == {}

    def test_returns_empty_dict_when_no_fields(self):
        assert _parse_metadata({}) == {}


# ── _build_city_name_map / _lookup_city_name ────────────────────────


class TestCityNameMap:
    def _init(self) -> dict:
        # Modelled on real ZTE init-data.
        return {
            "aesIv": "de7c21ed8d6f50fe",
            "jobsGroupedByLocation": [
                {"id": "深圳市", "label": "深圳市", "cityId": 440300},
                {"id": "南京市", "label": "南京市", "cityId": 320114},
                {"id": "北京市", "label": "北京市", "cityId": 110000},
                {"id": "西安市", "label": "西安市", "cityId": 610100},
            ],
        }

    def test_builds_map_from_init_data(self):
        m = _build_city_name_map(self._init())
        assert m[440300] == "深圳市"
        assert m[110000] == "北京市"
        assert m[610100] == "西安市"

    def test_seeds_parent_city_from_district(self):
        # 320114 (Nanjing Jiangning) -> seeds 320100 (Nanjing city).
        m = _build_city_name_map(self._init())
        assert m[320114] == "南京市"
        assert m[320100] == "南京市"

    def test_does_not_overwrite_explicit_parent(self):
        # If both 110000 and 110100 are present, the SPA's own labels win.
        init = {
            "jobsGroupedByLocation": [
                {"id": "北京市", "label": "北京市", "cityId": 110000},
                {"id": "Nope", "label": "Nope", "cityId": 110105},
            ]
        }
        m = _build_city_name_map(init)
        # 110105's parent is 110100 — seeded — but does not clobber 110000.
        assert m[110000] == "北京市"
        assert m[110100] == "Nope"

    def test_returns_empty_for_missing_section(self):
        assert _build_city_name_map({}) == {}
        assert _build_city_name_map(None) == {}

    def test_ignores_non_dict_groups(self):
        assert _build_city_name_map({"jobsGroupedByLocation": "garbage"}) == {}

    def test_lookup_city_district_to_city(self):
        # 110105 (Beijing Chaoyang) -> 110100 not present -> 110000 hit.
        m = {110000: "北京市"}
        assert _lookup_city_name(110105, m) == "北京市"

    def test_lookup_city_direct(self):
        m = {440300: "深圳市"}
        assert _lookup_city_name(440300, m) == "深圳市"

    def test_lookup_city_returns_empty_on_miss(self):
        assert _lookup_city_name(999999, {}) == ""

    def test_lookup_city_handles_none(self):
        assert _lookup_city_name(None, {1: "x"}) == ""


# ── _parse_locations ─────────────────────────────────────────────────


class TestParseLocations:
    def test_listing_path_uses_cityName_directly(self):
        # The listing API returns cityName directly.
        loc = [{"cityName": "深圳市", "country": "中国", "cityId": 440300}]
        assert _parse_locations({"locations": loc}) == ["深圳市, 中国"]

    def test_detail_path_resolves_via_city_map(self):
        # The detail API only carries cityId — must resolve via map.
        loc = [{"cityId": 440300, "country": "中国", "address": ""}]
        cmap = {440300: "深圳市"}
        assert _parse_locations({"locations": loc}, cmap) == ["深圳市, 中国"]

    def test_detail_path_collapses_duplicates(self):
        loc = [
            {"cityId": 440300, "country": "中国"},
            {"cityId": 440300, "country": "中国"},
        ]
        cmap = {440300: "深圳市"}
        assert _parse_locations({"locations": loc}, cmap) == ["深圳市, 中国"]

    def test_falls_back_to_provinceName(self):
        # Listing carries provinceName too — use as last-ditch.
        loc = [{"cityId": 999999, "provinceName": "广东", "country": "中国"}]
        assert _parse_locations({"locations": loc}) == ["广东, 中国"]

    def test_falls_back_to_country_only(self):
        # Truly-degenerate: only country is usable.
        loc = [{"cityId": 999999, "country": "中国"}]
        assert _parse_locations({"locations": loc}) == ["中国"]

    def test_returns_none_when_empty(self):
        assert _parse_locations({"locations": []}) is None
        assert _parse_locations({}) is None

    def test_handles_string_locations(self):
        # Defensive: some upstream variants flatten to strings.
        assert _parse_locations({"locations": ["Shanghai", "Beijing"]}) == [
            "Shanghai",
            "Beijing",
        ]


# ── monitor _parse_job ───────────────────────────────────────────────


class TestParseJobListing:
    """Listing-API ``_parse_job`` should populate every available field."""

    def test_full_listing_payload(self):
        # Listing-API job from a hypothetical mokahr tenant exposing all fields.
        raw = {
            "id": "abc-123",
            "title": "卫星激光通信系统工程师",
            "commitment": "全职",
            "publishedAt": "2026-04-22T08:41:14",
            "locations": [
                {"cityId": 440300, "cityName": "深圳市", "country": "中国"},
            ],
            "minSalary": 40,
            "maxSalary": 80,
            "salaryUnit": 0,
            "minExperience": 5,
            "maxExperience": 10,
            "education": "硕士",
            "department": {"id": 430278, "name": "中兴通讯股份有限公司"},
            "zhineng": {"id": 72363, "name": "研发类"},
            "jobDescription": "<p>工作职责</p>",
        }
        job = _parse_job(raw, "zte", 47588)
        assert job is not None
        assert job.title == "卫星激光通信系统工程师"
        assert job.url.endswith("/job/abc-123")
        assert job.employment_type == "Full-time"
        assert job.locations == ["深圳市, 中国"]
        assert job.base_salary == {
            "currency": "CNY",
            "min": 40000,
            "max": 80000,
            "unit": "monthly",
        }
        assert job.extras == {"experience": {"min_years": 5, "max_years": 10}}
        assert job.metadata == {
            "department": "中兴通讯股份有限公司",
            "education": "硕士",
            "job_function": "研发类",
        }

    def test_minimal_payload_no_extras(self):
        raw = {"id": "x", "title": "T", "commitment": "其它"}
        job = _parse_job(raw, "zte", 1)
        assert job is not None
        # 其它 (other) is intentionally unmapped — preserve the unspecified state.
        assert job.employment_type is None
        assert job.base_salary is None
        assert job.extras is None
        # Empty metadata collapses to None to keep the `metadata or None`
        # contract used by the rest of the pipeline.
        assert job.metadata is None

    def test_skips_jobs_missing_id_or_title(self):
        assert _parse_job({"title": "x"}, "zte", 1) is None
        assert _parse_job({"id": "x"}, "zte", 1) is None


# ── scraper _parse_url + _parse_detail ──────────────────────────────


class TestParseUrl:
    def test_parses_social_recruitment_url(self):
        url = "https://app.mokahr.com/social-recruitment/zte/47588#/job/abc-123"
        assert _parse_url(url) == ("social-recruitment", "zte", 47588, "abc-123")

    def test_parses_campus_recruitment_url(self):
        url = "https://app.mokahr.com/campus-recruitment/zte/46903#/job/cc8e0f6d"
        assert _parse_url(url) == ("campus-recruitment", "zte", 46903, "cc8e0f6d")

    def test_parses_legacy_underscore_path(self):
        url = "https://app.mokahr.com/campus_apply/high-flyer/4605#/job/abc"
        assert _parse_url(url) == ("campus_apply", "high-flyer", 4605, "abc")

    def test_returns_none_without_job_id(self):
        url = "https://app.mokahr.com/social-recruitment/zte/47588"
        assert _parse_url(url) is None

    def test_returns_none_for_unrelated_url(self):
        assert _parse_url("https://example.com/foo") is None


class TestParseDetail:
    def _detail(self, **overrides) -> dict:
        # Modelled on the real ZTE social-board detail payload.
        base = {
            "id": "0c44abe6",
            "title": "通信设备电源专家",
            "jobDescription": "<p>工作职责：</p><p>1、从事...</p>",
            "commitment": "全职",
            "publishedAt": "2026-04-28T08:41:14",
            "openedAt": "2025-09-01T00:00",
            "locations": [
                {"cityId": 420100, "country": "中国", "address": "湖北省武汉市"},
                {"cityId": 320100, "country": "中国", "address": "南京"},
            ],
            "minSalary": 40,
            "maxSalary": 80,
            "salaryUnit": 0,
            "minExperience": 5,
            "maxExperience": 10,
            "education": "硕士",
            "department": {"id": 430278, "name": "中兴通讯股份有限公司"},
            "zhineng": {"id": 72363, "name": "研发类"},
        }
        base.update(overrides)
        return base

    def _city_map(self) -> dict[int, str]:
        return {420100: "武汉市", 320100: "南京市"}

    def test_full_extraction(self):
        c = _parse_detail(self._detail(), self._city_map())
        assert isinstance(c, JobContent)
        assert c.title == "通信设备电源专家"
        assert c.description == "<p>工作职责：</p><p>1、从事...</p>"
        assert c.locations == ["武汉市, 中国", "南京市, 中国"]
        assert c.employment_type == "Full-time"
        assert c.date_posted == "2026-04-28"  # Date-only, time stripped.
        assert c.base_salary == {
            "currency": "CNY",
            "min": 40000,
            "max": 80000,
            "unit": "monthly",
        }
        assert c.extras == {"experience": {"min_years": 5, "max_years": 10}}
        assert c.metadata == {
            "department": "中兴通讯股份有限公司",
            "education": "硕士",
            "job_function": "研发类",
        }

    def test_chinese_intern_commitment(self):
        c = _parse_detail(self._detail(commitment="实习"), self._city_map())
        assert c.employment_type == "Intern"

    def test_chinese_part_time_commitment(self):
        c = _parse_detail(self._detail(commitment="兼职"), self._city_map())
        assert c.employment_type == "Part-time"

    def test_other_commitment_stays_none(self):
        c = _parse_detail(self._detail(commitment="其它"), self._city_map())
        assert c.employment_type is None

    def test_no_salary_when_both_zero(self):
        c = _parse_detail(self._detail(minSalary=0, maxSalary=0, salaryUnit=0), self._city_map())
        assert c.base_salary is None

    def test_no_experience_when_absent(self):
        c = _parse_detail(self._detail(minExperience=None, maxExperience=None), self._city_map())
        assert c.extras is None

    def test_falls_back_to_openedAt(self):
        c = _parse_detail(self._detail(publishedAt=None), self._city_map())
        assert c.date_posted == "2025-09-01"

    def test_locations_resolve_via_city_map(self):
        # Without the map, the parser would only emit ``["中国"]``.
        c = _parse_detail(self._detail(), {})
        # No map, no provinceName -> only country is usable -> deduped to one.
        assert c.locations == ["中国"]

    def test_locations_stay_unique_when_country_only(self):
        # Multiple locations with no city mapping must collapse to one,
        # not return ``["中国", "中国"]``.
        c = _parse_detail(
            self._detail(
                locations=[
                    {"cityId": 999998, "country": "中国"},
                    {"cityId": 999999, "country": "中国"},
                ]
            ),
            {},
        )
        assert c.locations == ["中国"]


# ── scrape() roundtrip via stubbed transport ────────────────────────


def _aes_encrypt(plain: bytes, key: str, iv: str) -> str:
    """Symmetric encrypt for tests — mirror the production decrypt path."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()

    cipher = Cipher(algorithms.AES(key.encode("ascii")), modes.CBC(iv.encode("ascii")))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode("ascii")


@pytest.mark.asyncio
async def test_scrape_decrypts_full_detail():
    """End-to-end: fetch IV from SPA, decrypt detail, return populated JobContent."""
    iv = "de7c21ed8d6f50fe"
    key = "1234567890abcdef"

    init_data = {
        "aesIv": iv,
        "jobsGroupedByLocation": [
            {"id": "深圳市", "label": "深圳市", "cityId": 440300},
        ],
    }
    init_value = json.dumps(init_data, ensure_ascii=False).replace('"', "&quot;")
    spa_html = f'<input id="init-data" type="hidden" value="{init_value}">'

    detail = {
        "title": "Senior Test Engineer",
        "jobDescription": "<p>JD body</p>",
        "commitment": "全职",
        "publishedAt": "2026-04-28T08:41:14",
        "locations": [{"cityId": 440300, "country": "中国"}],
        "minSalary": 25,
        "maxSalary": 50,
        "salaryUnit": 0,
        "minExperience": 3,
        "maxExperience": 7,
        "education": "本科",
        "department": {"id": 100, "name": "工程部"},
        "zhineng": {"id": 200, "name": "研发类"},
    }
    encrypted_data = _aes_encrypt(
        json.dumps({"data": detail}, ensure_ascii=False).encode("utf-8"), key, iv
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "social-recruitment" in str(request.url):
            return httpx.Response(200, text=spa_html)
        if request.method == "POST" and str(request.url).endswith("/website/job"):
            return httpx.Response(
                200,
                json={"data": encrypted_data, "necromancer": key},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        url = "https://app.mokahr.com/social-recruitment/zte/47588#/job/test-job"
        content = await scrape(url, {}, client)

    assert content.title == "Senior Test Engineer"
    assert content.description == "<p>JD body</p>"
    assert content.locations == ["深圳市, 中国"]
    assert content.employment_type == "Full-time"
    assert content.date_posted == "2026-04-28"
    assert content.base_salary == {
        "currency": "CNY",
        "min": 25000,
        "max": 50000,
        "unit": "monthly",
    }
    assert content.extras == {"experience": {"min_years": 3, "max_years": 7}}
    assert content.metadata == {
        "department": "工程部",
        "education": "本科",
        "job_function": "研发类",
    }


@pytest.mark.asyncio
async def test_scrape_returns_empty_on_unparseable_url():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        result = await scrape("https://example.com/foo", {}, c)
    assert result == JobContent()


@pytest.mark.asyncio
async def test_scrape_returns_empty_when_iv_missing():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>no init-data</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        result = await scrape("https://app.mokahr.com/social-recruitment/zte/1#/job/abc", {}, c)
    assert result == JobContent()


# ── _decrypt sanity (regression guard against accidental breakage) ──


def test_decrypt_roundtrip():
    iv = "abcdefghijklmnop"
    key = "1234567890ABCDEF"
    plain = b'{"data":{"title":"x"}}'
    enc = _aes_encrypt(plain, key, iv)
    out = _decrypt(enc, key, iv)
    assert out == {"data": {"title": "x"}}
