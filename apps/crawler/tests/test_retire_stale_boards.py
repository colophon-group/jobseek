from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from src.cli import parse_args
from src.retire_stale_boards import (
    _COMPANY_BOARDS_QUERY,
    _COMPANY_QUERY,
    _QUERY,
    ProbeObservation,
    RetirementReport,
    RetirementSafetyError,
    VerifiedGoneCompany,
    ZeroBoardRegistryOrphan,
    build_retirement_report,
    classify_candidate,
    find_dead_companies,
    find_dead_company_boards,
    find_stale_boards,
    format_md,
    format_shell_snippets,
    load_registry,
    probe_registry_rows,
    report_stale_boards,
)

NOW = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)
FIRST_GONE = NOW - timedelta(hours=12)
LAST_GONE = NOW - timedelta(hours=6)


def _candidate(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "00000000-0000-0000-0000-000000000001",
        "company_id": "00000000-0000-0000-0000-000000000010",
        "company_slug": "acme",
        "company_name": "Acme Corp",
        "board_slug": "acme-greenhouse",
        "crawler_type": "greenhouse",
        "board_url": "https://job-boards.greenhouse.io/acme",
        "board_status": "gone",
        "is_enabled": True,
        "last_success_at": NOW - timedelta(days=30),
        "consecutive_failures": 3,
        "gone_at": LAST_GONE,
        "gone_confirmation_count": 2,
        "gone_first_confirmed_at": FIRST_GONE,
        "gone_last_confirmed_at": LAST_GONE,
        "last_gone_status": 404,
        "last_gone_endpoint": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "stale_days": 30.0,
        "active_postings": 0,
        "healthy_siblings": 1,
        "company_total_boards": 2,
        "company_live_boards": 1,
        "candidate_scope": "board",
    }
    base.update(overrides)
    return base


def _observation(**overrides: Any) -> ProbeObservation:
    base: dict[str, Any] = {
        "status": "fail",
        "probed_at": NOW,
        "endpoint_class": "greenhouse",
        "endpoint_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs",
        "message": "404 Not Found",
        "http_status": 404,
        "redirect_url": None,
        "job_count": None,
    }
    base.update(overrides)
    return ProbeObservation(**base)


def _registry_row(**overrides: str) -> dict[str, str]:
    row = {
        "company_slug": "acme",
        "board_slug": "acme-greenhouse",
        "board_url": "https://job-boards.greenhouse.io/acme",
        "monitor_type": "greenhouse",
        "monitor_config": json.dumps({"token": "acme"}),
        "scraper_type": "skip",
        "scraper_config": "",
    }
    row.update(overrides)
    return row


