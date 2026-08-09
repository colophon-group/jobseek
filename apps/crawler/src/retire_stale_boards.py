"""Fail-closed stale-board retirement evidence report.

Database terminal state is only a candidate selector.  A candidate can reach
executable removal output only when a current provider-native probe reports a
gone signal and the crawler has already recorded spaced durable gone
confirmations.  Live, transient, rate-limited, unsupported, malformed, and
unconfigured candidates remain non-executable recovery/review evidence.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import csv
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import asyncpg
import httpx

from src.probe_boards import PROBES, probe_row
from src.processing.gone_policy import GONE_CONFIRMATION_SPACING
from src.shared.constants import DATA_DIR

_QUERY = """
WITH board_stats AS (
    SELECT
        jb.id,
        jb.company_id,
        jb.board_slug,
        jb.crawler_type,
        jb.board_url,
        jb.board_status,
        jb.is_enabled,
        jb.last_success_at,
        jb.consecutive_failures,
        jb.gone_at,
        jb.gone_confirmation_count,
        jb.gone_first_confirmed_at,
        jb.gone_last_confirmed_at,
        jb.last_gone_status,
        jb.last_gone_endpoint,
        EXTRACT(EPOCH FROM (now() - jb.last_success_at)) / 86400.0 AS stale_days,
        (
            SELECT COUNT(*)
            FROM job_posting jp
            WHERE jp.board_id = jb.id AND jp.is_active = true
        ) AS active_postings,
        (
            SELECT COUNT(*)
            FROM job_board sib
            WHERE sib.company_id = jb.company_id
              AND sib.id <> jb.id
              AND sib.board_status IN ('active', 'suspect')
              AND sib.is_enabled = true
        ) AS healthy_siblings,
        (
            SELECT COUNT(*) FROM job_board ctx WHERE ctx.company_id = jb.company_id
        ) AS company_total_boards,
        (
            SELECT COUNT(*)
            FROM job_board ctx
            WHERE ctx.company_id = jb.company_id
              AND ctx.is_enabled = true
              AND ctx.board_status IN ('active', 'suspect')
        ) AS company_live_boards
    FROM job_board jb
    WHERE jb.board_status IN ('disabled', 'gone')
      AND (
        jb.last_success_at IS NULL
        OR jb.last_success_at < now() - ($1::int || ' days')::interval
      )
)
SELECT
    bs.*,
    c.slug AS company_slug,
    c.name AS company_name
FROM board_stats bs
JOIN company c ON c.id = bs.company_id
WHERE bs.active_postings = 0
  AND bs.healthy_siblings >= 1
ORDER BY c.slug, bs.board_slug
"""


_COMPANY_QUERY = """
WITH per_board AS (
    SELECT
        jb.company_id,
        jb.id,
        jb.board_status,
        jb.is_enabled,
        jb.last_success_at,
        (
            jb.is_enabled = true
            AND jb.board_status IN ('active', 'suspect')
        ) AS is_live,
        (
            jb.last_success_at IS NULL
            OR jb.last_success_at < now() - ($1::int || ' days')::interval
        ) AS is_stale,
        (
            SELECT COUNT(*)
            FROM job_posting jp
            WHERE jp.board_id = jb.id AND jp.is_active = true
        ) AS active_postings
    FROM job_board jb
),
company_health AS (
    SELECT
        company_id,
        COUNT(*) AS total_boards,
        COUNT(*) FILTER (WHERE is_live) AS live_boards,
        COUNT(*) FILTER (WHERE NOT is_live AND is_stale) AS stale_dead_boards,
        SUM(active_postings) AS total_active_postings,
        MAX(GREATEST(
            EXTRACT(EPOCH FROM (now() - last_success_at)) / 86400.0,
            0
        )) AS oldest_stale_days
    FROM per_board
    GROUP BY company_id
)
SELECT
    c.id AS company_id,
    c.slug AS company_slug,
    c.name AS company_name,
    ch.total_boards,
    ch.live_boards,
    ch.stale_dead_boards,
    ch.oldest_stale_days
