"""Reviewed ECOM Teamtailor inventory and migration contracts."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.core.monitors import DiscoveredJob
from src.processing.board import (
    _ECOM_CANONICAL_URL_PATTERN,
    _ECOM_IDENTITY_MIGRATION,
    _ECOM_IDENTITY_MIGRATION_BOARD_SLUG,
    _ECOM_IDENTITY_MIGRATION_CONTRACT,
    _ECOM_IDENTITY_MIGRATION_VERSION,
    _ECOM_LEGACY_URL_PATTERN,
    _IDENTITY_MIGRATION_MAX_ROWS,
    _retire_canonicalized_provider_identities,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES
from src.sync import _monitor_config_fingerprint

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _ECOM_IDENTITY_MIGRATION_CONTRACT


def _config() -> dict:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == _ECOM_IDENTITY_MIGRATION_BOARD_SLUG
        )
    return json.loads(row["monitor_config"])


def _canonical_urls() -> set[str]:
    return {
        "https://ecomlatam.teamtailor.com/jobs/8224594",
        "https://ecomwestafrica.teamtailor.com/jobs/8218917",
        "https://ecomasiapacific.teamtailor.com/jobs/8173261",
        "https://ecomeurope.teamtailor.com/jobs/8120504",
        "https://ecombrazil.teamtailor.com/jobs/8094410",
        "https://ecommexico.teamtailor.com/jobs/7993821",
    }


def test_config_is_bound_to_the_reviewed_current_tenant_contract() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == _ECOM_IDENTITY_MIGRATION_BOARD_SLUG
        )
    config = json.loads(row["monitor_config"])

    assert row["board_url"] == _BOARD_URL
    assert row["monitor_type"] == _CRAWLER_TYPE
    assert config["identity_migration"] == _ECOM_IDENTITY_MIGRATION
    assert config["feed_url"] == "https://ecomtradinggroup.teamtailor.com/jobs.rss"
    assert (
        _monitor_config_fingerprint(row["board_url"], row["monitor_type"], config) == _FINGERPRINT
    )


def test_locale_and_retired_host_aliases_collapse_to_current_numeric_url() -> None:
    config = _config()
    aliases = [
        "https://ecomlatam.teamtailor.com/fr/jobs/8224594-techniciens",
        "https://careerslatam.ecomtrading.com/en/jobs/8224594-field-technicians",
        "https://ecomlatam.teamtailor.com/en/jobs/8224594-field-technicians",
    ]
    jobs = {
        url: DiscoveredJob(url=url, title=url.rsplit("/", 1)[-1], metadata={"id": "guid"})
        for url in aliases
    }
    result = MonitorResult(urls=set(aliases), jobs_by_url=jobs)

    allowed = _apply_url_allowlist(result, config)
    transformed = _apply_url_transform(allowed, config)

    assert transformed.urls == {"https://ecomlatam.teamtailor.com/jobs/8224594"}
    assert transformed.jobs_by_url is not None
    assert transformed.jobs_by_url[next(iter(transformed.urls))].url == next(iter(transformed.urls))


def test_provider_boundary_rejects_unreviewed_teamtailor_hosts() -> None:
    config = _config()
    url = "https://unrelated.teamtailor.com/jobs/8224594-field-technicians"
    result = MonitorResult(
        urls={url},
        jobs_by_url={url: DiscoveredJob(url=url, title="Foreign")},
    )

    allowed = _apply_url_allowlist(result, config)

    assert allowed.urls == set()
    assert allowed.security_filtered_count == 1


def test_legacy_and_canonical_patterns_are_disjoint_and_host_bounded() -> None:
    legacy = [
        "https://careerswestafrica.ecomtrading.com/jobs/8218917-field-officer",
        "https://ecomeurope.teamtailor.com/fr/jobs/8120504-controleur",
        "https://ecombrazil.teamtailor.com/jobs/8094410-analista",
    ]
    for url in legacy:
        assert re.fullmatch(_ECOM_LEGACY_URL_PATTERN, url)
        assert re.fullmatch(_ECOM_CANONICAL_URL_PATTERN, url) is None
    for url in _canonical_urls():
        assert re.fullmatch(_ECOM_CANONICAL_URL_PATTERN, url)
        assert re.fullmatch(_ECOM_LEGACY_URL_PATTERN, url) is None
    assert (
        re.fullmatch(
            _ECOM_LEGACY_URL_PATTERN,
            "https://unrelated.teamtailor.com/jobs/8224594-role",
        )
        is None
    )


async def test_healthy_canonical_inventory_retires_only_bounded_legacy_rows() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "active": 49,
        "legacy": 43,
        "canonical": 6,
        "unknown": 0,
        "discovered": 6,
        "validated": 6,
        "retired": 43,
        "receipt_written": True,
        "existing_receipt": None,
    }
    board_log = MagicMock()

    retired = await _retire_canonicalized_provider_identities(
        conn,
        board_id="board-id",
        company_id="company-id",
        board_slug=_ECOM_IDENTITY_MIGRATION_BOARD_SLUG,
        board_url=_BOARD_URL,
        crawler_type=_CRAWLER_TYPE,
        monitor_start_ts="2026-08-27T09:00:00+00:00",
        metadata={
            "identity_migration": _ECOM_IDENTITY_MIGRATION,
            "_monitor_config_fingerprint": _FINGERPRINT,
            "recent_discovered_counts": [6, 6, 6],
        },
        discovered=6,
        canonical_urls=_canonical_urls(),
        truncated=False,
        extraction_filtered=0,
        security_filtered=0,
        processing_filtered=0,
        all_canonical=True,
        board_log=board_log,
    )

    assert retired == 43
    args = conn.fetchrow.await_args.args
    assert args[:5] == (
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        "board-id",
        "company-id",
        "2026-08-27T09:00:00+00:00",
        _IDENTITY_MIGRATION_MAX_ROWS,
    )
    assert set(args[5]) == _canonical_urls()
    assert args[6:8] == (_ECOM_LEGACY_URL_PATTERN, _ECOM_CANONICAL_URL_PATTERN)
    assert json.loads(args[8]) == {
        "id": _ECOM_IDENTITY_MIGRATION,
        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
    }
    assert args[9:] == ("ecom-agroindustrial", False)