def _write_registry(path: Path, board_rows: list[dict[str, str]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    board_header = (
        "company_slug,board_slug,board_url,monitor_type,monitor_config,"
        "scraper_type,scraper_config\n"
    )
    board_lines = []
    for row in board_rows:
        config = row["monitor_config"].replace('"', '""')
        board_lines.append(
            f"{row['company_slug']},{row['board_slug']},{row['board_url']},"
            f'{row["monitor_type"]},"{config}",{row["scraper_type"]},'
            f"{row['scraper_config']}"
        )
    (path / "boards.csv").write_text(board_header + "\n".join(board_lines) + "\n", encoding="utf-8")
    configured = sorted({row["company_slug"] for row in board_rows})
    company_lines = [f"{slug},{slug.title()},https://{slug}.example" for slug in configured]
    company_lines.extend(
        [
            "banco-bradesco,Banco Bradesco,https://banco.example",
            "krea,Krea,https://krea.example",
        ]
    )
    (path / "companies.csv").write_text(
        "slug,name,website\n" + "\n".join(company_lines) + "\n",
        encoding="utf-8",
    )


class _StubConn:
    def __init__(
        self,
        board_rows: list[dict[str, Any]] | None = None,
        company_rows: list[dict[str, Any]] | None = None,
        company_board_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.board_rows = board_rows or []
        self.company_rows = company_rows or []
        self.company_board_rows = company_board_rows or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        if query == _QUERY:
            return self.board_rows
        if query == _COMPANY_QUERY:
            return self.company_rows
        if query == _COMPANY_BOARDS_QUERY:
            return self.company_board_rows
        raise AssertionError("unexpected query")


def _company_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "company_id": "00000000-0000-0000-0000-000000000020",
        "company_slug": "ghost",
        "company_name": "Ghost Corp",
        "total_boards": 1,
        "live_boards": 0,
        "stale_dead_boards": 1,
        "oldest_stale_days": 30.0,
    }
    row.update(overrides)
    return row


def _company_board(**overrides: Any) -> dict[str, Any]:
    values = {
        "id": "00000000-0000-0000-0000-000000000002",
        "company_id": "00000000-0000-0000-0000-000000000020",
        "company_slug": "ghost",
        "company_name": "Ghost Corp",
        "board_slug": "ghost-greenhouse",
        "board_url": "https://job-boards.greenhouse.io/ghost",
        "healthy_siblings": 0,
        "company_total_boards": 1,
        "company_live_boards": 0,
        "candidate_scope": "company",
    }
    values.update(overrides)
    return _candidate(**values)


# Query candidate selection remains conservative and deterministic.


def test_board_query_keeps_existing_candidate_guards_and_context() -> None:
    assert "board_status IN ('disabled', 'gone')" in _QUERY
    assert "bs.active_postings = 0" in _QUERY
    assert "bs.healthy_siblings >= 1" in _QUERY
    assert "sib.board_status IN ('active', 'suspect')" in _QUERY
    assert "$1::int || ' days'" in _QUERY
    assert "gone_confirmation_count" in _QUERY
    assert "company_total_boards" in _QUERY
    assert "ORDER BY c.slug, bs.board_slug" in _QUERY


def test_company_query_requires_every_board_stale_and_no_live_jobs() -> None:
    assert "ch.live_boards = 0" in _COMPANY_QUERY
    assert "ch.stale_dead_boards = ch.total_boards" in _COMPANY_QUERY
    assert "ch.total_active_postings = 0" in _COMPANY_QUERY
    assert "ch.total_boards >= 1" in _COMPANY_QUERY


def test_company_board_query_fetches_confirmation_and_sibling_context() -> None:
    assert "gone_first_confirmed_at" in _COMPANY_BOARDS_QUERY
    assert "gone_last_confirmed_at" in _COMPANY_BOARDS_QUERY
    assert "company_live_boards" in _COMPANY_BOARDS_QUERY
    assert "ANY($1::uuid[])" in _COMPANY_BOARDS_QUERY


@pytest.mark.asyncio
async def test_query_helpers_dispatch_days_and_company_ids() -> None:
    conn = _StubConn([_candidate()], [_company_row()], [_company_board()])
    assert len(await find_stale_boards(conn, days=14)) == 1  # type: ignore[arg-type]
    assert len(await find_dead_companies(conn, days=14)) == 1  # type: ignore[arg-type]
    assert (
        len(
            await find_dead_company_boards(
                conn,  # type: ignore[arg-type]
                ["00000000-0000-0000-0000-000000000020"],
            )
        )
        == 1
    )
    assert conn.calls[0][1] == (14,)
    assert conn.calls[1][1] == (14,)
    assert conn.calls[2][1] == (["00000000-0000-0000-0000-000000000020"],)


# Pure fail-closed classification.


def test_current_live_200_routes_terminal_board_to_recovery() -> None:
    item = classify_candidate(
        _candidate(),
        _observation(status="ok", http_status=200, message="200", job_count=17),
    )
    assert item.classification == "live_again"
    assert item.reason_code == "provider_live_currently"
    assert item.job_count == 17
    assert item.recommended_action == "recover_with_provider_native_monitor"


def test_empty_but_valid_board_is_live_not_gone() -> None:
    item = classify_candidate(
        _candidate(),
        _observation(status="ok", http_status=200, message="200", job_count=0),
    )
    assert item.classification == "live_again"
    assert item.job_count == 0


def test_true_404_with_spaced_confirmations_is_verified_gone() -> None:
    item = classify_candidate(_candidate(), _observation())
    assert item.classification == "verified_gone"
    assert item.reason_code == "provider_gone_spaced_confirmations"


@pytest.mark.parametrize(
    ("candidate_overrides", "reason"),
    [
        ({"gone_confirmation_count": 1}, "provider_gone_needs_spaced_confirmations"),
        (
            {"gone_last_confirmed_at": FIRST_GONE + timedelta(hours=5)},
            "provider_gone_needs_spaced_confirmations",
        ),
        (
            {"board_status": "disabled", "is_enabled": False},
            "provider_gone_needs_spaced_confirmations",
        ),
    ],
)
def test_404_without_durable_spaced_confirmation_is_inconclusive(
    candidate_overrides: dict[str, Any], reason: str
) -> None:
    item = classify_candidate(_candidate(**candidate_overrides), _observation())
    assert item.classification == "probe_inconclusive"
    assert item.reason_code == reason


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (
            _observation(status="warn", http_status=429, message="unexpected status 429"),
            "provider_rate_limited",
        ),
        (
            _observation(status="warn", http_status=503, message="unexpected status 503"),
            "provider_transient_http_error",
        ),
        (
            _observation(status="warn", http_status=None, message="network error: ReadTimeout"),
            "provider_network_error",
        ),
        (
            _observation(status="warn", http_status=302, message="unexpected status 302"),
            "provider_redirect_or_transient_status",
        ),
    ],
)
def test_transient_rate_limit_timeout_and_redirect_are_inconclusive(
    observation: ProbeObservation, reason: str
) -> None:
    item = classify_candidate(_candidate(), observation)
    assert item.classification == "probe_inconclusive"
    assert item.reason_code == reason


