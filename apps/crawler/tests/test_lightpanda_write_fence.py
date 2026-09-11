"""Focused unit contracts for the Lightpanda B0 PostgreSQL write fence."""

from __future__ import annotations

import asyncio
import importlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.processing.scrape as scrape_module
from src.core.scrapers import JobContent
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda.write_fence import (
    _ACTIVATE,
    _REQUIRE,
    _REVOKE,
    LightpandaWriteFence,
    LightpandaWriteFenceRejected,
    activate_write_fence,
    authoritative_write,
)
from src.lightpanda_queue import Lease, LightpandaB0Task, RouteIdentity
from src.processing.scrape import ScrapeItem
from src.queries.scrape import (
    _RECORD_SCRAPE_SUCCESS,
    _RECORD_SCRAPE_TRANSIENT,
    _UPDATE_ENRICH_CONTENT,
)
from src.runtime.extraction import PythonScrapeRuntime


def _lease(*, posting_id: uuid.UUID | None = None) -> Lease:
    assignment = resolve_render_assignment(
        "json-ld",
        {
            "browser_backend": "lightpanda",
            "render": True,
            "routing_revision": "b0+1",
            "timeout": 5_000,
            "wait": "load",
            "wait_fallback": None,
        },
    )
    assert assignment is not None
    route = RouteIdentity(shard_id="lightpanda-b0", routing_epoch=7)
    task = LightpandaB0Task.create(
        task_id=str(posting_id or uuid.uuid4()),
        board_id="board-1",
        source_url="https://jobs.example.com/posting",
        policy_key="lightpanda-b0-v1",
        domain="jobs.example.com",
        route=route,
        config_revision=3,
        initial_ready_at_ms=0,
        assignment=assignment,
    )
    return Lease(task=task, claim_token="7:31", lease_until_ms=1_000)


def test_fence_is_exact_frozen_identity_derived_from_lease() -> None:
    lease = _lease()
    fence = LightpandaWriteFence.from_lease(lease)

    assert str(fence.job_posting_id) == lease.task.task_id
    assert fence.shard_id == lease.task.route.shard_id
    assert fence.routing_epoch == lease.task.route.routing_epoch
    assert fence.engine_owner == "python"
    assert fence.config_revision == lease.task.config_revision
    assert fence.payload_sha256 == lease.task.payload_sha256
    assert fence.claim_token == lease.claim_token
    assert fence.claim_sequence == 31
    with pytest.raises(FrozenInstanceError):
        fence.config_revision = 4  # type: ignore[misc]


def test_fence_rejects_non_uuid_task_identity() -> None:
    lease = _lease()
    bad_task = replace(lease.task, task_id="posting-1")

    with pytest.raises(ValueError, match="job_posting UUID"):
        LightpandaWriteFence.from_lease(
            Lease(task=bad_task, claim_token=lease.claim_token, lease_until_ms=1_000)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("routing_epoch", True),
        ("routing_epoch", 0),
        ("shard_id", "unsafe shard"),
        ("shard_id", "a" * 129),
        ("engine_owner", "rust"),
        ("config_revision", 0),
        ("payload_sha256", "A" * 64),
        ("claim_token", "8:31"),
        ("claim_token", "7:031"),
    ],
)
def test_fence_rejects_noncanonical_identity(field: str, value: object) -> None:
    fence = LightpandaWriteFence.from_lease(_lease())
    values = {
        "job_posting_id": fence.job_posting_id,
        "shard_id": fence.shard_id,
        "routing_epoch": fence.routing_epoch,
        "engine_owner": fence.engine_owner,
        "config_revision": fence.config_revision,
        "payload_sha256": fence.payload_sha256,
        "claim_token": fence.claim_token,
    }
    values[field] = value

    with pytest.raises(ValueError, match="invalid Lightpanda B0 write fence"):
        LightpandaWriteFence(**values)  # type: ignore[arg-type]


