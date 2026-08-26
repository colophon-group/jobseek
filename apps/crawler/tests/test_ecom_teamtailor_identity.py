"""Stable Teamtailor identity and bounded ECOM alias-retirement contracts."""

from __future__ import annotations

import csv
import importlib
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.cli import parse_args
from src.core.monitor import (
    MonitorResult,
    _apply_url_allowlist,
    _apply_url_transform,
    monitor_one,
)
from src.core.monitors import DiscoveredJob
from src.ecom_teamtailor_cutover import (
    _migration_sql,
    apply_ecom_teamtailor_cutover,
    ecom_teamtailor_cutover_state,
    rollback_ecom_teamtailor_cutover,
)
from src.processing.board import (
    _ECOM_CANONICAL_URL_PATTERN,
    _ECOM_IDENTITY_MIGRATION,
    _ECOM_IDENTITY_MIGRATION_CONTRACT,
    _ECOM_IDENTITY_MIGRATION_MAX_ROWS,
    _ECOM_IDENTITY_MIGRATION_VERSION,
    _ECOM_LEGACY_URL_PATTERN,
    _ensure_ecom_identity_cutover_receipt,
    _identity_migration_canonical_url_pattern,
    _retire_canonicalized_provider_identities,
)
from src.sync import _monitor_config_fingerprint

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_SLUG, _BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _ECOM_IDENTITY_MIGRATION_CONTRACT
_CURRENT_HOST_COUNTS = {
    "careerslatam.ecomtrading.com": 10,
    "careerswestafrica.ecomtrading.com": 17,
    "careersasiapacific.ecomtrading.com": 3,
    "careersbrazil.ecomtrading.com": 4,
    "careersmexico.ecomtrading.com": 1,
    "ecomeurope.teamtailor.com": 9,
}


def _board() -> tuple[dict[str, str], dict]:
    with _BOARDS.open(newline="") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == _BOARD_SLUG)
    return row, json.loads(row["monitor_config"])


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _ECOM_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _FINGERPRINT,
        "recent_discovered_counts": [44, 44, 44],
    }
    metadata.update(overrides)
    return metadata


def _canonical_urls(count: int = 44) -> set[str]:
    urls: list[str] = []
    job_id = 8_000_000
    for host, host_count in _CURRENT_HOST_COUNTS.items():
        for _ in range(host_count):
            urls.append(f"https://{host}/jobs/{job_id}")
            job_id += 1
    return set(urls[:count])


def _row(**overrides) -> dict:
    row = {
        "active": 89,
        "legacy": 45,
        "canonical": 44,
        "unknown": 0,
        "discovered": 44,
        "validated": 44,
        "retired": 45,
        "receipt_written": True,
        "existing_receipt": None,
    }
    row.update(overrides)
    return row


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "ecom-board-id",
        "company_id": "ecom-company-id",
        "board_slug": _BOARD_SLUG,
        "board_url": _BOARD_URL,
        "crawler_type": _CRAWLER_TYPE,
        "monitor_start_ts": "2026-08-26T12:00:00+00:00",
        "metadata": _metadata(),
        "discovered": 44,
        "canonical_urls": _canonical_urls(),
        "truncated": False,
        "extraction_filtered": 0,
        "security_filtered": 0,
        "processing_filtered": 0,
        "all_canonical": True,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    return await _retire_canonicalized_provider_identities(conn, **kwargs), kwargs["board_log"]


async def test_ecom_board_dispatcher_accepts_all_44_regional_jobs_and_keeps_fetchable_hosts() -> (
    None
):
    row, metadata = _board()

    assert row["company_slug"] == "ecom-agroindustrial"
    assert (row["board_url"], row["monitor_type"]) == (_BOARD_URL, _CRAWLER_TYPE)
    assert metadata["identity_migration"] == _ECOM_IDENTITY_MIGRATION
    assert (
        _monitor_config_fingerprint(row["board_url"], row["monitor_type"], metadata) == _FINGERPRINT
    )

    items: list[str] = []
    expected: set[str] = set()
    job_id = 8_000_000
    for host, count in _CURRENT_HOST_COUNTS.items():
        for _ in range(count):
            source = f"https://{host}/jobs/{job_id}-regional-title"
            expected.add(f"https://{host}/jobs/{job_id}")
            items.append(f"<item><title>Job {job_id}</title><link>{source}</link></item>")
            job_id += 1
    feed = "<?xml version='1.0'?><rss><channel>" + "".join(items) + "</channel></rss>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/jobs.rss"
        return httpx.Response(200, text=feed)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await monitor_one(_BOARD_URL, _CRAWLER_TYPE, metadata, client)

    assert len(result.urls) == 44
    assert result.urls == expected
    assert result.security_filtered_count == 0
    assert all("ecomtradinggroup.teamtailor.com" not in url for url in result.urls)