def test_unprobed_monitor_is_inconclusive() -> None:
    item = classify_candidate(
        _candidate(),
        _observation(
            status="skipped",
            endpoint_class="unsupported",
            http_status=None,
            message="no probe configured",
        ),
    )
    assert item.classification == "probe_inconclusive"
    assert item.reason_code == "provider_probe_unsupported"


def test_probe_contract_failure_is_integration_broken() -> None:
    item = classify_candidate(
        _candidate(),
        _observation(status="warn", http_status=200, message="invalid listing shape"),
    )
    assert item.classification == "integration_broken"
    assert item.reason_code == "provider_probe_contract_broken"


def test_registry_missing_board_is_integration_broken_without_probe_trust() -> None:
    item = classify_candidate(
        _candidate(registry_missing=True),
        _observation(status="skipped", endpoint_class="registry", http_status=None),
    )
    assert item.classification == "integration_broken"
    assert item.reason_code == "registry_board_missing"


# Current HTTP evidence capture.


@pytest.mark.asyncio
async def test_probe_records_live_status_empty_job_count_and_custom_redirect() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"location": "https://custom.example/current"},
                request=request,
            )
        return httpx.Response(200, json={"jobs": []}, request=request)

    result = await probe_registry_rows(
        [_registry_row()],
        1,
        transport=httpx.MockTransport(handler),
    )
    assert len(result) == 1
    assert result[0].status == "ok"
    assert result[0].http_status == 200
    assert result[0].redirect_url == "https://custom.example/current"
    assert result[0].job_count == 0
    assert result[0].endpoint_class == "greenhouse"


@pytest.mark.asyncio
async def test_probe_records_true_404() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": "missing"}, request=request)
    )
    result = await probe_registry_rows([_registry_row()], 1, transport=transport)
    assert result[0].status == "fail"
    assert result[0].http_status == 404
    assert result[0].probed_at.tzinfo is not None


@pytest.mark.asyncio
async def test_probe_marks_unsupported_monitor_without_network() -> None:
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("must not request"))
    )
    result = await probe_registry_rows(
        [_registry_row(monitor_type="unsupported", monitor_config="{}")],
        1,
        transport=transport,
    )
    assert result[0].status == "skipped"
    assert result[0].endpoint_class == "unsupported"
    assert result[0].http_status is None


@pytest.mark.asyncio
async def test_unexpected_probe_exception_becomes_integration_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_probe_row(row: dict[str, str], client: httpx.AsyncClient):
        del row, client
        raise KeyError("provider shape changed")

    monkeypatch.setattr("src.retire_stale_boards.probe_row", broken_probe_row)
    result = await probe_registry_rows([_registry_row()], 1)
    assert result[0].status == "warn"
    assert result[0].http_status is None
    assert "probe exception: KeyError" in result[0].message
    evidence = classify_candidate(_candidate(), result[0])
    assert evidence.classification == "integration_broken"
    assert evidence.reason_code == "provider_probe_contract_broken"


# Registry-vs-runtime orphan detection.


def test_registry_surfaces_banco_bradesco_and_krea_zero_board_orphans(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path, [_registry_row()])
    by_url, orphans = load_registry(tmp_path)
    assert set(by_url) == {"https://job-boards.greenhouse.io/acme"}
    assert [item.company_slug for item in orphans] == ["banco-bradesco", "krea"]
    assert all(item.reason_code == "zero_configured_boards" for item in orphans)


def test_registry_duplicate_board_url_fails_closed(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [_registry_row(), _registry_row(board_slug="acme-copy")],
    )
    with pytest.raises(RetirementSafetyError, match="duplicate board_url"):
        load_registry(tmp_path)


# End-to-end report assembly and formatting.


