"""Lease-ordering tests for the inactive fixed Lightpanda claimant."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.lightpanda.claimant as claimant
import src.queries.lookups as lookup_module
from src.lightpanda.claimant import (
    CLAIMANT_WORKERS,
    LightpandaClaimantDependencies,
    LightpandaClaimantError,
    LightpandaLeaseLost,
    ScheduleState,
    _run_claim,
    run_lightpanda_claimant,
)
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import (
    ClaimResult,
    Decision,
    Lease,
    LightpandaB0Task,
    RouteIdentity,
    TransitionResult,
)


def _accepted(reason: str = "accepted") -> TransitionResult:
    return TransitionResult(Decision.ACCEPTED, reason, server_time_ms=1_000)


def _lease(sequence: int = 1) -> Lease:
    config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "route-test-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
    }
    assignment = resolve_render_assignment("json-ld", config)
    assert assignment is not None
    task = LightpandaB0Task.create(
        task_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://example.test/jobs/{sequence}")),
        board_id="board-1",
        source_url="https://example.test/jobs/1",
        policy_key="lightpanda-b0-v1",
        domain="example.test",
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        config_revision=1,
        initial_ready_at_ms=1,
        assignment=assignment,
    )
    return Lease(task=task, claim_token=f"1:{sequence}", lease_until_ms=61_000)


async def _finish_mock_persistence(kwargs: dict) -> None:
    assert kwargs["recover_browser_target"] is False
    assert kwargs["lookup_provider"] is claimant.lightpanda_lookups
    authority_guard = kwargs["authority_guard"]
    async with authority_guard():
        pass


def test_claimant_import_does_not_require_playwright() -> None:
    script = """
import builtins

real_import = builtins.__import__