FROM company_health ch
JOIN company c ON c.id = ch.company_id
WHERE ch.total_boards >= 1
  AND ch.live_boards = 0
  AND ch.stale_dead_boards = ch.total_boards
  AND ch.total_active_postings = 0
ORDER BY c.slug
"""


_COMPANY_BOARDS_QUERY = """
SELECT
    jb.id,
    jb.company_id,
    c.slug AS company_slug,
    c.name AS company_name,
    jb.board_slug,
    jb.crawler_type,
    jb.board_url,
    jb.board_status,
    jb.is_enabled,
    jb.last_success_at,
    jb.consecutive_failures,
    jb.gone_at,
    jb.gone_confirmation_count,
    jb.gone_first_confirmed_at,
    jb.gone_last_confirmed_at,
    jb.last_gone_status,
    jb.last_gone_endpoint,
    EXTRACT(EPOCH FROM (now() - jb.last_success_at)) / 86400.0 AS stale_days,
    (
        SELECT COUNT(*)
        FROM job_posting jp
        WHERE jp.board_id = jb.id AND jp.is_active = true
    ) AS active_postings,
    0::bigint AS healthy_siblings,
    (
        SELECT COUNT(*) FROM job_board ctx WHERE ctx.company_id = jb.company_id
    ) AS company_total_boards,
    (
        SELECT COUNT(*)
        FROM job_board ctx
        WHERE ctx.company_id = jb.company_id
          AND ctx.is_enabled = true
          AND ctx.board_status IN ('active', 'suspect')
    ) AS company_live_boards
FROM job_board jb
JOIN company c ON c.id = jb.company_id
WHERE jb.company_id = ANY($1::uuid[])
ORDER BY c.slug, jb.board_slug, jb.id
"""


class RetirementSafetyError(RuntimeError):
    """Raised when candidate evidence is incomplete or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    status: str
    probed_at: datetime
    endpoint_class: str
    endpoint_url: str
    message: str
    http_status: int | None = None
    redirect_url: str | None = None
    job_count: int | None = None


@dataclass(frozen=True, slots=True)
class RetirementEvidence:
    board_id: str
    company_id: str
    company_slug: str
    company_name: str
    board_slug: str
    board_url: str
    candidate_scope: str
    board_status: str
    is_enabled: bool
    classification: str
    reason_code: str
    recommended_action: str
    probed_at: datetime
    endpoint_class: str
    endpoint_url: str
    http_status: int | None
    redirect_url: str | None
    job_count: int | None
    probe_message: str
    gone_confirmation_count: int
    gone_first_confirmed_at: datetime | None
    gone_last_confirmed_at: datetime | None
    company_total_boards: int
    company_live_boards: int


@dataclass(frozen=True, slots=True)
class VerifiedGoneCompany:
    company_id: str
    company_slug: str
    company_name: str
    total_boards: int
    board_slugs: tuple[str, ...]
    evidence_at: datetime
    reason_code: str = "company_all_boards_provider_gone_confirmed"


@dataclass(frozen=True, slots=True)
class ZeroBoardRegistryOrphan:
    company_slug: str
    company_name: str
    website: str
    reason_code: str = "zero_configured_boards"
    recommended_action: str = "operator_review_registry_orphan"


@dataclass(frozen=True, slots=True)
class RetirementReport:
    generated_at: datetime
    stale_days: int
    evidence: tuple[RetirementEvidence, ...]
    verified_gone_companies: tuple[VerifiedGoneCompany, ...]
    zero_board_registry_orphans: tuple[ZeroBoardRegistryOrphan, ...]

    def section(self, classification: str) -> tuple[RetirementEvidence, ...]:
        return tuple(item for item in self.evidence if item.classification == classification)

    def to_dict(self) -> dict[str, Any]:
        sections = {
            name: [_json_ready(asdict(item)) for item in self.section(name)]
            for name in (
                "verified_gone",
                "live_again",
                "probe_inconclusive",
                "integration_broken",
            )
        }
        return {
            "generated_at": _iso(self.generated_at),
            "stale_days": self.stale_days,
            "sections": sections,
            "verified_gone_companies": [
                _json_ready(asdict(item)) for item in self.verified_gone_companies
            ],
            "zero_board_registry_orphans": [
                _json_ready(asdict(item)) for item in self.zero_board_registry_orphans
            ],
        }