@pytest.mark.asyncio
async def test_build_report_separates_sections_and_verifies_company(
    tmp_path: Path,
) -> None:
    board = _candidate()
    company = _company_row()
    company_board = _company_board()
    _write_registry(
        tmp_path,
        [
            _registry_row(),
            _registry_row(
                company_slug="ghost",
                board_slug="ghost-greenhouse",
                board_url="https://job-boards.greenhouse.io/ghost",
                monitor_config=json.dumps({"token": "ghost"}),
            ),
        ],
    )
    conn = _StubConn([board], [company], [company_board])

    async def fake_probe(rows: list[dict[str, str]], concurrency: int) -> list[ProbeObservation]:
        assert concurrency == 3
        assert [row["board_slug"] for row in rows] == [
            "acme-greenhouse",
            "ghost-greenhouse",
        ]
        return [
            _observation(status="ok", http_status=200, message="200, 4 jobs", job_count=4),
            _observation(endpoint_url="https://boards-api.greenhouse.io/v1/boards/ghost/jobs"),
        ]

    report = await build_retirement_report(
        conn,  # type: ignore[arg-type]
        days=14,
        concurrency=3,
        data_dir=tmp_path,
        probe_runner=fake_probe,
        now=NOW,
    )
    assert [item.board_slug for item in report.section("live_again")] == ["acme-greenhouse"]
    assert [item.board_slug for item in report.section("verified_gone")] == ["ghost-greenhouse"]
    assert [item.company_slug for item in report.verified_gone_companies] == ["ghost"]
    assert [item.company_slug for item in report.zero_board_registry_orphans] == [
        "banco-bradesco",
        "krea",
    ]


@pytest.mark.asyncio
async def test_company_removal_requires_every_board_verified(
    tmp_path: Path,
) -> None:
    company = _company_row(total_boards=2, stale_dead_boards=2)
    first = _company_board()
    second = _company_board(
        id="00000000-0000-0000-0000-000000000003",
        board_slug="ghost-second",
        board_url="https://job-boards.greenhouse.io/ghost-second",
    )
    _write_registry(
        tmp_path,
        [
            _registry_row(
                company_slug="ghost",
                board_slug="ghost-greenhouse",
                board_url=first["board_url"],
                monitor_config=json.dumps({"token": "ghost"}),
            ),
            _registry_row(
                company_slug="ghost",
                board_slug="ghost-second",
                board_url=second["board_url"],
                monitor_config=json.dumps({"token": "ghost-second"}),
            ),
        ],
    )
    conn = _StubConn([], [company], [first, second])

    async def fake_probe(rows: list[dict[str, str]], concurrency: int) -> list[ProbeObservation]:
        return [
            _observation(),
            _observation(status="ok", http_status=200, message="200", job_count=0),
        ]

    report = await build_retirement_report(
        conn,  # type: ignore[arg-type]
        days=14,
        data_dir=tmp_path,
        probe_runner=fake_probe,
        now=NOW,
    )
    assert report.verified_gone_companies == ()
    assert len(report.section("verified_gone")) == 1
    assert len(report.section("live_again")) == 1


@pytest.mark.asyncio
async def test_probe_cardinality_mismatch_fails_closed(tmp_path: Path) -> None:
    _write_registry(tmp_path, [_registry_row()])
    conn = _StubConn([_candidate()])

    async def broken_probe(rows: list[dict[str, str]], concurrency: int) -> list[ProbeObservation]:
        return []

    with pytest.raises(RetirementSafetyError, match="cardinality mismatch"):
        await build_retirement_report(
            conn,  # type: ignore[arg-type]
            days=14,
            data_dir=tmp_path,
            probe_runner=broken_probe,
        )


def _format_report() -> RetirementReport:
    verified = classify_candidate(_candidate(), _observation())
    live = classify_candidate(
        _candidate(
            id="00000000-0000-0000-0000-000000000004",
            company_slug="liveco",
            board_slug="liveco-greenhouse",
            board_url="https://job-boards.greenhouse.io/liveco",
        ),
        _observation(status="ok", http_status=200, message="200", job_count=0),
    )
    transient = classify_candidate(
        _candidate(
            id="00000000-0000-0000-0000-000000000005",
            company_slug="ratelimited",
            board_slug="ratelimited-greenhouse",
            board_url="https://job-boards.greenhouse.io/ratelimited",
        ),
        _observation(status="warn", http_status=429, message="unexpected status 429"),
    )
    broken = classify_candidate(
        _candidate(
            id="00000000-0000-0000-0000-000000000006",
            company_slug="broken",
            board_slug="broken-greenhouse",
            board_url="https://job-boards.greenhouse.io/broken",
        ),
        _observation(status="warn", http_status=200, message="invalid listing shape"),
    )
    return RetirementReport(
        generated_at=NOW,
        stale_days=14,
        evidence=(verified, live, transient, broken),
        verified_gone_companies=(),
        zero_board_registry_orphans=(
            ZeroBoardRegistryOrphan("krea", "Krea", "https://krea.example"),
        ),
    )


