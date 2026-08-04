"""Bounded, rate-aware company-import queue policy and refill runner."""

from __future__ import annotations

import asyncio
import random
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from src.ats_inventory.candidate_issues import (
    CandidateIssueAction,
    CandidateIssueCoordinator,
)
from src.ats_inventory.candidates import Candidate
from src.ats_inventory.github import GitHubClaim, GitHubRateLimitError, GitHubWorkItem
from src.ats_inventory.ledger import CandidateLedger
from src.ats_inventory.models import CompanyImpact, company_impact_rank_key

IMPORT_LABEL = "source:ats-inventory"
CLAIM_MARKER = "<!-- ws-claim -->"
_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.I)


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    low_water: int = 450
    target: int = 500
    hard_cap: int = 600
    per_tick_cap: int = 25
    daily_cap: int = 50
    rollout_cap: Literal[1, 5, 25] = 1
    claim_ttl_seconds: int = 4 * 60 * 60
    jitter_min_seconds: float = 1.0
    jitter_max_seconds: float = 3.0

    def __post_init__(self) -> None:
        if not 0 <= self.low_water <= self.target <= self.hard_cap:
            raise ValueError("queue thresholds must satisfy 0 <= low <= target <= hard cap")
        if self.per_tick_cap < 1 or self.daily_cap < 1:
            raise ValueError("queue creation caps must be positive")
        if self.rollout_cap not in (1, 5, 25):
            raise ValueError("rollout cap must be one of 1, 5, or 25")
        if self.claim_ttl_seconds < 1:
            raise ValueError("claim TTL must be positive")
        if not 0 <= self.jitter_min_seconds <= self.jitter_max_seconds:
            raise ValueError("jitter bounds must be non-negative and ordered")


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    total_open: int
    available: int
    fresh_claimed: int
    active_linked_pr: int
    import_open: int
    human_open: int
    unparseable_claims: int
    fresh_claim_issue_numbers: tuple[int, ...]
    linked_pr_issue_numbers: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QueueRunReport:
    mode: Literal["report", "dry-run", "refill"]
    status: str
    queue_before: QueueSnapshot
    available_after: int
    total_open_after: int
    created_today_before: int
    requested_creates: int
    inspected_candidates: int
    selected_candidates: int
    created: int
    hard_skip_counts: dict[str, int]
    actions: tuple[CandidateIssueAction, ...]
    rate_remaining: int | None = None
    rate_reset: int | None = None
    retry_after: int | None = None
    retry_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["queue_before"] = self.queue_before.to_dict()
        result["actions"] = [action.to_dict() for action in self.actions]
        return result


