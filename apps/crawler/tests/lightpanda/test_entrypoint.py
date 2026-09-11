from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.config import Settings
from src.lightpanda.entrypoint import (
    LightpandaEntrypointConfig,
    LightpandaEntrypointError,
    _await_startup_or_shutdown,
    _close_redis_bounded,
    _run_dedicated,
    _run_until_shutdown,
    run_lightpanda_entrypoint,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "lightpanda_b0_claimant_mode": "enabled",
        "lightpanda_b0_service_host": "10.0.0.5",
        "lightpanda_b0_ca_certificate": "/run/lightpanda/ca.pem",
        "lightpanda_b0_client_certificate": "/run/lightpanda/client.pem",
        "lightpanda_b0_client_private_key": "/run/lightpanda/client-key.pem",
        "lightpanda_b0_ca_sha256": "a" * 64,
        "lightpanda_b0_server_leaf_sha256": "b" * 64,
        "lightpanda_b0_server_spki_sha256": "c" * 64,
        "lightpanda_b0_queue_namespace": "production-b0",
        "lightpanda_b0_shard_id": "lightpanda-b0",
        "lightpanda_b0_routing_epoch": "7",
        "metrics_port": 9099,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_claimant_configuration_is_off_by_default() -> None:
    assert Settings(_env_file=None).lightpanda_b0_claimant_mode == "off"  # type: ignore[call-arg]


@pytest.mark.parametrize("mode", ["off", "true", "1", "yes", "Enabled", " enabled"])
def test_claimant_requires_exact_enabled_mode(mode: str) -> None:
    with pytest.raises(LightpandaEntrypointError, match="disabled"):
        LightpandaEntrypointConfig.from_settings(_settings(lightpanda_b0_claimant_mode=mode))


@pytest.mark.parametrize(
    "field",
    [
        "lightpanda_b0_service_host",
        "lightpanda_b0_ca_certificate",
        "lightpanda_b0_client_certificate",
        "lightpanda_b0_client_private_key",
        "lightpanda_b0_ca_sha256",
        "lightpanda_b0_server_leaf_sha256",
        "lightpanda_b0_server_spki_sha256",
        "lightpanda_b0_queue_namespace",
        "lightpanda_b0_shard_id",
    ],
)
@pytest.mark.parametrize("value", ["", " value", "value ", "value\n"])
def test_claimant_rejects_missing_or_noncanonical_values(field: str, value: str) -> None:
    with pytest.raises(LightpandaEntrypointError, match=field.upper()):
        LightpandaEntrypointConfig.from_settings(_settings(**{field: value}))


@pytest.mark.parametrize("epoch", ["", "0", "01", "+1", " 1", "1 ", "1.0"])
def test_claimant_rejects_noncanonical_routing_epoch(epoch: str) -> None:
    with pytest.raises(LightpandaEntrypointError, match="ROUTING_EPOCH"):
        LightpandaEntrypointConfig.from_settings(_settings(lightpanda_b0_routing_epoch=epoch))


def test_claimant_builds_exact_local_configuration() -> None:
    config = LightpandaEntrypointConfig.from_settings(_settings())

    assert config == LightpandaEntrypointConfig(
        service_host="10.0.0.5",
        ca_certificate=Path("/run/lightpanda/ca.pem"),
        client_certificate=Path("/run/lightpanda/client.pem"),
        client_private_key=Path("/run/lightpanda/client-key.pem"),
        ca_sha256="a" * 64,
        server_leaf_sha256="b" * 64,
        server_spki_sha256="c" * 64,
        queue_namespace="production-b0",
        shard_id="lightpanda-b0",
        routing_epoch=7,
    )


def test_disabled_dedicated_entrypoint_imports_no_runtime_or_browser_modules() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                (
                    "import sys",
                    "sys.addaudithook(lambda event, args: (",
                    "    (_ for _ in ()).throw(RuntimeError('network I/O'))",
                    "    if event == 'socket.connect' else None",
                    "))",
                    "import src.lightpanda.entrypoint as entrypoint",
                    "sys.argv = ['lightpanda-claimant']",
                    "if entrypoint.main() != 1:",
                    "    raise SystemExit('disabled configuration was accepted')",
                    "for name in ('src.lightpanda.claimant', 'src.lightpanda.client',",
                    "             'src.processing.cpu', 'src.core.occupation_resolve',",
                    "             'src.core.scrapers', 'src.workers.pipeline',",
                    "             'playwright', 'polars'):",
                    "    if name in sys.modules:",
                    "        raise SystemExit(f'unexpected import: {name}')",
                    "if any(name.startswith('google.protobuf') for name in sys.modules):",
                    "    raise SystemExit('unexpected protobuf import')",
                )
            ),
        ],
        cwd=Path(__file__).parents[2],
        env={**os.environ, "LIGHTPANDA_B0_CLAIMANT_MODE": "off"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


async def test_enabled_dedicated_entrypoint_configures_logging_and_always_closes_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.config as config_module
    import src.db as db
    import src.lightpanda.entrypoint as entrypoint
    import src.shared.logging as logging_module

    events: list[str] = []
    configured = Mock(log_level="INFO")

    def settings_factory() -> Mock:
        events.append("settings")
        return configured

    def setup_logging(level: str) -> None:
        assert level == "INFO"
        events.append("logging")

    async def fail_claimant(settings: Mock, shutdown_event: asyncio.Event) -> None:
        assert settings is configured
        assert not shutdown_event.is_set()
        events.append("claimant")
        raise RuntimeError("claimant failed")

    async def close_pools() -> None:
        events.append("close-pools")

    monkeypatch.setattr(config_module, "Settings", settings_factory)
    monkeypatch.setattr(logging_module, "setup_logging", setup_logging)
    monkeypatch.setattr(db, "close_all_pools", close_pools)
    monkeypatch.setattr(entrypoint, "run_lightpanda_entrypoint", fail_claimant)

    with pytest.raises(RuntimeError, match="claimant failed"):
        await _run_dedicated("enabled", validate_only=False)

    assert events == ["settings", "logging", "claimant", "close-pools"]


async def test_invalid_certificates_fail_before_redis_postgres_or_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    get_redis = Mock()
    create_pool = AsyncMock()
    start_metrics = Mock()
    close_redis = AsyncMock()
    monkeypatch.setattr(redis_queue, "get_redis", get_redis)
    monkeypatch.setattr(redis_queue, "close_redis", close_redis)
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(metrics, "start_metrics_server", start_metrics)

    with pytest.raises(ValueError, match="bounded regular file"):
        await run_lightpanda_entrypoint(_settings(), asyncio.Event())

    get_redis.assert_not_called()
    create_pool.assert_not_awaited()
    start_metrics.assert_not_called()
    close_redis.assert_not_awaited()


async def test_build_identity_failure_precedes_redis_postgres_and_claimant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.lightpanda.claimant as claimant
    import src.lightpanda.client as client
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    monkeypatch.setattr(client, "LightpandaB0Client", lambda _config: object())
    get_redis = Mock()
    create_pool = AsyncMock()
    run_claimant = AsyncMock()
    close_redis = AsyncMock()
    monkeypatch.setattr(redis_queue, "get_redis", get_redis)
    monkeypatch.setattr(redis_queue, "close_redis", close_redis)
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(claimant, "run_lightpanda_claimant", run_claimant)
    monkeypatch.setattr(
        metrics,
        "start_metrics_server",
        Mock(side_effect=RuntimeError("installed image identity mismatch")),
    )

    with pytest.raises(RuntimeError, match="installed image identity mismatch"):
        await run_lightpanda_entrypoint(_settings(), asyncio.Event())

    get_redis.assert_not_called()
    create_pool.assert_not_awaited()
    run_claimant.assert_not_awaited()
    close_redis.assert_not_awaited()


async def test_invalid_namespace_precedes_metrics_and_authority_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.lightpanda.client as client
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    monkeypatch.setattr(client, "LightpandaB0Client", lambda _config: object())
    get_redis = Mock()
    create_pool = AsyncMock()
    start_metrics = Mock()
    monkeypatch.setattr(redis_queue, "get_redis", get_redis)
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(metrics, "start_metrics_server", start_metrics)

    with pytest.raises(ValueError, match="namespace"):
        await run_lightpanda_entrypoint(
            _settings(lightpanda_b0_queue_namespace="bad namespace"),
            asyncio.Event(),
        )

    start_metrics.assert_not_called()
    get_redis.assert_not_called()
    create_pool.assert_not_awaited()


async def test_entrypoint_composes_claimant_and_closes_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.lightpanda.claimant as claimant
    import src.lightpanda.client as client
    import src.lightpanda_queue as lightpanda_queue
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    service_config: Any = None
    fake_client = object()
    fake_redis = object()
    fake_queue = object()
    fake_pool = object()
    observed_dependencies: Any = None
    claimant_cancelled = asyncio.Event()

    class FakeClient:
        def __new__(cls, config: object) -> object:
            nonlocal service_config
            service_config = config
            return fake_client

    class FakeQueue:
        def __new__(cls, redis: object, *, namespace: str) -> object:
            assert redis is fake_redis
            assert namespace == "production-b0"
            return fake_queue

    async def fake_claimant(dependencies: object) -> None:
        nonlocal observed_dependencies
        observed_dependencies = dependencies
        shutdown.set()
        try:
            await asyncio.Future()
        finally:
            claimant_cancelled.set()

    get_redis = Mock(return_value=fake_redis)
    create_pool = AsyncMock(return_value=fake_pool)
    close_redis = AsyncMock()
    start_metrics = Mock()
    monkeypatch.setattr(client, "LightpandaB0Client", FakeClient)
    monkeypatch.setattr(lightpanda_queue, "LightpandaB0Queue", FakeQueue)
    monkeypatch.setattr(claimant, "run_lightpanda_claimant", fake_claimant)
    monkeypatch.setattr(redis_queue, "get_redis", get_redis)
    monkeypatch.setattr(redis_queue, "close_redis", close_redis)
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(metrics, "start_metrics_server", start_metrics)
    shutdown = asyncio.Event()

    await run_lightpanda_entrypoint(_settings(), shutdown)

    assert service_config is not None
    assert service_config.host == "10.0.0.5"
    assert observed_dependencies is not None
    assert observed_dependencies.client is fake_client
    assert observed_dependencies.queue is fake_queue
    assert observed_dependencies.pool is fake_pool
    assert observed_dependencies.route == lightpanda_queue.RouteIdentity(
        shard_id="lightpanda-b0",
        routing_epoch=7,
    )
    assert observed_dependencies.schedule_reader is claimant.read_authoritative_schedule
    assert claimant_cancelled.is_set()
    get_redis.assert_called_once_with()
    create_pool.assert_awaited_once_with()
    start_metrics.assert_called_once_with(9099)
    close_redis.assert_awaited_once_with()


async def test_preset_shutdown_opens_no_metrics_or_authority_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.lightpanda.client as client
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    monkeypatch.setattr(client, "LightpandaB0Client", lambda _config: object())
    get_redis = Mock()
    create_pool = AsyncMock()
    start_metrics = Mock()
    monkeypatch.setattr(redis_queue, "get_redis", get_redis)
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(metrics, "start_metrics_server", start_metrics)
    shutdown = asyncio.Event()
    shutdown.set()

    await run_lightpanda_entrypoint(_settings(), shutdown)

    start_metrics.assert_not_called()
    get_redis.assert_not_called()
    create_pool.assert_not_awaited()


async def test_shutdown_during_postgres_startup_prevents_claimant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.db as db
    import src.lightpanda.claimant as claimant
    import src.lightpanda.client as client
    import src.lightpanda_queue as lightpanda_queue
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    monkeypatch.setattr(client, "LightpandaB0Client", lambda _config: object())
    monkeypatch.setattr(lightpanda_queue, "LightpandaB0Queue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(redis_queue, "get_redis", Mock(return_value=object()))
    close_redis = AsyncMock()
    monkeypatch.setattr(redis_queue, "close_redis", close_redis)
    monkeypatch.setattr(metrics, "start_metrics_server", Mock())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def create_pool() -> object:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    run_claimant = AsyncMock()
    monkeypatch.setattr(db, "create_local_pool", create_pool)
    monkeypatch.setattr(claimant, "run_lightpanda_claimant", run_claimant)
    shutdown = asyncio.Event()
    entrypoint = asyncio.create_task(run_lightpanda_entrypoint(_settings(), shutdown))
    await started.wait()
    shutdown.set()

    await entrypoint

    assert cancelled.is_set()
    run_claimant.assert_not_called()
    close_redis.assert_awaited_once_with()


async def test_external_cancellation_joins_postgres_startup() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def create_pool() -> object:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    supervisor = asyncio.create_task(
        _await_startup_or_shutdown(
            create_pool,
            asyncio.Event(),
            operation="PostgreSQL pool creation",
        )
    )
    await started.wait()
    supervisor.cancel()

    with pytest.raises(asyncio.CancelledError):
        await supervisor
    assert cancelled.is_set()


async def test_entrypoint_propagates_claimant_failure_and_closes_redis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import src.db as db
    import src.lightpanda.claimant as claimant
    import src.lightpanda.client as client
    import src.lightpanda_queue as lightpanda_queue
    import src.metrics as metrics
    import src.redis_queue as redis_queue

    monkeypatch.setattr(client, "LightpandaB0Client", lambda _config: object())
    monkeypatch.setattr(lightpanda_queue, "LightpandaB0Queue", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(redis_queue, "get_redis", Mock(return_value=object()))
    close_redis = AsyncMock(side_effect=RuntimeError("redis close failed"))
    monkeypatch.setattr(redis_queue, "close_redis", close_redis)
    monkeypatch.setattr(db, "create_local_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(metrics, "start_metrics_server", Mock())

    async def fail(_dependencies: object) -> None:
        raise RuntimeError("claimant failed closed")

    monkeypatch.setattr(claimant, "run_lightpanda_claimant", fail)
    settings = _settings(
        lightpanda_b0_ca_certificate=str(tmp_path / "ca.pem"),
        lightpanda_b0_client_certificate=str(tmp_path / "client.pem"),
        lightpanda_b0_client_private_key=str(tmp_path / "client-key.pem"),
    )

    with pytest.raises(RuntimeError, match="claimant failed closed") as error:
        await run_lightpanda_entrypoint(settings, asyncio.Event())

    assert error.value.__notes__ == [
        "Lightpanda B0 Redis cleanup also failed; the primary failure is preserved"
    ]
    close_redis.assert_awaited_once_with()


async def test_shutdown_preserves_cleanup_failure() -> None:
    shutdown = asyncio.Event()

    async def claimant() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError as exc:
            raise RuntimeError("shutdown audit failed") from exc

    task = asyncio.create_task(_run_until_shutdown(claimant, shutdown))
    await asyncio.sleep(0)
    shutdown.set()

    with pytest.raises(RuntimeError, match="shutdown audit failed"):
        await task


async def test_external_cancellation_joins_claimant_shutdown_audit() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def claimant() -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    supervisor = asyncio.create_task(_run_until_shutdown(claimant, asyncio.Event()))
    await started.wait()
    supervisor.cancel()

    with pytest.raises(asyncio.CancelledError):
        await supervisor
    assert cancelled.is_set()


async def test_cancellation_resistance_triggers_hard_fail_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.lightpanda.entrypoint as entrypoint

    release = asyncio.Event()

    async def claimant() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()

    class HardExit(Exception):
        pass

    def hard_exit(message: str) -> None:
        assert "claimant did not stop" in message
        release.set()
        raise HardExit

    monkeypatch.setattr(entrypoint, "_CLAIMANT_SHUTDOWN_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(entrypoint, "_hard_exit", hard_exit)
    shutdown = asyncio.Event()
    task = asyncio.create_task(_run_until_shutdown(claimant, shutdown))
    await asyncio.sleep(0)
    shutdown.set()

    with pytest.raises(HardExit):
        await task
    await asyncio.sleep(0)


async def test_redis_close_timeout_triggers_hard_fail_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.lightpanda.entrypoint as entrypoint

    release = asyncio.Event()

    async def close_redis() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()

    class HardExit(Exception):
        pass

    def hard_exit(message: str) -> None:
        assert "Redis cleanup did not stop" in message
        release.set()
        raise HardExit

    monkeypatch.setattr(entrypoint, "_REDIS_CLOSE_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(entrypoint, "_hard_exit", hard_exit)

    with pytest.raises(HardExit):
        await _close_redis_bounded(close_redis)
    await asyncio.sleep(0)


async def test_external_cancellation_joins_redis_cleanup() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def close_redis() -> None:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    supervisor = asyncio.create_task(_close_redis_bounded(close_redis))
    await started.wait()
    supervisor.cancel()

    with pytest.raises(asyncio.CancelledError):
        await supervisor
    assert cancelled.is_set()


async def test_second_cancellation_during_redis_cleanup_hard_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.lightpanda.entrypoint as entrypoint

    started = asyncio.Event()
    release = asyncio.Event()

    async def close_redis() -> None:
        started.set()
        await release.wait()

    class HardExit(Exception):
        pass

    def hard_exit(message: str) -> None:
        assert "supervisor cancellation interrupted Redis cleanup" in message
        release.set()
        raise HardExit

    monkeypatch.setattr(entrypoint, "_hard_exit", hard_exit)
    supervisor = asyncio.create_task(
        _close_redis_bounded(close_redis, primary_error=asyncio.CancelledError())
    )
    await started.wait()
    supervisor.cancel()

    with pytest.raises(HardExit):
        await supervisor
    await asyncio.sleep(0)


def test_claimant_is_installed_only_as_a_direct_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.cli import parse_args

    monkeypatch.setattr(sys, "argv", ["crawler", "run-lightpanda-claimant"])
    with pytest.raises(SystemExit):
        parse_args()
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    assert 'lightpanda-claimant = "src.lightpanda.entrypoint:main"' in pyproject.read_text()
