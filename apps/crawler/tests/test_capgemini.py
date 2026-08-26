"""Stable provider-identity contracts for Capgemini boards."""

from __future__ import annotations

import json

import pytest

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors import DiscoveredJob
from src.shared.constants import get_data_dir
from src.shared.csv_io import read_csv


def _rows() -> dict[str, dict[str, str]]:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    return {row["board_slug"]: row for row in rows if row["company_slug"] == "capgemini"}


def _config(slug: str) -> dict:
    return json.loads(_rows()[slug]["monitor_config"] or "{}")


def _canonicalize(slug: str, urls: set[str]) -> MonitorResult:
    config = _config(slug)
    result = _apply_url_allowlist(MonitorResult(urls=urls), config)
    return _apply_url_transform(result, config)


def _canonicalize_rich(slug: str, jobs: dict[str, DiscoveredJob]) -> MonitorResult:
    config = _config(slug)
    result = MonitorResult(urls=set(jobs), jobs_by_url=jobs)
    result = _apply_url_allowlist(result, config)
    return _apply_url_transform(result, config)


def test_capgemini_uses_five_non_overlapping_provider_boards() -> None:
    assert set(_rows()) == {
        "capgemini-cambridge-consultants",
        "capgemini-frog",
        "capgemini-global",
        "capgemini-government-solutions",
        "capgemini-purpose",
    }


def test_cambridge_consultants_uses_stable_greenhouse_provider_ids() -> None:
    row = _rows()["capgemini-cambridge-consultants"]

    assert row["board_url"] == ("https://job-boards.eu.greenhouse.io/cambridgeconsultantslimited")
    assert row["monitor_type"] == "greenhouse"
    assert _config("capgemini-cambridge-consultants") == {"token": "cambridgeconsultantslimited"}
    assert row["scraper_type"] == "skip"


def test_global_title_and_tracking_aliases_collapse_to_posting_id() -> None:
    aliases = {
        (
            "https://careers.capgemini.com/job/Amsterdam-Old-Title/1390944733/"
            "?feedId=388633&utm_source=CareerSite&tcsource=apply"
        ),
        "https://careers.capgemini.com/job/Changed-Title/1390944733/",
    }

    result = _canonicalize("capgemini-global", aliases)

    assert result.urls == {"https://careers.capgemini.com/job/-/1390944733/"}
    assert result.security_filtered_count == 0


@pytest.mark.parametrize("reverse", [False, True])
def test_global_rich_locale_and_source_selection_is_order_independent(reverse: bool) -> None:
    en_url = "https://careers.capgemini.com/job/Amsterdam-Data-Engineer/1390944733/"
    en_tracked_url = (
        "https://careers.capgemini.com/job/Amsterdam-Data-Engineer/1390944733/"
        "?feedId=388633&utm_source=CareerSite&tcsource=apply"
    )
    de_url = (
        "https://careers.capgemini.com/job/Berlin-Dateningenieur/1390944733/"
        "?feedId=388633&utm_source=CareerSite&tcsource=apply"
    )
    ordered = [
        (
            de_url,
            DiscoveredJob(
                url=de_url,
                title="Dateningenieur",
                description="<p>Deutsche Beschreibung</p>",
                locations=["Berlin"],
                metadata={"provider_ref": "473205-de_DE"},
            ),
        ),
        (
            en_tracked_url,
            DiscoveredJob(
                url=en_tracked_url,
                title="Tracked data engineer",
                description="<p>Tracked English description</p>",
                locations=["Amsterdam"],
                metadata={"provider_ref": "473205-en_GB"},
            ),
        ),
        (
            en_url,
            DiscoveredJob(
                url=en_url,
                title="Data Engineer",
                description="<p>English description</p>",
                locations=["Amsterdam"],
                metadata={"provider_ref": "473205-en_GB"},
            ),
        ),
    ]
    if reverse:
        ordered.reverse()

    result = _canonicalize_rich("capgemini-global", dict(ordered))

    assert result.jobs_by_url is not None
    selected = result.jobs_by_url["https://careers.capgemini.com/job/-/1390944733/"]
    assert selected.title == "Data Engineer"
    assert selected.description == "<p>English description</p>"
    assert selected.locations == ["Amsterdam"]
    assert selected.metadata == {"provider_ref": "473205-en_GB"}


def test_global_distinct_provider_posting_ids_remain_distinct() -> None:
    urls = {
        "https://careers.capgemini.com/job/Role-One/1390944733/",
        "https://careers.capgemini.com/job/Role-Two/1390944734/",
    }

    assert _canonicalize("capgemini-global", urls).urls == {
        "https://careers.capgemini.com/job/-/1390944733/",
        "https://careers.capgemini.com/job/-/1390944734/",
    }


def test_global_greenhouse_identity_is_already_stable() -> None:
    url = "https://job-boards.eu.greenhouse.io/capgeminideutschlandgmbh/jobs/4100659101"

    assert _canonicalize("capgemini-global", {url}).urls == {url}


def test_global_identity_contract_drops_untrusted_apply_host() -> None:
    result = _canonicalize(
        "capgemini-global",
        {"https://evil.example/job/Injected/1390944733/"},
    )

    assert result.urls == set()
    assert result.security_filtered_count == 1


def test_frog_title_and_location_suffix_churn_collapses_to_page_id() -> None:
    aliases = {
        ("https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7-london-brand-422152"),
        (
            "https://www.frog.co/careers/jobs/"
            "699c95a6939f64fc32ab74e7-changed-location-changed-title"
        ),
    }

    assert _canonicalize("capgemini-frog", aliases).urls == {
        "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7"
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_frog_rich_alias_selection_is_order_independent(reverse: bool) -> None:
    london_url = "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7-london-brand-strategy"
    paris_url = (
        "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7-paris-strategie-de-marque"
    )
    ordered = [
        (
            paris_url,
            DiscoveredJob(
                url=paris_url,
                title="Stratège de marque",
                description="<p>Description française</p>",
                locations=["Paris"],
            ),
        ),
        (
            london_url,
            DiscoveredJob(
                url=london_url,
                title="Brand strategist",
                description="<p>English description</p>",
                locations=["London"],
            ),
        ),
    ]
    if reverse:
        ordered.reverse()

    result = _canonicalize_rich("capgemini-frog", dict(ordered))

    assert result.jobs_by_url is not None
    selected = result.jobs_by_url["https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7"]
    assert selected.title == "Brand strategist"
    assert selected.description == "<p>English description</p>"
    assert selected.locations == ["London"]


@pytest.mark.parametrize(
    ("slug", "source_url", "forged_canonical"),
    [
        (
            "capgemini-global",
            "https://careers.capgemini.com/job/Original/1390944733/",
            "https://careers.capgemini.com/job/-/1390944734/",
        ),
        (
            "capgemini-frog",
            "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7-original",
            "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e8",
        ),
    ],
)
def test_collision_identity_rejects_forged_canonical_provider_id(
    slug: str,
    source_url: str,
    forged_canonical: str,
) -> None:
    config = _config(slug)
    config["url_transform"]["replace"] = forged_canonical
    job = DiscoveredJob(url=source_url, title="Untrusted")
    result = _apply_url_allowlist(
        MonitorResult(urls={source_url}, jobs_by_url={source_url: job}),
        config,
    )

    with pytest.raises(
        ValueError,
        match="provider identity does not match canonical URL",
    ):
        _apply_url_transform(result, config)
