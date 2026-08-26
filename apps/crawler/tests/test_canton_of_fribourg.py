"""Stable ownership and provider-identity contracts for Canton of Fribourg."""

from __future__ import annotations

import json
import re

import pytest

from src.core.monitor import MonitorResult, _apply_job_filter, _apply_url_transform
from src.core.monitors import DiscoveredJob
from src.shared.constants import get_data_dir
from src.shared.csv_io import read_csv


def _main_config() -> dict:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    row = next(row for row in rows if row["board_slug"] == "canton-of-fribourg-main")
    return json.loads(row["monitor_config"])


def _teaching_row() -> dict:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    return next(row for row in rows if row["board_slug"] == "canton-of-fribourg-teaching")


def test_main_board_uses_exact_required_scope_and_provider_identity() -> None:
    config = _main_config()
    assert config["preset"] == "successfactors"
    assert config["detail_fields"] == {"service": "dept", "adcode": "adcode"}
    assert config["job_filter"]["field"] == "metadata.service"
    assert config["job_filter"]["require_classification"] is True
    assert config["url_transform"]["collision_identity_metadata_key"] == "adcode"
    assert config["url_transform"]["replace"] == "https://jobs.fr.ch/search/?q={identity}"
    source_allowlist = re.compile(config["url_allowlist"])
    assert source_allowlist.fullmatch("https://jobs.fr.ch/job/example/1369263257/")
    assert source_allowlist.fullmatch("https://jobs.fr.ch/Police_Cantonale/job/example/1369197957/")
    assert not source_allowlist.fullmatch("https://evil.example/job/example/1369263257/")

    included = re.compile(config["job_filter"]["include"])
    excluded = re.compile(config["job_filter"]["exclude"])
    assert included.fullmatch("Service de l'action sociale")
    assert included.fullmatch("Kantonales Sozialamt")
    assert included.fullmatch("Justice de paix de l'arrondissement de la Glâne")
    assert included.fullmatch("Justice de paix de l'arrondissement de la Gruyère")
    assert excluded.fullmatch("Unifr-7640 Département de Médecine")
    assert excluded.fullmatch("Réseau fribourgeois de santé mentale")
    assert excluded.fullmatch("Grangeneuve")
    assert not included.search("Nouvelle institution autonome")
    assert not excluded.search("Nouvelle institution autonome")


def test_teaching_board_requires_exact_current_pdf_contract() -> None:
    row = _teaching_row()
    monitor = json.loads(row["monitor_config"])
    scraper = json.loads(row["scraper_config"])

    assert monitor.get("render") is not True
    assert monitor["url_filter"] == r"^https?://(?:www\.)?fr\.ch/document/[0-9]+$"
    assert monitor["require_unexpired_pdf"]["date_format"] == "%d %B %Y"
    assert monitor["empty_states"] == [
        {
            "selector": "article h1.coh-style-content-page-title",
            "exact_text": "Postes dans l'enseignement (écoles primaires, secondaires I et II)",
            "required_link_selector": 'a[href="https://isa.fr.ch/remplacement"]',
            "required_link_url_pattern": r"^https://isa\.fr\.ch/remplacement$",
        }
    ]
    assert scraper["title_source"] == "text"
    assert scraper["require_title_pattern"] is True


def test_service_classification_fails_closed_for_unknown_owner() -> None:
    url = "https://jobs.fr.ch/job/example/1001/"
    result = MonitorResult(
        urls={url},
        jobs_by_url={
            url: DiscoveredJob(
                url=url,
                title="Unknown role",
                metadata={"service": "Nouvelle institution autonome", "adcode": "10499"},
            )
        },
    )

    with pytest.raises(ValueError, match="neither include nor exclude"):
        _apply_job_filter(result, _main_config())


def test_localized_variants_collapse_to_one_stable_adcode_url() -> None:
    french = "https://jobs.fr.ch/job/Fribourg-Poste-francais-Sari/1369263257/"
    german = "https://jobs.fr.ch/job/Fribourg-Deutsche-Stelle-Saan/1369263157/"
    jobs = {
        french: DiscoveredJob(
            url=french,
            title="Collaborateur-trice administratif-ve",
            metadata={
                "id": "1369263257",
                "service": "Service public de l'emploi",
                "adcode": "10444",
            },
        ),
        german: DiscoveredJob(
            url=german,
            title="Verwaltungssachbearbeiter/in",
            metadata={"id": "1369263157", "service": "Amt für den Arbeitsmarkt", "adcode": "10444"},
        ),
    }

    transformed = _apply_url_transform(
        MonitorResult(urls=set(jobs), jobs_by_url=jobs),
        _main_config(),
    )

    canonical = "https://jobs.fr.ch/search/?q=10444"
    assert transformed.urls == {canonical}
    assert transformed.jobs_by_url is not None
    assert set(transformed.jobs_by_url) == {canonical}
    assert transformed.jobs_by_url[canonical].metadata["adcode"] == "10444"


def test_identity_transform_rejects_missing_required_metadata() -> None:
    url = "https://jobs.fr.ch/job/example/1001/"
    result = MonitorResult(
        urls={url},
        jobs_by_url={url: DiscoveredJob(url=url, title="Role", metadata={"id": "1001"})},
    )

    with pytest.raises(ValueError, match="metadata is missing or invalid"):
        _apply_url_transform(result, _main_config())