def test_fence_derives_exact_go_owner_from_go_lease() -> None:
    lease = _lease()
    go_task = replace(
        lease.task,
        route=RouteIdentity(
            shard_id=lease.task.route.shard_id,
            routing_epoch=lease.task.route.routing_epoch,
            engine_owner="go",
        ),
    )

    fence = LightpandaWriteFence.from_lease(
        Lease(task=go_task, claim_token=lease.claim_token, lease_until_ms=lease.lease_until_ms)
    )

    assert fence.engine_owner == "go"


def _pool_and_connection() -> tuple[MagicMock, AsyncMock]:
    pool = MagicMock()
    connection = AsyncMock()
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction = MagicMock(return_value=transaction)
    acquire = AsyncMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire)
    return pool, connection


async def test_activation_uses_one_short_transaction() -> None:
    pool, connection = _pool_and_connection()
    fence = LightpandaWriteFence.from_lease(_lease())

    await activate_write_fence(pool, fence)

    connection.transaction.assert_called_once_with()
    connection.execute.assert_awaited_once_with(_ACTIVATE, *fence.sql_args())


async def test_authoritative_write_checks_mutates_and_revokes_in_one_transaction() -> None:
    pool, connection = _pool_and_connection()
    fence = LightpandaWriteFence.from_lease(_lease())

    async with authoritative_write(pool, fence, job_posting_id=str(fence.job_posting_id)) as writer:
        assert writer is connection
        await writer.execute("UPDATE protected SET value = $1", "committed")

    assert connection.transaction.call_count == 1
    assert connection.execute.await_args_list == [
        ((_REQUIRE, *fence.sql_args()), {}),
        (("UPDATE protected SET value = $1", "committed"), {}),
        ((_REVOKE, *fence.sql_args()), {}),
    ]


async def test_authority_guard_enters_before_pool_and_exits_after_commit() -> None:
    pool, connection = _pool_and_connection()
    fence = LightpandaWriteFence.from_lease(_lease())
    guard_exited = False

    @asynccontextmanager
    async def authority_guard():
        nonlocal guard_exited
        pool.acquire.assert_not_called()
        yield
        assert connection.execute.await_args_list[-1].args[0] == _REVOKE
        guard_exited = True

    async with authoritative_write(
        pool,
        fence,
        job_posting_id=str(fence.job_posting_id),
        authority_guard=authority_guard,
    ) as writer:
        await writer.execute("UPDATE protected SET value = true")

    assert guard_exited


async def test_authority_guard_requires_a_write_fence() -> None:
    pool = MagicMock()

    @asynccontextmanager
    async def authority_guard():
        raise AssertionError("guard must not start")
        yield  # pragma: no cover

    with pytest.raises(ValueError, match="requires a write fence"):
        async with authoritative_write(
            pool,
            None,
            job_posting_id="legacy-id",
            authority_guard=authority_guard,
        ):
            raise AssertionError("unreachable")

    pool.acquire.assert_not_called()


async def test_legacy_authoritative_write_keeps_autocommit_shape() -> None:
    pool, connection = _pool_and_connection()

    async with authoritative_write(pool, None, job_posting_id="legacy-id") as writer:
        await writer.execute("UPDATE legacy SET value = true")

    connection.transaction.assert_not_called()
    connection.execute.assert_awaited_once_with("UPDATE legacy SET value = true")


async def test_authoritative_write_rechecks_target_before_pool_acquire() -> None:
    pool = MagicMock()
    fence = LightpandaWriteFence.from_lease(_lease())

    with pytest.raises(LightpandaWriteFenceRejected) as rejected:
        async with authoritative_write(pool, fence, job_posting_id=str(uuid.uuid4())):
            raise AssertionError("unreachable")

    assert rejected.value.reason == "job_posting_id_mismatch"
    pool.acquire.assert_not_called()


async def test_rejection_is_cancellation_and_bypasses_exception_handlers() -> None:
    pool, _connection = _pool_and_connection()
    fence = LightpandaWriteFence.from_lease(_lease())

    @asynccontextmanager
    async def rejected_transaction():
        raise LightpandaWriteFenceRejected("claim_token_mismatch")
        yield  # pragma: no cover

    pool.acquire = MagicMock(return_value=rejected_transaction())
    caught_by_exception = False
    try:
        async with authoritative_write(pool, fence, job_posting_id=str(fence.job_posting_id)):
            raise AssertionError("unreachable")
    except Exception:
        caught_by_exception = True
    except asyncio.CancelledError as exc:
        assert isinstance(exc, LightpandaWriteFenceRejected)
        assert exc.reason == "claim_token_mismatch"

    assert not caught_by_exception