@dataclass(frozen=True, slots=True)
class _ObservedResponse:
    status_code: int
    url: str
    redirect_url: str | None
    job_count: int | None


ProbeRunner = Callable[[list[dict[str, str]], int], Awaitable[list[ProbeObservation]]]

_RESPONSE_LOG: contextvars.ContextVar[list[_ObservedResponse] | None] = contextvars.ContextVar(
    "retirement_probe_response_log", default=None
)
_MONITOR_TYPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "retirement_probe_monitor_type", default=""
)
_PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_JOB_COUNT_RE = re.compile(r"\b(\d+)\s+jobs?\b", re.IGNORECASE)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RetirementSafetyError(f"missing CSV header: {path}")
            return [dict(row) for row in reader]
    except OSError as exc:
        raise RetirementSafetyError(f"cannot read registry CSV {path}: {exc}") from exc


def load_registry(
    data_dir: Path | None = None,
) -> tuple[dict[str, dict[str, str]], list[ZeroBoardRegistryOrphan]]:
    """Load exact deployed probe rows and detect companies with zero boards."""

    root = data_dir or DATA_DIR
    boards = _read_csv(root / "boards.csv")
    companies = _read_csv(root / "companies.csv")
    board_fields = {"company_slug", "board_slug", "board_url", "monitor_type", "monitor_config"}
    if boards and not board_fields.issubset(boards[0]):
        missing = sorted(board_fields - set(boards[0]))
        raise RetirementSafetyError(f"boards.csv missing columns: {missing}")
    if companies and not {"slug", "name"}.issubset(companies[0]):
        raise RetirementSafetyError("companies.csv missing slug/name columns")

    by_url: dict[str, dict[str, str]] = {}
    configured_company_slugs: set[str] = set()
    for row in boards:
        url = (row.get("board_url") or "").strip()
        if not url:
            raise RetirementSafetyError("boards.csv contains an empty board_url")
        if url in by_url:
            raise RetirementSafetyError(f"duplicate board_url in boards.csv: {url}")
        by_url[url] = row
        configured_company_slugs.add((row.get("company_slug") or "").strip())

    seen_company_slugs: set[str] = set()
    orphans: list[ZeroBoardRegistryOrphan] = []
    for row in companies:
        slug = (row.get("slug") or "").strip()
        if not slug:
            raise RetirementSafetyError("companies.csv contains an empty slug")
        if slug in seen_company_slugs:
            raise RetirementSafetyError(f"duplicate company slug in companies.csv: {slug}")
        seen_company_slugs.add(slug)
        if slug not in configured_company_slugs:
            orphans.append(
                ZeroBoardRegistryOrphan(
                    company_slug=slug,
                    company_name=(row.get("name") or "").strip(),
                    website=(row.get("website") or "").strip(),
                )
            )
    return by_url, sorted(orphans, key=lambda item: item.company_slug)


