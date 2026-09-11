"""Redis lifecycle for the inactive Lightpanda B0 scrape lane.

This queue is deliberately separate from the current crawler queues.  Python
remains the claimant and owns retries, parsing, and database writes; the Go
service only renders an already-claimed envelope.  Callers must reserve local
renderer capacity *before* calling :meth:`claim_next`.

The Lua script reads the existing ``ratelimit:<domain>`` and
``delay:<domain>`` keys so politeness is shared with the current workers.  The
script therefore requires the crawler's current single Redis deployment; it
must not be enabled on Redis Cluster, where those dynamic keys cannot share a
hash slot with the queue namespace.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from redis.asyncio import Redis
from redis.exceptions import NoScriptError, RedisError

from src.config import settings
from src.lightpanda.identity import (
    validate_lightpanda_b0_identifier,
    validate_lightpanda_b0_namespace,
)
from src.lightpanda.routing import RenderAssignment, resolve_render_assignment

MAX_INTEGER = 9_999_999_999_999
MAX_PAYLOAD_BYTES = 128 * 1024
MAX_RECORDS = 512
SCAN_LIMIT = 64
MAX_LEASE_TTL_MS = 60 * 60 * 1000
MAX_FAILURES = 100
POLICY_KEY = "lightpanda-b0-v1"

_SCRIPT = (Path(__file__).parent / "lua" / "lightpanda_b0_queue.lua").read_text(encoding="utf-8")
_SAFE_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\."
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^([1-9][0-9]*):([1-9][0-9]*)$")


class Decision(StrEnum):
    ACCEPTED = "accepted"
    FENCED = "fenced"
    NOT_CURRENT = "not_current"
    TRANSPORT_ERROR = "transport-error"


_OPERATIONS = frozenset(
    {
        "initialize",
        "register",
        "claim_next",
        "heartbeat",
        "complete",
        "reschedule_at",
        "reactivate",
        "reap_expired",
        "audit",
    }
)
_FENCE_REASONS = frozenset(
    {
        "shard_id_mismatch",
        "routing_epoch_mismatch",
        "engine_owner_mismatch",
        "config_revision_mismatch",
        "config_revision_not_advanced",
        "record_fence_mismatch",
        "claim_token_mismatch",
        "lease_deadline_mismatch",
        "payload_digest_mismatch",
    }
)
_COMMON_NOT_CURRENT = frozenset({"redis_time_invalid", "invalid_route", "namespace_corrupt"})
_RECORD_NOT_CURRENT = _COMMON_NOT_CURRENT | {
    "record_corrupt",
    "conservation_violation",
    "state_mismatch",
    "origin_holder_corrupt",
}
_REASONS: dict[str, dict[Decision, frozenset[str]]] = {
    "initialize": {
        Decision.ACCEPTED: frozenset({"initialized", "already_initialized"}),
        Decision.FENCED: frozenset(
            {"shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"}
        ),
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT,
    },
    "register": {
        Decision.ACCEPTED: frozenset({"registered", "already_registered"}),
        Decision.FENCED: frozenset(
            {"shard_id_mismatch", "routing_epoch_mismatch", "engine_owner_mismatch"}
        ),
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT
        | {
            "invalid_task_envelope",
            "namespace_full",
            "task_already_exists",
            "record_corrupt",
            "conservation_violation",
        },
    },
    "claim_next": {
        Decision.ACCEPTED: frozenset({"claimed"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT
        | {
            "invalid_lease_ttl",
            "invalid_claim_policy",
            "record_corrupt",
            "conservation_violation",
            "origin_holder_corrupt",
            "rate_limit_corrupt",
            "delay_corrupt",
            "numeric_overflow",
            "claim_sequence_exhausted",
            "no_work",
        },
    },
    "heartbeat": {
        Decision.ACCEPTED: frozenset({"lease_extended"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _RECORD_NOT_CURRENT
        | {
            "invalid_task_identity",
            "invalid_lease_fence",
            "invalid_lease_ttl",
            "lease_expired",
            "numeric_overflow",
            "lease_not_extended",
        },
    },
    "complete": {
        Decision.ACCEPTED: frozenset({"completed"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _RECORD_NOT_CURRENT
        | {"invalid_task_identity", "invalid_lease_fence", "lease_expired"},
    },
    "reschedule_at": {
        Decision.ACCEPTED: frozenset({"rescheduled"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _RECORD_NOT_CURRENT
        | {
            "invalid_task_identity",
            "invalid_lease_fence",
            "invalid_ready_at",
            "lease_expired",
        },
    },
    "reactivate": {
        Decision.ACCEPTED: frozenset({"reactivated"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT
        | {
            "invalid_task_envelope",
            "record_corrupt",
            "conservation_violation",
            "state_mismatch",
        },
    },
    "reap_expired": {
        Decision.ACCEPTED: frozenset({"reaped"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT
        | {"invalid_reap_policy", "conservation_violation", "origin_holder_corrupt"},
    },
    "audit": {
        Decision.ACCEPTED: frozenset({"audit_ok"}),
        Decision.FENCED: _FENCE_REASONS,
        Decision.NOT_CURRENT: _COMMON_NOT_CURRENT
        | {"audit_too_large", "conservation_violation", "origin_holder_corrupt"},
    },
}


@dataclass(frozen=True, slots=True)
class RouteIdentity:
    shard_id: str
    routing_epoch: int
    engine_owner: str = "python"

    def __post_init__(self) -> None:
        _safe_identifier(self.shard_id, "shard_id")
        _bounded_int(self.routing_epoch, "routing_epoch", minimum=1)
        if self.engine_owner != "python":
            raise ValueError("the Lightpanda B0 queue is owned by Python")


@dataclass(frozen=True, slots=True)
class LightpandaB0Task:
    """Canonical, immutable task envelope stored directly in Redis."""

    task_id: str
    board_id: str
    source_url: str
    policy_key: str
    domain: str
    route: RouteIdentity
    config_revision: int
    initial_ready_at_ms: int
    assignment: RenderAssignment
    payload: str
    payload_sha256: str
    payload_sha1: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        board_id: str,
        source_url: str,
        policy_key: str,
        domain: str,
        route: RouteIdentity,
        config_revision: int,
        initial_ready_at_ms: int,
        assignment: RenderAssignment,
    ) -> LightpandaB0Task:
        _safe_identifier(task_id, "task_id")
        _safe_identifier(board_id, "board_id")
        if policy_key != POLICY_KEY:
            raise ValueError(f"policy_key must be {POLICY_KEY!r}")
        normalized_domain = _validated_domain(domain)
        _bounded_int(config_revision, "config_revision", minimum=1)
        _bounded_int(initial_ready_at_ms, "initial_ready_at_ms", minimum=0)
        validated_assignment, parser_config = _validated_assignment(assignment)

        if (
            not isinstance(source_url, str)
            or not source_url
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in source_url
            )
        ):
            raise ValueError("source_url must be a non-empty URL without whitespace or controls")
        try:
            parsed = urlsplit(source_url)
            port = parsed.port
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ValueError("source_url is invalid") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in (None, 443)
        ):
            raise ValueError(
                "source_url must be credential-free HTTPS on port 443 without a fragment"
            )
        try:
            source_domain = parsed.hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("source_url hostname is invalid") from exc
        if source_domain != normalized_domain:
            raise ValueError("domain must exactly match the source_url hostname")

        envelope: dict[str, Any] = {
            "schema_version": "lightpanda-b0-task-v1",
            "task_kind": "scrape",
            "task_id": task_id,
            "board_id": board_id,
            "source_url": source_url,
            "policy_key": policy_key,
            "domain": normalized_domain,
            "shard_id": route.shard_id,
            "routing_epoch": route.routing_epoch,
            "engine_owner": route.engine_owner,
            "config_revision": config_revision,
            "initial_ready_at_ms": initial_ready_at_ms,
            "browser_backend": validated_assignment.browser_backend,
            "routing_revision": validated_assignment.routing_revision,
            "scraper_type": validated_assignment.scraper_type,
            "scraper_step": validated_assignment.scraper_step,
            "render": True,
            "wait": "load",
            "wait_fallback": None,
            "timeout_ms": validated_assignment.timeout_ms,
            "parser_config": parser_config,
            "assignment_digest_sha256": validated_assignment.config_digest_sha256,
        }
        try:
            payload = json.dumps(
                envelope,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("parser_config must contain only finite JSON values") from exc
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("task envelope exceeds the 128 KiB limit")
        return cls(
            task_id=task_id,
            board_id=board_id,
            source_url=source_url,
            policy_key=policy_key,
            domain=normalized_domain,
            route=route,
            config_revision=config_revision,
            initial_ready_at_ms=initial_ready_at_ms,
            assignment=validated_assignment,
            payload=payload,
            payload_sha256=hashlib.sha256(encoded).hexdigest(),
            payload_sha1=hashlib.sha1(encoded, usedforsecurity=False).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class TransitionResult:
    decision: Decision
    reason: str
    server_time_ms: int | None = None
    value: int | None = None
    secondary_value: int | None = None

    @property
    def accepted(self) -> bool:
        return self.decision is Decision.ACCEPTED


@dataclass(slots=True)
class Lease:
    task: LightpandaB0Task
    claim_token: str
    lease_until_ms: int

    def __post_init__(self) -> None:
        match = _TOKEN_RE.fullmatch(self.claim_token)
        if (
            match is None
            or int(match.group(1)) != self.task.route.routing_epoch
            or not 1 <= int(match.group(2)) <= MAX_INTEGER
        ):
            raise ValueError("invalid claim token")
        _bounded_int(self.lease_until_ms, "lease_until_ms", minimum=1)


@dataclass(frozen=True, slots=True)
class ClaimResult:
    transition: TransitionResult
    lease: Lease | None


@dataclass(frozen=True, slots=True)
class _Keys:
    route: str
    records: str
    ready: str
    inflight: str
    dead: str
    terminal: str
    origin_holders: str

    def ordered(self) -> list[str]:
        return [
            self.route,
            self.records,
            self.ready,
            self.inflight,
            self.dead,
            self.terminal,
            self.origin_holders,
        ]


class LightpandaB0Queue:
    """Strict async adapter for the inactive B0 Redis lifecycle."""

    def __init__(self, redis: Redis, *, namespace: str) -> None:
        namespace = validate_lightpanda_b0_namespace(namespace)
        tag = f"lightpanda-b0:{{{namespace}}}"
        self._redis = redis
        self._keys = _Keys(
            route=f"{tag}:route",
            records=f"{tag}:records",
            ready=f"{tag}:ready",
            inflight=f"{tag}:inflight",
            dead=f"{tag}:dead",
            terminal=f"{tag}:terminal",
            origin_holders=f"{tag}:origin-holders",
        )
        self._sha: str | None = None

    async def initialize(self, route: RouteIdentity) -> TransitionResult:
        return await self._transition("initialize", route=route)

    async def register(self, task: LightpandaB0Task) -> TransitionResult:
        _validate_task_identity(task)
        return await self._transition("register", route=task.route, task=task)

    async def claim_next(
        self,
        route: RouteIdentity,
        *,
        lease_ttl_ms: int,
    ) -> ClaimResult:
        _bounded_int(lease_ttl_ms, "lease_ttl_ms", minimum=1, maximum=MAX_LEASE_TTL_MS)
        raw = await self._invoke(
            "claim_next",
            route=route,
            lease_ttl_ms=lease_ttl_ms,
            default_delay_seconds=_validated_delay(settings.throttle_delay_default),
        )
        transition = self._decode_transition(
            "claim_next", raw, route=route, lease_ttl_ms=lease_ttl_ms
        )
        if not transition.accepted:
            return ClaimResult(transition=transition, lease=None)
        try:
            task = self._decode_claim_task(route, raw)
            fields = [_wire_text(value) for value in raw]
            token = _required_text(fields, 4, "claim token")
            lease_until_ms = _required_uint(fields, 5, "lease until")
        except (TypeError, UnicodeError, ValueError):
            return ClaimResult(
                transition=TransitionResult(Decision.TRANSPORT_ERROR, "payload_integrity_failure"),
                lease=None,
            )
        return ClaimResult(
            transition=transition,
            lease=Lease(task=task, claim_token=token, lease_until_ms=lease_until_ms),
        )

    async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
        _bounded_int(lease_ttl_ms, "lease_ttl_ms", minimum=1, maximum=MAX_LEASE_TTL_MS)
        outcome = await self._transition(
            "heartbeat",
            route=lease.task.route,
            task=lease.task,
            lease=lease,
            lease_ttl_ms=lease_ttl_ms,
        )
        if outcome.accepted:
            assert outcome.server_time_ms is not None
            lease.lease_until_ms = outcome.server_time_ms + lease_ttl_ms
        return outcome

    async def complete(self, lease: Lease) -> TransitionResult:
        return await self._transition(
            "complete", route=lease.task.route, task=lease.task, lease=lease
        )

    async def reschedule_at(self, lease: Lease, *, ready_at_ms: int) -> TransitionResult:
        _bounded_int(ready_at_ms, "ready_at_ms", minimum=0)
        return await self._transition(
            "reschedule_at",
            route=lease.task.route,
            task=lease.task,
            lease=lease,
            ready_at_ms=ready_at_ms,
        )

    async def reactivate(
        self, task: LightpandaB0Task, *, previous_payload_sha256: str
    ) -> TransitionResult:
        if not _SHA256_RE.fullmatch(previous_payload_sha256):
            raise ValueError("previous_payload_sha256 must be lowercase SHA-256")
        _validate_task_identity(task)
        raw = await self._invoke(
            "reactivate",
            route=task.route,
            task=task,
            previous_payload_sha256=previous_payload_sha256,
        )
        return self._decode_transition("reactivate", raw, task=task)

    async def reap_expired(self, route: RouteIdentity, *, max_failures: int) -> TransitionResult:
        _bounded_int(max_failures, "max_failures", minimum=1, maximum=MAX_FAILURES)
        return await self._transition("reap_expired", route=route, max_failures=max_failures)

    async def audit_conservation(self, route: RouteIdentity) -> TransitionResult:
        return await self._transition("audit", route=route)

    async def _transition(
        self,
        operation: str,
        *,
        route: RouteIdentity,
        task: LightpandaB0Task | None = None,
        lease: Lease | None = None,
        lease_ttl_ms: int = 0,
        ready_at_ms: int = 0,
        max_failures: int = 0,
    ) -> TransitionResult:
        raw = await self._invoke(
            operation,
            route=route,
            task=task,
            lease=lease,
            lease_ttl_ms=lease_ttl_ms,
            ready_at_ms=ready_at_ms,
            max_failures=max_failures,
        )
        return self._decode_transition(
            operation,
            raw,
            route=route,
            task=task,
            lease=lease,
            lease_ttl_ms=lease_ttl_ms,
            ready_at_ms=ready_at_ms,
        )

    async def _invoke(
        self,
        operation: str,
        *,
        route: RouteIdentity,
        task: LightpandaB0Task | None = None,
        lease: Lease | None = None,
        lease_ttl_ms: int = 0,
        ready_at_ms: int = 0,
        max_failures: int = 0,
        default_delay_seconds: float = 0.0,
        previous_payload_sha256: str = "",
    ) -> list[Any]:
        if operation not in _OPERATIONS:
            raise ValueError("unknown queue operation")
        argv = [
            operation,
            route.shard_id,
            str(route.routing_epoch),
            route.engine_owner,
            task.task_id if task else "",
            str(task.config_revision) if task else "0",
            lease.claim_token if lease else "",
            str(lease_ttl_ms),
            str(
                ready_at_ms
                if operation == "reschedule_at"
                else task.initial_ready_at_ms
                if task
                else 0
            ),
            str(max_failures),
            task.payload if task else "",
            task.payload_sha256 if task else "",
            task.payload_sha1 if task else "",
            str(SCAN_LIMIT),
            _decimal_wire(default_delay_seconds),
            previous_payload_sha256,
            str(lease.lease_until_ms if lease else 0),
        ]
        try:
            if self._sha is None:
                self._sha = cast(str, await self._redis.script_load(_SCRIPT))
            try:
                raw = await self._redis.evalsha(
                    self._sha,
                    len(self._keys.ordered()),
                    *self._keys.ordered(),
                    *argv,
                )
            except NoScriptError:
                self._sha = cast(str, await self._redis.script_load(_SCRIPT))
                raw = await self._redis.evalsha(
                    self._sha,
                    len(self._keys.ordered()),
                    *self._keys.ordered(),
                    *argv,
                )
        except (RedisError, OSError, TimeoutError):
            return [
                "transport-error",
                "redis_error",
                "0",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        if not isinstance(raw, list):
            return [
                "transport-error",
                "invalid_reply",
                "0",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        return raw

    @staticmethod
    def _decode_transition(
        operation: str,
        raw: list[Any],
        *,
        route: RouteIdentity | None = None,
        task: LightpandaB0Task | None = None,
        lease: Lease | None = None,
        lease_ttl_ms: int = 0,
        ready_at_ms: int = 0,
    ) -> TransitionResult:
        if len(raw) != 12:
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        try:
            fields = [_wire_text(value) for value in raw]
            decision = Decision(fields[0])
        except (TypeError, UnicodeError, ValueError):
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        reason = fields[1]
        if decision is Decision.TRANSPORT_ERROR:
            if reason not in {"redis_error", "invalid_reply"}:
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
            return TransitionResult(decision, reason)
        if reason not in _REASONS.get(operation, {}).get(decision, frozenset()):
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        try:
            server_time_ms = _required_uint(fields, 2, "server time")
            value = _optional_uint(fields[10], "value")
            secondary_value = _optional_uint(fields[11], "secondary value")
        except ValueError:
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        if reason != "redis_time_invalid" and server_time_ms < 1:
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        full_record = decision is Decision.ACCEPTED and operation == "claim_next"
        fence_record = decision is Decision.ACCEPTED and operation in {
            "heartbeat",
            "complete",
            "reschedule_at",
        }
        identity_record = decision is Decision.ACCEPTED and operation == "reactivate"
        if decision is not Decision.ACCEPTED and any(fields[index] for index in range(3, 10)):
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        if full_record or fence_record:
            try:
                returned_task_id = _required_text(fields, 3, "task id")
                returned_token = _required_text(fields, 4, "claim token")
                returned_lease_until = _required_uint(fields, 5, "lease until")
                returned_revision = _required_uint(fields, 6, "config revision")
                returned_digest = _required_text(fields, 7, "payload digest")
                if not _SHA256_RE.fullmatch(returned_digest):
                    raise ValueError("invalid payload digest")
                if full_record:
                    _required_text(fields, 8, "payload")
                    _required_text(fields, 9, "policy key")
                elif fields[8] or fields[9]:
                    raise ValueError("unexpected payload fields")
                expected_route = task.route if task else route
                if expected_route is None or not _token_matches_route(
                    returned_token, expected_route
                ):
                    raise ValueError("invalid claim token")
                if operation in {"claim_next", "heartbeat"} and returned_lease_until != (
                    server_time_ms + lease_ttl_ms
                ):
                    raise ValueError("invalid lease deadline")
                if lease is not None and (
                    returned_task_id != lease.task.task_id
                    or returned_token != lease.claim_token
                    or returned_revision != lease.task.config_revision
                    or returned_digest != lease.task.payload_sha256
                ):
                    raise ValueError("fence echo mismatch")
                if operation == "heartbeat" and (
                    returned_lease_until <= lease.lease_until_ms  # type: ignore[union-attr]
                ):
                    raise ValueError("lease was not extended")
                if operation in {"complete", "reschedule_at"} and (
                    returned_lease_until != lease.lease_until_ms  # type: ignore[union-attr]
                ):
                    raise ValueError("transition fence mismatch")
            except ValueError:
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        elif identity_record:
            try:
                if task is None:
                    raise ValueError("missing expected task")
                returned_task_id = _required_text(fields, 3, "task id")
                returned_revision = _required_uint(fields, 6, "config revision")
                returned_digest = _required_text(fields, 7, "payload digest")
                if (
                    fields[4]
                    or fields[5]
                    or fields[8]
                    or fields[9]
                    or returned_task_id != task.task_id
                    or returned_revision != task.config_revision
                    or returned_digest != task.payload_sha256
                ):
                    raise ValueError("identity echo mismatch")
            except ValueError:
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        elif any(fields[index] for index in range(3, 10)):
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        value_shape = {
            "register": (True, False),
            "reschedule_at": (True, False),
            "reactivate": (True, False),
            "reap_expired": (True, True),
            "audit": (True, True),
        }.get(operation, (False, False))
        if decision is not Decision.ACCEPTED:
            value_shape = (False, False)
        if (value is not None, secondary_value is not None) != value_shape:
            return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        if decision is Decision.ACCEPTED:
            expected_ready_at = {
                "register": task.initial_ready_at_ms if task else None,
                "reschedule_at": ready_at_ms,
                "reactivate": task.initial_ready_at_ms if task else None,
            }.get(operation)
            if expected_ready_at is not None and value != expected_ready_at:
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
            if (
                operation == "reap_expired"
                and cast(int, value) + cast(int, secondary_value) > SCAN_LIMIT
            ):
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
            if operation == "audit" and (
                cast(int, value) > MAX_RECORDS or cast(int, secondary_value) > cast(int, value)
            ):
                return TransitionResult(Decision.TRANSPORT_ERROR, "invalid_reply")
        return TransitionResult(decision, reason, server_time_ms, value, secondary_value)

    @staticmethod
    def _decode_claim_task(route: RouteIdentity, raw: list[Any]) -> LightpandaB0Task:
        fields = [_wire_text(value) for value in raw]
        task_id = _required_text(fields, 3, "task id")
        config_revision = _required_uint(fields, 6, "config revision")
        digest = _required_text(fields, 7, "payload digest")
        payload = _required_text(fields, 8, "payload")
        policy_key = _required_text(fields, 9, "policy key")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("payload_integrity_failure")
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError("payload_integrity_failure")
        try:
            envelope = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("payload_integrity_failure") from exc
        if not isinstance(envelope, dict):
            raise ValueError("payload_integrity_failure")
        try:
            rebuilt = LightpandaB0Task.create(
                task_id=task_id,
                board_id=envelope["board_id"],
                source_url=envelope["source_url"],
                policy_key=policy_key,
                domain=envelope["domain"],
                route=route,
                config_revision=config_revision,
                initial_ready_at_ms=envelope["initial_ready_at_ms"],
                assignment=_assignment_from_envelope(envelope),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("payload_integrity_failure") from exc
        if rebuilt.payload != payload or envelope.get("schema_version") != "lightpanda-b0-task-v1":
            raise ValueError("payload_integrity_failure")
        return rebuilt


def _safe_identifier(value: object, name: str) -> str:
    return validate_lightpanda_b0_identifier(value, name)


def _wire_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    raise TypeError("wire value is not text")


def _token_matches_route(token: str, route: RouteIdentity) -> bool:
    match = _TOKEN_RE.fullmatch(token)
    return (
        match is not None
        and int(match.group(1)) == route.routing_epoch
        and 1 <= int(match.group(2)) <= MAX_INTEGER
    )


def _decimal_wire(value: float) -> str:
    text = format(value, ".17f").rstrip("0").rstrip(".")
    return text or "0"


def _validated_delay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 86_400:
        raise ValueError("throttle_delay_default must be finite and between 0 and 86400")
    return float(value)


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw_json(item) for item in value]
    return value


def _validated_assignment(
    assignment: RenderAssignment,
) -> tuple[RenderAssignment, dict[str, Any]]:
    if not isinstance(assignment, RenderAssignment):
        raise ValueError("assignment must be a validated RenderAssignment")
    config = _thaw_json(assignment.config)
    if not isinstance(config, dict):
        raise ValueError("assignment config must be an object")
    try:
        resolved = resolve_render_assignment(
            assignment.scraper_type,
            config,
            scraper_step=assignment.scraper_step,
        )
    except ValueError as exc:
        raise ValueError("assignment is not a valid Lightpanda B0 assignment") from exc
    if (
        resolved is None
        or resolved.browser_backend != assignment.browser_backend
        or resolved.routing_revision != assignment.routing_revision
        or resolved.timeout_ms != assignment.timeout_ms
        or resolved.config_digest_sha256 != assignment.config_digest_sha256
    ):
        raise ValueError("assignment identity mismatch")
    return resolved, cast(dict[str, Any], config)


def _validate_task_identity(task: LightpandaB0Task) -> None:
    if not isinstance(task, LightpandaB0Task):
        raise ValueError("task must be a LightpandaB0Task")
    rebuilt = LightpandaB0Task.create(
        task_id=task.task_id,
        board_id=task.board_id,
        source_url=task.source_url,
        policy_key=task.policy_key,
        domain=task.domain,
        route=task.route,
        config_revision=task.config_revision,
        initial_ready_at_ms=task.initial_ready_at_ms,
        assignment=task.assignment,
    )
    if (
        rebuilt.payload != task.payload
        or rebuilt.payload_sha256 != task.payload_sha256
        or rebuilt.payload_sha1 != task.payload_sha1
    ):
        raise ValueError("task identity mismatch")


def _assignment_from_envelope(envelope: dict[str, Any]) -> RenderAssignment:
    config = envelope["parser_config"]
    if not isinstance(config, dict):
        raise ValueError("payload_integrity_failure")
    assignment = resolve_render_assignment(
        envelope["scraper_type"], config, scraper_step=envelope["scraper_step"]
    )
    if assignment is None:
        raise ValueError("payload_integrity_failure")
    expected = {
        "browser_backend": assignment.browser_backend,
        "routing_revision": assignment.routing_revision,
        "render": True,
        "wait": "load",
        "wait_fallback": None,
        "timeout_ms": assignment.timeout_ms,
        "assignment_digest_sha256": assignment.config_digest_sha256,
    }
    if any(envelope.get(key) != value for key, value in expected.items()):
        raise ValueError("payload_integrity_failure")
    return assignment


def _validated_domain(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("domain must be a DNS hostname")
    try:
        normalized = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("domain must be a DNS hostname") from exc
    if not _SAFE_DOMAIN_RE.fullmatch(normalized):
        raise ValueError("domain must be a DNS hostname")
    return normalized


def _bounded_int(value: object, name: str, *, minimum: int, maximum: int = MAX_INTEGER) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _required_text(raw: list[Any], index: int, name: str) -> str:
    value = raw[index]
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid {name}")
    return value


def _required_uint(raw: list[Any], index: int, name: str) -> int:
    value = _optional_uint(raw[index], name)
    if value is None:
        raise ValueError(f"invalid {name}")
    return value


def _optional_uint(value: Any, name: str) -> int | None:
    if value == "":
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    text = str(value)
    if not re.fullmatch(r"0|[1-9][0-9]*", text):
        raise ValueError(f"invalid {name}")
    return _bounded_int(int(text), name, minimum=0)
