"""Fail-closed composition root for the dark Lightpanda B0 claimant."""

from __future__ import annotations

import asyncio
import os
import re
import signal
import stat
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Final, NoReturn

if TYPE_CHECKING:
    from src.config import Settings

_ENABLED_MODE: Final = "enabled"
_DARK_MODE: Final = "dark"
_OFF_MODE: Final = "off"
_READY_FILE: Final = Path("/run/jobseek/lightpanda-claimant.ready")
_POSTGRES_STARTUP_TIMEOUT_SECONDS: Final = 15.0
_STARTUP_CANCEL_TIMEOUT_SECONDS: Final = 5.0
_CLAIMANT_SHUTDOWN_TIMEOUT_SECONDS: Final = 15.0
_REDIS_CLOSE_TIMEOUT_SECONDS: Final = 5.0
_CANONICAL_POSITIVE_INTEGER: Final = re.compile(r"^[1-9][0-9]*$")
_REQUIRED_STRING_FIELDS: Final = (
    "lightpanda_b0_service_host",
    "lightpanda_b0_ca_certificate",
    "lightpanda_b0_client_certificate",
    "lightpanda_b0_client_private_key",
    "lightpanda_b0_ca_sha256",
    "lightpanda_b0_server_leaf_sha256",
    "lightpanda_b0_server_spki_sha256",
    "lightpanda_b0_queue_namespace",
    "lightpanda_b0_shard_id",
)
_DARK_ENVIRONMENT_FIELDS: Final = {
    "service_host": "LIGHTPANDA_B0_SERVICE_HOST",
    "ca_certificate": "LIGHTPANDA_B0_CA_CERTIFICATE",
    "client_certificate": "LIGHTPANDA_B0_CLIENT_CERTIFICATE",
    "client_private_key": "LIGHTPANDA_B0_CLIENT_PRIVATE_KEY",
    "ca_sha256": "LIGHTPANDA_B0_CA_SHA256_FILE",
    "server_leaf_sha256": "LIGHTPANDA_B0_SERVER_LEAF_SHA256_FILE",
    "server_spki_sha256": "LIGHTPANDA_B0_SERVER_SPKI_SHA256_FILE",
    "queue_namespace": "LIGHTPANDA_B0_QUEUE_NAMESPACE",
    "shard_id": "LIGHTPANDA_B0_SHARD_ID",
    "routing_epoch": "LIGHTPANDA_B0_ROUTING_EPOCH",
}


class LightpandaEntrypointError(RuntimeError):
    """The dedicated claimant cannot start or stop with proved authority."""


def _hard_exit(message: str) -> NoReturn:
    """Terminate the container when cooperative cleanup cannot be proved."""

    sys.stderr.write(f"Lightpanda B0 claimant failed closed: {message}\n")
    sys.stderr.flush()
    os._exit(1)


