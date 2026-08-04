"""Lifecycle-aware inspection and recovery for Redis dead-letter tasks.

Monitor dead-letter membership is intentionally independent from the normal
queue.  A deploy-time sync may repair a board hash and schedule while the
historical poison descriptor remains parked for operator review.  This module
joins those descriptors to authoritative local Postgres board state and only
allows exact, explicitly selected entries to be retried or pruned.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg

import src.redis_queue as redis_queue

DeadletterLifecycle = Literal["actionable", "retired", "superseded", "unresolved"]
DEADLETTER_LIFECYCLES: tuple[DeadletterLifecycle, ...] = (
    "actionable",
    "retired",
    "superseded",
    "unresolved",
)
DEADLETTER_WORKER_TYPES = ("simple", "browser")

_FETCH_BOARD_LIFECYCLES = """
SELECT id::text AS board_id,
       board_slug,
       board_url,
       crawler_type,
       board_status,
       is_enabled,
       throttle_key,
       monitor_needs_browser
FROM job_board
WHERE id = ANY($1::uuid[])
"""


class DeadletterResolutionError(ValueError):
    """Raised when an explicit resolution would violate lifecycle guards."""


@dataclass(frozen=True, slots=True)
class DeadletterEntry:
    """One classified Redis dead-letter descriptor."""

    member: str
    wtype: str
    reaped_at: float
    task_type: str
    domain: str
    task_id: str
    lifecycle: DeadletterLifecycle
    reason: str
    resolution: str
    board_slug: str | None = None
    board_status: str | None = None
    expected_domain: str | None = None
    expected_wtype: str | None = None
    config_state: str = "not_applicable"

    @property
    def ref(self) -> str:
        """Stable CLI selector that disambiguates simple/browser entries."""

        return f"{self.wtype}:{self.member}"

    @property
    def retryable(self) -> bool:
        return self.lifecycle == "actionable" and self.reason == "active"

    @property
    def prunable(self) -> bool:
        return self.lifecycle in {"retired", "superseded"}

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["ref"] = self.ref
        result["reaped_at_iso"] = datetime.fromtimestamp(self.reaped_at, tz=UTC).isoformat()
        result["retryable"] = self.retryable
        result["prunable"] = self.prunable
        return result


def _parse_member(wtype: str, member: str, score: float) -> DeadletterEntry:
    parts = member.split("|", 2)
    if len(parts) != 3 or not all(parts):
        return DeadletterEntry(
            member=member,
            wtype=wtype,
            reaped_at=score,
            task_type=parts[0] if parts else "",
            domain=parts[1] if len(parts) > 1 else "",
            task_id=parts[2] if len(parts) > 2 else "",
            lifecycle="unresolved",
            reason="malformed_descriptor",
            resolution="manual_review",
        )
    task_type, domain, task_id = parts
    if task_type != "monitor":
        return DeadletterEntry(
            member=member,
            wtype=wtype,
            reaped_at=score,
            task_type=task_type,
            domain=domain,
            task_id=task_id,
            lifecycle="unresolved",
            reason="non_monitor_task",
            resolution="manual_review",
        )
    try:
        uuid.UUID(task_id)
    except ValueError:
        return DeadletterEntry(
            member=member,
            wtype=wtype,
            reaped_at=score,
            task_type=task_type,
            domain=domain,
            task_id=task_id,
            lifecycle="unresolved",
            reason="invalid_monitor_id",
            resolution="manual_review",
        )
    return DeadletterEntry(
        member=member,
        wtype=wtype,
        reaped_at=score,
        task_type=task_type,
        domain=domain,
        task_id=task_id,
        lifecycle="unresolved",
        reason="unclassified",
        resolution="manual_review",
    )


def _redis_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _config_state(config: dict[str, str], row: Any) -> str:
    if not config:
        return "missing"
    expected_browser = bool(row["monitor_needs_browser"])
    if (
        config.get("domain") != (row["throttle_key"] or "")
        or config.get("board_url") != row["board_url"]
        or config.get("crawler_type") != row["crawler_type"]
        or _redis_bool(config.get("monitor_needs_browser")) != expected_browser
    ):
        return "stale"
    return "valid"


async def classify_deadletters(
    db: asyncpg.Pool | asyncpg.Connection,
) -> list[DeadletterEntry]:
    """Join every dead-letter descriptor to local Postgres lifecycle truth.

    No Redis or database state is mutated. Missing database rows are
    authoritative retirement evidence; malformed and non-monitor descriptors
    remain unresolved so they cannot be accidentally cleared by this monitor
    recovery path.
    """

    redis = redis_queue.get_redis()
    raw_entries: list[DeadletterEntry] = []
    for wtype in DEADLETTER_WORKER_TYPES:
        values = await redis.zrange(f"deadletter:{wtype}", 0, -1, withscores=True)
        raw_entries.extend(
            _parse_member(
                wtype,
                member.decode() if isinstance(member, bytes) else str(member),
                float(score),
            )
            for member, score in values
        )

    monitor_ids = sorted(
        {
            uuid.UUID(entry.task_id)
            for entry in raw_entries
            if entry.task_type == "monitor" and entry.reason == "unclassified"
        },
        key=str,
    )
    rows = await db.fetch(_FETCH_BOARD_LIFECYCLES, monitor_ids) if monitor_ids else []
    board_rows = {str(row["board_id"]): row for row in rows}

    configs: dict[str, dict[str, str]] = {}
    if board_rows:
        pipe = redis.pipeline(transaction=False)
        ordered_ids = sorted(board_rows)
        for board_id in ordered_ids:
            pipe.hgetall(f"board:{board_id}")
        config_values = await pipe.execute()
        configs = dict(zip(ordered_ids, config_values, strict=True))

    classified: list[DeadletterEntry] = []
    for entry in raw_entries:
        if entry.reason != "unclassified":
            classified.append(entry)
            continue

        row = board_rows.get(entry.task_id)
        if row is None:
            classified.append(
                DeadletterEntry(
                    **{
                        **asdict(entry),
                        "lifecycle": "retired",
                        "reason": "removed",
                        "resolution": "prune",
                    }
                )
            )
            continue

        board_slug = row["board_slug"]
        board_status = row["board_status"]
        expected_domain = row["throttle_key"] or ""
        expected_wtype = "browser" if row["monitor_needs_browser"] else "simple"
        config_state = _config_state(configs.get(entry.task_id, {}), row)
        common = {
            **asdict(entry),
            "board_slug": board_slug,
            "board_status": board_status,
            "expected_domain": expected_domain,
            "expected_wtype": expected_wtype,
            "config_state": config_state,
        }

        if not row["is_enabled"] or board_status == "disabled":
            classified.append(
                DeadletterEntry(
                    **{
                        **common,
                        "lifecycle": "retired",
                        "reason": "disabled",
                        "resolution": "prune",
                    }
                )
            )
        elif entry.domain != expected_domain or entry.wtype != expected_wtype:
            if config_state == "valid":
                classified.append(
                    DeadletterEntry(
                        **{
                            **common,
                            "lifecycle": "superseded",
                            "reason": "monitor_route_changed",
                            "resolution": "prune_after_current_schedule",
                        }
                    )
                )
            else:
                classified.append(
                    DeadletterEntry(
                        **{
                            **common,
                            "lifecycle": "unresolved",
                            "reason": f"superseded_config_{config_state}",
                            "resolution": "sync_then_inspect",
                        }
                    )
                )
        elif config_state != "valid":
            classified.append(
                DeadletterEntry(
                    **{
                        **common,
                        "lifecycle": "actionable",
                        "reason": f"active_config_{config_state}",
                        "resolution": "sync_then_retry",
                    }
                )
            )
        else:
            classified.append(
                DeadletterEntry(
                    **{
                        **common,
                        "lifecycle": "actionable",
                        "reason": "active",
                        "resolution": "retry",
                    }
                )
            )

    return sorted(classified, key=lambda item: (item.wtype, item.reaped_at, item.member))


def lifecycle_counts(entries: list[DeadletterEntry]) -> dict[str, dict[str, int]]:
    """Return bounded metric/log counts, including zero-valued states."""

    counts = Counter((entry.wtype, entry.lifecycle) for entry in entries)
    return {
        wtype: {lifecycle: counts[(wtype, lifecycle)] for lifecycle in DEADLETTER_LIFECYCLES}
        for wtype in DEADLETTER_WORKER_TYPES
    }


async def _ensure_current_schedule(
    entry: DeadletterEntry,
    config: dict[str, str],
) -> str:
    """Ensure one current-route monitor schedule exists without duplicating it."""

    assert entry.expected_domain is not None
    assert entry.expected_wtype is not None
    redis = redis_queue.get_redis()
    current_member = f"monitor|{entry.expected_domain}|{entry.task_id}"
    pipe = redis.pipeline(transaction=False)
    pipe.zscore(f"ft_monitors_{entry.expected_wtype}:{entry.expected_domain}", entry.task_id)
    pipe.zscore(f"monitors_{entry.expected_wtype}:{entry.expected_domain}", entry.task_id)
    pipe.zscore(f"inflight:{entry.expected_wtype}", current_member)
    locations = await pipe.execute()
    if any(score is not None for score in locations):
        return "already_scheduled"

    added = await redis_queue.enqueue_monitor(
        entry.expected_domain,
        entry.task_id,
        time.time(),
        dict(config),
        browser=entry.expected_wtype == "browser",
        first_time=False,
    )
    if not added:
        # A concurrent sync/worker may have won the NX race. Its schedule is
        # authoritative and is exactly the desired idempotent outcome.
        return "concurrently_scheduled"
    return "enqueued"


async def _remove_superseded_route(entry: DeadletterEntry) -> None:
    """Remove only the descriptor's obsolete route, preserving current config."""

    redis = redis_queue.get_redis()
    if await redis.zscore(f"inflight:{entry.wtype}", entry.member) is not None:
        raise DeadletterResolutionError(
            f"superseded route is currently inflight; inspect again later: {entry.ref}"
        )
    pipe = redis.pipeline(transaction=False)
    pipe.zrem(f"ft_monitors_{entry.wtype}:{entry.domain}", entry.task_id)
    pipe.zrem(f"monitors_{entry.wtype}:{entry.domain}", entry.task_id)
    pipe.hdel(f"inflight_strikes:{entry.wtype}", entry.member)
    await pipe.execute()