def _install_scrape_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Failure-path coverage must not be the first call through the module's
    # lazy structlog proxy.  Caching that production processor chain would
    # leak across tests and defeat a later ``capture_logs()`` context.
    monkeypatch.setattr(scrape_module, "log", MagicMock())
    monkeypatch.setattr(
        scrape_module,
        "_batch",
        SimpleNamespace(
            _get_location_resolver=AsyncMock(return_value=object()),
            _resolve_locations=AsyncMock(return_value=([], [])),
            _get_technology_ids=AsyncMock(return_value={}),
            _get_occupation_ids=AsyncMock(return_value={}),
            _get_seniority_ids=AsyncMock(return_value={}),
            _get_currency_rates=AsyncMock(return_value={"EUR": 1.0}),
            _flush_location_misses=AsyncMock(),
        ),
    )
    monkeypatch.setattr(scrape_module, "_stage_r2_pending", MagicMock(return_value=None))


def _recording_authority(
    monkeypatch: pytest.MonkeyPatch,
    connection: AsyncMock,
    seen_fences: list[LightpandaWriteFence | None],
) -> None:
    @asynccontextmanager
    async def recording_write(_pool, fence, *, job_posting_id, authority_guard=None):
        assert authority_guard is None
        if fence is not None:
            assert job_posting_id == str(fence.job_posting_id)
        seen_fences.append(fence)
        yield connection

    monkeypatch.setattr(scrape_module, "authoritative_write", recording_write)


