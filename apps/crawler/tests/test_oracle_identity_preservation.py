"""Stable source-identity contracts for Oracle's HCM catalogue switch."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitor import monitor_one
from src.core.monitors import DiscoveredJob
from src.core.scrapers.oracle_hcm import scrape
from src.queries.monitor import _DIFF_BATCH, _MARK_GONE_BY_TIMESTAMP

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_SLUG = "oracle-careers"
_BOARD_URL = "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_45001"
_RAW_PREFIX = f"{_BOARD_URL}/job/"
_LEGACY_PREFIX = "https://careers.oracle.com/en/job/"
_TRUSTED_HOST = "eeho.fa.us2.oraclecloud.com"
_TRUSTED_SITE = "CX_45001"
_URL_ALLOWLIST = (
    r"^https://eeho\.fa\.us2\.oraclecloud\.com/"
    r"hcmUI/CandidateExperience/en/sites/CX_45001/job/[0-9]+$"
)
_URL_TRANSFORM_FIND = (
    r"^https://eeho\.fa\.us2\.oraclecloud\.com/"
    r"hcmUI/CandidateExperience/en/sites/CX_45001/job/([0-9]+)$"
)


def _board_rows() -> list[dict[str, str]]:
    with _BOARDS.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _oracle_row() -> dict[str, str]:
    rows = [row for row in _board_rows() if row["company_slug"] == "oracle"]
    assert len(rows) == 1
    return rows[0]


def _monitor_config() -> dict:
    return json.loads(_oracle_row()["monitor_config"])


def _scraper_config() -> dict:
    return json.loads(_oracle_row()["scraper_config"])


def test_oracle_switch_is_an_in_place_exact_identity_transform():
    row = _oracle_row()
    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])

    assert row["board_slug"] == _BOARD_SLUG
    assert row["board_url"] == _BOARD_URL
    assert row["monitor_type"] == row["scraper_type"] == "oracle_hcm"
    assert monitor_config == {
        "total_count_tolerance": 5,
        "page_shortfall_tolerance": 3,
        "url_allowlist": _URL_ALLOWLIST,
        "url_transform": {
            "find": _URL_TRANSFORM_FIND,
            "replace": r"https://careers.oracle.com/en/job/\1",
        },
    }
    assert "identity_migration" not in monitor_config
    assert scraper_config == {
        "enrich": ["description"],
        "host": _TRUSTED_HOST,
        "site": _TRUSTED_SITE,
    }


def test_trusted_scraper_tenant_pair_is_owned_only_by_exact_oracle_board():
    copied = []
    for row in _board_rows():
        config = json.loads(row["scraper_config"] or "{}")
        if config.get("host") == _TRUSTED_HOST or config.get("site") == _TRUSTED_SITE:
            copied.append((row["company_slug"], row["board_slug"], config))

    assert copied == [
        (
            "oracle",
            _BOARD_SLUG,
            {
                "enrich": ["description"],
                "host": _TRUSTED_HOST,
                "site": _TRUSTED_SITE,
            },
        )
    ]


@pytest.mark.parametrize("job_id", ["1", "342458", "999999999"])
async def test_dispatcher_maps_exact_hcm_id_to_existing_source_identity(job_id):
    raw_url = f"{_RAW_PREFIX}{job_id}"
    job = DiscoveredJob(url=raw_url, title=f"Oracle role {job_id}")

    async def discoverer(board, client, pw=None):
        return [job]

    with patch("src.core.monitor.get_discoverer", return_value=discoverer):
        result = await monitor_one(
            _BOARD_URL,
            "oracle_hcm",
            _monitor_config(),
            AsyncMock(spec=httpx.AsyncClient),
        )

    legacy_url = f"{_LEGACY_PREFIX}{job_id}"
    assert result.urls == {legacy_url}
    assert result.security_filtered_count == 0
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == {legacy_url}
    assert result.jobs_by_url[legacy_url].url == legacy_url
    assert result.jobs_by_url[legacy_url].title == f"Oracle role {job_id}"


@pytest.mark.parametrize(
    "source_url",
    [
        f"{_RAW_PREFIX}not-numeric",
        f"{_RAW_PREFIX}342458?source=oracle",
        f"{_RAW_PREFIX}342458#apply",
        f"{_BOARD_URL.replace('CX_45001', 'CX_45002')}/job/342458",
        f"{_BOARD_URL.replace('eeho.fa.us2', 'evil.fa.us2')}/job/342458",
        "https://careers.oracle.com/en/job/342458",
    ],
)
async def test_allowlist_rejects_every_nonexact_pretransform_source(source_url):
    async def discoverer(board, client, pw=None):
        return [DiscoveredJob(url=source_url, title="Untrusted role")]

    with patch("src.core.monitor.get_discoverer", return_value=discoverer):
        result = await monitor_one(
            _BOARD_URL,
            "oracle_hcm",
            _monitor_config(),
            AsyncMock(spec=httpx.AsyncClient),
        )

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == 1


async def test_allowlist_runs_before_transform_and_rejects_a_copied_tenant():
    trusted = DiscoveredJob(url=f"{_RAW_PREFIX}342458", title="Trusted")
    copied = DiscoveredJob(
        url=(
            "https://copied.fa.us2.oraclecloud.com/"
            "hcmUI/CandidateExperience/en/sites/CX_45001/job/342458"
        ),
        title="Copied",
    )

    async def discoverer(board, client, pw=None):
        return [trusted, copied]

    with patch("src.core.monitor.get_discoverer", return_value=discoverer):
        result = await monitor_one(
            _BOARD_URL,
            "oracle_hcm",
            _monitor_config(),
            AsyncMock(spec=httpx.AsyncClient),
        )

    assert result.urls == {f"{_LEGACY_PREFIX}342458"}
    assert result.security_filtered_count == 1
    assert result.jobs_by_url is not None
    assert result.jobs_by_url[f"{_LEGACY_PREFIX}342458"].title == "Trusted"


async def test_scraper_enriches_preserved_legacy_url_through_trusted_hcm_tenant():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "Id": "342458",
                        "Title": "Principal Software Engineer",
                        "PrimaryLocation": "Zurich, Switzerland",
                        "ExternalDescriptionStr": "<p>Build Oracle Cloud.</p>",
                        "ExternalQualificationsStr": "<p>Distributed systems</p>",
                        "ExternalResponsibilitiesStr": "<p>Lead delivery</p>",
                        "ExternalPostedStartDate": "2026-08-20",
                        "JobSchedule": "Full time",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            f"{_LEGACY_PREFIX}342458",
            _scraper_config(),
            client,
        )

    assert len(requested_urls) == 1
    request_url = requested_urls[0]
    assert request_url.startswith(f"https://{_TRUSTED_HOST}/hcmRestApi/")
    assert "siteNumber=CX_45001" in request_url
    assert "Id=%22342458%22" in request_url or 'Id="342458"' in request_url
    assert "careers.oracle.com" not in request_url
    assert content.title == "Principal Software Engineer"
    assert content.description == "<p>Build Oracle Cloud.</p>"


@pytest.mark.parametrize(
    "config",
    [
        {"host": "evil.example", "site": _TRUSTED_SITE},
        {"host": _TRUSTED_HOST, "site": "CX_45001,other"},
    ],
)
async def test_scraper_rejects_untrusted_copied_host_or_site(config):
    client = AsyncMock(spec=httpx.AsyncClient)

    with pytest.raises(ValueError, match="Oracle HCM scraper .* metadata is invalid"):
        await scrape(f"{_LEGACY_PREFIX}342458", config, client)

    client.get.assert_not_awaited()


def test_transformed_identity_hits_touched_diff_path_and_leaves_only_stale_ids_for_gone():
    diff_sql = " ".join(_DIFF_BATCH.split())
    gone_sql = " ".join(_MARK_GONE_BY_TIMESTAMP.split())

    assert "JOIN discovered d ON d.url = jp.source_url" in diff_sql
    assert "locked.board_id = $2 AND locked.is_active = true" in diff_sql
    assert "SELECT 'touched' AS action" in diff_sql
    assert (
        "new_urls AS ( SELECT d.url FROM discovered d WHERE NOT EXISTS "
        "( SELECT 1 FROM locked_existing locked WHERE locked.source_url = d.url ) )" in diff_sql
    )
    assert "WHERE board_id = $1 AND is_active = true AND last_seen_at < $2" in gone_sql


def test_exact_transform_is_one_to_one_for_reviewed_catalogue_scale():
    config = _monitor_config()["url_transform"]
    pattern = re.compile(config["find"])
    raw_urls = {f"{_RAW_PREFIX}{job_id}" for job_id in range(330_000, 332_162)}
    mapped = {pattern.sub(config["replace"], url) for url in raw_urls}

    assert len(raw_urls) == len(mapped) == 2_162
    assert mapped == {f"{_LEGACY_PREFIX}{job_id}" for job_id in range(330_000, 332_162)}