def test_markdown_has_all_required_sections_evidence_and_reason_codes() -> None:
    out = format_md(_format_report())
    assert "## Verified gone" in out
    assert "## Live again — recover, do not retire" in out
    assert "## Probe inconclusive — no removal output" in out
    assert "## Integration broken — repair, do not retire" in out
    assert "## Zero-board registry orphans — operator review" in out
    assert "provider_live_currently" in out
    assert "provider_rate_limited" in out
    assert "2026-08-03T20:00:00Z" in out
    assert "https://boards-api.greenhouse.io/v1/boards/acme/jobs" in out
    assert "404 Not Found" in out
    assert "0 live / 2 total" not in out
    assert "1 live / 2 total" in out


def test_shell_emits_commands_only_for_verified_rows() -> None:
    out = format_shell_snippets(_format_report())
    command_lines = [line for line in out.splitlines() if line and not line.startswith("#")]
    assert len(command_lines) == 1
    assert "job-boards.greenhouse.io/acme" in command_lines[0]
    assert "liveco" not in command_lines[0]
    assert "ratelimited" not in command_lines[0]
    assert "broken" not in command_lines[0]
    assert "# RECOVER liveco/liveco-greenhouse" in out
    assert "# INCONCLUSIVE ratelimited/ratelimited-greenhouse" in out


def test_shell_emits_nothing_executable_when_no_candidate_is_verified() -> None:
    report = _format_report()
    report = RetirementReport(
        generated_at=report.generated_at,
        stale_days=report.stale_days,
        evidence=tuple(item for item in report.evidence if item.classification != "verified_gone"),
        verified_gone_companies=(),
        zero_board_registry_orphans=report.zero_board_registry_orphans,
    )
    out = format_shell_snippets(report)
    assert not [line for line in out.splitlines() if line and not line.startswith("#")]
    assert "No candidates passed every executable removal gate" in out


def test_shell_company_commands_require_verified_company_summary() -> None:
    report = _format_report()
    report = RetirementReport(
        generated_at=report.generated_at,
        stale_days=report.stale_days,
        evidence=report.evidence,
        verified_gone_companies=(
            VerifiedGoneCompany(
                company_id="00000000-0000-0000-0000-000000000020",
                company_slug="ghost",
                company_name="Ghost Corp",
                total_boards=1,
                board_slugs=("ghost-greenhouse",),
                evidence_at=NOW,
            ),
        ),
        zero_board_registry_orphans=report.zero_board_registry_orphans,
    )
    out = format_shell_snippets(report)
    assert "'^ghost,' data/companies.csv" in out
    assert "'^ghost,' data/boards.csv" in out


@pytest.mark.asyncio
async def test_report_json_is_machine_readable(tmp_path: Path) -> None:
    _write_registry(tmp_path, [_registry_row()])
    conn = _StubConn([_candidate()])

    async def fake_probe(rows: list[dict[str, str]], concurrency: int) -> list[ProbeObservation]:
        return [_observation()]

    out = await report_stale_boards(
        conn,  # type: ignore[arg-type]
        days=14,
        fmt="json",
        data_dir=tmp_path,
        probe_runner=fake_probe,
        now=NOW,
    )
    payload = json.loads(out)
    assert payload["generated_at"] == "2026-08-03T20:00:00Z"
    assert payload["sections"]["verified_gone"][0]["reason_code"] == (
        "provider_gone_spaced_confirmations"
    )
    assert [row["company_slug"] for row in payload["zero_board_registry_orphans"]] == [
        "banco-bradesco",
        "krea",
    ]


def test_cli_accepts_json_and_probe_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop_arguments(parser: Any) -> None:
        del parser

    salary_module = ModuleType("src.salary_reprocess")
    salary_module.add_salary_reprocess_arguments = _noop_arguments  # type: ignore[attr-defined]
    occupation_module = ModuleType("src.occupation_reprocess")
    occupation_module.add_occupation_reprocess_arguments = _noop_arguments  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.salary_reprocess", salary_module)
    monkeypatch.setitem(sys.modules, "src.occupation_reprocess", occupation_module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crawler",
            "retire-stale-boards",
            "--format",
            "json",
            "--probe-concurrency",
            "7",
        ],
    )
    args = parse_args()
    assert args.command == "retire-stale-boards"
    assert args.format == "json"
    assert args.probe_concurrency == 7