@pytest.mark.parametrize(
    ("content", "scrape_error", "expected_query", "expected_success"),
    [
        (JobContent(title="Engineer"), None, _RECORD_SCRAPE_SUCCESS, True),
        (JobContent(), None, _RECORD_SCRAPE_TRANSIENT, False),
        (None, RuntimeError("render failed"), _RECORD_SCRAPE_TRANSIENT, False),
    ],
    ids=("success", "empty", "failure"),
)
async def test_fenced_scrape_outcomes_use_one_authoritative_write(
    monkeypatch: pytest.MonkeyPatch,
    content: JobContent | None,
    scrape_error: Exception | None,
    expected_query: str,
    expected_success: bool,
) -> None:
    _install_scrape_dependencies(monkeypatch)
    fence = LightpandaWriteFence.from_lease(_lease())
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value="UPDATE 1")
    seen_fences: list[LightpandaWriteFence | None] = []
    _recording_authority(monkeypatch, connection, seen_fences)
    scrape = AsyncMock()
    runtime = PythonScrapeRuntime(scrape)
    if scrape_error is not None:
        scrape.side_effect = scrape_error
    else:
        scrape.return_value = content
    item = ScrapeItem(
        job_posting_id=str(fence.job_posting_id),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    success, _duration = await scrape_module._process_one_scrape(
        item,
        MagicMock(),
        AsyncMock(),
        "json-ld",
        None,
        scrape_runtime=runtime,
        write_fence=fence,
    )

    assert success is expected_success
    assert seen_fences == [fence]
    assert any(call.args[0] == expected_query for call in connection.execute.await_args_list)


@pytest.mark.parametrize(
    ("content", "expected_query", "expected_success"),
    [
        (JobContent(title="Engineer"), _UPDATE_ENRICH_CONTENT, True),
        (JobContent(), _RECORD_SCRAPE_TRANSIENT, False),
    ],
    ids=("success", "empty"),
)
async def test_fenced_enrich_outcomes_use_one_authoritative_write(
    monkeypatch: pytest.MonkeyPatch,
    content: JobContent,
    expected_query: str,
    expected_success: bool,
) -> None:
    _install_scrape_dependencies(monkeypatch)
    fence = LightpandaWriteFence.from_lease(_lease())
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value="UPDATE 1")
    seen_fences: list[LightpandaWriteFence | None] = []
    _recording_authority(monkeypatch, connection, seen_fences)
    runtime = PythonScrapeRuntime(AsyncMock(return_value=content))
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    item = ScrapeItem(
        job_posting_id=str(fence.job_posting_id),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    success, _duration = await scrape_module._process_one_enrich_scrape(
        item,
        pool,
        AsyncMock(),
        "json-ld",
        None,
        ["title"],
        scrape_runtime=runtime,
        write_fence=fence,
    )

    assert success is expected_success
    assert seen_fences == [fence]
    assert any(call.args[0] == expected_query for call in connection.execute.await_args_list)


async def test_fence_rejection_bypasses_scrape_failure_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scrape_dependencies(monkeypatch)
    fence = LightpandaWriteFence.from_lease(_lease())
    authority_attempts = 0

    @asynccontextmanager
    async def rejected_write(_pool, supplied_fence, *, job_posting_id, authority_guard=None):
        assert authority_guard is None
        nonlocal authority_attempts
        authority_attempts += 1
        assert supplied_fence == fence
        assert job_posting_id == str(fence.job_posting_id)
        raise LightpandaWriteFenceRejected("claim_token_mismatch")
        yield  # pragma: no cover

    monkeypatch.setattr(scrape_module, "authoritative_write", rejected_write)
    runtime = PythonScrapeRuntime(AsyncMock(return_value=JobContent()))
    item = ScrapeItem(
        job_posting_id=str(fence.job_posting_id),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    with pytest.raises(LightpandaWriteFenceRejected):
        await scrape_module._process_one_scrape(
            item,
            MagicMock(),
            AsyncMock(),
            "json-ld",
            None,
            scrape_runtime=runtime,
            write_fence=fence,
        )

    assert authority_attempts == 1


@pytest.mark.parametrize("enrich", [False, True], ids=("scrape", "enrich"))
async def test_mismatched_item_and_fence_cancel_before_render_or_sql(enrich: bool) -> None:
    fence = LightpandaWriteFence.from_lease(_lease())
    scrape = AsyncMock(return_value=JobContent(title="Engineer"))
    runtime = PythonScrapeRuntime(scrape)
    pool = MagicMock()
    item = ScrapeItem(
        job_posting_id=str(uuid.uuid4()),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    with pytest.raises(LightpandaWriteFenceRejected) as rejected:
        if enrich:
            await scrape_module._process_one_enrich_scrape(
                item,
                pool,
                AsyncMock(),
                "json-ld",
                None,
                ["title"],
                scrape_runtime=runtime,
                write_fence=fence,
            )
        else:
            await scrape_module._process_one_scrape(
                item,
                pool,
                AsyncMock(),
                "json-ld",
                None,
                scrape_runtime=runtime,
                write_fence=fence,
            )

    assert rejected.value.reason == "job_posting_id_mismatch"
    scrape.assert_not_awaited()
    pool.acquire.assert_not_called()


async def test_fenced_location_miss_flush_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scrape_dependencies(monkeypatch)
    scrape_module._batch._flush_location_misses.side_effect = RuntimeError("flush unavailable")
    fence = LightpandaWriteFence.from_lease(_lease())
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value="UPDATE 1")
    _recording_authority(monkeypatch, connection, [])
    runtime = PythonScrapeRuntime(AsyncMock(return_value=JobContent(title="Engineer")))
    item = ScrapeItem(
        job_posting_id=str(fence.job_posting_id),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    success, _duration = await scrape_module._process_one_scrape(
        item,
        MagicMock(),
        AsyncMock(),
        "json-ld",
        None,
        scrape_runtime=runtime,
        write_fence=fence,
    )

    assert success is True


@pytest.mark.parametrize("enrich", [False, True], ids=("scrape", "enrich"))
async def test_fenced_failure_persistence_database_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
    enrich: bool,
) -> None:
    _install_scrape_dependencies(monkeypatch)
    fence = LightpandaWriteFence.from_lease(_lease())

    @asynccontextmanager
    async def unavailable_write(
        _pool,
        supplied_fence,
        *,
        job_posting_id,
        authority_guard=None,
    ):
        assert authority_guard is None
        assert supplied_fence == fence
        assert job_posting_id == str(fence.job_posting_id)
        raise RuntimeError("failure persistence unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(scrape_module, "authoritative_write", unavailable_write)
    runtime = PythonScrapeRuntime(AsyncMock(side_effect=RuntimeError("render failed")))
    item = ScrapeItem(
        job_posting_id=str(fence.job_posting_id),
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    with pytest.raises(RuntimeError, match="failure persistence unavailable"):
        if enrich:
            await scrape_module._process_one_enrich_scrape(
                item,
                MagicMock(),
                AsyncMock(),
                "json-ld",
                None,
                ["title"],
                scrape_runtime=runtime,
                write_fence=fence,
            )
        else:
            await scrape_module._process_one_scrape(
                item,
                MagicMock(),
                AsyncMock(),
                "json-ld",
                None,
                scrape_runtime=runtime,
                write_fence=fence,
            )


@pytest.mark.parametrize("enrich", [False, True], ids=("scrape", "enrich"))
async def test_legacy_failure_persistence_database_errors_remain_suppressed(
    monkeypatch: pytest.MonkeyPatch,
    enrich: bool,
) -> None:
    _install_scrape_dependencies(monkeypatch)

    @asynccontextmanager
    async def unavailable_write(
        _pool,
        supplied_fence,
        *,
        job_posting_id,
        authority_guard=None,
    ):
        assert authority_guard is None
        assert supplied_fence is None
        assert job_posting_id == "legacy-posting"
        raise RuntimeError("failure persistence unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(scrape_module, "authoritative_write", unavailable_write)
    runtime = PythonScrapeRuntime(AsyncMock(side_effect=RuntimeError("render failed")))
    item = ScrapeItem(
        job_posting_id="legacy-posting",
        url="https://jobs.example.com/posting",
        board_id="board-1",
    )

    if enrich:
        success, _duration = await scrape_module._process_one_enrich_scrape(
            item,
            MagicMock(),
            AsyncMock(),
            "json-ld",
            None,
            ["title"],
            scrape_runtime=runtime,
        )
    else:
        success, _duration = await scrape_module._process_one_scrape(
            item,
            MagicMock(),
            AsyncMock(),
            "json-ld",
            None,
            scrape_runtime=runtime,
        )

    assert success is False


def test_migration_is_retained_public_uuid_fence_with_guarded_downgrade() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0024_add_lightpanda_b0_write_fence"
    )
    install = " ".join(migration._INSTALL_WRITE_FENCE.split())
    remove = " ".join(migration._REMOVE_WRITE_FENCE.split())

    assert migration.revision == "0024"
    assert migration.down_revision == "0023"
    assert "CREATE TABLE public.lightpanda_b0_write_fence" in install
    assert "job_posting_id uuid PRIMARY KEY" in install
    assert "REFERENCES public.job_posting(id) ON DELETE CASCADE" in install
    assert "engine_owner text NOT NULL CHECK (engine_owner = 'python')" in install
    assert install.count("^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$") == 3
    assert "payload_sha256" in install
    assert "FOR UPDATE" in install
    assert "state IN ('active', 'revoked')" in install
    assert "retained rows exist" in remove
    assert remove.index("IF EXISTS") < remove.index("DROP TABLE")


def test_go_owner_migration_changes_constraint_and_all_fence_functions() -> None:
    migration = importlib.import_module("src.migrations.versions.0026_allow_go_lightpanda_b0_owner")

    assert migration.revision == "0026"
    assert migration.down_revision == "0025"
    assert (
        migration._GO_OWNER_FUNCTIONS.count(  # noqa: SLF001
            "supplied_engine_owner NOT IN ('python', 'go')"
        )
        == 2
    )
    assert "supplied_engine_owner IS DISTINCT FROM current_fence.engine_owner" in (
        migration._GO_OWNER_FUNCTIONS  # noqa: SLF001
    )
    assert "Go rows exist" in migration._REFUSE_DOWNGRADE_WITH_GO_ROWS  # noqa: SLF001
