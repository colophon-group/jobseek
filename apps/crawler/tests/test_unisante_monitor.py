from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from src.core.monitors import all_monitor_types, unisante
from src.probe_boards import probe_row


def _listing(
    jobs: list[tuple[str, str]],
    *,
    alias: bool = False,
    pagination: bool = False,
    broken_classes: bool = False,
    empty_visible: bool | None = None,
) -> str:
    if empty_visible is None:
        empty_visible = not jobs
    prefix = "/offre/" if alias else "/index.php/offre/"
    cards = "".join(
        (
            '<div class="offres-item">'
            f'<a class="{"changed-link" if broken_classes else "box-job__header_link"}" '
            f'href="{prefix}{slug}" title="{title}">{title}</a>'
            "</div>"
        )
        for slug, title in jobs
    )
    next_link = '<a rel="next" href="?page=2">Next</a>' if pagination else ""
    return f"""
    <html><body><main id="main">
      <h1>Nos offres d'emploi</h1>
      <select id="offres-filter"><option value="0">Toutes</option></select>
      <div class="row offres-items">{cards}</div>
      <div id="no-ads" {"" if empty_visible else 'style="display: none;"'}>
        Aucune offre n'est disponible pour le moment.
      </div>
      {next_link}
    </main></body></html>
    """


def _detail(
    reference: str,
    *,
    deadline: str | None = None,
    visible_mojibake: bool = False,
    malformed_deadline: bool = False,
) -> str:
    deadline_text = ""
    if deadline is not None:
        deadline_text = f"<p>Délai de postulation : {deadline}</p>"
    if malformed_deadline:
        deadline_text = "<p>Délai de postulation : prochainement</p>"
    body = (
        "Nous recherchons une personne qui contribue aux missions de santé "
        "publique, collabore avec les équipes cliniques et scientifiques, "
        "assure un travail rigoureux et participe activement à l'amélioration "
        "continue des prestations. "
    ) * 3
    if visible_mojibake:
        body += "secrÃ©tariat"
    structured = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Structured title is not authoritative",
        "description": "secr&Atilde;&copy;tariat",
        "datePosted": "2026-08-20",
        "employmentType": "Temps plein",
        "hiringOrganization": {
            "@type": "Organization",
            "name": "Unisanté, Centre universitaire de médecine générale",
            "sameAs": "https://www.unisante.ch",
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": "Lausanne",
                "addressCountry": "Suisse",
            },
        },
    }
    return f"""
    <html><head><script type="application/ld+json">{json.dumps(structured)}</script></head>
    <body><main id="main"><div class="container"><div class="row">
      <div class="col-12 col-sm-12 col-md-10">
        <h1>Titre visible</h1><p>{body}</p>
        {deadline_text}<p>Référence : {reference}</p>
      </div>
      <div class="col-md-2"><form><input name="email"></form></div>
    </div></div></main></body></html>
    """


