"""Stable SmartRecruiters identity for localized publications."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.smartrecruiters import (
    CANONICAL_IDENTITY_JOB_LOCATION_V1,
    _canonical_identity_components,
    _canonical_source_url,
    _canonicalize_details,
    _normalize_coordinate,
    discover,
)
from src.core.scrapers import JobContent

_TOKEN = "SwissMedicalNetwork1"
_BILINGUAL_JOB_ID = "095623dd-81c4-41fc-8c8c-e2612fa22ca0"
_REUSED_JOB_ID = "819700b3-a847-46f1-9b08-cf23a9591f68"


def _detail(
    publication_id: str,
    *,
    job_id: str = _BILINGUAL_JOB_ID,
    language: str = "de",
    latitude: str | None = "47.1391567",
    longitude: str | None = "7.2443098",
    default: bool = False,
) -> dict:
    location = {
        "country": "ch",
        "postalCode": "2502",
        "remote": False,
        "hybrid": False,
    }
    if latitude is not None:
        location.update({"latitude": latitude, "longitude": longitude})
    return {
        "id": publication_id,
        "jobId": job_id,
        "active": True,
        "visibility": "PUBLIC",
        "defaultJobAd": default,
        "language": {"code": language},
        "company": {"identifier": _TOKEN},
        "location": location,
        "department": {"id": 10114871},
        "customField": [
            {
                "fieldId": "642d47ae571c9c5746eeeec4",
                "valueId": "RDA",
            },
            {
                "fieldId": "642d47ae571c9c5746eeeec5",
                "valueId": "10114871",
            },
        ],
    }


@pytest.fixture(autouse=True)
def _stub_detail_parser(monkeypatch):
    def parse(detail: dict) -> JobContent:
        language = detail["language"]["code"]
        return JobContent(
            title=f"{language.upper()} title",
            description=f"<p>{language.upper()} description</p>",
            locations=["Biel"],
            language=language,
        )

    monkeypatch.setattr("src.core.scrapers.smartrecruiters._parse_detail", parse)


def test_de_only_and_fr_only_keep_the_same_canonical_url():
    de = _detail("744000144497156", language="de")
    fr = _detail("744000144497769", language="fr", default=True)

    de_url = _canonicalize_details(_TOKEN, [de])[0].url
    fr_url = _canonicalize_details(_TOKEN, [fr])[0].url
    bilingual = _canonicalize_details(_TOKEN, [fr, de])

    assert de_url == fr_url == bilingual[0].url
    assert bilingual[0].localizations.keys() == {"de", "fr"}
    assert bilingual[0].source_aliases == [
        "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497156",
        "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497769",
    ]


def test_reordering_publications_does_not_change_identity_or_primary_locale():
    de = _detail("744000144497156", language="de")
    fr = _detail("744000144497769", language="fr", default=True)

    first = _canonicalize_details(_TOKEN, [de, fr])[0]
    second = _canonicalize_details(_TOKEN, [fr, de])[0]

    assert first.url == second.url
    assert first.language == second.language == "fr"
    assert first.title == second.title == "FR title"


def test_reused_job_id_at_two_exact_locations_stays_two_vacancies():
    solothurn = _detail(
        "744000131823739",
        job_id=_REUSED_JOB_ID,
        latitude="47.2055637",
        longitude="7.5302145",
    )
    langendorf = _detail(
        "744000131822324",
        job_id=_REUSED_JOB_ID,
        latitude="47.2048921",
        longitude="7.531092300000001",
    )

    jobs = _canonicalize_details(_TOKEN, [solothurn, langendorf])

    assert len(jobs) == 2
    assert len({job.url for job in jobs}) == 2


def test_localized_text_never_participates_in_identity():
    original = _detail("744000144497156")
    translated = deepcopy(original)
    translated["location"].update(
        {
            "city": "Bienne",
            "address": "Rue traduite",
            "fullLocation": "Bienne, Suisse",
        }
    )
    original["location"].update(
        {
            "city": "Biel",
            "address": "Unionsgasse",
            "fullLocation": "Biel, Schweiz",
        }
    )

    assert _canonical_identity_components(original) == _canonical_identity_components(translated)


@pytest.mark.parametrize(
    ("value", "latitude"),
    [
        (47.1, True),
        (True, True),
        ("91", True),
        ("181", False),
        ("1e2", False),
        ("nan", False),
        (" 47.1", True),
    ],
)
def test_coordinates_are_strict_typed_bounded_decimals(value, latitude):
    with pytest.raises(ValueError):
        _normalize_coordinate(value, latitude=latitude)


def test_coordinate_format_normalizes_without_rounding():
    assert _normalize_coordinate("47.1391567000", latitude=True) == "47.1391567"
    assert _normalize_coordinate("7.531092300000001", latitude=False) == "7.531092300000001"
    assert _normalize_coordinate("-0.0", latitude=False) == "0"


def test_fallback_is_allowed_once_but_rejected_for_repeated_job_id():
    fallback = _detail("744000100000001", latitude=None, longitude=None)

    assert len(_canonicalize_details(_TOKEN, [fallback])) == 1
    duplicate = deepcopy(fallback)
    duplicate["id"] = "744000100000002"
    duplicate["language"] = {"code": "fr"}

    with pytest.raises(ValueError, match="without unambiguous provider coordinates"):
        _canonicalize_details(_TOKEN, [fallback, duplicate])


def test_hash_is_tenant_bound_and_uses_full_sha256():
    components = _canonical_identity_components(_detail("744000144497156"))
    smn = _canonical_source_url(_TOKEN, components)
    other = _canonical_source_url("OtherTenant", components)

    assert smn != other
    assert smn.startswith(
        "https://careers.smartrecruiters.com/SwissMedicalNetwork1?_jobseek_sr_identity=v1."
    )
    assert len(smn.rsplit(".", 1)[1]) == 64


async def test_canonical_discovery_requires_every_exact_detail(monkeypatch):
    monkeypatch.setattr(
        "src.core.monitors.smartrecruiters.asyncio.sleep",
        AsyncMock(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"id": "744000144497156"},
                        {"id": "744000144497769"},
                    ],
                    "totalFound": 2,
                },
            )
        if request.url.path.endswith("/744000144497156"):
            return httpx.Response(200, json=_detail("744000144497156", language="de"))
        return httpx.Response(503, text="detail unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception, match="detail unavailable|status=503|503"):
            await discover(
                {
                    "board_url": f"https://careers.smartrecruiters.com/{_TOKEN}",
                    "metadata": {
                        "token": _TOKEN,
                        "canonical_identity": CANONICAL_IDENTITY_JOB_LOCATION_V1,
                    },
                },
                client,
            )


async def test_canonical_discovery_returns_one_complete_rich_result():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"id": "744000144497156"},
                        {"id": "744000144497769"},
                    ],
                    "totalFound": 2,
                },
            )
        publication_id = request.url.path.rsplit("/", 1)[1]
        language = "de" if publication_id.endswith("156") else "fr"
        return httpx.Response(
            200,
            json=_detail(
                publication_id,
                language=language,
                default=language == "fr",
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {
                "board_url": f"https://careers.smartrecruiters.com/{_TOKEN}",
                "metadata": {
                    "token": _TOKEN,
                    "canonical_identity": CANONICAL_IDENTITY_JOB_LOCATION_V1,
                },
            },
            client,
        )

    assert len(result.urls) == 1
    assert result.jobs_by_url is not None
    job = next(iter(result.jobs_by_url.values()))
    assert job.localizations.keys() == {"de", "fr"}


async def test_canonical_discovery_rejects_list_total_drift():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "content": [{"id": str(744000100000000 + index)} for index in range(100)],
                    "totalFound": 101,
                },
            )
        return httpx.Response(
            200,
            json={"content": [{"id": "744000100000100"}], "totalFound": 102},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="total changed"):
            await discover(
                {
                    "board_url": f"https://careers.smartrecruiters.com/{_TOKEN}",
                    "metadata": {
                        "token": _TOKEN,
                        "canonical_identity": CANONICAL_IDENTITY_JOB_LOCATION_V1,
                    },
                },
                client,
            )

    assert calls == 2
