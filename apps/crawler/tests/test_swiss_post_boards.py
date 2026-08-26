"""Identity split contracts for Swiss Post and PostFinance boards."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from src.core.monitor import (
    MonitorResult,
    _apply_job_filter,
    _apply_url_allowlist,
    _apply_url_transform,
)
from src.core.monitors import DiscoveredJob
from src.processing.board import (
    _POSTFINANCE_CANONICAL_URL_PATTERN,
    _POSTFINANCE_IDENTITY_MIGRATION,
)

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_POSTFINANCE_PATTERN = "(?i)PostFinance"


def _company_rows(company_slug: str) -> list[dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["company_slug"] == company_slug]


def _config(company_slug: str) -> dict:
    rows = _company_rows(company_slug)
    assert len(rows) == 1
    return json.loads(rows[0]["monitor_config"])


def _canonicalize(company_slug: str, jobs: list[DiscoveredJob]) -> MonitorResult:
    config = _config(company_slug)
    source = MonitorResult(
        urls={job.url for job in jobs},
        jobs_by_url={job.url: job for job in jobs},
    )
    allowed = _apply_url_allowlist(source, config)
    return _apply_url_transform(allowed, config)


def test_registry_uses_one_complementary_feed_board_per_company():
    swiss_post = _company_rows("swiss-post")
    postfinance = _company_rows("postfinance")

    assert len(swiss_post) == len(postfinance) == 1
    swiss_post_config = json.loads(swiss_post[0]["monitor_config"])
    postfinance_config = json.loads(postfinance[0]["monitor_config"])

    assert swiss_post[0]["monitor_type"] == postfinance[0]["monitor_type"] == "rss"
    assert swiss_post_config["job_filter"] == {"exclude": _POSTFINANCE_PATTERN}
    assert postfinance_config["job_filter"] == {"include": _POSTFINANCE_PATTERN}
    assert swiss_post_config["feed_url"] == "https://job.post.ch/googlefeed.xml"
    assert postfinance_config["feed_url"] == "https://jobs.postfinance.ch/googlefeed.xml"
    assert postfinance[0]["board_url"].startswith("https://jobs.postfinance.ch/")
    assert "identity_migration" not in swiss_post_config
    assert postfinance_config["identity_migration"] == _POSTFINANCE_IDENTITY_MIGRATION


def test_complementary_filters_partition_rich_jobs_without_overlap():
    swiss_url = "https://job.post.ch/PostKG/job/Bern-Engineer/1/"
    postfinance_url = "https://job.post.ch/PostFinance/job/Bern-Analyst/2/"
    jobs = {
        swiss_url: DiscoveredJob(
            url=swiss_url,
            title="Engineer",
            description="Build logistics systems",
            metadata={"employer": "Post CH AG"},
        ),
        postfinance_url: DiscoveredJob(
            url=postfinance_url,
            title="Analyst",
            description="Support PostFinance customers",
            metadata={"employer": "PostFinance AG"},
        ),
    }
    source = MonitorResult(urls=set(jobs), jobs_by_url=jobs)

    swiss_post = _apply_job_filter(
        source,
        {"job_filter": {"exclude": _POSTFINANCE_PATTERN}},
    )
    postfinance = _apply_job_filter(
        source,
        {"job_filter": {"include": _POSTFINANCE_PATTERN}},
    )

    assert swiss_post.urls == {swiss_url}
    assert postfinance.urls == {postfinance_url}
    assert not swiss_post.urls & postfinance.urls
    assert swiss_post.urls | postfinance.urls == source.urls


@pytest.mark.parametrize(
    ("company_slug", "canonical_host", "preferred_title"),
    [
        ("swiss-post", "job.post.ch", "PostKG German"),
        ("postfinance", "jobs.postfinance.ch", "PostFinance German"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_locale_and_title_aliases_converge_to_one_stable_provider_identity(
    company_slug,
    canonical_host,
    preferred_title,
    reverse,
):
    provider_id = "1426471633"
    variants = [
        DiscoveredJob(
            url=f"https://job.post.ch/job/English-title/{provider_id}/",
            title="Generic English",
            metadata={"id": provider_id},
        ),
        DiscoveredJob(
            url=f"https://job.post.ch/PostKG/job/Deutscher-Titel/{provider_id}/",
            title="PostKG German",
            metadata={"id": provider_id},
        ),
        DiscoveredJob(
            url=f"https://job.post.ch/PostFinance/job/Deutscher-PF-Titel/{provider_id}/",
            title="PostFinance German",
            metadata={"id": provider_id},
        ),
        DiscoveredJob(
            url=f"https://job.post.ch/job/Titolo-italiano/{provider_id}/",
            title="Generic Italian",
            metadata={"id": provider_id},
        ),
    ]
    if reverse:
        variants.reverse()

    result = _canonicalize(company_slug, variants)
    canonical = f"https://{canonical_host}/job/_/{provider_id}/"

    assert result.security_filtered_count == 0
    assert result.urls == {canonical}
    assert result.jobs_by_url is not None
    assert result.jobs_by_url[canonical].url == canonical
    assert result.jobs_by_url[canonical].title == preferred_title


@pytest.mark.parametrize("company_slug", ["swiss-post", "postfinance"])
def test_identity_boundary_rejects_foreign_canonical_and_malformed_urls(company_slug):
    invalid = [
        "https://evil.example/job/Title/1426471633/",
        "https://job.post.ch/job/_/1426471633/",
        "https://job.post.ch/job/Title/not-numeric/",
        "https://job.post.ch/job/1426471633/",
        "https://job.post.ch/job/Title/1426471633/?locale=fr_FR",
    ]
    jobs = [
        DiscoveredJob(url=url, title="Invalid", metadata={"id": "1426471633"}) for url in invalid
    ]

    result = _canonicalize(company_slug, jobs)

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == len(invalid)


def test_provider_metadata_must_match_transformed_identity():
    source = DiscoveredJob(
        url="https://job.post.ch/PostFinance/job/Analyst/1426471633/",
        title="Analyst",
        metadata={"id": "wrong"},
    )

    with pytest.raises(ValueError, match="provider identity"):
        _canonicalize("postfinance", [source])


def test_postfinance_canonical_shape_is_stable_host_and_numeric_guid():
    assert re.fullmatch(
        _POSTFINANCE_CANONICAL_URL_PATTERN,
        "https://jobs.postfinance.ch/job/_/1426471633/",
    )
    for invalid in (
        "https://job.post.ch/job/_/1426471633/",
        "https://jobs.postfinance.ch/job/Title/1426471633/",
        "https://jobs.postfinance.ch/job/_/not-numeric/",
        "https://jobs.postfinance.ch/job/_/1426471633/?locale=de_DE",
    ):
        assert re.fullmatch(_POSTFINANCE_CANONICAL_URL_PATTERN, invalid) is None