def _client(
    primary_jobs: list[tuple[str, str]],
    *,
    alias_jobs: list[tuple[str, str]] | None = None,
    details: dict[str, str] | None = None,
    primary_options: dict | None = None,
    alias_options: dict | None = None,
) -> httpx.AsyncClient:
    details = details or {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/index.php/offres":
            return httpx.Response(
                200,
                text=_listing(primary_jobs, **(primary_options or {})),
                request=request,
            )
        if path == "/offres":
            return httpx.Response(
                200,
                text=_listing(
                    primary_jobs if alias_jobs is None else alias_jobs,
                    alias=True,
                    **(alias_options or {}),
                ),
                request=request,
            )
        slug = path.removeprefix("/index.php/offre/")
        if path.startswith("/index.php/offre/") and slug in details:
            return httpx.Response(200, text=details[slug], request=request)
        return httpx.Response(404, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(unisante, "_today", lambda: date(2026, 8, 25))


@pytest.mark.asyncio
async def test_discovers_evergreen_and_excludes_expired_listing() -> None:
    jobs = [
        ("1405-assistante-de-direction", "Assistant·e de direction"),
        ("medecin-assistant-cmg", "Médecin assistant·e CMG"),
        ("1215-medecin-pediatre", "Médecin pédiatre"),
    ]
    details = {
        "1405-assistante-de-direction": _detail("1405", deadline="13-09-2026"),
        "medecin-assistant-cmg": _detail("8"),
        "1215-medecin-pediatre": _detail("1215", deadline="2 août 2026"),
    }
    async with _client(jobs, details=details) as client:
        discovered = await unisante.discover(
            {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
        )

    assert [job.metadata["provider_reference"] for job in discovered] == ["8", "1405"]
    evergreen, numeric = discovered
    assert evergreen.url == "https://emploi.unisante.ch/index.php/offres?reference=8"
    assert evergreen.metadata["detail_url"].endswith("/index.php/offre/medecin-assistant-cmg")
    assert numeric.title == "Assistant·e de direction"
    assert numeric.locations == ["Lausanne, Suisse"]
    assert numeric.employment_type == "Temps plein"
    assert numeric.date_posted == "2026-08-20"
    assert len(numeric.description or "") >= 300
    assert "Atilde" not in (numeric.description or "")
    assert numeric.metadata["application_deadline"] == "2026-09-13"


@pytest.mark.asyncio
async def test_accepts_zero_only_when_both_aliases_prove_empty() -> None:
    async with _client([]) as client:
        assert (
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )
            == []
        )


@pytest.mark.asyncio
async def test_rejects_matching_blank_shells_with_normal_hidden_marker() -> None:
    """Two matching pre-render shells cannot silently delist the board."""
    async with _client(
        [],
        primary_options={"empty_visible": False},
        alias_options={"empty_visible": False},
    ) as client:
        with pytest.raises(ValueError, match="without authoritative zero evidence"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete_alias", ["primary", "alias"])
async def test_rejects_one_incomplete_alias_shell(incomplete_alias: str) -> None:
    """Either listing alias independently has to prove its empty state."""
    primary_options = {"empty_visible": incomplete_alias != "primary"}
    alias_options = {"empty_visible": incomplete_alias != "alias"}
    async with _client(
        [],
        primary_options=primary_options,
        alias_options=alias_options,
    ) as client:
        with pytest.raises(ValueError, match="without authoritative zero evidence"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_rejects_alias_inventory_mismatch() -> None:
    jobs = [("1405-role", "Role")]
    async with _client(jobs, alias_jobs=[]) as client:
        with pytest.raises(ValueError, match="different inventories"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_rejects_job_links_outside_authoritative_card_selector() -> None:
    jobs = [("1405-role", "Role")]
    async with _client(
        jobs,
        primary_options={"broken_classes": True},
        alias_options={"broken_classes": True},
    ) as client:
        with pytest.raises(ValueError, match="card/link structure changed"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_rejects_pagination() -> None:
    async with _client([], primary_options={"pagination": True}) as client:
        with pytest.raises(ValueError, match="unsupported pagination"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_rejects_unparseable_deadline() -> None:
    jobs = [("1405-role", "Role")]
    details = {"1405-role": _detail("1405", malformed_deadline=True)}
    async with _client(jobs, details=details) as client:
        with pytest.raises(ValueError, match="unparseable application deadline"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_rejects_visible_mojibake() -> None:
    jobs = [("1405-role", "Role")]
    details = {"1405-role": _detail("1405", visible_mojibake=True)}
    async with _client(jobs, details=details) as client:
        with pytest.raises(ValueError, match="contains mojibake"):
            await unisante.discover(
                {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
            )


@pytest.mark.asyncio
async def test_deduplicates_mutable_slugs_by_displayed_reference() -> None:
    jobs = [("8-evergreen-role", "Evergreen role"), ("evergreen-role", "Evergreen role")]
    async with _client(
        jobs,
        details={slug: _detail("8") for slug, _title in jobs},
    ) as client:
        discovered = await unisante.discover(
            {"board_url": "https://emploi.unisante.ch/index.php/offres"}, client
        )

    assert len(discovered) == 1
    assert discovered[0].url == "https://emploi.unisante.ch/index.php/offres?reference=8"
    assert discovered[0].metadata["detail_url"].endswith("/index.php/offre/8-evergreen-role")
    assert discovered[0].metadata["provider_reference"] == "8"


@pytest.mark.asyncio
async def test_daily_probe_accepts_monitor_verified_zero() -> None:
    row = {
        "company_slug": "unisante",
        "board_slug": "unisante-emploi",
        "board_url": "https://emploi.unisante.ch/index.php/offres",
        "monitor_type": "unisante",
        "monitor_config": json.dumps({"identity_migration": "unisante-provider-reference-v1"}),
        "scraper_type": "skip",
        "scraper_config": "",
    }
    async with _client([]) as client:
        result = await probe_row(row, client)

    assert result.status == "ok"
    assert result.message == "authoritative inventory: 0 active jobs"


def test_monitor_is_registered_as_rich_company_specific_type() -> None:
    assert "unisante" in all_monitor_types()


def test_company_pr_label_allowlist_supports_unisante() -> None:
    script = Path(__file__).parents[3] / ".github" / "scripts" / "label-pr.sh"
    assert "|unisante|" in script.read_text()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13-09-2026", date(2026, 9, 13)),
        ("31.08.2026", date(2026, 8, 31)),
        ("2 août 2026", date(2026, 8, 2)),
    ],
)
def test_deadline_formats(raw: str, expected: date) -> None:
    assert unisante._parse_deadline(f"Délai de postulation : {raw}") == expected


def test_listing_rejects_missing_empty_marker() -> None:
    html = _listing([]).replace('id="no-ads"', 'id="changed-empty-marker"')
    with pytest.raises(ValueError, match="omitted its scoped empty marker"):
        unisante._parse_listing(html, "https://emploi.unisante.ch/index.php/offres")


def test_listing_rejects_visible_empty_state_with_jobs() -> None:
    html = _listing([("1405-role", "Role")], empty_visible=True)
    with pytest.raises(ValueError, match="jobs with a visible empty state"):
        unisante._parse_listing(html, "https://emploi.unisante.ch/index.php/offres")


def test_listing_rejects_cross_origin_job_link() -> None:
    html = _listing([("1405-role", "Role")]).replace(
        'href="/index.php/offre/1405-role"',
        'href="https://evil.example/offre/1405-role"',
    )
    with pytest.raises(ValueError, match="invalid job URL"):
        unisante._parse_listing(html, "https://emploi.unisante.ch/index.php/offres")


def test_detail_rejects_reference_that_disagrees_with_numeric_slug() -> None:
    listing_job = unisante._ListingJob("1405-role", "Role")
    with pytest.raises(ValueError, match="reference disagrees"):
        unisante._parse_detail(_detail("999"), listing_job)