def test_ecom_locale_and_title_variants_collapse_without_changing_regional_host() -> None:
    _, metadata = _board()
    variants = {
        "https://ecomeurope.teamtailor.com/de/jobs/7769137-accounts-executive",
        "https://ecomeurope.teamtailor.com/fr/jobs/7769137-responsable-comptes",
        "https://ecomeurope.teamtailor.com/it/jobs/7769137-account-executive",
        "https://ecomeurope.teamtailor.com/en/jobs/7769137-assistant-finance-manager",
        "https://careerseurope.ecomtrading.com/jobs/7769137-old-title",
    }
    filtered = _apply_url_allowlist(
        MonitorResult(urls=variants),
        {"url_allowlist": metadata["url_allowlist"]},
    )
    transformed = _apply_url_transform(filtered, metadata)

    assert transformed.urls == {"https://ecomeurope.teamtailor.com/jobs/7769137"}
    assert transformed.security_filtered_count == 0


async def test_ecom_rich_locale_alias_selection_is_stable_across_feed_order() -> None:
    row, metadata = _board()
    variants = [
        (
            "German title",
            "https://ecomeurope.teamtailor.com/de/jobs/7769137-deutscher-titel",
        ),
        (
            "French title",
            "https://ecomeurope.teamtailor.com/fr/jobs/7769137-titre-francais",
        ),
        (
            "Italian title",
            "https://ecomeurope.teamtailor.com/it/jobs/7769137-titolo-italiano",
        ),
        (
            "Old English title",
            "https://careerseurope.ecomtrading.com/en/jobs/7769137-old-title",
        ),
        (
            "Current English title",
            "https://ecomeurope.teamtailor.com/en/jobs/7769137-current-title",
        ),
    ]

    async def dispatch(items: list[tuple[str, str]]) -> MonitorResult:
        feed = (
            "<?xml version='1.0'?><rss><channel>"
            + "".join(
                "<item>"
                f"<title>{title}</title>"
                f"<description>{title} body</description>"
                f"<link>{url}</link>"
                f"<guid>guid-{title.split()[0].lower()}</guid>"
                "</item>"
                for title, url in items
            )
            + "</channel></rss>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/jobs.rss"
            return httpx.Response(200, text=feed)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await monitor_one(
                row["board_url"],
                row["monitor_type"],
                metadata,
                client,
            )

    forward = await dispatch(variants)
    reverse = await dispatch(list(reversed(variants)))

    assert forward == reverse
    assert forward.urls == {"https://ecomeurope.teamtailor.com/jobs/7769137"}
    assert forward.jobs_by_url is not None
    selected = forward.jobs_by_url["https://ecomeurope.teamtailor.com/jobs/7769137"]
    assert selected.title == "Current English title"
    assert selected.description == "Current English title body"
    assert selected.metadata == {"id": "guid-current"}


def test_ecom_rich_collision_rejects_mismatched_provider_identity() -> None:
    _, metadata = _board()
    tampered_metadata = json.loads(json.dumps(metadata))
    tampered_metadata["url_transform"]["steps"][1]["replace"] = (
        "https://ecomeurope.teamtailor.com/jobs/9999999"
    )
    source_url = "https://ecomeurope.teamtailor.com/en/jobs/7769137-current-title"
    result = MonitorResult(
        urls={source_url},
        jobs_by_url={
            source_url: DiscoveredJob(
                url=source_url,
                title="Injected identity",
            )
        },
    )

    with pytest.raises(
        ValueError,
        match="provider identity does not match canonical URL",
    ):
        _apply_url_transform(result, tampered_metadata)


def test_ecom_global_host_is_rejected_instead_of_creating_404_identities() -> None:
    _, metadata = _board()
    source = "https://ecomtradinggroup.teamtailor.com/jobs/7769137-current-title"

    filtered = _apply_url_allowlist(
        MonitorResult(urls={source}),
        {"url_allowlist": metadata["url_allowlist"]},
    )

    assert filtered.urls == set()
    assert filtered.security_filtered_count == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://careerswestafrica.ecomtrading.com/jobs/7769137-accounts-executive",
        "https://ecomeurope.teamtailor.com/de/jobs/7769137-finanzmanager",
        "https://careerseurope.ecomtrading.com/jobs/7769137-old-title",
        "https://careerseurope.ecomtrading.com/jobs/7769137",
    ],
)
def test_legacy_contract_covers_only_ecom_title_alias_namespace(url: str) -> None:
    assert re.fullmatch(_ECOM_LEGACY_URL_PATTERN, url)


