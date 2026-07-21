from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.jarvi import (
    API_URL,
    _embed_metadata,
    _parse_job,
    can_handle,
    discover,
)

PUBLIC_KEY = "public-key"
BOARD_URL = "https://example.com/careers"


def _field(
    purpose: str,
    *,
    value: str | None = None,
    choice: str | None = None,
    location: dict | None = None,
) -> dict:
    return {
        "field": {"purpose": purpose},
        "value": value,
        "fieldValue": {"technicalValue": choice} if choice else None,
        "location": location,
    }


def _offer() -> dict:
    return {
        "id": "offer-uuid",
        "shortId": "Ab123",
        "name": "<p>Fallback title</p>",
        "publishedAt": "2026-07-20T14:49:48.108Z",
        "updatedAt": "2026-07-21T10:00:00Z",
        "fieldsValues": [
            _field("joboffer_title", value="<p>Ingénieur(e) Logiciel</p>"),
            _field(
                "joboffer_company_description",
                value="<p>Build low-carbon industrial heat.</p>",
            ),
            _field(
                "joboffer_description",
                value="<p>Design and ship reliable systems.</p>",
            ),
            _field(
                "joboffer_profile_description",
                value="<p>Python and distributed systems experience.</p>",
            ),
            _field(
                "joboffer_location",
                location={"formattedAddress": "Paris, France"},
            ),
            _field("joboffer_contract_type", choice="permanent"),
            _field("joboffer_remote_days_per_week", value="1"),
            _field("joboffer_min_years_of_experience", value="5"),
            _field("joboffer_salary_is_public", value="true"),
            _field("joboffer_salary_per_year_min", value="60"),
            _field("joboffer_salary_per_year_max", value="80"),
        ],
    }


def _page() -> str:
    return f'''<script defer src="https://app.jarvi.tech/sdk/jarvi-sdk.umd.js"
      data-public-api-key="{PUBLIC_KEY}"
      data-currency="eur"
      data-sdk="jarvi"></script>'''


class TestEmbedMetadata:
    def test_extracts_public_key_and_currency(self):
        assert _embed_metadata(_page()) == {
            "public_api_key": PUBLIC_KEY,
            "currency": "EUR",
        }

    def test_requires_jarvi_sdk_and_public_key(self):
        assert _embed_metadata('<script data-public-api-key="key"></script>') is None
        assert _embed_metadata('<script data-sdk="jarvi"></script>') is None


class TestParseJob:
    def test_maps_nested_public_fields(self):
        job = _parse_job(_offer(), BOARD_URL, "EUR")

        assert isinstance(job, DiscoveredJob)
        assert job.url == ("https://example.com/careers?q=Ab123%2Fingenieure-logiciel")
        assert job.title == "Ingénieur(e) Logiciel"
        assert job.locations == ["Paris, France"]
        assert job.employment_type == "permanent"
        assert job.job_location_type == "hybrid"
        assert job.date_posted == "2026-07-20T14:49:48.108Z"
        assert job.base_salary == {
            "currency": "EUR",
            "min": 60,
            "max": 80,
            "unit": "year",
        }
        assert "Build low-carbon" in job.description
        assert "Design and ship" in job.description
        assert "Python and distributed" in job.description
        assert job.extras == {
            "responsibilities": "<p>Design and ship reliable systems.</p>",
            "qualifications": "<p>Python and distributed systems experience.</p>",
        }
        assert job.metadata["minimum_years_experience"] == 5

    def test_skips_offer_without_title_or_id(self):
        no_title = {**_offer(), "name": None, "fieldsValues": []}
        assert _parse_job(no_title, BOARD_URL, "EUR") is None
        no_id = {**_offer(), "id": None, "shortId": None}
        assert _parse_job(no_id, BOARD_URL, "EUR") is None

    def test_omits_private_salary_and_remote_type(self):
        raw = _offer()
        for field in raw["fieldsValues"]:
            if field["field"]["purpose"] == "joboffer_salary_is_public":
                field["value"] = "false"
            if field["field"]["purpose"] == "joboffer_remote_days_per_week":
                field["value"] = None
        job = _parse_job(raw, BOARD_URL, "EUR")
        assert job.base_salary is None
        assert job.job_location_type is None

    def test_location_falls_back_to_profile_text(self):
        raw = _offer()
        raw["fieldsValues"] = [
            field
            for field in raw["fieldsValues"]
            if field["field"]["purpose"] != "joboffer_location"
        ]
        for field in raw["fieldsValues"]:
            if field["field"]["purpose"] == "joboffer_profile_description":
                field["value"] += "<p>Localisation : Paris 15</p><p>Contrat : CDI</p>"

        assert _parse_job(raw, BOARD_URL, "EUR").locations == ["Paris 15"]


class TestDiscover:
    async def test_uses_configured_public_key_and_returns_rich_jobs(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"data": [_offer()], "total": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": BOARD_URL,
                    "metadata": {"public_api_key": PUBLIC_KEY, "currency": "EUR"},
                },
                client,
            )

        assert len(jobs) == 1
        assert jobs[0].description
        assert str(requests[0].url).startswith(f"{API_URL}?limit=")
        assert requests[0].headers["x-api-key"] == PUBLIC_KEY

    async def test_fetches_embed_when_config_is_missing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == BOARD_URL:
                return httpx.Response(200, text=_page())
            return httpx.Response(200, json={"data": [_offer()], "total": 1})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL}, client)
        assert len(jobs) == 1

    async def test_rejects_malformed_payload(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"offers": []}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="data array"):
                await discover(
                    {"board_url": BOARD_URL, "metadata": {"public_api_key": PUBLIC_KEY}},
                    client,
                )


class TestCanHandle:
    async def test_detects_embed_and_reports_total(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == BOARD_URL:
                return httpx.Response(200, text=_page())
            assert request.url.params["limit"] == "1"
            return httpx.Response(200, json={"data": [_offer()], "total": 22})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "public_api_key": PUBLIC_KEY,
                "currency": "EUR",
                "jobs": 22,
            }

    async def test_rejects_non_jarvi_page(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) is None
