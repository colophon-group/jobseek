"""Stable SmartRecruiters identity for localized publications."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitors.smartrecruiters import (
    _DETAIL_RESPONSE_MAX_BYTES,
    _LIST_RESPONSE_MAX_BYTES,
    CANONICAL_IDENTITY_JOB_LOCATION_V1,
    _canonical_identity_components,
    _canonical_source_identity,
    _canonicalize_details,
    _fetch_canonical_details,
    _normalize_coordinate,
    discover,
)
from src.core.scrapers import JobContent
from src.shared.http_retry import ResponseBodyTooLargeError

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
        "name": f"{language.upper()} title",
        "uuid": f"00000000-0000-4000-8000-{publication_id[-12:]}",
        "jobId": job_id,
        "jobAdId": f"10000000-0000-4000-8000-{publication_id[-12:]}",
        "active": True,
        "visibility": "PUBLIC",
        "defaultJobAd": default,
        "refNumber": "REF-123",
        "releasedDate": "2026-08-25T12:00:00.000Z",
        "language": {"code": language},
        "company": {"identifier": _TOKEN, "name": "Swiss Medical Network"},
        "location": location,
        "department": {"id": 10114871},
        "industry": {"id": "hospital_and_health_care"},
        "function": {"id": "health_care_provider"},
        "typeOfEmployment": {"id": "full-time"},
        "experienceLevel": {"id": "not_applicable"},
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


def _listed(detail: dict) -> dict:
    """Build the stable fields the live list API overlaps with detail."""
    listed = {
        key: deepcopy(detail[key])
        for key in (
            "id",
            "name",
            "uuid",
            "jobAdId",
            "defaultJobAd",
            "refNumber",
            "company",
            "releasedDate",
            "location",
            "industry",
            "department",
            "function",
            "typeOfEmployment",
            "experienceLevel",
            "customField",
            "visibility",
            "language",
        )
    }
    listed["department"]["id"] = str(listed["department"]["id"])
    listed["ref"] = f"https://api.smartrecruiters.com/v1/companies/{_TOKEN}/postings/{detail['id']}"
    return listed


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


def test_de_only_and_fr_only_keep_identity_while_outbound_url_tracks_publication():
    de = _detail("744000144497156", language="de")
    fr = _detail("744000144497769", language="fr", default=True)

    de_job = _canonicalize_details(_TOKEN, [de])[0]
    fr_job = _canonicalize_details(_TOKEN, [fr])[0]
    bilingual = _canonicalize_details(_TOKEN, [fr, de])

    assert de_job.source_identity == fr_job.source_identity == bilingual[0].source_identity
    assert de_job.url == "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497156"
    assert fr_job.url == "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497769"
    assert bilingual[0].url == fr_job.url
    assert bilingual[0].localizations.keys() == {"de", "fr"}


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
    smn = _canonical_source_identity(_TOKEN, components)
    other = _canonical_source_identity("OtherTenant", components)

    assert smn != other
    assert smn.startswith(f"smartrecruiters:swissmedicalnetwork1:{components[0]}/geo/")
    assert len(smn.rsplit("/", 1)[1]) == 64


async def test_canonical_discovery_requires_every_exact_detail(monkeypatch):
    monkeypatch.setattr(
        "src.core.monitors.smartrecruiters.asyncio.sleep",
        AsyncMock(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            de = _detail("744000144497156", language="de")
            fr = _detail("744000144497769", language="fr", default=True)
            return httpx.Response(
                200,
                json={
                    "content": [_listed(de), _listed(fr)],
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
            de = _detail("744000144497156", language="de")
            fr = _detail("744000144497769", language="fr", default=True)
            return httpx.Response(
                200,
                json={
                    "content": [_listed(de), _listed(fr)],
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


@pytest.mark.parametrize(
    ("field", "mutate", "fallback"),
    [
        (
            "uuid",
            lambda detail: detail.__setitem__("uuid", "f2671ebd-86c2-4db7-8542-2296b78981f1"),
            False,
        ),
        ("uuid", lambda detail: detail.pop("uuid"), False),
        (
            "jobAdId",
            lambda detail: detail.__setitem__("jobAdId", "35a6e3e2-5fc7-4e5a-a690-70f85268eb47"),
            False,
        ),
        (
            "refNumber",
            lambda detail: detail.__setitem__("refNumber", "REF-CHANGED"),
            False,
        ),
        (
            "releasedDate",
            lambda detail: detail.__setitem__("releasedDate", "2026-08-26T00:00:00.000Z"),
            False,
        ),
        (
            "defaultJobAd",
            lambda detail: detail.__setitem__("defaultJobAd", True),
            False,
        ),
        (
            "company",
            lambda detail: detail.__setitem__(
                "company", {"identifier": "OtherTenant", "name": "Swiss Medical Network"}
            ),
            False,
        ),
        ("visibility", lambda detail: detail.__setitem__("visibility", "PRIVATE"), False),
        ("language", lambda detail: detail.__setitem__("language", {"code": "fr"}), False),
        ("location", lambda detail: detail["location"].__setitem__("country", "de"), False),
        ("location", lambda detail: detail["location"].__setitem__("postalCode", "9999"), False),
        ("location", lambda detail: detail["location"].__setitem__("latitude", "47.2"), False),
        ("latitude", lambda detail: detail["location"].pop("latitude"), False),
        ("department", lambda detail: detail.__setitem__("department", {"id": 999}), True),
        (
            "customField",
            lambda detail: detail["customField"][0].__setitem__("valueId", "changed"),
            True,
        ),
    ],
)
async def test_canonical_discovery_rejects_same_id_snapshot_drift(field, mutate, fallback):
    listed_detail = _detail("744000144497156")
    if fallback:
        listed_detail["location"].pop("latitude")
        listed_detail["location"].pop("longitude")
    fetched_detail = deepcopy(listed_detail)
    mutate(fetched_detail)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={"content": [_listed(listed_detail)], "totalFound": 1},
            )
        return httpx.Response(200, json=fetched_detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match=field):
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


async def test_canonical_list_response_is_streamed_with_a_hard_body_cap():
    body = b"{" + b" " * _LIST_RESPONSE_MAX_BYTES + b"}"

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(ResponseBodyTooLargeError):
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


async def test_canonical_detail_response_is_streamed_with_a_hard_body_cap():
    detail = _detail("744000144497156")
    oversized = b"{" + b" " * _DETAIL_RESPONSE_MAX_BYTES + b"}"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={"content": [_listed(detail)], "totalFound": 1},
            )
        return httpx.Response(200, content=oversized)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ResponseBodyTooLargeError):
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


async def test_detail_failure_cancels_other_in_flight_responses(monkeypatch):
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def fake_detail(_client, _token, posting_id):
        if posting_id == "1":
            await sibling_started.wait()
            raise ResponseBodyTooLargeError("https://api.smartrecruiters.com/detail/1", 1)
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    monkeypatch.setattr("src.core.monitors.smartrecruiters._get_detail_with_retry", fake_detail)

    with pytest.raises(ResponseBodyTooLargeError):
        await _fetch_canonical_details(AsyncMock(), _TOKEN, [{"id": "1"}, {"id": "2"}])

    assert sibling_cancelled.is_set()


async def test_duplicate_detail_publication_ids_fail_closed(monkeypatch):
    async def fake_detail(_client, _token, _posting_id):
        return {"id": "same"}

    monkeypatch.setattr("src.core.monitors.smartrecruiters._get_detail_with_retry", fake_detail)
    monkeypatch.setattr(
        "src.core.monitors.smartrecruiters._validate_detail_identity",
        lambda *_args: None,
    )

    with pytest.raises(ValueError, match="repeated one publication id"):
        await _fetch_canonical_details(AsyncMock(), _TOKEN, [{"id": "1"}, {"id": "2"}])


async def test_unsafe_token_is_rejected_before_any_request():
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="bounded provider identifier"):
            await discover(
                {
                    "board_url": "https://careers.smartrecruiters.com/SwissMedicalNetwork1",
                    "metadata": {
                        "token": "../other?redirect=https://evil.example",
                        "canonical_identity": CANONICAL_IDENTITY_JOB_LOCATION_V1,
                    },
                },
                client,
            )

    assert requests == 0


async def test_stable_identity_tolerates_localized_labels_and_classification_cache_lag():
    """Localized text and mutable classifications never define geo identity."""
    listed_detail = _detail("744000144497156")
    listed_detail["language"].update({"label": "German", "labelNative": "Deutsch"})
    listed_detail["location"].update(
        {
            "city": "Biel",
            "region": "BE",
            "address": "Unionsgasse 1",
            "fullLocation": "Biel, BE, Switzerland",
        }
    )
    fetched_detail = deepcopy(listed_detail)
    fetched_detail["name"] = "Titre localisé"
    fetched_detail["company"]["name"] = "Réseau médical suisse"
    fetched_detail["language"].update({"label": "Allemand", "labelNative": "Deutsch (CH)"})
    fetched_detail["location"].update(
        {
            "city": "Bienne",
            "region": "Berne",
            "address": "Rue de l'Union 1",
            "fullLocation": "Bienne, BE, Suisse",
        }
    )
    fetched_detail["department"] = {"id": 999, "label": "Changed department"}
    fetched_detail["typeOfEmployment"] = {"id": "part-time", "label": "Part-time"}
    fetched_detail["customField"][0]["valueId"] = "changed-brand"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/postings"):
            return httpx.Response(
                200,
                json={"content": [_listed(listed_detail)], "totalFound": 1},
            )
        return httpx.Response(200, json=fetched_detail)

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