def classify_queue(
    items: Iterable[GitHubWorkItem],
    claims: Iterable[GitHubClaim],
    *,
    now: datetime,
    claim_ttl_seconds: int,
) -> QueueSnapshot:
    """Classify all open company requests from bulk issue, PR, and comment reads."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    open_issues = {
        item.number: item for item in items if item.kind == "issue" and item.state == "open"
    }
    linked: set[int] = set()
    for item in items:
        if item.kind != "pr" or item.state != "open":
            continue
        linked.update(
            number for number in _linked_issue_numbers(item.body) if number in open_issues
        )

    cutoff = now.astimezone(UTC) - timedelta(seconds=claim_ttl_seconds)
    fresh_claims: set[int] = set()
    unparseable = 0
    for claim in claims:
        if claim.issue_number not in open_issues or not claim.body.startswith(CLAIM_MARKER):
            continue
        created_at = _parse_timestamp(claim.created_at)
        if created_at is None:
            # Unknown claim age is fail-closed for queue sizing, but observable.
            unparseable += 1
            fresh_claims.add(claim.issue_number)
        elif created_at >= cutoff:
            fresh_claims.add(claim.issue_number)

    unavailable = fresh_claims | linked
    import_open = sum(IMPORT_LABEL in item.labels for item in open_issues.values())
    return QueueSnapshot(
        total_open=len(open_issues),
        available=len(open_issues.keys() - unavailable),
        fresh_claimed=len(fresh_claims),
        active_linked_pr=len(linked),
        import_open=import_open,
        human_open=len(open_issues) - import_open,
        unparseable_claims=unparseable,
        fresh_claim_issue_numbers=tuple(sorted(fresh_claims)),
        linked_pr_issue_numbers=tuple(sorted(linked)),
    )


class QueueRefiller:
    def __init__(
        self,
        *,
        coordinator: CandidateIssueCoordinator,
        ledger: CandidateLedger,
        items: list[GitHubWorkItem],
        claims: list[GitHubClaim],
        policy: QueuePolicy,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.coordinator = coordinator
        self.ledger = ledger
        self.items = items
        self.claims = claims
        self.policy = policy
        self.now = now or (lambda: datetime.now(UTC))
        self.sleep = sleep
        self.jitter = jitter

    async def run(
        self,
        companies: Iterable[CompanyImpact],
        *,
        mode: Literal["report", "dry-run", "refill"],
        admission_block: str | None = None,
    ) -> QueueRunReport:
        now = self.now()
        before = classify_queue(
            self.items,
            self.claims,
            now=now,
            claim_ttl_seconds=self.policy.claim_ttl_seconds,
        )
        day_start = int(
            now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        created_today = self.ledger.count_created_since(day_start)
        if admission_block is not None:
            return self._report(
                mode=mode,
                status=admission_block,
                before=before,
                created_today=created_today,
                requested=0,
            )
        requested, status = _requested_creates(before, created_today, self.policy)
        if mode == "report" or requested == 0:
            return self._report(
                mode=mode,
                status=status,
                before=before,
                created_today=created_today,
                requested=requested,
            )

        hard_skips: Counter[str] = Counter()
        actions: list[CandidateIssueAction] = []
        inspected = 0
        selected = 0
        created = 0
        retry_after: int | None = None
        retry_at: int | None = None
        reserved_sources: set[str] = set()
        for company in sorted(companies, key=company_impact_rank_key):
            if selected >= requested:
                break
            inspected += 1
            try:
                candidate = Candidate.from_impact(company)
            except ValueError:
                hard_skips["unsupported_family"] += 1
                continue
            if candidate.source_key in reserved_sources:
                hard_skips["selected_source_key"] += 1
                continue
            plan = self.coordinator.plan(candidate)
            if not plan.eligible:
                for evidence in plan.hard_skips:
                    hard_skips[evidence.code] += 1
                continue

            selected += 1
            reserved_sources.add(candidate.source_key)
            try:
                action = await self.coordinator.create(
                    candidate,
                    dry_run=mode == "dry-run",
                )
            except GitHubRateLimitError as exc:
                status = "rate_limited"
                retry_after = exc.retry_after
                retry_at = _retry_at(exc, now)
                break
            actions.append(action)
            if action.action in {"created", "created_reconciled"}:
                created += 1
                if selected < requested:
                    await self.sleep(
                        self.jitter(
                            self.policy.jitter_min_seconds,
                            self.policy.jitter_max_seconds,
                        )
                    )

        if status != "rate_limited":
            if selected < requested:
                status = "candidate_pool_exhausted"
            elif mode == "dry-run":
                status = "dry_run"
            else:
                status = "refilled"
        return self._report(
            mode=mode,
            status=status,
            before=before,
            created_today=created_today,
            requested=requested,
            inspected=inspected,
            selected=selected,
            created=created,
            hard_skips=dict(sorted(hard_skips.items())),
            actions=tuple(actions),
            retry_after=retry_after,
            retry_at=retry_at,
        )

    def _report(
        self,
        *,
        mode: Literal["report", "dry-run", "refill"],
        status: str,
        before: QueueSnapshot,
        created_today: int,
        requested: int,
        inspected: int = 0,
        selected: int = 0,
        created: int = 0,
        hard_skips: dict[str, int] | None = None,
        actions: tuple[CandidateIssueAction, ...] = (),
        retry_after: int | None = None,
        retry_at: int | None = None,
    ) -> QueueRunReport:
        client = self.coordinator.client
        return QueueRunReport(
            mode=mode,
            status=status,
            queue_before=before,
            available_after=before.available + created,
            total_open_after=before.total_open + created,
            created_today_before=created_today,
            requested_creates=requested,
            inspected_candidates=inspected,
            selected_candidates=selected,
            created=created,
            hard_skip_counts=hard_skips or {},
            actions=actions,
            rate_remaining=getattr(client, "rate_remaining", None),
            rate_reset=getattr(client, "rate_reset", None),
            retry_after=retry_after,
            retry_at=retry_at,
        )


def _requested_creates(
    queue: QueueSnapshot,
    created_today: int,
    policy: QueuePolicy,
) -> tuple[int, str]:
    if queue.available >= policy.low_water:
        return 0, "healthy"
    capacities = (
        policy.target - queue.available,
        policy.hard_cap - queue.total_open,
        policy.per_tick_cap,
        policy.daily_cap - created_today,
        policy.rollout_cap,
    )
    requested = max(0, min(capacities))
    if requested:
        return requested, "refill_needed"
    if queue.total_open >= policy.hard_cap:
        return 0, "hard_cap"
    if created_today >= policy.daily_cap:
        return 0, "daily_cap"
    return 0, "no_capacity"


def _linked_issue_numbers(body: str) -> set[int]:
    return {int(match.group(1)) for match in _CLOSES_RE.finditer(body or "")}


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _retry_at(error: GitHubRateLimitError, now: datetime) -> int | None:
    candidates = [value for value in (error.reset_at,) if value is not None]
    if error.retry_after is not None:
        candidates.append(int(now.timestamp()) + max(0, error.retry_after))
    return max(candidates) if candidates else None


__all__ = [
    "IMPORT_LABEL",
    "QueuePolicy",
    "QueueRefiller",
    "QueueRunReport",
    "QueueSnapshot",
    "classify_queue",
]
