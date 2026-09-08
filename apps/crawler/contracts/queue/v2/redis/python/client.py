"""Typed client for the inactive Redis queue-v2 lifecycle candidate."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import NoScriptError, RedisError

MAX_INTEGER = 9_999_999_999_999
_SCRIPT = (Path(__file__).parents[1] / "lifecycle.lua").read_text(encoding="utf-8")
_NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_CANONICAL_UINT_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_TOKEN_RE = re.compile(r"([1-9][0-9]*):([1-9][0-9]*)\Z")
_OWNERS = frozenset({"python", "go"})
_OPERATIONS = frozenset(
    {"initialize", "register", "claim", "heartbeat", "complete", "reschedule", "reap"}
)
_ROUTE_FENCE_REASONS = frozenset(
    {"shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"}
)
_TASK_FENCE_REASONS = _ROUTE_FENCE_REASONS | {
    "config_revision_mismatch",
    "record_fence_mismatch",
}
_FENCE_REASONS = {
    "initialize": _ROUTE_FENCE_REASONS,
    "register": _ROUTE_FENCE_REASONS,
    "claim": _TASK_FENCE_REASONS,
    "heartbeat": _TASK_FENCE_REASONS | {"claim_token_mismatch"},
    "complete": _TASK_FENCE_REASONS | {"claim_token_mismatch"},
    "reschedule": _TASK_FENCE_REASONS | {"claim_token_mismatch"},
    "reap": _TASK_FENCE_REASONS | {"claim_token_mismatch"},
}
_NOT_CURRENT_REASONS = {
    "initialize": frozenset({"redis_time_invalid", "invalid_route", "namespace_corrupt"}),
    "register": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "task_already_exists",
        }
    ),
    "claim": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "config_missing",
            "record_missing",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
            "not_due",
            "invalid_lease_ttl",
            "numeric_overflow",
            "claim_sequence_exhausted",
            "claim_sequence_corrupt",
        }
    ),
    "heartbeat": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "config_missing",
            "record_missing",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
            "lease_expired",
            "invalid_lease_ttl",
            "numeric_overflow",
            "lease_not_extended",
        }
    ),
    "complete": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "config_missing",
            "record_missing",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
            "lease_expired",
        }
    ),
    "reschedule": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "config_missing",
            "record_missing",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
            "lease_expired",
            "invalid_reschedule_delay",
            "numeric_overflow",
        }
    ),
    "reap": frozenset(
        {
            "redis_time_invalid",
            "invalid_route",
            "invalid_task_identity",
            "namespace_corrupt",
            "config_missing",
            "record_missing",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
            "invalid_max_failures",
            "lease_active",
            "numeric_overflow",
        }
    ),
}
_ACCEPTED_REASONS = {
    "initialize": frozenset({"initialized", "already_initialized"}),
    "register": frozenset({"registered"}),
    "claim": frozenset({"claimed"}),
    "heartbeat": frozenset({"lease_extended"}),
    "complete": frozenset({"completed"}),
    "reschedule": frozenset({"rescheduled"}),
    "reap": frozenset({"requeued", "dead_lettered"}),
}


class Decision(StrEnum):
    ACCEPTED = "accepted"
    FENCED = "fenced"
    NOT_CURRENT = "not_current"
    TRANSPORT_ERROR = "transport-error"


@dataclass(frozen=True, slots=True)
class RouteIdentity:
    shard_id: str
    routing_epoch: int
    engine_owner: str

    def __post_init__(self) -> None:
        if not isinstance(self.shard_id, str) or not self.shard_id:
            raise ValueError("shard_id must be non-empty")
        _bounded_int(self.routing_epoch, name="routing_epoch", minimum=1)
        if self.engine_owner not in _OWNERS:
            raise ValueError("engine_owner must be 'python' or 'go'")


@dataclass(frozen=True, slots=True)
class Fence:
    task_id: str
    route: RouteIdentity
    config_revision: int
    claim_token: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError("task_id must be non-empty")
        _bounded_int(self.config_revision, name="config_revision", minimum=1)
        _validate_token(self.claim_token, expected_epoch=self.route.routing_epoch)


@dataclass(frozen=True, slots=True)
class TransitionResult:
    decision: Decision
    reason: str
    server_time_ms: int | None = None
    claim_token: str | None = None
    value: int | None = None

    @property
    def accepted(self) -> bool:
        return self.decision is Decision.ACCEPTED


@dataclass(slots=True)
class LeaseHandle:
    """Claim handle whose loss event is the worker cancellation contract."""

    fence: Fence
    lease_until_ms: int
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    loss: TransitionResult | None = None
    closed: bool = False

    def __post_init__(self) -> None:
        _bounded_int(self.lease_until_ms, name="lease_until_ms", minimum=1)

    def mark_lost(self, outcome: TransitionResult) -> None:
        if outcome.accepted:
            raise ValueError("an accepted transition is not lease loss")
        if self.loss is None:
            self.loss = outcome
            self.lost.set()

    def raise_if_lost(self) -> None:
        if self.loss is not None:
            raise LeaseLostError(self.loss)


class LeaseLostError(RuntimeError):
    def __init__(self, outcome: TransitionResult) -> None:
        super().__init__(f"queue-v2 lease lost: {outcome.decision.value}/{outcome.reason}")
        self.outcome = outcome


@dataclass(frozen=True, slots=True)
class ClaimResult:
    transition: TransitionResult
    lease: LeaseHandle | None


@dataclass(frozen=True, slots=True)
class _Keys:
    route: str
    configs: str
    records: str
    ready: str
    inflight: str
    dead_letter: str
    terminal: str

    def ordered(self) -> list[str]:
        return [
            self.route,
            self.configs,
            self.records,
            self.ready,
            self.inflight,
            self.dead_letter,
            self.terminal,
        ]


class QueueV2Candidate:
    """Explicitly inactive adapter around the candidate lifecycle script."""

    def __init__(self, redis: Redis, *, namespace: str) -> None:
        if not isinstance(namespace, str) or not _NAMESPACE_RE.fullmatch(namespace):
            raise ValueError("namespace must be 1-64 safe key characters")
        tag = f"queue-v2-candidate:{{{namespace}}}"
        self._redis = redis
        self._keys = _Keys(
            route=f"{tag}:route",
            configs=f"{tag}:configs",
            records=f"{tag}:records",
            ready=f"{tag}:ready",
            inflight=f"{tag}:inflight",
            dead_letter=f"{tag}:dead-letter",
            terminal=f"{tag}:terminal",
        )
        self._sha: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._keys.ordered())

    async def initialize(self, route: RouteIdentity) -> TransitionResult:
        return await self._execute("initialize", route=route)

    async def register(
        self, task_id: str, *, route: RouteIdentity, config_revision: int
    ) -> TransitionResult:
        _validate_task_revision(task_id, config_revision)
        return await self._execute(
            "register", task_id=task_id, route=route, config_revision=config_revision
        )

    async def claim(
        self,
        task_id: str,
        *,
        route: RouteIdentity,
        config_revision: int,
        lease_ttl_ms: int,
    ) -> ClaimResult:
        _validate_task_revision(task_id, config_revision)
        _bounded_int(lease_ttl_ms, name="lease_ttl_ms", minimum=1)
        transition = await self._execute(
            "claim",
            task_id=task_id,
            route=route,
            config_revision=config_revision,
            lease_ttl_ms=lease_ttl_ms,
        )
        if not transition.accepted:
            return ClaimResult(transition=transition, lease=None)
        assert transition.claim_token is not None and transition.value is not None
        return ClaimResult(
            transition=transition,
            lease=LeaseHandle(
                fence=Fence(task_id, route, config_revision, transition.claim_token),
                lease_until_ms=transition.value,
            ),
        )

    async def heartbeat(self, lease: LeaseHandle, *, lease_ttl_ms: int) -> TransitionResult:
        _bounded_int(lease_ttl_ms, name="lease_ttl_ms", minimum=1)
        lease.raise_if_lost()
        result = await self._execute_for_lease(
            "heartbeat",
            lease,
            lease_ttl_ms=lease_ttl_ms,
            previous_lease_until=lease.lease_until_ms,
        )
        if result.accepted:
            assert result.value is not None
            lease.lease_until_ms = result.value
        return result

    async def complete(self, lease: LeaseHandle) -> TransitionResult:
        lease.raise_if_lost()
        result = await self._execute_for_lease("complete", lease)
        if result.accepted:
            lease.closed = True
        return result

    async def reschedule(self, lease: LeaseHandle, *, delay_ms: int = 0) -> TransitionResult:
        _bounded_int(delay_ms, name="delay_ms", minimum=0)
        lease.raise_if_lost()
        result = await self._execute_for_lease("reschedule", lease, reschedule_delay_ms=delay_ms)
        if result.accepted:
            lease.closed = True
        return result

    async def reap(self, lease: LeaseHandle, *, max_failures: int) -> TransitionResult:
        _bounded_int(max_failures, name="max_failures", minimum=1)
        lease.raise_if_lost()
        result = await self._execute_for_lease("reap", lease, max_failures=max_failures)
        if result.accepted:
            lease.closed = True
        return result

    async def _execute_for_lease(
        self,
        operation: str,
        lease: LeaseHandle,
        *,
        lease_ttl_ms: int = 0,
        reschedule_delay_ms: int = 0,
        max_failures: int = 0,
        previous_lease_until: int | None = None,
    ) -> TransitionResult:
        fence = lease.fence
        result = await self._execute(
            operation,
            task_id=fence.task_id,
            route=fence.route,
            config_revision=fence.config_revision,
            claim_token=fence.claim_token,
            lease_ttl_ms=lease_ttl_ms,
            reschedule_delay_ms=reschedule_delay_ms,
            max_failures=max_failures,
            previous_lease_until=previous_lease_until,
        )
        if not result.accepted:
            lease.mark_lost(result)
        return result

    async def _execute(
        self,
        operation: str,
        *,
        task_id: str = "",
        route: RouteIdentity,
        config_revision: int = 0,
        claim_token: str = "",
        lease_ttl_ms: int = 0,
        reschedule_delay_ms: int = 0,
        max_failures: int = 0,
        previous_lease_until: int | None = None,
    ) -> TransitionResult:
        arguments = [
            operation,
            task_id,
            route.shard_id,
            str(route.routing_epoch),
            route.engine_owner,
            str(config_revision),
            claim_token,
            str(lease_ttl_ms),
            str(reschedule_delay_ms),
            str(max_failures),
        ]
        try:
            if self._sha is None:
                self._sha = await self._redis.script_load(_SCRIPT)
            try:
                raw = await self._redis.evalsha(
                    self._sha, len(self._keys.ordered()), *self._keys.ordered(), *arguments
                )
            except NoScriptError:
                self._sha = await self._redis.script_load(_SCRIPT)
                raw = await self._redis.evalsha(
                    self._sha, len(self._keys.ordered()), *self._keys.ordered(), *arguments
                )
        except (RedisError, OSError, TimeoutError):
            return TransitionResult(Decision.TRANSPORT_ERROR, "redis_error")
        return _decode_result(
            operation,
            raw,
            route=route,
            expected_token=claim_token or None,
            previous_lease_until=previous_lease_until,
        )


def _bounded_int(value: Any, *, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > MAX_INTEGER:
        raise ValueError(f"{name} must be between {minimum} and {MAX_INTEGER}")
    return value


def _validate_task_revision(task_id: str, config_revision: int) -> None:
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id must be non-empty")
    _bounded_int(config_revision, name="config_revision", minimum=1)


def _canonical_uint(text: str, *, minimum: int = 0) -> int:
    if not _CANONICAL_UINT_RE.fullmatch(text):
        raise ValueError("not canonical decimal")
    value = int(text)
    return _bounded_int(value, name="wire integer", minimum=minimum)


def _validate_token(token: str, *, expected_epoch: int) -> tuple[int, int]:
    if not isinstance(token, str):
        raise ValueError("claim_token must be a string")
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        raise ValueError("claim_token must contain canonical epoch:sequence")
    epoch = _canonical_uint(match.group(1), minimum=1)
    sequence = _canonical_uint(match.group(2), minimum=1)
    if epoch != expected_epoch:
        raise ValueError("claim_token epoch differs from route")
    return epoch, sequence


def _decode_result(
    operation: str,
    raw: Any,
    *,
    route: RouteIdentity,
    expected_token: str | None = None,
    previous_lease_until: int | None = None,
) -> TransitionResult:
    invalid = TransitionResult(Decision.TRANSPORT_ERROR, "invalid_redis_reply")
    if operation not in _OPERATIONS or not isinstance(raw, (list, tuple)) or len(raw) != 5:
        return invalid
    try:
        decision_text, reason, time_text, token, value_text = [_wire_text(item) for item in raw]
        decision = Decision(decision_text)
        if decision is Decision.TRANSPORT_ERROR:
            return invalid
        server_time = _canonical_uint(time_text)
        value = _canonical_uint(value_text) if value_text else None
    except (TypeError, ValueError, UnicodeError):
        return invalid
    if not reason or (reason != "redis_time_invalid" and server_time == 0):
        return invalid

    if decision is Decision.ACCEPTED:
        if reason not in _ACCEPTED_REASONS[operation]:
            return invalid
        if operation == "initialize":
            valid = not token and value is None
        elif operation == "register":
            valid = not token and value == server_time
        elif operation == "claim":
            try:
                _validate_token(token, expected_epoch=route.routing_epoch)
            except ValueError:
                valid = False
            else:
                valid = value is not None and value > server_time
        elif operation == "heartbeat":
            valid = (
                token == expected_token
                and value is not None
                and value > server_time
                and previous_lease_until is not None
                and value > previous_lease_until
            )
        elif operation == "complete":
            valid = token == expected_token and value is None
        elif operation == "reschedule":
            valid = token == expected_token and value is not None and value >= server_time
        else:  # reap
            valid = token == expected_token and value is not None and value >= 1
        if not valid:
            return invalid
    elif decision is Decision.FENCED:
        if reason not in _FENCE_REASONS[operation] or token or value is not None:
            return invalid
    else:
        if reason not in _NOT_CURRENT_REASONS[operation]:
            return invalid
        if reason == "redis_time_invalid":
            valid = server_time == 0 and not token and value is None
        elif reason == "not_due":
            valid = operation == "claim" and not token and value is not None and value > server_time
        elif reason == "lease_active":
            valid = (
                operation == "reap"
                and token == expected_token
                and value is not None
                and value > server_time
            )
        else:
            valid = not token and value is None
        if not valid:
            return invalid

    return TransitionResult(
        decision=decision,
        reason=reason,
        server_time_ms=server_time,
        claim_token=token or None,
        value=value,
    )


def _wire_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise TypeError("Redis reply element is not text")