@dataclass(frozen=True, slots=True)
class LightpandaEntrypointConfig:
    service_host: str
    ca_certificate: Path
    client_certificate: Path
    client_private_key: Path
    ca_sha256: str
    server_leaf_sha256: str
    server_spki_sha256: str
    queue_namespace: str
    shard_id: str
    routing_epoch: int

    @classmethod
    def from_dark_environment(cls) -> LightpandaEntrypointConfig:
        """Validate the networkless service environment and immutable bundle."""

        if os.environ.get("LIGHTPANDA_B0_CLAIMANT_MODE", _OFF_MODE) != _DARK_MODE:
            raise LightpandaEntrypointError(
                'Lightpanda B0 dark claimant mode must be exactly "dark"'
            )
        if os.environ.get("CRAWLER_DB_POOL_MIN") != "0":
            raise LightpandaEntrypointError("CRAWLER_DB_POOL_MIN must be exactly 0")
        if os.environ.get("CRAWLER_DB_POOL_MAX") != "1":
            raise LightpandaEntrypointError("CRAWLER_DB_POOL_MAX must be exactly 1")

        values = {
            field: _canonical_environment_value(environment_name)
            for field, environment_name in _DARK_ENVIRONMENT_FIELDS.items()
        }
        if values["service_host"] != "10.0.0.5":
            raise LightpandaEntrypointError(
                "LIGHTPANDA_B0_SERVICE_HOST must be the fixed renderer private IP"
            )
        if _CANONICAL_POSITIVE_INTEGER.fullmatch(values["routing_epoch"]) is None:
            raise LightpandaEntrypointError(
                "LIGHTPANDA_B0_ROUTING_EPOCH must be a canonical positive integer"
            )

        from src.lightpanda.credentials import (
            ClaimantCredentialPaths,
            validate_installed_claimant_credentials,
        )
        from src.lightpanda.identity import (
            validate_lightpanda_b0_namespace,
            validate_lightpanda_b0_shard_id,
        )

        try:
            queue_namespace = validate_lightpanda_b0_namespace(values["queue_namespace"])
            shard_id = validate_lightpanda_b0_shard_id(values["shard_id"])
            pins = validate_installed_claimant_credentials(
                ClaimantCredentialPaths(
                    ca_certificate=Path(values["ca_certificate"]),
                    client_certificate=Path(values["client_certificate"]),
                    client_private_key=Path(values["client_private_key"]),
                    ca_sha256=Path(values["ca_sha256"]),
                    server_leaf_sha256=Path(values["server_leaf_sha256"]),
                    server_spki_sha256=Path(values["server_spki_sha256"]),
                )
            )
        except ValueError as exc:
            raise LightpandaEntrypointError("Lightpanda B0 dark configuration is invalid") from exc

        return cls(
            service_host=values["service_host"],
            ca_certificate=Path(values["ca_certificate"]),
            client_certificate=Path(values["client_certificate"]),
            client_private_key=Path(values["client_private_key"]),
            ca_sha256=pins.ca_sha256,
            server_leaf_sha256=pins.server_leaf_sha256,
            server_spki_sha256=pins.server_spki_sha256,
            queue_namespace=queue_namespace,
            shard_id=shard_id,
            routing_epoch=int(values["routing_epoch"]),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> LightpandaEntrypointConfig:
        """Validate the complete local configuration before any external I/O."""

        if settings.lightpanda_b0_claimant_mode != _ENABLED_MODE:
            raise LightpandaEntrypointError(
                'Lightpanda B0 claimant is disabled; mode must be exactly "enabled"'
            )

        values: dict[str, str] = {}
        for field in _REQUIRED_STRING_FIELDS:
            value = getattr(settings, field)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise LightpandaEntrypointError(
                    f"{field.upper()} is required and must be canonical"
                )
            values[field] = value

        raw_epoch = settings.lightpanda_b0_routing_epoch
        if (
            not isinstance(raw_epoch, str)
            or _CANONICAL_POSITIVE_INTEGER.fullmatch(raw_epoch) is None
        ):
            raise LightpandaEntrypointError(
                "LIGHTPANDA_B0_ROUTING_EPOCH must be a canonical positive integer"
            )

        return cls(
            service_host=values["lightpanda_b0_service_host"],
            ca_certificate=Path(values["lightpanda_b0_ca_certificate"]),
            client_certificate=Path(values["lightpanda_b0_client_certificate"]),
            client_private_key=Path(values["lightpanda_b0_client_private_key"]),
            ca_sha256=values["lightpanda_b0_ca_sha256"],
            server_leaf_sha256=values["lightpanda_b0_server_leaf_sha256"],
            server_spki_sha256=values["lightpanda_b0_server_spki_sha256"],
            queue_namespace=values["lightpanda_b0_queue_namespace"],
            shard_id=values["lightpanda_b0_shard_id"],
            routing_epoch=int(raw_epoch),
        )


def _canonical_environment_value(name: str) -> str:
    value = os.environ.get(name)
    if (
        value is None
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise LightpandaEntrypointError(f"{name} is required and must be canonical")
    return value


async def run_lightpanda_entrypoint(
    settings: Settings,
    shutdown_event: asyncio.Event,
) -> None:
    """Compose and supervise the fixed claimant without browser-worker imports."""

    # All local scalar validation is deliberately ahead of imports that own
    # Redis/Postgres resources.  LightpandaB0Client then validates the bounded
    # certificate files and pins before either external dependency is opened.
    config = LightpandaEntrypointConfig.from_settings(settings)

    from src.lightpanda.claimant import (
        LightpandaClaimantDependencies,
        read_authoritative_schedule,
        run_lightpanda_claimant,
    )
    from src.lightpanda.client import (
        LightpandaB0Client,
        LightpandaServiceConfig,
    )
    from src.lightpanda_queue import (
        LightpandaB0Queue,
        RouteIdentity,
        validate_lightpanda_b0_namespace,
    )

    route = RouteIdentity(
        shard_id=config.shard_id,
        routing_epoch=config.routing_epoch,
    )
    queue_namespace = validate_lightpanda_b0_namespace(config.queue_namespace)
    client = LightpandaB0Client(
        LightpandaServiceConfig(
            host=config.service_host,
            ca_certificate=config.ca_certificate,
            client_certificate=config.client_certificate,
            client_private_key=config.client_private_key,
            ca_sha256=config.ca_sha256,
            server_leaf_sha256=config.server_leaf_sha256,
            server_spki_sha256=config.server_spki_sha256,
        )
    )

    from src.metrics import start_metrics_server

    if shutdown_event.is_set():
        return

    # The metrics bootstrap first validates the installed image/version
    # identity.  Keep it ahead of Redis/Postgres so a mismatched artifact has
    # no opportunity to contact an authority service.
    start_metrics_server(settings.metrics_port)

    if shutdown_event.is_set():
        return

    from src.db import create_local_pool
    from src.redis_queue import close_redis, get_redis

    redis_opened = False
    primary_error: BaseException | None = None
    primary_traceback: TracebackType | None = None
    try:
        redis = get_redis()
        redis_opened = True
        queue = LightpandaB0Queue(redis, namespace=queue_namespace)
        pool = await _await_startup_or_shutdown(
            create_local_pool,
            shutdown_event,
            operation="PostgreSQL pool creation",
        )
        if pool is not None and not shutdown_event.is_set():
            dependencies = LightpandaClaimantDependencies(
                client=client,
                queue=queue,
                pool=pool,
                route=route,
                schedule_reader=read_authoritative_schedule,
            )
            await _run_until_shutdown(
                lambda: run_lightpanda_claimant(dependencies),
                shutdown_event,
            )
    except BaseException as exc:
        primary_error = exc
        primary_traceback = exc.__traceback__
    finally:
        cleanup_error = (
            await _close_redis_bounded(close_redis, primary_error=primary_error)
            if redis_opened
            else None
        )
        if primary_error is not None:
            if cleanup_error is not None:
                primary_error.add_note(
                    "Lightpanda B0 Redis cleanup also failed; the primary failure is preserved"
                )
            raise primary_error.with_traceback(primary_traceback)
        if cleanup_error is not None:
            raise LightpandaEntrypointError("Lightpanda B0 Redis cleanup failed") from cleanup_error


async def _await_startup_or_shutdown[Result](
    operation_factory: Callable[[], Awaitable[Result]],
    shutdown_event: asyncio.Event,
    *,
    operation: str,
) -> Result | None:
    """Bound one authority-service startup and make shutdown win before claims."""

    if shutdown_event.is_set():
        return None
    operation_awaitable = operation_factory()
    operation_task = asyncio.ensure_future(operation_awaitable)
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    try:
        done, _pending = await asyncio.wait(
            (operation_task, shutdown_task),
            timeout=_POSTGRES_STARTUP_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            return await operation_task

        await _cancel_and_join(
            operation_task,
            timeout_seconds=_STARTUP_CANCEL_TIMEOUT_SECONDS,
            failure=f"{operation} did not cancel within its shutdown deadline",
        )
        if shutdown_task in done:
            return None
        raise LightpandaEntrypointError(f"{operation} timed out")
    except asyncio.CancelledError as cancellation:
        await _cancel_and_join_preserving_cancellation(
            operation_task,
            cancellation=cancellation,
            timeout_seconds=_STARTUP_CANCEL_TIMEOUT_SECONDS,
            failure=f"{operation} did not cancel within its shutdown deadline",
            cleanup_note=f"{operation} cleanup also failed; supervisor cancellation is preserved",
        )
        raise
    finally:
        shutdown_task.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown_task


async def _run_until_shutdown(
    claimant_factory: Callable[[], Awaitable[None]],
    shutdown_event: asyncio.Event,
) -> None:
    """Cancel and join the claimant so its final queue audit cannot be skipped."""

    if shutdown_event.is_set():
        return
    claimant_task = asyncio.ensure_future(claimant_factory())
    shutdown_task: asyncio.Task[bool] | None = None
    try:
        # No await occurs between task creation and this second check, so a
        # stop already requested at the scheduling boundary cancels before
        # claimant code can execute.
        if shutdown_event.is_set():
            await _cancel_and_join(
                claimant_task,
                timeout_seconds=_CLAIMANT_SHUTDOWN_TIMEOUT_SECONDS,
                failure="claimant did not stop within its shutdown deadline",
            )
            return
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        done, _pending = await asyncio.wait(
            (claimant_task, shutdown_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if claimant_task in done:
            try:
                await claimant_task
            except asyncio.CancelledError as exc:
                raise LightpandaEntrypointError(
                    "Lightpanda B0 claimant was cancelled unexpectedly"
                ) from exc
            raise LightpandaEntrypointError("Lightpanda B0 claimant stopped unexpectedly")

        claimant_task.cancel()
        await _cancel_and_join(
            claimant_task,
            timeout_seconds=_CLAIMANT_SHUTDOWN_TIMEOUT_SECONDS,
            failure="claimant did not stop within its shutdown deadline",
        )
    except asyncio.CancelledError as cancellation:
        await _cancel_and_join_preserving_cancellation(
            claimant_task,
            cancellation=cancellation,
            timeout_seconds=_CLAIMANT_SHUTDOWN_TIMEOUT_SECONDS,
            failure="claimant did not stop within its shutdown deadline",
            cleanup_note=(
                "Lightpanda B0 claimant cleanup also failed; supervisor cancellation is preserved"
            ),
        )
        raise
    finally:
        if not claimant_task.done():
            claimant_task.cancel()
        if shutdown_task is not None:
            shutdown_task.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown_task


async def _cancel_and_join[Result](
    task: asyncio.Future[Result],
    *,
    timeout_seconds: float,
    failure: str,
) -> None:
    """Cooperatively cancel one task or terminate the unsafe process."""

    task.cancel()
    done, _pending = await asyncio.wait((task,), timeout=timeout_seconds)
    if task not in done:
        _hard_exit(failure)
    with suppress(asyncio.CancelledError):
        await task


async def _cancel_and_join_preserving_cancellation[Result](
    task: asyncio.Future[Result],
    *,
    cancellation: asyncio.CancelledError,
    timeout_seconds: float,
    failure: str,
    cleanup_note: str,
) -> None:
    """Finish owned-task teardown before propagating supervisor cancellation."""

    try:
        await _cancel_and_join(
            task,
            timeout_seconds=timeout_seconds,
            failure=failure,
        )
    except asyncio.CancelledError:
        # A second supervisor cancellation must not detach the owned task.
        _hard_exit(failure)
    except BaseException:
        cancellation.add_note(cleanup_note)


async def _close_redis_bounded(
    close_redis: Callable[[], Awaitable[None]],
    *,
    primary_error: BaseException | None = None,
) -> BaseException | None:
    """Close the Redis pool without masking a prior authoritative failure."""

    try:
        close_awaitable = close_redis()
    except BaseException as exc:
        return exc
    close_task = asyncio.ensure_future(close_awaitable)
    try:
        done, _pending = await asyncio.wait((close_task,), timeout=_REDIS_CLOSE_TIMEOUT_SECONDS)
    except asyncio.CancelledError as cancellation:
        if primary_error is not None:
            # Cleanup is already preserving an earlier failure/cancellation;
            # another cancellation cannot safely replace it or detach Redis.
            _hard_exit("supervisor cancellation interrupted Redis cleanup")
        await _cancel_and_join_preserving_cancellation(
            close_task,
            cancellation=cancellation,
            timeout_seconds=_REDIS_CLOSE_TIMEOUT_SECONDS,
            failure="Redis cleanup did not stop within its deadline",
            cleanup_note=(
                "Lightpanda B0 Redis cleanup also failed; supervisor cancellation is preserved"
            ),
        )
        raise
    if close_task not in done:
        close_task.cancel()
        _hard_exit("Redis cleanup did not stop within its deadline")
    try:
        await close_task
    except BaseException as exc:
        return exc
    return None


def _write_ready_file() -> None:
    """Publish local process readiness only after every dark check succeeds."""

    try:
        _READY_FILE.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
        descriptor = os.open(
            _READY_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o400,
        )
        try:
            os.write(descriptor, b"lightpanda-b0-dark-ready\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise LightpandaEntrypointError("could not publish dark claimant readiness") from exc


def _remove_ready_file() -> None:
    with suppress(FileNotFoundError):
        _READY_FILE.unlink()


def _healthcheck() -> int:
    """Check the Docker-local marker without importing runtime dependencies."""

    try:
        metadata = _READY_FILE.lstat()
        payload = _READY_FILE.read_bytes()
    except OSError:
        return 1
    return int(
        not (
            stat.S_ISREG(metadata.st_mode)
            and not _READY_FILE.is_symlink()
            and stat.S_IMODE(metadata.st_mode) == 0o400
            and metadata.st_uid == os.getuid()
            and metadata.st_gid == os.getgid()
            and payload == b"lightpanda-b0-dark-ready\n"
        )
    )


async def _run_dark_claimant(shutdown_event: asyncio.Event) -> None:
    """Validate the future authority boundary, then wait without network I/O."""

    LightpandaEntrypointConfig.from_dark_environment()
    _remove_ready_file()
    _write_ready_file()
    try:
        await shutdown_event.wait()
    finally:
        _remove_ready_file()


async def _run_dedicated(mode: str, *, validate_only: bool) -> None:
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, shutdown_event.set)

    if mode == _DARK_MODE:
        if validate_only:
            LightpandaEntrypointConfig.from_dark_environment()
            return
        await _run_dark_claimant(shutdown_event)
        return

    if validate_only:
        raise LightpandaEntrypointError("validation-only is supported only in dark mode")
    from src.config import Settings

    await run_lightpanda_entrypoint(Settings(), shutdown_event)  # type: ignore[call-arg]


def main() -> int:
    """Dedicated console entry point; dark mode never routes through ``src.cli``."""

    arguments = sys.argv[1:]
    if arguments == ["--healthcheck"]:
        return _healthcheck()
    if arguments not in ([], ["--validate-only"]):
        sys.stderr.write("Lightpanda B0 claimant failed closed: unsupported argument\n")
        return 2

    mode = os.environ.get("LIGHTPANDA_B0_CLAIMANT_MODE", _OFF_MODE)
    if mode not in {_DARK_MODE, _ENABLED_MODE}:
        sys.stderr.write(
            'Lightpanda B0 claimant failed closed: mode must be exactly "dark" or "enabled"\n'
        )
        return 1
    try:
        asyncio.run(_run_dedicated(mode, validate_only=bool(arguments)))
    except (LightpandaEntrypointError, ValueError) as exc:
        sys.stderr.write(f"Lightpanda B0 claimant failed closed: {exc}\n")
        return 1
    return 0


__all__ = [
    "LightpandaEntrypointConfig",
    "LightpandaEntrypointError",
    "main",
    "run_lightpanda_entrypoint",
]


if __name__ == "__main__":
    raise SystemExit(main())
