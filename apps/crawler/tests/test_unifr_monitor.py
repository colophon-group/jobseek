from __future__ import annotations

from datetime import date

import httpx
import pytest

from src.core.monitors.unifr import (
    _accordion_jobs,
    _AccordionSource,
    _central_jobs,
    _link_inventory,
    _LinkSource,
)

FR = "https://www.unifr.ch/sp/fr/postes-vacants.html"
DE = "https://www.unifr.ch/sp/de/offene-stellen.html"
DETAIL = "https://webapps.unifr.ch/sp/ws/b49e151b85b415a7201e88d3e9ecf54f11d7589e/detail"


def _central_html(locale: str, jobs: list[tuple[str, str]], *, pagination: bool = False) -> str:
    if locale == "fr":
        title = (
            "Postes vacants - Offres d'emploi à l'Université de Fribourg "
            "| Service du personnel | Université de Fribourg"
        )
        heading = "Postes vacants - Offres d'emploi à l'Université de Fribourg"
    else:
        title = (
            "Offene Stellen - Stellenangebote an der Universität Freiburg "
            "| Personaldienst | Universität Freiburg"
        )
        heading = "Offene Stellen - Stellenangebote an der Universität Freiburg"
    cards = "".join(
        f'<li class="list-group-item" id="{identifier}">'
        f'<h4 class="list-group-item-heading name">{job_title}</h4>'
        f'<div id="open{identifier}">Loading...</div></li>'
        for identifier, job_title in jobs
    )
    next_link = '<a rel="next">next</a>' if pagination else ""
    return (
        f"<html><head><title>{title}</title></head><body><main><h2>{heading}</h2>"
        f'<ul class="list-group list">{cards}</ul>{next_link}</main></body></html>'
    )


def _detail_payload(identifier: str, locale: str, title: str, **changes) -> dict:
    listing = FR if locale == "fr" else DE
    payload = {
        "id": identifier,
        "autorite": "Université" if locale == "fr" else "Universität",
        "fonction": title,
        "content": f"<p>{locale} detail for {identifier}</p>",
        "startpublish": "2026-08-01T00:00:00+02:00",
        "endpublish": "2026-09-30T00:00:00+02:00",
        "link": f"{listing}#{identifier}",
    }
    payload.update(changes)
    return payload


def _central_transport(
    fr_jobs: list[tuple[str, str]],
    de_jobs: list[tuple[str, str]],
    *,
    mutate_detail=None,
) -> httpx.MockTransport:
    titles = {
        (identifier, locale): title
        for locale, jobs in (("fr", fr_jobs), ("de", de_jobs))
        for identifier, title in jobs
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == FR:
            return httpx.Response(
                200, text=_central_html("fr", fr_jobs), headers={"content-type": "text/html"}
            )
        if url == DE:
            return httpx.Response(
                200, text=_central_html("de", de_jobs), headers={"content-type": "text/html"}
            )
        if url.startswith(f"{DETAIL}/"):
            _root, locale, identifier = url.rsplit("/", 2)
            payload = _detail_payload(identifier, locale, titles[(identifier, locale)])
            if mutate_detail is not None:
                mutate_detail(identifier, locale, payload)
            return httpx.Response(200, json=payload, headers={"content-type": "application/json"})
        raise AssertionError(f"unexpected URL: {url}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_central_unions_locales_by_provider_id_and_preserves_localizations():
    transport = _central_transport(
        [("1885", "Titre français"), ("1891", "Shared French title")],
        [("1891", "Gemeinsamer deutscher Titel"), ("1897", "Deutscher Titel")],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await _central_jobs(client, date(2026, 8, 26))

    provider_ids = []
    for job in jobs:
        assert job.metadata is not None
        provider_ids.append(job.metadata["unifr_provider_id"])
    assert provider_ids == ["1885", "1891", "1897"]
    shared = jobs[1]
    assert shared.url == f"{FR}?_jid=1891"
    assert shared.title == "Shared French title"
    assert shared.localizations is not None
    assert set(shared.localizations) == {"fr", "de"}
    assert shared.localizations["de"]["title"] == "Gemeinsamer deutscher Titel"


@pytest.mark.asyncio
async def test_central_identity_survives_locale_and_title_changes():
    first = _central_transport([("1911", "Old title")], [("1911", "Alter Titel")])
    second = _central_transport([("1911", "New punctuation!")], [("1911", "Neuer Titel")])
    async with httpx.AsyncClient(transport=first) as client:
        old = await _central_jobs(client, date(2026, 8, 26))
    async with httpx.AsyncClient(transport=second) as client:
        new = await _central_jobs(client, date(2026, 8, 26))
    assert old[0].url == new[0].url == f"{FR}?_jid=1911"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda _id, _locale, payload: payload.update(id="1904"), "ID does not match"),
        (lambda _id, _locale, payload: payload.update(autorite="External"), "authority"),
        (
            lambda _id, _locale, payload: payload.update(endpublish="2026-08-01T00:00:00+02:00"),
            "expired",
        ),
    ],
)
async def test_central_rejects_response_identity_owner_and_expiry(mutator, message):
    transport = _central_transport(
        [("1911", "Researcher")],
        [("1911", "Forscher")],
        mutate_detail=mutator,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match=message):
            await _central_jobs(client, date(2026, 8, 26))