async def resolve_deadletters(
    db: asyncpg.Pool | asyncpg.Connection,
    *,
    action: Literal["inspect", "retry", "prune"],
    selected_refs: list[str] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Inspect or explicitly resolve selected dead-letter entries.

    Retry is restricted to active boards with current Redis config. Prune is
    restricted to database-confirmed retired entries or a stale route whose
    current config is already valid. Every mutation targets one exact ZSET
    member and mutations are disabled unless ``apply`` is true.
    """

    entries = await classify_deadletters(db)
    by_ref = {entry.ref: entry for entry in entries}
    selected_refs = selected_refs or []

    if action == "inspect" and apply:
        raise DeadletterResolutionError("inspect is always read-only; omit --apply")
    if action == "inspect":
        selected = entries
    else:
        if not selected_refs:
            raise DeadletterResolutionError(
                f"{action} requires at least one explicit --entry selector"
            )
        missing = sorted(set(selected_refs) - set(by_ref))
        if missing:
            raise DeadletterResolutionError(
                "selected dead-letter entries no longer exist: " + ", ".join(missing)
            )
        selected = [by_ref[ref] for ref in dict.fromkeys(selected_refs)]

    if action == "retry":
        blocked = [entry for entry in selected if not entry.retryable]
        if blocked:
            details = ", ".join(f"{entry.ref} ({entry.reason})" for entry in blocked)
            raise DeadletterResolutionError(f"retry blocked by lifecycle guard: {details}")
    elif action == "prune":
        blocked = [entry for entry in selected if not entry.prunable]
        if blocked:
            details = ", ".join(f"{entry.ref} ({entry.reason})" for entry in blocked)
            raise DeadletterResolutionError(f"prune blocked by lifecycle guard: {details}")

    outcomes: list[dict[str, str]] = []
    if action != "inspect":
        redis = redis_queue.get_redis()
        for entry in selected:
            if not apply:
                outcomes.append({"ref": entry.ref, "outcome": f"would_{action}"})
                continue

            if await redis.zscore(f"deadletter:{entry.wtype}", entry.member) is None:
                raise DeadletterResolutionError(
                    f"selected dead-letter entry disappeared before {action}: {entry.ref}"
                )

            schedule_outcome = "not_applicable"
            if action == "retry" or entry.lifecycle == "superseded":
                raw_config = await redis.hgetall(f"board:{entry.task_id}")
                if not raw_config:
                    raise DeadletterResolutionError(
                        f"current config disappeared before {action}: {entry.ref}; run sync"
                    )
                config = {
                    key.decode() if isinstance(key, bytes) else str(key): (
                        value.decode() if isinstance(value, bytes) else str(value)
                    )
                    for key, value in raw_config.items()
                }
                schedule_outcome = await _ensure_current_schedule(entry, config)
            if entry.lifecycle == "superseded":
                await _remove_superseded_route(entry)

            removed = await redis.zrem(f"deadletter:{entry.wtype}", entry.member)
            if removed != 1:
                raise DeadletterResolutionError(
                    f"failed to remove exact dead-letter entry during {action}: {entry.ref}"
                )
            outcomes.append(
                {
                    "ref": entry.ref,
                    "outcome": "retried" if action == "retry" else "pruned",
                    "schedule": schedule_outcome,
                }
            )

    return {
        "action": action,
        "dry_run": action == "inspect" or not apply,
        "total": len(entries),
        "counts": lifecycle_counts(entries),
        "selected": len(selected),
        "entries": [entry.to_dict() for entry in selected],
        "outcomes": outcomes,
    }