async def find_stale_boards(conn: asyncpg.Connection, *, days: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(_QUERY, days)
    return [dict(row) for row in rows]


async def find_dead_companies(conn: asyncpg.Connection, *, days: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(_COMPANY_QUERY, days)
    return [dict(row) for row in rows]


async def find_dead_company_boards(
    conn: asyncpg.Connection, company_ids: Sequence[Any]
) -> list[dict[str, Any]]:
    if not company_ids:
        return []
    rows = await conn.fetch(_COMPANY_BOARDS_QUERY, list(company_ids))
    return [dict(row) for row in rows]


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _infer_job_count(payload: Any, monitor_type: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in ("total", "totalCount", "totalFound", "totalNumber", "count"):
        count = _integer(payload.get(key))
        if count is not None:
            return count
    meta = payload.get("meta")
    if isinstance(meta, dict):
        for key in ("total", "totalCount", "totalFound", "totalNumber", "count"):
            count = _integer(meta.get(key))
            if count is not None:
                return count
    # These endpoints return their complete listing in one response. Lever's
    # liveness request is deliberately limited to one row and is excluded.
    list_keys_by_monitor = {
        "greenhouse": ("jobs",),
        "ashby": ("jobs",),
        "recruitee": ("offers",),
        "smartrecruiters": ("content",),
    }
    for key in list_keys_by_monitor.get(monitor_type, ()):
        rows = payload.get(key)
        if isinstance(rows, list):
            return len(rows)
    return None


async def _capture_response(response: httpx.Response) -> None:
    response_log = _RESPONSE_LOG.get()
    if response_log is None:
        return
    await response.aread()
    redirect_url = None
    location = response.headers.get("location")
    if location:
        redirect_url = urljoin(str(response.request.url), location)
    job_count = None
    with contextlib.suppress(ValueError):
        job_count = _infer_job_count(response.json(), _MONITOR_TYPE.get())
    response_log.append(
        _ObservedResponse(
            status_code=response.status_code,
            url=str(response.request.url),
            redirect_url=redirect_url,
            job_count=job_count,
        )
    )


async def probe_registry_rows(
    rows: list[dict[str, str]],
    concurrency: int,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[ProbeObservation]:
    """Run bounded provider-native probes and retain response evidence."""

    if concurrency < 1 or concurrency > 20:
        raise ValueError("probe concurrency must be between 1 and 20")
    semaphore = asyncio.Semaphore(concurrency)
    headers = {
        "User-Agent": "jobseek-retirement-probe/1.0 (+https://github.com/colophon-group/jobseek)"
    }
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT,
        follow_redirects=True,
        headers=headers,
        event_hooks={"response": [_capture_response]},
        transport=transport,
    ) as client:

        async def _one(row: dict[str, str]) -> ProbeObservation:
            async with semaphore:
                response_log: list[_ObservedResponse] = []
                token = _RESPONSE_LOG.set(response_log)
                monitor_type = (row.get("monitor_type") or "").strip()
                monitor_token = _MONITOR_TYPE.set(monitor_type)
                result = None
                probe_error: Exception | None = None
                try:
                    try:
                        result = await probe_row(row, client)
                    except Exception as exc:  # noqa: BLE001 - evidence must fail closed
                        probe_error = exc
                finally:
                    _MONITOR_TYPE.reset(monitor_token)
                    _RESPONSE_LOG.reset(token)

                selected = response_log[-1] if response_log else None
                redirect = next(
                    (
                        observed.redirect_url
                        for observed in reversed(response_log)
                        if observed.redirect_url
                    ),
                    None,
                )
                message = (
                    result.message
                    if result is not None
                    else f"probe exception: {type(probe_error).__name__}: {probe_error}"
                )
                message_count = _JOB_COUNT_RE.search(message)
                job_count = int(message_count.group(1)) if message_count else None
                if job_count is None:
                    job_count = next(
                        (
                            observed.job_count
                            for observed in reversed(response_log)
                            if observed.job_count is not None
                        ),
                        None,
                    )
                result_monitor_type = result.monitor_type if result is not None else monitor_type
                endpoint_class = (
                    result_monitor_type if result_monitor_type in PROBES else "unsupported"
                )
                return ProbeObservation(
                    status=result.status if result is not None else "warn",
                    probed_at=datetime.now(UTC),
                    endpoint_class=endpoint_class,
                    endpoint_url=(
                        result.probe_url if result is not None else (row.get("board_url") or "")
                    ),
                    message=message,
                    http_status=selected.status_code if selected else None,
                    redirect_url=redirect,
                    job_count=job_count,
                )

        return list(await asyncio.gather(*[_one(row) for row in rows]))


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _has_spaced_gone_confirmations(candidate: dict[str, Any]) -> bool:
    first = _aware(candidate.get("gone_first_confirmed_at"))
    last = _aware(candidate.get("gone_last_confirmed_at"))
    return bool(
        candidate.get("board_status") == "gone"
        and candidate.get("is_enabled") is True
        and candidate.get("gone_at") is not None
        and int(candidate.get("gone_confirmation_count") or 0) >= 2
        and first is not None
        and last is not None
        and last - first >= GONE_CONFIRMATION_SPACING
    )


def classify_candidate(
    candidate: dict[str, Any], observation: ProbeObservation
) -> RetirementEvidence:
    """Classify one DB candidate without ever trusting terminal state alone."""

    if candidate.get("registry_missing"):
        classification = "integration_broken"
        reason_code = "registry_board_missing"
        action = "repair_registry_runtime_drift"
    elif observation.status == "ok":
        classification = "live_again"
        reason_code = "provider_live_currently"
        action = "recover_with_provider_native_monitor"
    elif observation.status == "skipped":
        classification = "probe_inconclusive"
        reason_code = "provider_probe_unsupported"
        action = "manual_provider_native_verification"
    elif observation.status == "warn":
        status = observation.http_status
        message = observation.message.casefold()
        if status == 429:
            classification = "probe_inconclusive"
            reason_code = "provider_rate_limited"
            action = "retry_probe_after_backoff"
        elif status is not None and status >= 500:
            classification = "probe_inconclusive"
            reason_code = "provider_transient_http_error"
            action = "retry_probe_after_backoff"
        elif status in {301, 302, 303, 307, 308, 408, 425}:
            classification = "probe_inconclusive"
            reason_code = "provider_redirect_or_transient_status"
            action = "review_redirect_and_retry"
        elif status is None and ("network error" in message or "timeout" in message):
            classification = "probe_inconclusive"
            reason_code = "provider_network_error"
            action = "retry_probe_after_backoff"
        else:
            classification = "integration_broken"
            reason_code = "provider_probe_contract_broken"
            action = "repair_monitor_configuration"
    elif observation.status == "fail" and _has_spaced_gone_confirmations(candidate):
        classification = "verified_gone"
        reason_code = "provider_gone_spaced_confirmations"
        action = "review_verified_removal"
    elif observation.status == "fail":
        classification = "probe_inconclusive"
        reason_code = "provider_gone_needs_spaced_confirmations"
        action = "allow_durable_confirmation_policy_to_complete"
    else:
        classification = "integration_broken"
        reason_code = "unknown_probe_status"
        action = "repair_retirement_probe"

    return RetirementEvidence(
        board_id=str(candidate["id"]),
        company_id=str(candidate["company_id"]),
        company_slug=str(candidate["company_slug"]),
        company_name=str(candidate["company_name"]),
        board_slug=str(candidate.get("board_slug") or ""),
        board_url=str(candidate["board_url"]),
        candidate_scope=str(candidate["candidate_scope"]),
        board_status=str(candidate["board_status"]),
        is_enabled=bool(candidate["is_enabled"]),
        classification=classification,
        reason_code=reason_code,
        recommended_action=action,
        probed_at=observation.probed_at,
        endpoint_class=observation.endpoint_class,
        endpoint_url=observation.endpoint_url,
        http_status=observation.http_status,
        redirect_url=observation.redirect_url,
        job_count=observation.job_count,
        probe_message=observation.message,
        gone_confirmation_count=int(candidate.get("gone_confirmation_count") or 0),
        gone_first_confirmed_at=_aware(candidate.get("gone_first_confirmed_at")),
        gone_last_confirmed_at=_aware(candidate.get("gone_last_confirmed_at")),
        company_total_boards=int(candidate.get("company_total_boards") or 0),
        company_live_boards=int(candidate.get("company_live_boards") or 0),
    )


async def build_retirement_report(
    conn: asyncpg.Connection,
    *,
    days: int,
    concurrency: int = 5,
    data_dir: Path | None = None,
    probe_runner: ProbeRunner = probe_registry_rows,
    now: datetime | None = None,
) -> RetirementReport:
    if days < 1:
        raise ValueError("days must be positive")
    if concurrency < 1 or concurrency > 20:
        raise ValueError("probe concurrency must be between 1 and 20")

    registry_by_url, zero_board_orphans = load_registry(data_dir)
    board_candidates = await find_stale_boards(conn, days=days)
    company_candidates = await find_dead_companies(conn, days=days)
    company_ids = [row["company_id"] for row in company_candidates]
    company_board_candidates = await find_dead_company_boards(conn, company_ids)

    candidates: list[dict[str, Any]] = []
    for row in board_candidates:
        candidate = dict(row)
        candidate["candidate_scope"] = "board"
        candidates.append(candidate)
    for row in company_board_candidates:
        candidate = dict(row)
        candidate["candidate_scope"] = "company"
        candidates.append(candidate)
    candidates.sort(key=lambda row: (str(row["company_slug"]), str(row.get("board_slug") or "")))

    probe_inputs: list[dict[str, str]] = []
    configured_candidates: list[dict[str, Any]] = []
    evidence: list[RetirementEvidence] = []
    generated_at = _aware(now) or datetime.now(UTC)
    for candidate in candidates:
        registry_row = registry_by_url.get(str(candidate["board_url"]))
        if registry_row is None:
            candidate["registry_missing"] = True
            evidence.append(
                classify_candidate(
                    candidate,
                    ProbeObservation(
                        status="skipped",
                        probed_at=generated_at,
                        endpoint_class="registry",
                        endpoint_url=str(candidate["board_url"]),
                        message="candidate board URL is absent from deployed boards.csv",
                    ),
                )
            )
            continue
        probe_inputs.append(registry_row)
        configured_candidates.append(candidate)

    observations = await probe_runner(probe_inputs, concurrency)
    if len(observations) != len(configured_candidates):
        raise RetirementSafetyError(
            "probe result cardinality mismatch: "
            f"expected {len(configured_candidates)}, got {len(observations)}"
        )
    evidence.extend(
        classify_candidate(candidate, observation)
        for candidate, observation in zip(configured_candidates, observations, strict=True)
    )
    evidence.sort(key=lambda item: (item.company_slug, item.board_slug, item.board_id))

    company_rows_by_id = {str(row["company_id"]): row for row in company_candidates}
    company_evidence: dict[str, list[RetirementEvidence]] = defaultdict(list)
    for item in evidence:
        if item.candidate_scope == "company":
            company_evidence[item.company_id].append(item)

    verified_companies: list[VerifiedGoneCompany] = []
    for company_id, company_row in company_rows_by_id.items():
        rows = company_evidence.get(company_id, [])
        expected = int(company_row["total_boards"])
        if len(rows) != expected:
            raise RetirementSafetyError(
                f"company candidate {company_row['company_slug']} expected {expected} boards, "
                f"found {len(rows)} evidence rows"
            )
        if rows and all(row.classification == "verified_gone" for row in rows):
            verified_companies.append(
                VerifiedGoneCompany(
                    company_id=company_id,
                    company_slug=str(company_row["company_slug"]),
                    company_name=str(company_row["company_name"]),
                    total_boards=expected,
                    board_slugs=tuple(sorted(row.board_slug for row in rows)),
                    evidence_at=max(row.probed_at for row in rows),
                )
            )

    return RetirementReport(
        generated_at=generated_at,
        stale_days=days,
        evidence=tuple(evidence),
        verified_gone_companies=tuple(
            sorted(verified_companies, key=lambda item: item.company_slug)
        ),
        zero_board_registry_orphans=tuple(zero_board_orphans),
    )


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_evidence_table(rows: Sequence[RetirementEvidence]) -> str:
    if not rows:
        return "None."
    lines = [
        "| Scope | Company | Board | DB state | Reason code | Probed | Endpoint class | "
        "Endpoint URL | HTTP | Redirect | Jobs | Probe evidence | Company context | Action |",
        "|---|---|---|---|---|---|---|---|---:|---|---:|---|---|---|",
    ]
    for row in rows:
        context = f"{row.company_live_boards} live / {row.company_total_boards} total"
        scope = _cell(row.candidate_scope)
        company = _cell(row.company_slug)
        board = _cell(row.board_slug)
        state = _cell(row.board_status)
        reason = _cell(row.reason_code)
        probed = _cell(_iso(row.probed_at))
        endpoint = _cell(row.endpoint_class)
        endpoint_url = _cell(row.endpoint_url)
        http_status = _cell(row.http_status)
        redirect = _cell(row.redirect_url)
        jobs = _cell(row.job_count)
        probe_message = _cell(row.probe_message)
        action = _cell(row.recommended_action)
        lines.append(
            f"| {scope} | {company} | `{board}` | `{state}` | `{reason}` | {probed} | "
            f"`{endpoint}` | {endpoint_url} | {http_status} | {redirect} | {jobs} | "
            f"{probe_message} | {_cell(context)} | `{action}` |"
        )
    return "\n".join(lines)


def format_md(report: RetirementReport) -> str:
    sections = {
        "verified_gone": report.section("verified_gone"),
        "live_again": report.section("live_again"),
        "probe_inconclusive": report.section("probe_inconclusive"),
        "integration_broken": report.section("integration_broken"),
    }
    lines = [
        "# Fail-closed stale-board retirement report",
        "",
        f"Generated: {_iso(report.generated_at)}. Staleness threshold: {report.stale_days} days.",
        "",
        "Database terminal state selected candidates only. Removal output requires a current "
        "provider-gone result plus durable six-hour-spaced confirmations.",
        "",
        "## Summary",
        "",
        f"- Verified-gone board evidence: {len(sections['verified_gone'])}",
        f"- Fully verified-gone companies: {len(report.verified_gone_companies)}",
        f"- Live again (recovery): {len(sections['live_again'])}",
        f"- Probe inconclusive: {len(sections['probe_inconclusive'])}",
        f"- Integration broken: {len(sections['integration_broken'])}",
        f"- Zero-board registry orphans: {len(report.zero_board_registry_orphans)}",
        "",
        "## Verified gone",
        "",
        _format_evidence_table(sections["verified_gone"]),
        "",
        "### Fully verified-gone companies",
        "",
    ]
    if report.verified_gone_companies:
        lines.extend(
            [
                "| Company | Boards | Evidence timestamp | Reason code |",
                "|---|---:|---|---|",
            ]
        )
        for company in report.verified_gone_companies:
            lines.append(
                f"| {_cell(company.company_slug)} | {company.total_boards} | "
                f"{_iso(company.evidence_at)} | `{company.reason_code}` |"
            )
    else:
        lines.append("None.")
    lines.extend(
        [
            "",
            "## Live again — recover, do not retire",
            "",
            _format_evidence_table(sections["live_again"]),
            "",
            "## Probe inconclusive — no removal output",
            "",
            _format_evidence_table(sections["probe_inconclusive"]),
            "",
            "## Integration broken — repair, do not retire",
            "",
            _format_evidence_table(sections["integration_broken"]),
            "",
            "## Zero-board registry orphans — operator review",
            "",
        ]
    )
    if report.zero_board_registry_orphans:
        lines.extend(
            [
                "| Company | Name | Website | Reason code | Action |",
                "|---|---|---|---|---|",
            ]
        )
        for orphan in report.zero_board_registry_orphans:
            lines.append(
                f"| {_cell(orphan.company_slug)} | {_cell(orphan.company_name)} | "
                f"{_cell(orphan.website)} | `{orphan.reason_code}` | "
                f"`{orphan.recommended_action}` |"
            )
    else:
        lines.append("None.")
    return "\n".join(lines)


def _safe_board_snippet(row: RetirementEvidence) -> str | None:
    if "'" in row.board_url or "\n" in row.board_url or "\r" in row.board_url:
        return None
    return (
        f"grep -vF -- ',{row.board_url},' data/boards.csv > data/boards.csv.new "
        f"&& mv data/boards.csv.new data/boards.csv  "
        f"# {row.company_slug} {row.board_slug} {row.reason_code} {_iso(row.probed_at)}"
    )


def format_shell_snippets(report: RetirementReport) -> str:
    """Emit commands only for evidence that passed every removal gate."""

    lines = [
        "# Fail-closed stale-board retirement output.",
        f"# Evidence generated at {_iso(report.generated_at)}.",
        "# Live, inconclusive, integration-broken, and orphan rows below are comments only.",
    ]
    verified_board_rows = [
        row for row in report.section("verified_gone") if row.candidate_scope == "board"
    ]
    if verified_board_rows:
        lines.extend(["", "# --- Verified-gone boards ---"])
        for row in verified_board_rows:
            snippet = _safe_board_snippet(row)
            if snippet is None:
                lines.append(
                    f"# BLOCKED {row.company_slug}/{row.board_slug}: shell-unsafe board URL"
                )
            else:
                lines.append(snippet)

    if report.verified_gone_companies:
        lines.extend(["", "# --- Fully verified-gone companies ---"])
        for company in report.verified_gone_companies:
            slug = company.company_slug
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
                lines.append(f"# BLOCKED {slug}: shell-unsafe company slug")
                continue
            lines.append(
                f"grep -v -- '^{slug},' data/companies.csv > data/companies.csv.new "
                f"&& mv data/companies.csv.new data/companies.csv  "
                f"# {company.reason_code} {_iso(company.evidence_at)}"
            )
            lines.append(
                f"grep -v -- '^{slug},' data/boards.csv > data/boards.csv.new "
                "&& mv data/boards.csv.new data/boards.csv"
            )

    for classification, label in (
        ("live_again", "RECOVER"),
        ("probe_inconclusive", "INCONCLUSIVE"),
        ("integration_broken", "REPAIR"),
    ):
        rows = report.section(classification)
        if rows:
            lines.extend(["", f"# --- {label}: non-executable ---"])
            for row in rows:
                lines.append(
                    f"# {label} {row.company_slug}/{row.board_slug} "
                    f"reason={row.reason_code} probed_at={_iso(row.probed_at)}"
                )
    if report.zero_board_registry_orphans:
        lines.extend(["", "# --- ZERO-BOARD ORPHANS: non-executable ---"])
        for orphan in report.zero_board_registry_orphans:
            lines.append(f"# REVIEW {orphan.company_slug} reason={orphan.reason_code}")

    if not verified_board_rows and not report.verified_gone_companies:
        lines.extend(["", "# No candidates passed every executable removal gate."])
    return "\n".join(lines).rstrip() + "\n"


async def report_stale_boards(
    conn: asyncpg.Connection,
    *,
    days: int,
    fmt: str,
    concurrency: int = 5,
    data_dir: Path | None = None,
    probe_runner: ProbeRunner = probe_registry_rows,
    now: datetime | None = None,
) -> str:
    report = await build_retirement_report(
        conn,
        days=days,
        concurrency=concurrency,
        data_dir=data_dir,
        probe_runner=probe_runner,
        now=now,
    )
    if fmt == "shell":
        return format_shell_snippets(report)
    if fmt == "json":
        return json.dumps(report.to_dict(), indent=2, sort_keys=True)
    if fmt != "md":
        raise ValueError(f"unsupported report format: {fmt}")
    return format_md(report)


__all__ = [
    "ProbeObservation",
    "RetirementEvidence",
    "RetirementReport",
    "RetirementSafetyError",
    "VerifiedGoneCompany",
    "ZeroBoardRegistryOrphan",
    "build_retirement_report",
    "classify_candidate",
    "find_dead_companies",
    "find_dead_company_boards",
    "find_stale_boards",
    "format_md",
    "format_shell_snippets",
    "load_registry",
    "probe_registry_rows",
    "report_stale_boards",
]
