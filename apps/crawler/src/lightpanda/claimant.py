"""Inactive Python claimant for the closed Lightpanda B0 source lane."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Final, NoReturn, cast

import asyncpg
import httpx

import src.queries.lookups as lightpanda_lookups
from src.lightpanda.client import (
    SERVICE_CAPACITY,
    LightpandaB0Client,
    LightpandaB0Reservation,
)
from src.lightpanda.runtime import LightpandaB0ScrapeRuntime
from src.lightpanda.write_fence import (
    LightpandaWriteFence,
    LightpandaWriteFenceRejected,
    activate_write_fence,
)
from src.lightpanda_queue import (
    ClaimResult,
    Decision,
    Lease,
    LightpandaB0Queue,
    RouteIdentity,
    TransitionResult,
)
from src.processing.scrape import ScrapeItem, _process_one_scrape

CLAIMANT_WORKERS: Final = 4
LEASE_TTL_MS: Final = 60_000
HEARTBEAT_INTERVAL_SECONDS: Final = 15.0
HEARTBEAT_RESPONSE_TIMEOUT_SECONDS: Final = 5.0
IDLE_WAIT_SECONDS: Final = 1.0
REAPER_INTERVAL_SECONDS: Final = 30.0
MAX_LEASE_FAILURES: Final = 3
SHUTDOWN_AUDIT_TIMEOUT_SECONDS: Final = 5.0
AUTHORITATIVE_WRITE_TIMEOUT_SECONDS: Final = 15.0
SCHEDULE_READ_TIMEOUT_SECONDS: Final = 5.0
TERMINAL_RESPONSE_TIMEOUT_SECONDS: Final = 5.0
QUEUE_RESPONSE_TIMEOUT_SECONDS: Final = 5.0
CLAIM_LIFECYCLE_TIMEOUT_SECONDS: Final = 180.0

if CLAIMANT_WORKERS != SERVICE_CAPACITY:  # pragma: no cover - constant contract
    raise RuntimeError("Lightpanda claimant and service capacities differ")

_FETCH_SCHEDULE = """
SELECT is_active, next_scrape_at
FROM job_posting
WHERE id = $1::uuid
"""


class LightpandaClaimantError(RuntimeError):
    """The inactive claimant cannot safely continue its fixed source lane."""


class LightpandaLeaseLost(LightpandaClaimantError):
    """The current claim is stale or its Redis authority is indeterminate."""


async def _bounded_queue_call[QueueResult](
    operation: str,
    call: Awaitable[QueueResult],
) -> QueueResult:
    """Bound claimant-facing Redis calls; timeout means authority is unknown."""

    try:
        async with asyncio.timeout(QUEUE_RESPONSE_TIMEOUT_SECONDS):
            return await call
    except TimeoutError as exc:
        raise LightpandaClaimantError(f"{operation} timed out with indeterminate state") from exc


@dataclass(frozen=True, slots=True)
class ScheduleState:
    is_active: bool
    next_scrape_at: datetime | None


ScheduleReader = Callable[[asyncpg.Pool, str], Awaitable[ScheduleState | None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class LightpandaClaimantDependencies:
    """All authority and I/O injected by a future, separately reviewed entry point."""

    client: LightpandaB0Client
    queue: LightpandaB0Queue
    pool: asyncpg.Pool
    route: RouteIdentity
    schedule_reader: ScheduleReader
    sleep: Sleep = asyncio.sleep


async def read_authoritative_schedule(
    pool: asyncpg.Pool,
    job_posting_id: str,
) -> ScheduleState | None:
    """Read the sole Postgres authority for the next Redis transition."""

    async with pool.acquire() as connection:
        row = await connection.fetchrow(_FETCH_SCHEDULE, job_posting_id)
    if row is None:
        return None
    is_active = row["is_active"]
    next_scrape_at = row["next_scrape_at"]
    if type(is_active) is not bool or (
        next_scrape_at is not None and not isinstance(next_scrape_at, datetime)
    ):
        raise LightpandaClaimantError("authoritative scrape schedule has an invalid shape")
    return ScheduleState(is_active=is_active, next_scrape_at=next_scrape_at)


async def run_lightpanda_claimant(dependencies: LightpandaClaimantDependencies) -> None:
    """Run exactly four reservation-first loops; this module does not configure itself."""

    if not isinstance(dependencies, LightpandaClaimantDependencies):
        raise TypeError("dependencies must be LightpandaClaimantDependencies")
    startup = _ReservationStartup()
    try:
        async with (
            httpx.AsyncClient(transport=_RejectingOriginTransport()) as no_origin_http,
            asyncio.TaskGroup() as workers,
        ):
            for worker_index in range(CLAIMANT_WORKERS):
                workers.create_task(
                    _worker_loop(dependencies, startup, no_origin_http),
                    name=f"lightpanda-b0-claimant-{worker_index}",
                )
            workers.create_task(
                _reap_expired_loop(dependencies, startup),
                name="lightpanda-b0-reaper",
            )
    finally:
        if startup.queue_touched:
            await _shutdown_audit(dependencies)


class _RejectingOriginTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        raise LightpandaClaimantError("local origin HTTP is forbidden in the Lightpanda B0 lane")


class _ReservationStartup:
    """Open all four verified service slots before initializing queue authority."""

    def __init__(self) -> None:
        self._arrivals = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._queue_touched = False

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def queue_touched(self) -> bool:
        return self._queue_touched

    async def wait(self) -> None:
        await self._ready.wait()

    async def arrive(self, dependencies: LightpandaClaimantDependencies) -> None:
        initialize = False
        async with self._lock:
            self._arrivals += 1
            if self._arrivals > CLAIMANT_WORKERS:
                raise LightpandaClaimantError("too many claimant startup reservations")
            initialize = self._arrivals == CLAIMANT_WORKERS
            if initialize:
                # A missing/failed response is indeterminate: the queue may
                # already have mutated, so shutdown must audit conservation.
                self._queue_touched = True
                _require_accepted(
                    "initialize",
                    await _bounded_queue_call(
                        "initialize",
                        dependencies.queue.initialize(dependencies.route),
                    ),
                )
                _require_accepted(
                    "startup reap",
                    await _bounded_queue_call(
                        "startup reap",
                        dependencies.queue.reap_expired(
                            dependencies.route,
                            max_failures=MAX_LEASE_FAILURES,
                        ),
                    ),
                )
                _require_accepted(
                    "audit",
                    await _bounded_queue_call(
                        "startup audit",
                        dependencies.queue.audit_conservation(dependencies.route),
                    ),
                )
                self._ready.set()
        if not initialize:
            await self._ready.wait()


async def _reap_expired_loop(
    dependencies: LightpandaClaimantDependencies,
    startup: _ReservationStartup,
) -> None:
    await startup.wait()
    while True:
        await dependencies.sleep(REAPER_INTERVAL_SECONDS)
        _require_accepted(
            "reap_expired",
            await _bounded_queue_call(
                "periodic reap",
                dependencies.queue.reap_expired(
                    dependencies.route,
                    max_failures=MAX_LEASE_FAILURES,
                ),
            ),
        )
        _require_accepted(
            "post-reap audit",
            await _bounded_queue_call(
                "post-reap audit",
                dependencies.queue.audit_conservation(dependencies.route),
            ),
        )


async def _shutdown_audit(dependencies: LightpandaClaimantDependencies) -> None:
    """Bound shutdown; an indeterminate audit deliberately fails closed.

    A successful audit leaves any active cancellation untouched. A rejected,
    failed, or timed-out audit replaces it with a claimant error so a future
    supervisor cannot mistake an unproved shutdown for a clean stop.
    """

    try:
        async with asyncio.timeout(SHUTDOWN_AUDIT_TIMEOUT_SECONDS):
            transition = await dependencies.queue.audit_conservation(dependencies.route)
    except Exception as exc:
        raise LightpandaClaimantError("shutdown conservation audit was indeterminate") from exc
    _require_accepted("shutdown audit", transition)


async def _worker_loop(
    dependencies: LightpandaClaimantDependencies,
    startup: _ReservationStartup,
    no_origin_http: httpx.AsyncClient,
) -> None:
    first_reservation = True
    while True:
        no_work = False
        async with dependencies.client.reserve() as reservation:
            if first_reservation:
                await startup.arrive(dependencies)
                first_reservation = False

            claim = await _bounded_queue_call(
                "claim_next",
                dependencies.queue.claim_next(
                    dependencies.route,
                    lease_ttl_ms=LEASE_TTL_MS,
                ),
            )
            if _is_no_work(claim):
                no_work = True
            else:
                lease = _require_claim(claim)
                await _run_claim(dependencies, reservation, lease, no_origin_http)
        if no_work:
            await dependencies.sleep(IDLE_WAIT_SECONDS)


async def _run_claim(
    dependencies: LightpandaClaimantDependencies,
    reservation: LightpandaB0Reservation,
    lease: Lease,
    no_origin_http: httpx.AsyncClient,
) -> None:
    fence = LightpandaWriteFence.from_lease(lease)
    heartbeat = _HeartbeatGuard(
        queue=dependencies.queue,
        lease=lease,
        reservation=reservation,
        sleep=dependencies.sleep,
    )
    await heartbeat.verify_before_activation()

    async def lifecycle() -> None:
        try:
            async with asyncio.timeout(CLAIM_LIFECYCLE_TIMEOUT_SECONDS):
                await activate_write_fence(dependencies.pool, fence)
                parser_config = _parser_config(lease)
                runtime = LightpandaB0ScrapeRuntime(lease.task, reservation)
                await _process_one_scrape(
                    ScrapeItem(
                        job_posting_id=lease.task.task_id,
                        url=lease.task.source_url,
                        board_id=lease.task.board_id,
                    ),
                    dependencies.pool,
                    no_origin_http,
                    "json-ld",
                    parser_config,
                    pw=None,
                    scrape_step=0,
                    scrape_runtime=runtime,
                    write_fence=fence,
                    recover_browser_target=False,
                    authority_guard=heartbeat.authorize_write,
                    lookup_provider=lightpanda_lookups,
                )
                try:
                    async with asyncio.timeout(SCHEDULE_READ_TIMEOUT_SECONDS):
                        schedule = await dependencies.schedule_reader(
                            dependencies.pool,
                            lease.task.task_id,
                        )
                except TimeoutError as exc:
                    raise LightpandaClaimantError("authoritative schedule read timed out") from exc
                if schedule is None or not schedule.is_active or schedule.next_scrape_at is None:
                    await heartbeat.complete()
                else:
                    await heartbeat.reschedule_at(_epoch_milliseconds(schedule.next_scrape_at))
        except TimeoutError as exc:
            raise LightpandaClaimantError("Lightpanda claim lifecycle timed out") from exc
        except LightpandaWriteFenceRejected as exc:
            reservation.cancel()
            raise LightpandaLeaseLost("Postgres rejected the current Lightpanda lease") from exc

    work = asyncio.create_task(lifecycle(), name=f"lightpanda-b0-lease-{lease.claim_token}")
    pulses = asyncio.create_task(
        heartbeat.run(),
        name=f"lightpanda-b0-heartbeat-{lease.claim_token}",
    )
    try:
        done, _pending = await asyncio.wait(
            {work, pulses},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if work in done:
            return work.result()
        pulse_error = pulses.exception()
        if pulse_error is not None:
            work.cancel()
            raise pulse_error
        return await work
    except BaseException:
        reservation.cancel()
        raise
    finally:
        for task in (work, pulses):
            if not task.done():
                task.cancel()
        # Keep structured ownership even when a dependency delays cancellation:
        # TLS is already synchronously closed above, and this claimant cannot
        # release the reservation or audit shutdown while lifecycle code lives.
        await asyncio.gather(work, pulses, return_exceptions=True)


class _HeartbeatGuard:
    """Serialize lease heartbeats with the one authoritative terminal transition."""

    def __init__(
        self,
        *,
        queue: LightpandaB0Queue,
        lease: Lease,
        reservation: LightpandaB0Reservation,
        sleep: Sleep,
    ) -> None:
        self._queue = queue
        self._lease = lease
        self._reservation = reservation
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._finishing = False
        self._persistence_complete = False
        self._lost: LightpandaLeaseLost | None = None

    async def verify_before_activation(self) -> None:
        await self._heartbeat("pre-activation heartbeat")

    async def run(self) -> None:
        while True:
            await self._sleep(HEARTBEAT_INTERVAL_SECONDS)
            async with self._lock:
                if self._finishing:
                    return
                await self._heartbeat_locked("heartbeat")

    @asynccontextmanager
    async def authorize_write(self) -> AsyncIterator[None]:
        """Refresh authority, then exclude heartbeats through one bounded commit."""

        async with self._lock:
            if self._persistence_complete:
                raise LightpandaClaimantError("duplicate Lightpanda persistence transaction")
            if self._finishing:
                raise LightpandaClaimantError("Lightpanda write started after terminal transition")
            await self._heartbeat_locked("pre-write heartbeat")
            try:
                async with asyncio.timeout(AUTHORITATIVE_WRITE_TIMEOUT_SECONDS):
                    yield
            except TimeoutError as exc:
                self._lose("authoritative write exceeded its lease-safe deadline", cause=exc)
            else:
                self._persistence_complete = True

    async def complete(self) -> None:
        await self._terminal("complete")

    async def reschedule_at(self, ready_at_ms: int) -> None:
        await self._terminal("reschedule_at", ready_at_ms=ready_at_ms)

    async def _heartbeat(self, operation: str) -> None:
        async with self._lock:
            await self._heartbeat_locked(operation)

    async def _heartbeat_locked(self, operation: str) -> None:
        if self._lost is not None:
            raise self._lost
        started = monotonic()
        try:
            async with asyncio.timeout(HEARTBEAT_RESPONSE_TIMEOUT_SECONDS):
                transition = await self._queue.heartbeat(
                    self._lease,
                    lease_ttl_ms=LEASE_TTL_MS,
                )
        except Exception as exc:
            self._lose(f"{operation} was indeterminate", cause=exc)
        if monotonic() - started > HEARTBEAT_RESPONSE_TIMEOUT_SECONDS:
            self._lose(f"{operation} response exceeded its authority-age budget")
        if not transition.accepted:
            self._lose(
                f"{operation} lost authority: {transition.decision.value}/{transition.reason}"
            )

    async def _terminal(self, operation: str, *, ready_at_ms: int | None = None) -> None:
        async with self._lock:
            if self._lost is not None:
                raise self._lost
            if self._finishing:
                raise LightpandaClaimantError("duplicate Lightpanda terminal transition")
            if not self._persistence_complete:
                raise LightpandaClaimantError("terminal transition preceded persistence")
            self._finishing = True
            try:
                async with asyncio.timeout(TERMINAL_RESPONSE_TIMEOUT_SECONDS):
                    if operation == "complete":
                        transition = await self._queue.complete(self._lease)
                    else:
                        assert ready_at_ms is not None
                        transition = await self._queue.reschedule_at(
                            self._lease,
                            ready_at_ms=ready_at_ms,
                        )
            except Exception as exc:
                self._lose(f"{operation} was indeterminate", cause=exc)
            if not transition.accepted:
                self._lose(
                    f"{operation} lost authority: {transition.decision.value}/{transition.reason}"
                )

    def _lose(self, message: str, *, cause: Exception | None = None) -> NoReturn:
        lost = LightpandaLeaseLost(message)
        self._lost = lost
        self._reservation.cancel()
        if cause is None:
            raise lost
        raise lost from cause


def _is_no_work(claim: ClaimResult) -> bool:
    return (
        claim.lease is None
        and claim.transition.decision is Decision.NOT_CURRENT
        and claim.transition.reason == "no_work"
    )


def _require_claim(claim: ClaimResult) -> Lease:
    if not claim.transition.accepted or claim.lease is None:
        raise LightpandaClaimantError(
            f"claim_next failed closed: {claim.transition.decision.value}/{claim.transition.reason}"
        )
    return claim.lease


def _require_accepted(operation: str, transition: TransitionResult) -> None:
    if not transition.accepted:
        raise LightpandaClaimantError(
            f"{operation} failed closed: {transition.decision.value}/{transition.reason}"
        )


def _parser_config(lease: Lease) -> dict:
    try:
        envelope = json.loads(lease.task.payload)
        parser_config = envelope["parser_config"]
    except (KeyError, TypeError, ValueError) as exc:
        raise LightpandaClaimantError("claimed task has no canonical parser config") from exc
    if not isinstance(parser_config, Mapping):
        raise LightpandaClaimantError("claimed task parser config is not an object")
    return cast(dict, parser_config)


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LightpandaClaimantError("authoritative next_scrape_at must be timezone-aware")
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    milliseconds = delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000
    if not 0 <= milliseconds <= 9_999_999_999_999:
        raise LightpandaClaimantError("authoritative next_scrape_at is out of range")
    return milliseconds


__all__ = [
    "CLAIMANT_WORKERS",
    "LightpandaClaimantDependencies",
    "LightpandaClaimantError",
    "LightpandaLeaseLost",
    "ScheduleState",
    "read_authoritative_schedule",
    "run_lightpanda_claimant",
]