def import_without_playwright(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "playwright" or name.startswith("playwright."):
        raise ModuleNotFoundError(f"{name} deliberately unavailable")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_playwright
import src.lightpanda.claimant
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_claimant_processing_path_does_not_import_registry_or_playwright() -> None:
    script = """
import asyncio
import builtins
from contextlib import asynccontextmanager
from types import SimpleNamespace

real_import = builtins.__import__

def import_without_browser_stack(name, globals=None, locals=None, fromlist=(), level=0):
    blocked = ("playwright", "src.batch")
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
        raise ModuleNotFoundError(f"{name} deliberately unavailable")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_browser_stack

from src.core.job_content import JobContent
import src.processing.scrape as scrape

class Runtime:
    implementation = "go"

    async def scrape(self, *args, **kwargs):
        return JobContent(title="Engineer")

class Resolver:
    def drain_location_misses(self):
        return []

class Connection:
    async def execute(self, *args):
        return "UPDATE 1"

async def value(result):
    return result

lookups = SimpleNamespace(
    _get_location_resolver=lambda pool: value(Resolver()),
    _resolve_locations=lambda *args, **kwargs: value((None, None)),
    _get_technology_ids=lambda pool: value({}),
    _get_occupation_ids=lambda pool: value({}),
    _get_seniority_ids=lambda pool: value({}),
    _get_currency_rates=lambda pool: value({"EUR": 1.0}),
)

@asynccontextmanager
async def authority():
    yield

@asynccontextmanager
async def fake_write(_pool, _fence, *, job_posting_id, authority_guard):
    async with authority_guard():
        yield Connection()

scrape.authoritative_write = fake_write
scrape._stage_r2_pending = lambda **kwargs: None

async def main():
    success, _ = await scrape._process_one_scrape(
        scrape.ScrapeItem("00000000-0000-0000-0000-000000000001", "https://example.invalid/job"),
        object(),
        object(),
        "json-ld",
        None,
        scrape_runtime=Runtime(),
        recover_browser_target=False,
        authority_guard=authority,
        lookup_provider=lookups,
    )
    assert success

asyncio.run(main())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


class _Reservation:
    def __init__(self) -> None:
        self.cancel_count = 0
        self.closed = False

    def cancel(self) -> None:
        if self.cancel_count == 0:
            self.cancel_count = 1


class _StartupClient:
    def __init__(self) -> None:
        self.active = 0
        self.opened = 0
        self.maximum_active = 0
        self.reservations: list[_Reservation] = []

    @asynccontextmanager
    async def reserve(self):
        self.active += 1
        self.opened += 1
        self.maximum_active = max(self.maximum_active, self.active)
        reservation = _Reservation()
        self.reservations.append(reservation)
        try:
            yield reservation
        finally:
            reservation.closed = True
            self.active -= 1


class _StartupQueue:
    def __init__(self, client: _StartupClient) -> None:
        self.client = client
        self.initialized = asyncio.Event()
        self.reaped = False
        self.audited = False
        self.audit_count = 0
        self.claims = 0

    async def initialize(self, route: RouteIdentity) -> TransitionResult:
        del route
        assert self.client.active == CLAIMANT_WORKERS
        assert self.claims == 0
        self.initialized.set()
        return _accepted("initialized")

    async def reap_expired(self, route: RouteIdentity, *, max_failures: int) -> TransitionResult:
        del route
        assert max_failures == 3
        assert self.client.active == CLAIMANT_WORKERS
        assert self.initialized.is_set()
        assert self.claims == 0
        self.reaped = True
        return _accepted("reaped")

    async def audit_conservation(self, route: RouteIdentity) -> TransitionResult:
        del route
        self.audit_count += 1
        if self.audit_count == 1:
            assert self.client.active == CLAIMANT_WORKERS
            assert self.initialized.is_set()
            assert self.reaped
            assert self.claims == 0
            self.audited = True
        return _accepted("audit_ok")

    async def claim_next(self, route: RouteIdentity, *, lease_ttl_ms: int) -> ClaimResult:
        del route, lease_ttl_ms
        assert self.audited
        self.claims += 1
        return ClaimResult(
            TransitionResult(Decision.NOT_CURRENT, "no_work"),
            None,
        )


async def test_exactly_four_verified_reservations_precede_queue_startup() -> None:
    client = _StartupClient()
    queue = _StartupQueue(client)
    sleeping = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await sleeping.wait()

    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=AsyncMock(),
        sleep=sleep,
    )
    runner = asyncio.create_task(run_lightpanda_claimant(dependencies))
    try:
        await asyncio.wait_for(queue.initialized.wait(), timeout=1)
        for _ in range(20):
            if queue.claims == CLAIMANT_WORKERS:
                break
            await asyncio.sleep(0)
        assert client.opened == CLAIMANT_WORKERS
        assert queue.claims == CLAIMANT_WORKERS
    finally:
        runner.cancel("shutdown")
        with pytest.raises(asyncio.CancelledError) as raised:
            await runner
        assert raised.value.args == ("shutdown",)


async def test_shutdown_audit_is_bounded_after_all_reservations_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StartupClient()
    queue = _StartupQueue(client)
    original_audit = queue.audit_conservation
    shutdown_audit_started = asyncio.Event()

    async def audit(route: RouteIdentity) -> TransitionResult:
        if not queue.audited:
            return await original_audit(route)
        assert client.active == 0
        shutdown_audit_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    queue.audit_conservation = audit  # type: ignore[method-assign]
    blocked = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await blocked.wait()

    monkeypatch.setattr(claimant, "SHUTDOWN_AUDIT_TIMEOUT_SECONDS", 0.001)
    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=AsyncMock(),
        sleep=sleep,
    )
    runner = asyncio.create_task(run_lightpanda_claimant(dependencies))
    await asyncio.wait_for(queue.initialized.wait(), timeout=1)
    for _ in range(20):
        if queue.claims == CLAIMANT_WORKERS:
            break
        await asyncio.sleep(0)

    runner.cancel("shutdown")
    with pytest.raises(LightpandaClaimantError, match="audit was indeterminate"):
        await asyncio.wait_for(runner, timeout=0.1)

    assert shutdown_audit_started.is_set()
    assert client.active == 0


@pytest.mark.parametrize(
    ("failure_stage", "expected_audits"),
    [("initialize", 1), ("reap", 1), ("audit", 2)],
)
async def test_partial_queue_startup_closes_reservations_then_audits(
    failure_stage: str,
    expected_audits: int,
) -> None:
    client = _StartupClient()

    class PartialStartupQueue(_StartupQueue):
        async def initialize(self, route: RouteIdentity) -> TransitionResult:
            if failure_stage == "initialize":
                self.initialized.set()
                raise RuntimeError("initialize response lost")
            return await super().initialize(route)

        async def reap_expired(
            self,
            route: RouteIdentity,
            *,
            max_failures: int,
        ) -> TransitionResult:
            if failure_stage == "reap":
                del route, max_failures
                self.reaped = True
                raise RuntimeError("reap response lost")
            return await super().reap_expired(route, max_failures=max_failures)

        async def audit_conservation(self, route: RouteIdentity) -> TransitionResult:
            del route
            self.audit_count += 1
            if failure_stage == "audit" and self.audit_count == 1:
                return TransitionResult(Decision.FENCED, "conservation_mismatch")
            assert client.active == 0
            return _accepted("audit_ok")

    queue = PartialStartupQueue(client)
    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=AsyncMock(),
    )

    with pytest.raises(ExceptionGroup):
        await run_lightpanda_claimant(dependencies)

    assert client.active == 0
    assert all(reservation.closed for reservation in client.reservations)
    assert queue.audit_count == expected_audits


async def test_startup_queue_timeout_closes_reservations_then_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StartupClient()

    class TimedOutStartupQueue(_StartupQueue):
        async def initialize(self, route: RouteIdentity) -> TransitionResult:
            del route
            self.initialized.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def audit_conservation(self, route: RouteIdentity) -> TransitionResult:
            del route
            self.audit_count += 1
            assert client.active == 0
            return _accepted("shutdown_audit_ok")

    queue = TimedOutStartupQueue(client)
    monkeypatch.setattr(claimant, "QUEUE_RESPONSE_TIMEOUT_SECONDS", 0.001)
    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=AsyncMock(),
    )

    with pytest.raises(ExceptionGroup, match="unhandled errors in a TaskGroup"):
        await run_lightpanda_claimant(dependencies)

    assert client.active == 0
    assert all(reservation.closed for reservation in client.reservations)
    assert queue.audit_count == 1


async def test_claim_queue_timeout_closes_all_slots_then_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StartupClient()

    class TimedOutClaimQueue(_StartupQueue):
        async def claim_next(self, route: RouteIdentity, *, lease_ttl_ms: int) -> ClaimResult:
            del route, lease_ttl_ms
            assert self.audited
            self.claims += 1
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    queue = TimedOutClaimQueue(client)
    monkeypatch.setattr(claimant, "QUEUE_RESPONSE_TIMEOUT_SECONDS", 0.001)
    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=AsyncMock(),
    )

    with pytest.raises(ExceptionGroup, match="unhandled errors in a TaskGroup"):
        await run_lightpanda_claimant(dependencies)

    assert queue.claims == CLAIMANT_WORKERS
    assert client.active == 0
    assert all(reservation.closed for reservation in client.reservations)
    assert queue.audit_count == 2


class _CapacityQueue:
    def __init__(self) -> None:
        self.claim_count = 0
        self._claim_lock = asyncio.Lock()
        self.terminal_started = asyncio.Event()
        self.release_terminal = asyncio.Event()
        self._blocked_terminal = False

    async def initialize(self, route: RouteIdentity) -> TransitionResult:
        del route
        return _accepted("initialized")

    async def audit_conservation(self, route: RouteIdentity) -> TransitionResult:
        del route
        return _accepted("audit_ok")

    async def reap_expired(self, route: RouteIdentity, *, max_failures: int) -> TransitionResult:
        del route
        assert max_failures == 3
        return _accepted("reaped")

    async def claim_next(self, route: RouteIdentity, *, lease_ttl_ms: int) -> ClaimResult:
        del route, lease_ttl_ms
        async with self._claim_lock:
            if self.claim_count >= 5:
                return ClaimResult(
                    TransitionResult(Decision.NOT_CURRENT, "no_work"),
                    None,
                )
            self.claim_count += 1
            return ClaimResult(_accepted("claimed"), _lease(self.claim_count))

    async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
        del lease, lease_ttl_ms
        return _accepted("lease_extended")

    async def complete(self, lease: Lease) -> TransitionResult:
        del lease
        if not self._blocked_terminal:
            self._blocked_terminal = True
            self.terminal_started.set()
            await self.release_terminal.wait()
        return _accepted("completed")

    async def reschedule_at(self, lease: Lease, *, ready_at_ms: int) -> TransitionResult:
        del lease, ready_at_ms
        if not self._blocked_terminal:
            self._blocked_terminal = True
            self.terminal_started.set()
            await self.release_terminal.wait()
        return _accepted("rescheduled")


async def test_fifth_claim_waits_for_full_lifecycle_and_cancellation_closes_four_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StartupClient()
    queue = _CapacityQueue()
    release = asyncio.Semaphore(0)
    four_processing = asyncio.Event()
    processing = 0
    maximum_processing = 0

    async def process(*_args, **kwargs) -> tuple[bool, float]:
        nonlocal processing, maximum_processing
        processing += 1
        maximum_processing = max(maximum_processing, processing)
        if processing == CLAIMANT_WORKERS:
            four_processing.set()
        try:
            await release.acquire()
            await _finish_mock_persistence(kwargs)
            return True, 0.0
        finally:
            processing -= 1

    async def schedule_reader(_pool, _posting_id: str) -> ScheduleState | None:
        return None

    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    dependencies = LightpandaClaimantDependencies(
        client=client,  # type: ignore[arg-type]
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=schedule_reader,
        sleep=sleep,
    )
    runner = asyncio.create_task(run_lightpanda_claimant(dependencies))
    try:
        await asyncio.wait_for(four_processing.wait(), timeout=1)
        assert queue.claim_count == CLAIMANT_WORKERS
        assert client.active == CLAIMANT_WORKERS

        release.release()
        await asyncio.wait_for(queue.terminal_started.wait(), timeout=1)
        for _ in range(20):
            await asyncio.sleep(0)
        assert queue.claim_count == CLAIMANT_WORKERS
        assert client.active == CLAIMANT_WORKERS

        queue.release_terminal.set()
        for _ in range(100):
            if queue.claim_count == 5 and processing == CLAIMANT_WORKERS:
                break
            await asyncio.sleep(0)
        assert queue.claim_count == 5
        assert processing == CLAIMANT_WORKERS
        assert maximum_processing == CLAIMANT_WORKERS
        assert client.maximum_active == CLAIMANT_WORKERS
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    assert client.active == 0
    assert all(reservation.closed for reservation in client.reservations)


class _Queue:
    def __init__(self, events: list[str], heartbeats: list[object] | None = None) -> None:
        self.events = events
        self.heartbeats = list(heartbeats or [_accepted("lease_extended")])
        self.complete_count = 0
        self.reschedules: list[int] = []

    async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
        del lease, lease_ttl_ms
        self.events.append("heartbeat")
        outcome = self.heartbeats.pop(0) if self.heartbeats else _accepted("lease_extended")
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, TransitionResult)
        return outcome

    async def complete(self, lease: Lease) -> TransitionResult:
        del lease
        self.events.append("complete")
        self.complete_count += 1
        return _accepted("completed")

    async def reschedule_at(self, lease: Lease, *, ready_at_ms: int) -> TransitionResult:
        del lease
        self.events.append("reschedule")
        self.reschedules.append(ready_at_ms)
        return _accepted("rescheduled")


def _dependencies(
    queue: _Queue,
    schedule_reader: AsyncMock,
    sleep=asyncio.sleep,
) -> LightpandaClaimantDependencies:
    return LightpandaClaimantDependencies(
        client=AsyncMock(),
        queue=queue,  # type: ignore[arg-type]
        pool=AsyncMock(),
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        schedule_reader=schedule_reader,
        sleep=sleep,
    )


async def test_claim_orders_heartbeat_activation_processing_schedule_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    queue = _Queue(events)
    blocked = asyncio.Event()

    async def activate(_pool, _fence) -> None:
        events.append("activate")

    async def process(*args, **kwargs) -> tuple[bool, float]:
        item = args[0]
        assert item.description_r2_hash is None
        assert args[3] == "json-ld"
        assert "fallback" not in args[4]
        assert kwargs["scrape_step"] == 0
        assert kwargs["pw"] is None
        assert kwargs["scrape_runtime"].implementation == "go"
        events.append("process")
        await _finish_mock_persistence(kwargs)
        return True, 0.0

    async def read_schedule(_pool, _posting_id) -> ScheduleState | None:
        events.append("schedule")
        return None

    async def sleep(_seconds: float) -> None:
        await blocked.wait()

    monkeypatch.setattr(claimant, "activate_write_fence", activate)
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()

    await _run_claim(
        _dependencies(queue, AsyncMock(side_effect=read_schedule), sleep),
        reservation,  # type: ignore[arg-type]
        _lease(),
        AsyncMock(),
    )

    assert events == [
        "heartbeat",
        "activate",
        "process",
        "heartbeat",
        "schedule",
        "complete",
    ]
    assert reservation.cancel_count == 0


async def test_authoritative_schedule_drives_exact_reschedule_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    queue = _Queue(events)
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())

    async def process(*_args, **kwargs) -> tuple[bool, float]:
        await _finish_mock_persistence(kwargs)
        return False, 0.0

    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reader = AsyncMock(
        return_value=ScheduleState(
            is_active=True,
            next_scrape_at=datetime(2026, 9, 10, 12, 34, 56, 789999, tzinfo=UTC),
        )
    )
    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    await _run_claim(
        _dependencies(queue, reader, sleep),
        _Reservation(),  # type: ignore[arg-type]
        _lease(),
        AsyncMock(),
    )

    expected = int(datetime(2026, 9, 10, 12, 34, 56, 789000, tzinfo=UTC).timestamp() * 1000)
    assert queue.reschedules == [expected]
    assert queue.complete_count == 0


async def test_heartbeat_continues_after_persistence_until_terminal_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule_started = asyncio.Event()
    post_write_heartbeat = asyncio.Event()

    class PostWriteHeartbeatQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([])
            self.heartbeat_count = 0

        async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
            self.heartbeat_count += 1
            if self.heartbeat_count == 3:
                post_write_heartbeat.set()
            return await super().heartbeat(lease, lease_ttl_ms=lease_ttl_ms)

    async def process(*_args, **kwargs) -> tuple[bool, float]:
        await _finish_mock_persistence(kwargs)
        return True, 0.0

    async def read_schedule(_pool, _posting_id: str) -> ScheduleState | None:
        schedule_started.set()
        await post_write_heartbeat.wait()
        return None

    pulse_count = 0
    never = asyncio.Event()

    async def pulse(_seconds: float) -> None:
        nonlocal pulse_count
        pulse_count += 1
        if pulse_count == 1:
            await schedule_started.wait()
        else:
            await never.wait()

    queue = PostWriteHeartbeatQueue()
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()

    await _run_claim(
        _dependencies(queue, AsyncMock(side_effect=read_schedule), pulse),
        reservation,  # type: ignore[arg-type]
        _lease(),
        AsyncMock(),
    )

    assert queue.heartbeat_count == 3
    assert queue.complete_count == 1
    assert reservation.cancel_count == 0


async def test_pre_activation_heartbeat_loss_closes_tls_without_work_or_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    queue = _Queue(
        events,
        [TransitionResult(Decision.FENCED, "claim_token_mismatch")],
    )
    activate = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(claimant, "activate_write_fence", activate)
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()

    with pytest.raises(LightpandaLeaseLost, match="pre-activation"):
        await _run_claim(
            _dependencies(queue, AsyncMock()),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    assert reservation.cancel_count == 1
    activate.assert_not_awaited()
    process.assert_not_awaited()
    assert queue.complete_count == 0
    assert queue.reschedules == []


async def test_pre_activation_heartbeat_transport_error_is_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([], [TimeoutError("redis timed out")])
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    reservation = _Reservation()

    with pytest.raises(LightpandaLeaseLost, match="indeterminate"):
        await _run_claim(
            _dependencies(queue, AsyncMock()),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    assert reservation.cancel_count == 1
    assert queue.complete_count == 0
    assert queue.reschedules == []


async def test_claim_lifecycle_timeout_cancels_tls_without_processing_or_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([])
    activation_started = asyncio.Event()

    async def blocked_activation(_pool, _fence) -> None:
        activation_started.set()
        await asyncio.Event().wait()

    process = AsyncMock()
    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    monkeypatch.setattr(claimant, "CLAIM_LIFECYCLE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(claimant, "activate_write_fence", blocked_activation)
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()

    with pytest.raises(LightpandaClaimantError, match="claim lifecycle timed out"):
        await _run_claim(
            _dependencies(queue, AsyncMock(), sleep),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    assert activation_started.is_set()
    assert reservation.cancel_count == 1
    process.assert_not_awaited()
    assert queue.complete_count == 0
    assert queue.reschedules == []


async def test_heartbeat_loss_during_render_cancels_tls_and_forbids_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    queue = _Queue(
        events,
        [
            _accepted("lease_extended"),
            TransitionResult(Decision.NOT_CURRENT, "lease_expired"),
        ],
    )
    render_started = asyncio.Event()
    render_cancelled = asyncio.Event()

    async def process(*_args, **_kwargs) -> tuple[bool, float]:
        render_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            render_cancelled.set()

    async def pulse(_seconds: float) -> None:
        await render_started.wait()

    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    schedule_reader = AsyncMock()
    reservation = _Reservation()

    with pytest.raises(LightpandaLeaseLost, match="lost authority"):
        await _run_claim(
            _dependencies(queue, schedule_reader, pulse),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    assert render_cancelled.is_set()
    assert reservation.cancel_count == 1
    assert schedule_reader.await_count == 0
    assert queue.complete_count == 0
    assert queue.reschedules == []


async def test_inflight_rejected_heartbeat_blocks_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_inflight = asyncio.Event()
    release_heartbeat = asyncio.Event()
    render_returned = asyncio.Event()
    write_entered = asyncio.Event()

    class RacingQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([])
            self.heartbeat_count = 0

        async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
            del lease, lease_ttl_ms
            self.heartbeat_count += 1
            if self.heartbeat_count == 1:
                return _accepted("lease_extended")
            heartbeat_inflight.set()
            await release_heartbeat.wait()
            return TransitionResult(Decision.FENCED, "claim_token_mismatch")

    async def process(*_args, **kwargs) -> tuple[bool, float]:
        render_returned.set()
        await heartbeat_inflight.wait()
        async with kwargs["authority_guard"]():
            write_entered.set()
        return True, 0.0

    async def pulse(_seconds: float) -> None:
        await render_returned.wait()

    queue = RacingQueue()
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()
    owner = asyncio.create_task(
        _run_claim(
            _dependencies(queue, AsyncMock(), pulse),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )
    )

    await asyncio.wait_for(heartbeat_inflight.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not write_entered.is_set()
    release_heartbeat.set()
    with pytest.raises(LightpandaLeaseLost, match="lost authority"):
        await owner

    assert not write_entered.is_set()
    assert reservation.cancel_count == 1
    assert queue.complete_count == 0


async def test_schedule_read_failure_leaves_redis_lease_untransitioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    queue = _Queue(events)
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())

    async def persisted_process(*_args, **kwargs) -> tuple[bool, float]:
        await _finish_mock_persistence(kwargs)
        return True, 0.0

    process = AsyncMock(side_effect=persisted_process)
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    reservation = _Reservation()
    with pytest.raises(RuntimeError, match="schedule unavailable"):
        await _run_claim(
            _dependencies(
                queue,
                AsyncMock(side_effect=RuntimeError("schedule unavailable")),
                sleep,
            ),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    process.assert_awaited_once()
    assert reservation.cancel_count == 1
    assert queue.complete_count == 0
    assert queue.reschedules == []


async def test_terminal_transport_error_closes_tls_without_second_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([])

    async def unavailable_complete(lease: Lease) -> TransitionResult:
        del lease
        queue.complete_count += 1
        raise TimeoutError("redis timed out")

    queue.complete = unavailable_complete  # type: ignore[method-assign]
    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())

    async def process(*_args, **kwargs) -> tuple[bool, float]:
        await _finish_mock_persistence(kwargs)
        return True, 0.0

    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    reservation = _Reservation()
    with pytest.raises(LightpandaLeaseLost, match="complete was indeterminate"):
        await _run_claim(
            _dependencies(queue, AsyncMock(return_value=None), sleep),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )

    assert reservation.cancel_count == 1
    assert queue.complete_count == 1
    assert queue.reschedules == []


async def test_heartbeat_and_terminal_transitions_are_serialized() -> None:
    heartbeat_started = asyncio.Event()
    release_heartbeat = asyncio.Event()
    terminal_called = asyncio.Event()

    class SerialQueue:
        async def heartbeat(self, lease: Lease, *, lease_ttl_ms: int) -> TransitionResult:
            del lease, lease_ttl_ms
            heartbeat_started.set()
            await release_heartbeat.wait()
            return _accepted("lease_extended")

        async def complete(self, lease: Lease) -> TransitionResult:
            del lease
            terminal_called.set()
            return _accepted("completed")

    guard = claimant._HeartbeatGuard(
        queue=SerialQueue(),  # type: ignore[arg-type]
        lease=_lease(),
        reservation=_Reservation(),  # type: ignore[arg-type]
        sleep=asyncio.sleep,
    )

    async def commit() -> None:
        async with guard.authorize_write():
            pass

    heartbeat = asyncio.create_task(commit())
    await asyncio.wait_for(heartbeat_started.wait(), timeout=1)
    terminal = asyncio.create_task(guard.complete())
    await asyncio.sleep(0)
    assert not terminal_called.is_set()

    release_heartbeat.set()
    await asyncio.gather(heartbeat, terminal)
    assert terminal_called.is_set()


async def test_authoritative_write_timeout_cancels_tls_and_loses_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([])
    reservation = _Reservation()
    guard = claimant._HeartbeatGuard(
        queue=queue,  # type: ignore[arg-type]
        lease=_lease(),
        reservation=reservation,  # type: ignore[arg-type]
        sleep=asyncio.sleep,
    )
    monkeypatch.setattr(claimant, "AUTHORITATIVE_WRITE_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(LightpandaLeaseLost, match="lease-safe deadline"):
        async with guard.authorize_write():
            await asyncio.Event().wait()

    assert reservation.cancel_count == 1
    assert queue.complete_count == 0


async def test_delayed_accepted_heartbeat_cannot_authorize_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([])
    reservation = _Reservation()
    guard = claimant._HeartbeatGuard(
        queue=queue,  # type: ignore[arg-type]
        lease=_lease(),
        reservation=reservation,  # type: ignore[arg-type]
        sleep=asyncio.sleep,
    )
    clock = iter((10.0, 16.0))
    monkeypatch.setattr(claimant, "monotonic", lambda: next(clock))
    write_entered = False

    with pytest.raises(LightpandaLeaseLost, match="authority-age budget"):
        async with guard.authorize_write():
            write_entered = True

    assert not write_entered
    assert reservation.cancel_count == 1


async def test_location_resolver_is_published_only_after_one_complete_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_started = asyncio.Event()
    release_load = asyncio.Event()
    resolver = MagicMock()
    resolver.entry_count = 1

    async def load(_pool) -> None:
        load_started.set()
        await release_load.wait()

    resolver.load = load
    factory = MagicMock(return_value=resolver)
    monkeypatch.setattr(lookup_module, "_location_resolver", None)
    monkeypatch.setattr(lookup_module, "_location_resolver_lock", asyncio.Lock())
    monkeypatch.setattr(lookup_module, "LocationResolver", factory)

    first = asyncio.create_task(lookup_module._get_location_resolver(AsyncMock()))
    await asyncio.wait_for(load_started.wait(), timeout=1)
    second = asyncio.create_task(lookup_module._get_location_resolver(AsyncMock()))
    await asyncio.sleep(0)
    assert not second.done()

    release_load.set()
    resolved_first, resolved_second = await asyncio.gather(first, second)
    assert resolved_first is resolver
    assert resolved_second is resolver
    factory.assert_called_once_with()


async def test_owner_cancellation_closes_tls_and_retains_child_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue([])
    process_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def process(*_args, **_kwargs) -> tuple[bool, float]:
        process_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()
            raise

    async def schedule_reader(_pool, _posting_id) -> ScheduleState:
        return ScheduleState(True, datetime(2026, 9, 10, tzinfo=UTC))

    never = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await never.wait()

    monkeypatch.setattr(claimant, "activate_write_fence", AsyncMock())
    monkeypatch.setattr(claimant, "_process_one_scrape", process)
    reservation = _Reservation()
    owner = asyncio.create_task(
        _run_claim(
            _dependencies(queue, AsyncMock(side_effect=schedule_reader), sleep),
            reservation,  # type: ignore[arg-type]
            _lease(),
            AsyncMock(),
        )
    )
    await asyncio.wait_for(process_started.wait(), timeout=1)

    owner.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    assert reservation.cancel_count == 1
    assert not owner.done()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=0.1)

    assert cleanup_finished.is_set()
