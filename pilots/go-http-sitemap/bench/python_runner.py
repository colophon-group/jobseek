#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import os
import random
import resource
import sys
import time
import traceback
import types
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from common import interval_max, job_manifest_sha256, validate_fixture_origin

RUNTIME_PACKAGES = (
    "anyio",
    "certifi",
    "h11",
    "httpcore",
    "httpx",
    "idna",
    "prometheus-client",
    "structlog",
    "typing-extensions",
)
ABSENT_RUNTIME_PACKAGES = ("sniffio",)
PROTOCOL_FRAME_LIMIT_BYTES = 16 * 1024 * 1024


def _canonical_distribution_name(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


def _distribution_census() -> list[dict[str, str]]:
    rows = [
        {
            "name": _canonical_distribution_name(str(distribution.metadata["Name"])),
            "version": distribution.version,
        }
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    ]
    return sorted(rows, key=lambda row: (row["name"], row["version"]))


def _os_release() -> dict[str, str]:
    release_path = Path("/etc/os-release")
    if not release_path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in release_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def _load_production_sitemap(source_bundle: Path):
    source_root = source_bundle.resolve()
    expected = source_root / "src" / "core" / "monitors" / "sitemap.py"
    if not expected.is_file():
        raise RuntimeError("staged production sitemap.py is missing")
    sys.path.insert(0, str(source_root))

    # Import only the allowlisted production modules. The real monitor registry
    # eagerly imports every crawler provider, which is unrelated to this
    # isolated executor comparison. Registration and save_raw are the only
    # sitemap.py imports replaced by this seam; discover() and its production
    # retry/TDM/metrics dependencies execute unchanged from staged source.
    monitors_package = types.ModuleType("src.core.monitors")
    monitors_package.__path__ = [str(source_root / "src" / "core" / "monitors")]
    monitors_package.register = lambda *args, **kwargs: None
    sys.modules["src.core.monitors"] = monitors_package
    raw_module = types.ModuleType("src.core.monitors.raw")

    async def unused_save_text_response(*args: object, **kwargs: object) -> None:
        raise RuntimeError("benchmark must not use the raw-artifact callback")

    raw_module.save_text_response = unused_save_text_response
    sys.modules["src.core.monitors.raw"] = raw_module

    import src.metrics as metrics
    from src.core.monitors import sitemap
    from src.shared import constants as shared_constants
    from src.shared import http_retry, tdm

    modules = (shared_constants, http_retry, tdm, metrics, sitemap)
    identities = []
    for module in modules:
        path = Path(module.__file__).resolve()
        if source_root not in path.parents:
            raise RuntimeError(f"production module escaped source bundle: {module.__name__}")
        identities.append(
            {
                "module": module.__name__,
                "relative_path": str(path.relative_to(source_root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return sitemap, http_retry, identities


def _usage() -> tuple[int, int]:
    value = resource.getrusage(resource.RUSAGE_SELF)
    return (
        round(value.ru_utime * 1_000_000_000),
        round(value.ru_stime * 1_000_000_000),
    )


def _set_fd_limit(limit: int) -> int:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if limit <= 0 or limit > hard:
        raise RuntimeError("invalid file-descriptor limit")
    resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def _open_fds() -> int:
    for directory in (Path("/proc/self/fd"), Path("/dev/fd")):
        try:
            return len(list(directory.iterdir()))
        except OSError:
            pass
    return -1


def _protocol_stream_reader() -> asyncio.StreamReader:
    return asyncio.StreamReader(limit=PROTOCOL_FRAME_LIMIT_BYTES)


def _httpcore_pool_limits(client: httpx.AsyncClient) -> tuple[int, int]:
    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    max_connections = getattr(pool, "_max_connections", None)
    max_keepalive_connections = getattr(pool, "_max_keepalive_connections", None)
    if not isinstance(max_connections, int) or not isinstance(max_keepalive_connections, int):
        raise RuntimeError("pinned httpcore pool limits are unavailable")
    return max_connections, max_keepalive_connections


def _url_digest(urls: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(urls)).encode("utf-8")).hexdigest()


def _classify(
    exc: BaseException | None, pagination_error: type[BaseException]
) -> tuple[str, str | None]:
    if exc is None:
        return "success", None
    if isinstance(exc, pagination_error):
        return "retry_exhausted", type(exc).__name__
    if isinstance(exc, TimeoutError):
        return "deadline", type(exc).__name__
    return "error", type(exc).__name__


class CountingClient:
    """Per-job counters over the one process-owned AsyncClient."""

    def __init__(self, client: httpx.AsyncClient, max_requests: int = 8) -> None:
        self.client = client
        self.max_requests = max_requests
        self.requests = 0
        self.wire_attempts = 0
        self.decoded_bytes = 0
        self.status_body_bytes = 0

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.requests >= self.max_requests:
            raise RuntimeError("per-job request cap exceeded")
        self.requests += 1
        self.wire_attempts += 1
        response = await self.client.get(url, **kwargs)
        if response.status_code == 200:
            self.decoded_bytes += len(response.content)
        else:
            self.status_body_bytes += len(response.content)
        return response


@dataclass(slots=True)
class Envelope:
    batch_id: str
    job: dict[str, Any]
    accepted_ns: int


class AsyncWorkerPool:
    def __init__(
        self,
        *,
        sitemap_module: Any,
        pagination_error: type[BaseException],
        client: httpx.AsyncClient,
        workers: int,
        capacity: int,
        result_capacity: int,
        per_origin: int,
        job_timeout_seconds: int,
    ) -> None:
        self.sitemap = sitemap_module
        self.pagination_error = pagination_error
        self.client = client
        self.capacity = capacity
        self.job_timeout_seconds = job_timeout_seconds
        self.input: asyncio.Queue[Envelope | None] = asyncio.Queue(maxsize=capacity)
        self.results: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=result_capacity)
        self.slots = asyncio.Semaphore(capacity)
        self.origin_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(per_origin)
        )
        self.workers = [asyncio.create_task(self._worker()) for _ in range(workers)]
        self.accepted = 0
        self.completed = 0
        self.panics = 0
        self.closed = False

    async def _worker(self) -> None:
        while True:
            envelope = await self.input.get()
            if envelope is None:
                self.input.task_done()
                return
            origin = str(envelope.job["origin"])
            async with self.origin_limits[origin]:
                started_ns = time.perf_counter_ns()
                result = await self._process(envelope, started_ns)
            await self.results.put(result)
            self.completed += 1
            self.slots.release()
            self.input.task_done()

    async def _process(self, envelope: Envelope, started_ns: int) -> dict[str, Any]:
        job = envelope.job
        counting = CountingClient(self.client)
        board = {
            "board_url": job["sitemap_url"],
            "metadata": {"sitemap_url": job["sitemap_url"]},
        }
        urls: set[str] = set()
        new_sitemap: str | None = None
        exc: BaseException | None = None
        try:
            async with asyncio.timeout(self.job_timeout_seconds):
                urls, new_sitemap = await self.sitemap.discover(board, counting)
            if new_sitemap is not None:
                raise RuntimeError("cached fixture sitemap unexpectedly rediscovered")
        except Exception as error:  # exact production terminal exceptions become records
            exc = error
        finished_ns = time.perf_counter_ns()
        outcome, error_type = _classify(exc, self.pagination_error)
        return {
            "id": job["id"],
            "origin": job["origin"],
            "scenario": job["scenario"],
            "outcome": outcome,
            "error_type": error_type,
            "url_count": len(urls),
            "url_digest_sha256": _url_digest(urls),
            "filtered_count": 0,
            "truncated": False,
            "requests": counting.requests,
            "wire_attempts": counting.wire_attempts,
            "decoded_bytes": counting.decoded_bytes,
            "status_body_bytes": counting.status_body_bytes,
            "queue_ns": started_ns - envelope.accepted_ns,
            "service_ns": finished_ns - started_ns,
            "end_to_end_ns": finished_ns - envelope.accepted_ns,
            "accepted_ns": envelope.accepted_ns,
            "started_ns": started_ns,
            "finished_ns": finished_ns,
        }

    async def run_batch(self, command: dict[str, Any]) -> dict[str, Any]:
        jobs = list(command["jobs"])
        digest = job_manifest_sha256(jobs)
        if digest != command["manifest_sha256"]:
            raise RuntimeError("job manifest digest mismatch")
        if len({job["id"] for job in jobs}) != len(jobs):
            raise RuntimeError("duplicate job ID")
        for job in jobs:
            origin = validate_fixture_origin(str(job["origin"]))
            target = urlsplit(str(job["sitemap_url"]))
            if f"{target.scheme}://{target.hostname}:{target.port}" != origin:
                raise RuntimeError("sitemap URL escaped its declared fixture origin")

        accepted_before = self.accepted
        completed_before = self.completed
        user_before, system_before = _usage()
        wall_start = time.perf_counter_ns()

        async def feed() -> None:
            for job in jobs:
                await self.slots.acquire()
                accepted_ns = time.perf_counter_ns()
                self.accepted += 1
                self.input.put_nowait(
                    Envelope(batch_id=str(command["batch_id"]), job=job, accepted_ns=accepted_ns)
                )

        feeder = asyncio.create_task(feed())
        outputs = [await self.results.get() for _ in jobs]
        for _ in outputs:
            self.results.task_done()
        await feeder
        await self.input.join()
        wall_ns = time.perf_counter_ns() - wall_start
        user_after, system_after = _usage()
        outputs.sort(key=lambda output: str(output["id"]))
        return {
            "type": "batch",
            "implementation": "python-production-sitemap",
            "batch_id": command["batch_id"],
            "phase": command["phase"],
            "manifest_sha256": digest,
            "jobs": outputs,
            "wall_ns": wall_ns,
            "user_cpu_ns": user_after - user_before,
            "sys_cpu_ns": system_after - system_before,
            "open_fds_end": _open_fds(),
            "max_queued": interval_max(
                [(output["accepted_ns"], output["started_ns"]) for output in outputs]
            ),
            "max_in_flight": interval_max(
                [(output["started_ns"], output["finished_ns"]) for output in outputs]
            ),
            "max_unfinished": interval_max(
                [(output["accepted_ns"], output["finished_ns"]) for output in outputs]
            ),
            "accepted": self.accepted - accepted_before,
            "completed": self.completed - completed_before,
            "panics": self.panics,
            "unfinished": len(jobs) - len(outputs),
            "asyncio_tasks_end": len(asyncio.all_tasks()),
            "process_children": 0,
        }

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for _ in self.workers:
            await self.input.put(None)
        await self.input.join()
        await asyncio.gather(*self.workers)


async def _protocol(args: argparse.Namespace) -> int:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(f"Python comparator requires Python 3.13, got {sys.version.split()[0]}")
    soft_fd_limit = _set_fd_limit(args.fd_limit)
    sitemap, http_retry, source_modules = _load_production_sitemap(args.source_bundle)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    random.seed(args.retry_random_seed)
    executable_path = Path(sys.executable)
    executable_sha256 = hashlib.sha256(executable_path.read_bytes()).hexdigest()
    lock_sha256 = hashlib.sha256(args.lock_file.read_bytes()).hexdigest()
    package_versions = {name: importlib.metadata.version(name) for name in RUNTIME_PACKAGES}
    absent_packages = [
        name
        for name in ABSENT_RUNTIME_PACKAGES
        if not any(
            row["name"] == _canonical_distribution_name(name) for row in _distribution_census()
        )
    ]

    limits = httpx.Limits(
        max_connections=args.total_connections,
        max_keepalive_connections=args.global_idle,
        keepalive_expiry=float(args.idle_expiry_seconds),
    )
    timeout = httpx.Timeout(float(args.request_timeout_seconds))
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=False,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        pool_max_connections, pool_max_keepalive_connections = _httpcore_pool_limits(client)
        if pool_max_connections != args.total_connections:
            raise RuntimeError("httpcore total-connection limit does not match configuration")
        if pool_max_keepalive_connections != args.global_idle:
            raise RuntimeError("httpcore idle-connection limit does not match configuration")
        pool = AsyncWorkerPool(
            sitemap_module=sitemap,
            pagination_error=http_retry.PaginationFetchError,
            client=client,
            workers=args.workers,
            capacity=args.capacity,
            result_capacity=args.result_capacity,
            per_origin=args.per_origin,
            job_timeout_seconds=args.job_timeout_seconds,
        )
        ready = {
            "type": "ready",
            "implementation": "python-production-sitemap",
            "pid": os.getpid(),
            "runtime": sys.version.split()[0],
            "workers": args.workers,
            "capacity": args.capacity,
            "result_capacity": args.result_capacity,
            "per_origin_concurrency": args.per_origin,
            "global_active_requests": args.global_active,
            "global_total_connections": args.total_connections,
            "global_idle_connections": args.global_idle,
            "per_origin_idle_audit_bound": args.idle_per_origin,
            "per_origin_idle_enforcement": "workload-and-fixture-audit-not-httpx-setting",
            "idle_expiry_seconds": args.idle_expiry_seconds,
            "request_timeout_seconds": args.request_timeout_seconds,
            "job_timeout_seconds": args.job_timeout_seconds,
            "file_descriptor_soft_limit": soft_fd_limit,
            "client_constructed_before_ready": True,
            "pool_constructed_before_ready": True,
            "httpcore_pool_max_connections": pool_max_connections,
            "httpcore_pool_max_keepalive_connections": pool_max_keepalive_connections,
            "source_modules": source_modules,
            "source_commit": args.source_commit,
            "source_identity_sha256": args.source_identity_sha256,
            "lock_sha256": lock_sha256,
            "runtime_package_versions": package_versions,
            "absent_runtime_packages": absent_packages,
            "installed_distributions": _distribution_census(),
            "executable_path_basename": executable_path.name,
            "executable_path": str(executable_path),
            "executable_sha256": executable_sha256,
            "os_release": _os_release(),
            "isolation_seam": [
                "src.core.monitors.register",
                "src.core.monitors.raw.save_text_response",
            ],
            "production_entrypoint": "src.core.monitors.sitemap.discover",
            "retry_random_seed": args.retry_random_seed,
        }
        print(json.dumps(ready, sort_keys=True), flush=True)

        loop = asyncio.get_running_loop()
        reader = _protocol_stream_reader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        try:
            while line := await reader.readline():
                command = json.loads(line)
                if command.get("action") == "batch":
                    output = await pool.run_batch(command)
                    print(json.dumps(output, sort_keys=True), flush=True)
                elif command.get("action") == "shutdown":
                    await pool.close()
                    print(
                        json.dumps(
                            {"type": "stopped", "implementation": "python-production-sitemap"},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    return 0
                else:
                    raise RuntimeError("unknown command")
        finally:
            with contextlib.suppress(Exception):
                await pool.close()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-identity-sha256", required=True)
    parser.add_argument("--lock-file", type=Path, required=True)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--capacity", type=int, required=True)
    parser.add_argument("--result-capacity", type=int, required=True)
    parser.add_argument("--per-origin", type=int, required=True)
    parser.add_argument("--global-active", type=int, required=True)
    parser.add_argument("--total-connections", type=int, required=True)
    parser.add_argument("--global-idle", type=int, required=True)
    parser.add_argument("--idle-per-origin", type=int, required=True)
    parser.add_argument("--idle-expiry-seconds", type=int, required=True)
    parser.add_argument("--request-timeout-seconds", type=int, required=True)
    parser.add_argument("--job-timeout-seconds", type=int, required=True)
    parser.add_argument("--fd-limit", type=int, required=True)
    parser.add_argument("--retry-random-seed", type=int, default=7948)
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(_protocol(_parse_args()))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