def test_ecom_canonical_contract_is_title_free_numeric_provider_identity() -> None:
    assert re.fullmatch(
        _ECOM_CANONICAL_URL_PATTERN,
        "https://careerswestafrica.ecomtrading.com/jobs/7769137",
    )
    for invalid in (
        "https://careerswestafrica.ecomtrading.com/jobs/7769137-title",
        "https://careerseurope.ecomtrading.com/jobs/7769137",
        "https://ecomtradinggroup.teamtailor.com/jobs/7769137",
        "https://evil.example/jobs/7769137",
        "https://ecomeurope.teamtailor.com/jobs/not-numeric",
        "https://ecomeurope.teamtailor.com/jobs/7769137?source=bad",
    ):
        assert re.fullmatch(_ECOM_CANONICAL_URL_PATTERN, invalid) is None

    assert (
        _identity_migration_canonical_url_pattern(_ECOM_IDENTITY_MIGRATION)
        == _ECOM_CANONICAL_URL_PATTERN
    )


async def test_post_discovery_ecom_lane_requires_pre_discovery_rollback_receipt() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, board_log = await _run(conn)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()
    board_log.warning.assert_called_once_with(
        "batch.monitor.ecom_identity_receipt_missing_before_discovery"
    )


def _rollback_receipt() -> dict:
    return {
        "id": _ECOM_IDENTITY_MIGRATION,
        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
        "completed_at": "2026-08-26T12:00:00+00:00",
        "retired_count": 1,
        "rollback_rows": [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "source_url": ("https://careerswestafrica.ecomtrading.com/jobs/7769137-old-title"),
                "is_active": False,
                "missing_count": 4,
                "next_scrape_at": None,
            }
        ],
    }


async def test_runtime_recovery_runs_exact_ecom_revision_before_discovery(monkeypatch) -> None:
    connection = AsyncMock()
    connection.fetchval.return_value = {
        **_metadata(),
        "_identity_migration_receipt": _rollback_receipt(),
    }
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction = MagicMock(return_value=transaction)
    acquire = AsyncMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    apply = AsyncMock(return_value="DO")
    monkeypatch.setattr("src.ecom_teamtailor_cutover.apply_ecom_teamtailor_cutover", apply)
    board_log = MagicMock()

    refreshed = await _ensure_ecom_identity_cutover_receipt(
        pool,
        board_id="ecom-board-id",
        company_id="ecom-company-id",
        board_slug=_BOARD_SLUG,
        board_url=_BOARD_URL,
        crawler_type=_CRAWLER_TYPE,
        metadata=_metadata(),
        board_log=board_log,
    )

    assert refreshed["_identity_migration_receipt"] == _rollback_receipt()
    apply.assert_awaited_once_with(connection)
    connection.fetchval.assert_awaited_once()
    board_log.info.assert_called_once_with("batch.monitor.ecom_identity_recovery_completed")


async def test_runtime_recovery_fails_closed_without_exact_ecom_rollback_receipt(
    monkeypatch,
) -> None:
    connection = AsyncMock()
    connection.fetchval.return_value = _metadata()
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction = MagicMock(return_value=transaction)
    acquire = AsyncMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    monkeypatch.setattr(
        "src.ecom_teamtailor_cutover.apply_ecom_teamtailor_cutover", AsyncMock(return_value="DO")
    )

    with pytest.raises(RuntimeError, match="did not produce an exact rollback receipt"):
        await _ensure_ecom_identity_cutover_receipt(
            pool,
            board_id="ecom-board-id",
            company_id="ecom-company-id",
            board_slug=_BOARD_SLUG,
            board_url=_BOARD_URL,
            crawler_type=_CRAWLER_TYPE,
            metadata=_metadata(),
            board_log=MagicMock(),
        )