@pytest.mark.asyncio
async def test_central_rejects_duplicate_ids_pagination_and_unproved_zero():
    for body, message in (
        (_central_html("fr", [("1", "One"), ("1", "Two")]), "duplicate"),
        (_central_html("fr", [("1", "One")], pagination=True), "pagination"),
        (_central_html("fr", []), "safe zero"),
    ):

        def handler(request: httpx.Request, body=body) -> httpx.Response:
            locale = "fr" if "/fr/" in str(request.url) else "de"
            page = body if locale == "fr" else _central_html("de", [("2", "Zwei")])
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match=message):
                await _central_jobs(client, date(2026, 8, 26))


def _accordion_html(
    *,
    title: str,
    heading: str,
    items: list[tuple[str, str, str]],
    list_class: str = "brandedstyle",
) -> str:
    rows = "".join(
        f'<li><a href="#" data-accordion-toggler="{identifier}">{job_title}</a>'
        f'<div data-accordion-content="{identifier}"><p>{description}</p></div></li>'
        for identifier, job_title, description in items
    )
    return (
        f"<html><head><title>{title}</title></head><body><main><h2>{heading}</h2>"
        f'<ul class="accordion {list_class}">{rows}</ul></main></body></html>'
    )


@pytest.mark.asyncio
async def test_accordion_identity_is_source_owned_and_title_independent():
    source = _AccordionSource(
        url="https://www.unifr.ch/unit/en/jobs.html",
        page_title_suffix="| Unit | University of Fribourg",
        heading="Open Positions",
        expected_ids=frozenset({"cms-42"}),
        excluded_central_ids={},
    )

    async def run(title: str):
        html = _accordion_html(
            title="Open Positions | Unit | University of Fribourg",
            heading="Open Positions",
            items=[("cms-42", title, "Official open listing with no deadline")],
        )
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await _accordion_jobs(client, source, date(2026, 8, 26))

    old = await run("Old title")
    new = await run("Titre français modifié")
    assert old[0].url == new[0].url == f"{source.url}?_jid=cms-42"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([], "safe zero"),
        ([("cms-99", "Unexpected", "Open")], "drifted"),
        ([("cms-42", "One", "Open"), ("cms-42", "Two", "Open")], "duplicate"),
    ],
)
async def test_accordion_fails_closed_on_zero_drift_and_duplicate_ids(items, message):
    source = _AccordionSource(
        url="https://www.unifr.ch/unit/en/jobs.html",
        page_title_suffix="| Unit | University of Fribourg",
        heading="Open Positions",
        expected_ids=frozenset({"cms-42"}),
        excluded_central_ids={},
    )
    html = _accordion_html(
        title="Open Positions | Unit | University of Fribourg",
        heading="Open Positions",
        items=items,
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match=message):
            await _accordion_jobs(client, source, date(2026, 8, 26))


@pytest.mark.asyncio
async def test_accordion_filters_expired_items_but_rejects_ambiguous_deadline():
    source = _AccordionSource(
        url="https://www.ami.swiss/en/jobs.html",
        page_title_suffix="| AMI | Université de Fribourg",
        heading="Open positions",
        expected_ids=frozenset({"active", "expired"}),
        excluded_central_ids={},
        deadline_required=frozenset({"expired"}),
        immediately_available=frozenset({"active"}),
    )
    html = _accordion_html(
        title="Open positions | AMI | Université de Fribourg",
        heading="Open positions",
        items=[
            ("active", "Polymer role", "The position is available immediately."),
            ("expired", "MSCA 2026", "MSCA 2026 applications close by July 15."),
        ],
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await _accordion_jobs(client, source, date(2026, 8, 26))
    source_ids = []
    for job in jobs:
        assert job.metadata is not None
        source_ids.append(job.metadata["unifr_source_id"])
    assert source_ids == ["active"]

    ambiguous = html.replace(
        "MSCA 2026 applications close by July 15.",
        "MSCA 2026 applications close by July 15 or by August 1, 2026.",
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=ambiguous, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="ambiguous"):
            await _accordion_jobs(client, source, date(2026, 8, 26))


@pytest.mark.asyncio
async def test_link_inventory_uses_complete_first_party_urls_without_snapshot_filter():
    source = _LinkSource(
        url="https://www.unifr.ch/unit/de/jobs.html",
        page_title_suffix="| Unit | Universität Freiburg",
        heading="Offene Stellen",
        path_prefix="/unit/de/assets/jobs/",
    )
    html = (
        "<html><head><title>Offene Stellen | Unit | Universität Freiburg</title></head>"
        "<body><main><h2>Offene Stellen</h2>"
        '<a href="assets/jobs/first.pdf">First</a>'
        '<a href="assets/jobs/replacement-2099.pdf">Replacement</a>'
        "</main></body></html>"
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await _link_inventory(client, source)
    assert urls == {
        "https://www.unifr.ch/unit/de/assets/jobs/first.pdf",
        "https://www.unifr.ch/unit/de/assets/jobs/replacement-2099.pdf",
    }
