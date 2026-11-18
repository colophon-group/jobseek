"""Export current monitoring evidence for one approved source scope."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

APPROVAL_LIMIT = 4096
CHECK_GRACE = timedelta(minutes=15)
MAX_CHECK_AGE = timedelta(days=1)
SOURCE_VERSION = "jobseek-monitoring-v1"

# The current scope revision belongs to the same fenced terminal-check ledger
# used by the historical exporter. A later failed or partial check supersedes
# an earlier complete check for current monitoring.
MONITORING_SQL = """
SELECT b.is_enabled, b.board_status, b.check_interval_minutes,
       s.revision, s.tracked_since,
       s.effective_config_sha256 IS NOT NULL
         AND NULLIF(b.metadata->>'_monitor_config_fingerprint', '') IS NOT NULL AS config_bound,
       latest.started_at, latest.finished_at, latest.next_due_at, latest.outcome
FROM public.job_board b
LEFT JOIN public.source_coverage_scope s ON s.board_id=b.id
LEFT JOIN LATERAL (
    SELECT started_at, finished_at, next_due_at, outcome
    FROM public.source_coverage_check
    WHERE board_id=b.id AND revision=s.revision
    ORDER BY finished_at DESC, id DESC LIMIT 1
) latest ON s.revision=$3::uuid
WHERE b.company_id=$1::uuid AND b.id=$2::uuid
"""


@dataclass(frozen=True)
class ApprovedScope:
    company: UUID
    board: UUID
    revision: UUID


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("Duplicate approval field.")
    return obj


def approved_uuid(value: Any) -> UUID:
    identifier = UUID(value)
    if str(identifier) != value or not identifier.int:
        raise ValueError("Expected a canonical nonzero UUID.")
    return identifier


def read_approved_scope(path: Path) -> ApprovedScope:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Expected a canonical private approval path.")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    )
    with os.fdopen(descriptor, "rb") as approval:
        info = os.fstat(approval.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= APPROVAL_LIMIT
        ):
            raise ValueError("Expected a private single-link approval file.")
        raw = approval.read(APPROVAL_LIMIT + 1)
    if len(raw) > APPROVAL_LIMIT:
        raise ValueError("Approval exceeds its size limit.")
    data = json.loads(raw, object_pairs_hook=unique_object)
    if (
        not isinstance(data, dict)
        or set(data) != {"schemaVersion", "companyId", "boardId", "revision"}
        or type(data["schemaVersion"]) is not int
        or data["schemaVersion"] != 1
        or not all(
            isinstance(data[key], str) for key in ("companyId", "boardId", "revision")
        )
    ):
        raise ValueError("Unrecognized approval format.")
    return ApprovedScope(
        approved_uuid(data["companyId"]),
        approved_uuid(data["boardId"]),
        approved_uuid(data["revision"]),
    )


def stamp(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def monitoring_receipt(
    scope: ApprovedScope,
    observed_at: datetime,
    row: asyncpg.Record | dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceVersion": SOURCE_VERSION,
        "companyId": str(scope.company),
        "boardId": str(scope.board),
        "revision": str(scope.revision),
        "observedAt": stamp(observed_at),
        "state": "unavailable",
        "reason": "scope_changed",
        "verifiedAt": None,
        "expiresAt": None,
    }
    if row is None or row["revision"] != scope.revision:
        return result
    if not row["is_enabled"] or row["board_status"] != "active":
        return {**result, "reason": "scope_inactive"}
    if not row["config_bound"] or row["outcome"] != "complete":
        return {**result, "reason": "no_complete_check"}

    tracked = row["tracked_since"]
    started = row["started_at"]
    finished = row["finished_at"]
    due = row["next_due_at"]
    minutes = row["check_interval_minutes"]
    if (
        not tracked <= started <= finished <= observed_at
        or not timedelta(0) < due - finished <= MAX_CHECK_AGE
        or not 0 < minutes <= 24 * 60
    ):
        return {**result, "reason": "invalid_schedule"}
    expires = min(
        due + CHECK_GRACE,
        finished + timedelta(minutes=minutes) + CHECK_GRACE,
        finished + MAX_CHECK_AGE,
    )
    if stamp(observed_at) >= stamp(expires):
        return {**result, "reason": "expired_check"}
    return {
        **result,
        "state": "current",
        "reason": None,
        "verifiedAt": stamp(finished),
        "expiresAt": stamp(expires),
    }


async def read_receipt(dsn: str, scope: ApprovedScope) -> dict[str, Any]:
    db = await asyncpg.connect(dsn, timeout=6, command_timeout=8)
    try:
        async with db.transaction(isolation="repeatable_read", readonly=True):
            observed_at = await db.fetchval("SELECT transaction_timestamp()")
            assert isinstance(observed_at, datetime)
            row = await db.fetchrow(
                MONITORING_SQL, scope.company, scope.board, scope.revision
            )
            return monitoring_receipt(scope, observed_at, row)
    finally:
        await db.close(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("approval", type=Path)
    args = parser.parse_args()
    try:
        scope = read_approved_scope(args.approval)
        receipt = asyncio.run(read_receipt(os.environ["LOCAL_DATABASE_URL"], scope))
    except (
        OSError,
        ValueError,
        KeyError,
        TimeoutError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ):
        print("Monitoring receipt refused.", file=sys.stderr)
        return 1
    print(json.dumps(receipt, separators=(",", ":"), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