async def test_runtime_recovery_fails_before_discovery_on_copied_or_stale_contract() -> None:
    pool = MagicMock()
    board_log = MagicMock()

    with pytest.raises(RuntimeError, match="mismatched board contract"):
        await _ensure_ecom_identity_cutover_receipt(
            pool,
            board_id="ecom-board-id",
            company_id="ecom-company-id",
            board_slug=_BOARD_SLUG,
            board_url=_BOARD_URL,
            crawler_type=_CRAWLER_TYPE,
            metadata=_metadata(_monitor_config_fingerprint="stale"),
            board_log=board_log,
        )

    pool.acquire.assert_not_called()
    board_log.warning.assert_called_once_with(
        "batch.monitor.ecom_identity_recovery_contract_mismatch"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"board_slug": "another-board"},
        {"board_url": "https://evil.example/jobs"},
        {"crawler_type": "dom"},
        {"metadata": _metadata(_monitor_config_fingerprint="wrong")},
        {"metadata": _metadata(identity_migration="ecom-teamtailor-stable-id-v2")},
    ],
)
async def test_copied_ecom_marker_or_wrong_contract_never_enters_sql(overrides) -> None:
    conn = AsyncMock()

    retired, _ = await _run(conn, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()


async def test_ecom_receipt_is_bounded_and_makes_replay_a_permanent_noop() -> None:
    conn = AsyncMock()
    receipt = {
        "id": _ECOM_IDENTITY_MIGRATION,
        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
        "completed_at": "2026-08-26T12:00:00+00:00",
        "retired_count": 45,
        "rollback_rows": [],
    }
    metadata = _metadata(_identity_migration_receipt=receipt)

    first, _ = await _run(conn, metadata=metadata)
    second, _ = await _run(conn, metadata=metadata)

    assert (first, second) == (0, 0)
    conn.fetchrow.assert_not_awaited()


def test_revision_0022_is_exact_bounded_receipt_backed_and_reversible() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )

    assert (migration.revision, migration.down_revision) == ("0022", "0021")
    assert migration._MIGRATION_ID == _ECOM_IDENTITY_MIGRATION
    assert migration._CONFIG_FINGERPRINT == _FINGERPRINT
    assert migration._LEGACY_PATTERN == _ECOM_LEGACY_URL_PATTERN
    assert migration._CANONICAL_PATTERN == _ECOM_CANONICAL_URL_PATTERN
    assert migration._MAX_ROWS == _ECOM_IDENTITY_MIGRATION_MAX_ROWS

    forward = migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES
    rollback = migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES
    for sql in (forward, rollback):
        assert "company.slug = 'ecom-agroindustrial'" in sql
        assert "board.board_slug = 'ecom-agroindustrial-global'" in sql
        assert "_identity_migration_receipt" in sql
        assert "rollback_rows" in sql
    assert "unknown active source identities" in forward
    assert "foreign canonical URL ownership" in forward
    assert "canonical/legacy row collisions" in forward
    assert "candidate_count > 100" in forward
    assert "row_number() OVER" in forward
    assert "SET source_url = candidates.canonical_url" in forward
    assert "THEN 'ecomeurope.teamtailor.com'" in forward
    assert "_monitor_config_fingerprint" in forward
    assert "metadata - '_identity_migration_receipt'" in rollback
    assert "occupied legacy identities" in rollback

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    assert execute.call_args_list[0].args == (forward,)
    assert execute.call_args_list[1].args == (rollback,)


def test_revision_0022_sql_has_no_accidental_sqlalchemy_bind_parameters() -> None:
    from sqlalchemy import text

    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )

    assert not text(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)._bindparams
    assert not text(migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES)._bindparams


@pytest.mark.parametrize(
    "command",
    [
        "repair-ecom-teamtailor-cutover",
        "rollback-ecom-teamtailor-cutover",
        "ecom-teamtailor-cutover-state",
    ],
)
def test_ecom_cutover_commands_have_no_unbounded_arguments(monkeypatch, command) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", command])
    assert vars(parse_args()) == {"command": command}


async def test_ecom_cutover_hooks_reuse_exact_revision_sql() -> None:
    connection = AsyncMock()
    connection.execute.return_value = "DO"

    assert await apply_ecom_teamtailor_cutover(connection) == "DO"
    assert await rollback_ecom_teamtailor_cutover(connection) == "DO"

    assert connection.execute.await_args_list[0].args == (_migration_sql(),)
    assert connection.execute.await_args_list[1].args == (_migration_sql(rollback=True),)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "absent"),
        ([{"receipt": None}], "pending"),
        (
            [{"receipt": _rollback_receipt()}],
            "complete",
        ),
    ],
)
async def test_ecom_cutover_state_assigns_rollback_to_only_pending_deploy(rows, expected) -> None:
    connection = AsyncMock()
    connection.fetch.return_value = rows

    assert await ecom_teamtailor_cutover_state(connection) == expected


async def test_ecom_cutover_state_rejects_ambiguous_or_mismatched_receipts() -> None:
    connection = AsyncMock()
    connection.fetch.return_value = [{"receipt": None}, {"receipt": None}]
    with pytest.raises(RuntimeError, match="ambiguous"):
        await ecom_teamtailor_cutover_state(connection)

    connection.fetch.return_value = [{"receipt": {"id": "other"}}]
    with pytest.raises(RuntimeError, match="mismatched"):
        await ecom_teamtailor_cutover_state(connection)
