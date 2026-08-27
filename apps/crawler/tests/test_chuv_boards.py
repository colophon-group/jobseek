"""Stable provider-identity contracts for the CHUV board."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors import DiscoveredJob

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_SLUG = "chuv-careers"
_SOURCE_ALLOWLIST = r"(?i)^https://recrutement\.chuv\.ch/vacancy/[a-z0-9][^/?#]*-[0-9]+\.html$"
_STABLE_TRANSFORM = {
    "find": (r"(?i)^https://recrutement\.chuv\.ch/vacancy/[a-z0-9][^/?#]*-([0-9]+)\.html$"),
    "replace": r"https://recrutement.chuv.ch/vacancy/_-\1.html",
    "collision_policy": "prefer_source_pattern",
    "collision_preferred_source_patterns": [r"(?i)^https://recrutement\.chuv\.ch/vacancy/"],
    "collision_canonical_identity_regex": (
        r"^https://recrutement\.chuv\.ch/vacancy/_-([0-9]+)\.html$"
    ),
    "collision_identity_metadata_key": "provider_id",
    "collision_stream_buffer_limit": 500,
}


def _config() -> dict:
    with _BOARDS_PATH.open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == _BOARD_SLUG)
    return json.loads(row["monitor_config"])


def _canonicalize(
    source_jobs: list[tuple[str, str, object]],
) -> MonitorResult:
    jobs = {
        url: DiscoveredJob(
            url=url,
            title=title,
            metadata={"provider_id": provider_id},
        )
        for url, title, provider_id in source_jobs
    }
    result = MonitorResult(urls=set(jobs), jobs_by_url=jobs)
    result = _apply_url_allowlist(result, {"url_allowlist": _SOURCE_ALLOWLIST})
    return _apply_url_transform(result, {"url_transform": _STABLE_TRANSFORM})


def test_chuv_board_has_fail_closed_numeric_identity_contract() -> None:
    config = _config()

    assert config["url_allowlist"] == _SOURCE_ALLOWLIST
    assert config["url_transform"] == _STABLE_TRANSFORM
    assert config["fields"]["metadata.provider_id"] == "id"


@pytest.mark.parametrize("reverse", [False, True])
def test_chuv_title_and_locale_variants_converge_deterministically(reverse: bool) -> None:
    aliases = [
        (
            "https://recrutement.chuv.ch/vacancy/medecin-chef-fe-de-clinique-fr-316965.html",
            "Médecin chef-fe de clinique",
            316965,
        ),
        (
            "https://recrutement.chuv.ch/vacancy/arzt-oberarztin-de-316965.html",
            "Arzt / Oberärztin",
            316965,
        ),
    ]
    if reverse:
        aliases.reverse()

    result = _canonicalize(aliases)

    canonical = "https://recrutement.chuv.ch/vacancy/_-316965.html"
    assert result.urls == {canonical}
    assert result.jobs_by_url is not None
    assert result.jobs_by_url[canonical].title == "Arzt / Oberärztin"
    assert result.jobs_by_url[canonical].metadata == {"provider_id": 316965}


def test_chuv_identity_contract_rejects_unstable_or_foreign_shapes() -> None:
    invalid = [
        ("https://recrutement.chuv.ch/vacancy/title-not-numeric.html", "Bad", 316965),
        ("https://recrutement.chuv.ch/vacancy/_-316965.html", "Already canonical", 316965),
        ("https://evil.example/vacancy/title-316965.html", "Foreign", 316965),
        ("https://recrutement.chuv.ch/vacancy/title-316965.html?lang=fr", "Query", 316965),
    ]

    result = _canonicalize(invalid)

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == len(invalid)


@pytest.mark.parametrize("provider_id", [316964, True, 316965.0, None])
def test_chuv_transform_requires_exact_integer_provider_identity(provider_id: object) -> None:
    with pytest.raises(ValueError, match="provider identity does not match"):
        _canonicalize(
            [
                (
                    "https://recrutement.chuv.ch/vacancy/title-316965.html",
                    "Role",
                    provider_id,
                )
            ]
        )
