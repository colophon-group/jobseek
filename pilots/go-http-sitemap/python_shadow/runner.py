"""Credential-free Python reference for the direct-urlset fleet benchmark.

The image copies the production Python sitemap parser, monitor result/filter,
TDM, and SSRF modules from ``apps/crawler``.  This runner deliberately narrows
the transport contract to one direct HTTPS GET per job: no redirects, retries,
rediscovery, or sitemap-index traversal.  Output is sanitized aggregate JSON;
discovered URLs and exception text are never serialized.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import http.cookiejar
import ipaddress
import json
import re
import resource
import ssl
import stat
import struct
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
MANIFEST_PATH = Path("/canary/fleet.json")
MAX_MANIFEST_BYTES = 128 << 10
MIN_MANIFEST_JOBS = 16
MAX_MANIFEST_JOBS = 32
MAX_RESPONSE_BYTES = 8 << 20
MAX_URLS = 50_000
MAX_INDEX_CHILDREN_SENTINEL = 1
MAX_AGGREGATE_BYTES = 32 << 20
MAX_CONNECTIONS = 20
MAX_KEEPALIVE_CONNECTIONS = 10
REQUEST_TIMEOUT_SECONDS = 20
JOB_TIMEOUT_SECONDS = 25
ROUND_TIMEOUT_SECONDS = 90
PROCESS_TIMEOUT_SECONDS = 210
MAX_FILE_DESCRIPTORS = 256
ROUNDS = 2
ALLOWED_CONCURRENCY = (2, 4, 5, 8, 12, 16)
ALLOWED_PROFILES = tuple(f"c{value}" for value in ALLOWED_CONCURRENCY)
HASH_ALGORITHM = "sha256-length-prefixed-v1"
SOURCE_COMMIT = "@SOURCE_COMMIT@"
IMAGE_IDENTITY = "@IMAGE_IDENTITY@"
EXPECTED_MANIFEST_SHA256 = "@MANIFEST_SHA256@"
EXPECTED_IMAGE_PREFIX = "ghcr.io/colophon-group/jobseek-sitemap-fleet-python:sha-"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_REGEX_META = frozenset(r".\\^$*+?{}[]|()")
_SITEMAP_HEADERS = {
    "User-Agent": "jobseek-crawler (+https://jseek.co/)",
    "Accept": "application/xml,text/xml,*/*;q=0.8",
    "Accept-Encoding": "identity",
}
_current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "python_shadow_current_job", default=None
)


class ManifestError(ValueError):
    pass


class TargetRejectedError(RuntimeError):
    pass


class AttemptLimitError(RuntimeError):
    pass


class ResponseBodyTooLargeError(RuntimeError):
    pass


class RedirectRejectedError(RuntimeError):
    pass


class StatusRejectedError(RuntimeError):
    pass


class ContentEncodingRejectedError(RuntimeError):
    pass


class SitemapIndexRejectedError(RuntimeError):
    pass


class EmptyResultError(RuntimeError):
    pass


class _RejectAllCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request) -> bool:
        del cookie, request
        return False


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    board_url: str
    sitemap_url: str
    include_literal: str
    exclude_literal: str
    max_urls: int
    max_index_children: int


@dataclass(frozen=True, slots=True)
class Manifest:
    schema_version: int
    jobs: tuple[Job, ...]


@dataclass(slots=True)
class AttemptStats:
    requests: int = 0
    wire_attempts: int = 0


@dataclass(slots=True)
class RoundMeter:
    attempts: dict[str, AttemptStats] = field(default_factory=dict)
    expected_urls: dict[str, str] = field(default_factory=dict)
    in_flight: int = 0
    max_in_flight: int = 0

    def reset(self, jobs: tuple[Job, ...]) -> None:
        self.attempts = {job.id: AttemptStats() for job in jobs}
        self.expected_urls = {job.id: job.sitemap_url for job in jobs}
        self.in_flight = 0
        self.max_in_flight = 0


@dataclass(slots=True)
class ConnectionProbe:
    transport: Any
    maximum_connections: int = 0
    maximum_waiters: int = 0
    maximum_fd_count: int | None = None
    baseline_fd_count: int | None = None
    current_waiters: int = 0

    def __post_init__(self) -> None:
        self.baseline_fd_count = self._fd_count()

    @staticmethod
    def _fd_count() -> int | None:
        try:
            return len(tuple(Path("/proc/self/fd").iterdir()))
        except OSError:
            return None

    def sample(self) -> int:
        try:
            open_connections = len(self.transport._pool.connections)
            waiters = sum(1 for request in self.transport._pool._requests if request.is_queued())
        except (AttributeError, TypeError) as exc:
            raise RuntimeError("connection metrics unavailable") from exc
        self.maximum_connections = max(self.maximum_connections, open_connections)
        self.current_waiters = waiters
        self.maximum_waiters = max(self.maximum_waiters, waiters)
        fd_count = self._fd_count()
        if fd_count is not None:
            self.maximum_fd_count = max(self.maximum_fd_count or 0, fd_count)
        return open_connections


def _is_public_https_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2_048:
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed.hostname.isascii()
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_plain_literal(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 256
        and not any(character in _REGEX_META for character in value)
    )


def decode_manifest(contents: bytes) -> Manifest:
    if not contents or len(contents) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest size invalid")
    try:
        raw = json.loads(contents)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("manifest JSON invalid") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "jobs"}:
        raise ManifestError("manifest shape invalid")
    raw_jobs = raw["jobs"]
    if raw["schema_version"] != SCHEMA_VERSION or not isinstance(raw_jobs, list):
        raise ManifestError("manifest version invalid")
    if not MIN_MANIFEST_JOBS <= len(raw_jobs) <= MAX_MANIFEST_JOBS:
        raise ManifestError("manifest fleet size invalid")

    expected_fields = {
        "id",
        "board_url",
        "sitemap_url",
        "include_literal",
        "exclude_literal",
        "max_urls",
        "max_index_children",
    }
    jobs: list[Job] = []
    ids: set[str] = set()
    origins: set[tuple[str, str, int]] = set()
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict) or set(raw_job) != expected_fields:
            raise ManifestError("manifest job shape invalid")
        try:
            job = Job(**raw_job)
        except TypeError as exc:
            raise ManifestError("manifest job type invalid") from exc
        if not isinstance(job.id, str) or not _ID_RE.fullmatch(job.id) or job.id in ids:
            raise ManifestError("manifest job id invalid")
        if not _is_public_https_url(job.board_url) or not _is_public_https_url(job.sitemap_url):
            raise ManifestError("manifest URL invalid")
        parsed = urlparse(job.sitemap_url)
        origin = (parsed.scheme, parsed.hostname or "", parsed.port or 443)
        if origin in origins:
            raise ManifestError("manifest sitemap origins must be distinct")
        if (
            not _is_plain_literal(job.include_literal)
            or not _is_plain_literal(job.exclude_literal)
            or job.max_urls != MAX_URLS
            or job.max_index_children != MAX_INDEX_CHILDREN_SENTINEL
        ):
            raise ManifestError("manifest limits or filters invalid")
        ids.add(job.id)
        origins.add(origin)
        jobs.append(job)
    return Manifest(schema_version=SCHEMA_VERSION, jobs=tuple(jobs))


def load_fixed_manifest() -> tuple[Manifest, str]:
    try:
        file_stat = MANIFEST_PATH.lstat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise ManifestError("manifest is not a regular file")
        if file_stat.st_size <= 0 or file_stat.st_size > MAX_MANIFEST_BYTES:
            raise ManifestError("manifest file size invalid")
        contents = MANIFEST_PATH.read_bytes()
    except OSError as exc:
        raise ManifestError("manifest file invalid") from exc
    digest = hashlib.sha256(contents).hexdigest()
    if digest != EXPECTED_MANIFEST_SHA256:
        raise ManifestError("manifest does not match embedded digest")
    return decode_manifest(contents), digest


def parse_profile(argv: list[str]) -> tuple[str, int]:
    # Hand parsing is intentional: argparse writes rejected input to stderr,
    # while the artifact protocol permits exactly one sanitized stdout line.
    if len(argv) != 2 or argv[0] != "--profile" or argv[1] not in ALLOWED_PROFILES:
        raise ManifestError("arguments rejected")
    return argv[1], int(argv[1][1:])


def valid_source_identity() -> bool:
    return bool(
        re.fullmatch(r"[0-9a-f]{40}", SOURCE_COMMIT)
        and IMAGE_IDENTITY == EXPECTED_IMAGE_PREFIX + SOURCE_COMMIT
    )


def canonical_url_sha256(urls: set[str]) -> str:
    digest = hashlib.sha256()
    for url in sorted(urls):
        encoded = url.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def _resource_snapshot() -> tuple[float, float, int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # The benchmark image is Linux/arm64, where ru_maxrss is KiB.
    return usage.ru_utime, usage.ru_stime, int(usage.ru_maxrss) * 1024


def _current_rss_bytes() -> int:
    try:
        contents = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError:
        return -1
    for line in contents.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "VmRSS:" and fields[2] == "kB":
            try:
                value = int(fields[1])
            except ValueError:
                return -1
            return value * 1024 if value >= 0 else -1
    return -1


def _open_fds() -> int:
    value = ConnectionProbe._fd_count()
    return value if value is not None else -1


def _cgroup_value(name: str) -> int | None:
    try:
        raw = Path("/sys/fs/cgroup", name).read_text(encoding="ascii").strip()
        value = int(raw)
    except (OSError, ValueError):
        return None
    return value if value >= 0 else None


def _cgroup_event(name: str) -> int | None:
    try:
        contents = Path("/sys/fs/cgroup/memory.events").read_text(encoding="ascii")
    except OSError:
        return None
    for line in contents.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] != name:
            continue
        try:
            value = int(fields[1])
        except ValueError:
            return None
        return value if value >= 0 else None
    return None


def _safe_error_kind(exc: BaseException) -> str:
    names = {cls.__name__ for cls in type(exc).__mro__}
    mapping = (
        ("TDMReservedError", "tdm_reserved"),
        ("SSRFError", "ssrf_refused"),
        ("TargetRejectedError", "target_rejected"),
        ("AttemptLimitError", "attempt_limit"),
        ("ResponseBodyTooLargeError", "response_too_large"),
        ("RedirectRejectedError", "redirect_rejected"),
        ("StatusRejectedError", "status_rejected"),
        ("ContentEncodingRejectedError", "content_encoding_rejected"),
        ("SitemapIndexRejectedError", "sitemap_index_rejected"),
        ("EmptyResultError", "empty_result"),
        ("TimeoutError", "deadline"),
        ("HTTPError", "http_transport"),
        ("ParseError", "xml"),
    )
    for class_name, kind in mapping:
        if class_name in names:
            return kind
    return "internal"


def _monitor_config(job: Job) -> dict[str, Any]:
    if job.include_literal and job.exclude_literal:
        url_filter: str | dict[str, str] = {
            "include": job.include_literal,
            "exclude": job.exclude_literal,
        }
    elif job.include_literal:
        url_filter = job.include_literal
    else:
        url_filter = {"exclude": job.exclude_literal}
    return {"sitemap_url": job.sitemap_url, "url_filter": url_filter}


def _load_runtime():
    app_path = "/app"
    if app_path not in sys.path and Path(app_path).is_dir():
        sys.path.insert(0, app_path)

    import certifi
    import httpx
    import structlog

    structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
    from src.core import monitor as production_monitor
    from src.core.monitors import sitemap as production_sitemap
    from src.shared import ssrf as production_ssrf
    from src.shared import tdm as production_tdm

    return httpx, certifi, production_monitor, production_sitemap, production_ssrf, production_tdm


class ExactTargetTransport:
    """Factory for an httpx transport that admits one exact request per job."""

    @staticmethod
    def make(httpx, inner, meter: RoundMeter):
        class Transport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                job_id = _current_job.get()
                if job_id is None or job_id not in meter.expected_urls:
                    raise TargetRejectedError
                if str(request.url) != meter.expected_urls[job_id]:
                    raise TargetRejectedError
                headers = {key.lower(): value for key, value in request.headers.items()}
                if (
                    headers.get("user-agent") != _SITEMAP_HEADERS["User-Agent"]
                    or headers.get("accept") != _SITEMAP_HEADERS["Accept"]
                    or headers.get("accept-encoding") != "identity"
                    or any(
                        sensitive in headers
                        for sensitive in ("authorization", "proxy-authorization", "cookie")
                    )
                ):
                    raise TargetRejectedError
                attempt = meter.attempts[job_id]
                if attempt.requests != 0:
                    raise AttemptLimitError
                attempt.requests += 1
                response = await inner.handle_async_request(request)
                attempt.wire_attempts += 1
                return response

            async def aclose(self) -> None:
                await inner.aclose()

        return Transport()


async def _fetch_and_extract(
    job: Job,
    *,
    client,
    production_monitor,
    production_sitemap,
    production_tdm,
    connection_probe,
) -> tuple[set[str], int, bool, int, int]:
    async with client.stream(
        "GET",
        job.sitemap_url,
        headers=_SITEMAP_HEADERS,
        follow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        connection_probe.sample()
        # Header policy and terminal status are checked before any body byte.
        production_tdm.check_response(response)
        if 300 <= response.status_code < 400:
            raise RedirectRejectedError
        if response.status_code != 200:
            raise StatusRejectedError
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if encoding not in ("", "identity"):
            raise ContentEncodingRejectedError
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
            raise ResponseBodyTooLargeError

        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ResponseBodyTooLargeError
            body.extend(chunk)
        decoded_bytes = len(body)
        text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
        production_tdm.check_response(response, body_excerpt=text)

    root = ET.fromstring(text)
    local_tag = root.tag.rsplit("}", 1)[-1].lower() if isinstance(root.tag, str) else ""
    if local_tag == "sitemapindex":
        raise SitemapIndexRejectedError
    if local_tag != "urlset":
        raise ET.ParseError
    raw_urls = production_sitemap._extract_urls(root)
    truncated = len(raw_urls) > job.max_urls
    if truncated:
        raw_urls = sorted(raw_urls)[: job.max_urls]
    before_filter = set(raw_urls)
    result = production_monitor.MonitorResult(urls=before_filter)
    result = production_monitor._apply_url_filter(result, _monitor_config(job))
    if not result.urls:
        raise EmptyResultError
    return result.urls, result.filtered_count, truncated, 1, decoded_bytes


async def _run_round(
    jobs: tuple[Job, ...],
    *,
    round_number: int,
    concurrency: int,
    client,
    meter: RoundMeter,
    production_monitor,
    production_sitemap,
    production_tdm,
    connection_probe,
) -> dict[str, Any]:
    meter.reset(jobs)
    semaphore = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    user_before, system_before, _ = _resource_snapshot()
    timings: dict[str, tuple[int, int]] = {}

    async def run_job(job: Job) -> dict[str, Any]:
        queued_at = time.perf_counter()
        token = _current_job.set(job.id)
        try:
            async with semaphore:
                service_started = time.perf_counter()
                meter.in_flight += 1
                meter.max_in_flight = max(meter.max_in_flight, meter.in_flight)
                try:
                    async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
                        (
                            urls,
                            filtered,
                            truncated,
                            request_count,
                            decoded_bytes,
                        ) = await _fetch_and_extract(
                            job,
                            client=client,
                            production_monitor=production_monitor,
                            production_sitemap=production_sitemap,
                            production_tdm=production_tdm,
                            connection_probe=connection_probe,
                        )
                    return {
                        "id": job.id,
                        "status": "succeeded",
                        "canonical_url_count": len(urls),
                        "canonical_url_sha256": canonical_url_sha256(urls),
                        "canonical_url_hash_algorithm": HASH_ALGORITHM,
                        "filtered_count": filtered,
                        "truncated": truncated,
                        "request_count": request_count,
                        "decoded_bytes": decoded_bytes,
                        "status_body_bytes": 0,
                    }
                except Exception as exc:  # noqa: BLE001 - sanitized per-job failure boundary
                    return {
                        "id": job.id,
                        "status": "failed",
                        "error_kind": _safe_error_kind(exc),
                        "canonical_url_count": 0,
                        "filtered_count": 0,
                        "truncated": False,
                        "request_count": meter.attempts[job.id].requests,
                        "decoded_bytes": 0,
                        "status_body_bytes": 0,
                    }
                finally:
                    meter.in_flight -= 1
                    finished = time.perf_counter()
                    timings[job.id] = (
                        round((service_started - queued_at) * 1000),
                        round((finished - service_started) * 1000),
                    )
                    connection_probe.sample()
        finally:
            _current_job.reset(token)

    # The second process/pool-warm round uses a deterministic counter-order.
    # Connection reuse is not inferred from ordering.
    ordered = jobs if round_number == 1 else tuple(reversed(jobs))
    tasks = [asyncio.create_task(run_job(job)) for job in ordered]
    round_timed_out = False
    try:
        async with asyncio.timeout(ROUND_TIMEOUT_SECONDS):
            job_reports = await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except TimeoutError:
        round_timed_out = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        job_reports = [
            {
                "id": job.id,
                "status": "failed",
                "error_kind": "round_deadline",
                "canonical_url_count": 0,
                "filtered_count": 0,
                "truncated": False,
                "request_count": meter.attempts[job.id].requests,
                "decoded_bytes": 0,
                "status_body_bytes": 0,
            }
            for job in jobs
        ]
    job_reports.sort(key=lambda item: item["id"])
    for report in job_reports:
        queue_ms, service_ms = timings.get(report["id"], (0, 0))
        attempt = meter.attempts[report["id"]]
        report.update(
            wire_attempt_count=attempt.wire_attempts,
            queue_duration_ms=queue_ms,
            service_duration_ms=service_ms,
        )
        if report["status"] == "succeeded" and (
            report["request_count"] != 1 or attempt.wire_attempts != 1
        ):
            report.update(status="failed", error_kind="request_invariant")

    user_after, system_after, max_rss = _resource_snapshot()
    current_rss = _current_rss_bytes()
    open_fds = _open_fds()
    succeeded = not round_timed_out and all(
        report["status"] == "succeeded" for report in job_reports
    )
    resource_metrics_valid = (
        user_after >= user_before
        and system_after >= system_before
        and max_rss > 0
        and current_rss > 0
        and 0 <= open_fds <= MAX_FILE_DESCRIPTORS
    )
    concurrency_observed = meter.max_in_flight == concurrency
    succeeded = succeeded and resource_metrics_valid and concurrency_observed
    output: dict[str, Any] = {
        "round": round_number,
        "connection_state": "cold" if round_number == 1 else "pool_warm",
        "status": "succeeded" if succeeded else "failed",
        "run_duration_ms": round((time.perf_counter() - started) * 1000),
        "cpu_user_ms": round((user_after - user_before) * 1000),
        "cpu_system_ms": round((system_after - system_before) * 1000),
        "process_max_rss_bytes": max_rss,
        "process_current_rss_bytes": current_rss,
        "open_fds": open_fds,
        "max_in_flight": meter.max_in_flight,
        "request_count": sum(stats.requests for stats in meter.attempts.values()),
        "wire_attempt_count": sum(stats.wire_attempts for stats in meter.attempts.values()),
        "decoded_bytes": sum(report["decoded_bytes"] for report in job_reports),
        "status_body_bytes": sum(report["status_body_bytes"] for report in job_reports),
        "jobs": job_reports,
    }
    if not succeeded:
        output["error_kind"] = (
            "round_deadline"
            if round_timed_out
            else "resource_metrics_invalid"
            if not resource_metrics_valid
            else "concurrency_not_observed"
            if not concurrency_observed
            else "job_failed"
        )
    return output


def _final_invariants_valid(final: dict[str, Any], concurrency: int) -> bool:
    return not (
        final["completed"] != final["accepted"]
        or final["in_flight"] != 0
        or final["connections_open"] is None
        or final["connections_open"] != 0
        or final["connection_waiters"] is None
        or final["connection_waiters"] != 0
        or final["maximum_connections"] is None
        or final["maximum_connections"] > MAX_CONNECTIONS
        or final["max_in_flight"] != concurrency
    )


async def run(
    manifest: Manifest, manifest_sha256: str, profile: str, concurrency: int
) -> dict[str, Any]:
    total_started = time.perf_counter()
    startup_started = time.perf_counter()
    startup_user_after, startup_system_after, _ = _resource_snapshot()
    process_timed_out = False
    startup_complete = False
    startup_duration_ms = 0
    startup_rss_bytes = -1
    startup_open_fds = -1
    meter = RoundMeter()
    connection_probe: ConnectionProbe | None = None
    client = None
    rounds: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(PROCESS_TIMEOUT_SECONDS):
            (
                httpx,
                certifi,
                production_monitor,
                production_sitemap,
                production_ssrf,
                production_tdm,
            ) = _load_runtime()

            context = ssl.create_default_context(cafile=certifi.where())
            context.options |= ssl.OP_NO_TICKET
            context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
            inner = httpx.AsyncHTTPTransport(
                verify=context,
                http1=True,
                http2=False,
                limits=httpx.Limits(
                    max_connections=MAX_CONNECTIONS,
                    max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
                    keepalive_expiry=30.0,
                ),
                retries=0,
            )
            ssrf = production_ssrf.SSRFGuardedTransport(inner)
            transport = ExactTargetTransport.make(httpx, ssrf, meter)
            connection_probe = ConnectionProbe(inner)
            connection_probe.sample()
            startup_duration_ms = round((time.perf_counter() - startup_started) * 1000)
            startup_user_after, startup_system_after, _ = _resource_snapshot()
            startup_rss_bytes = _current_rss_bytes()
            startup_open_fds = _open_fds()
            startup_complete = True

            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
                follow_redirects=False,
                trust_env=False,
                cookies=http.cookiejar.CookieJar(policy=_RejectAllCookiePolicy()),
            ) as client:
                for round_number in range(1, ROUNDS + 1):
                    round_report = await _run_round(
                        manifest.jobs,
                        round_number=round_number,
                        concurrency=concurrency,
                        client=client,
                        meter=meter,
                        production_monitor=production_monitor,
                        production_sitemap=production_sitemap,
                        production_tdm=production_tdm,
                        connection_probe=connection_probe,
                    )
                    rounds.append(round_report)
                    if round_report["status"] != "succeeded":
                        break
    except TimeoutError:
        process_timed_out = True
    finally:
        if client is not None and not client.is_closed:
            await client.aclose()
    if not startup_complete:
        startup_duration_ms = round((time.perf_counter() - startup_started) * 1000)
        startup_user_after, startup_system_after, _ = _resource_snapshot()
        startup_rss_bytes = _current_rss_bytes()
        startup_open_fds = _open_fds()
    connections_open = connection_probe.sample() if connection_probe is not None else None
    total_user_after, total_system_after, max_rss = _resource_snapshot()
    final_current_rss = _current_rss_bytes()
    final_open_fds = _open_fds()
    cgroup_memory_peak = _cgroup_value("memory.peak")
    cgroup_memory_current = _cgroup_value("memory.current")
    cgroup_oom_events = _cgroup_event("oom")
    succeeded = (
        not process_timed_out
        and len(rounds) == ROUNDS
        and all(item["status"] == "succeeded" for item in rounds)
    )
    final = {
        "accepted": sum(len(item["jobs"]) for item in rounds),
        "completed": sum(len(item["jobs"]) for item in rounds),
        "failed": sum(1 for item in rounds for job in item["jobs"] if job["status"] != "succeeded"),
        "queued": 0,
        "in_flight": meter.in_flight,
        "max_in_flight": max((item["max_in_flight"] for item in rounds), default=0),
        "connections_open": connections_open,
        "connection_limit": MAX_CONNECTIONS,
        "per_origin_limit": 1,
        "connection_waiters": (
            connection_probe.current_waiters if connection_probe is not None else None
        ),
        "maximum_connections": (
            connection_probe.maximum_connections if connection_probe is not None else None
        ),
        "process_current_rss_bytes": final_current_rss,
        "open_fds": final_open_fds,
    }
    resource_metrics_valid = (
        startup_user_after >= 0
        and startup_system_after >= 0
        and startup_rss_bytes > 0
        and 0 <= startup_open_fds <= MAX_FILE_DESCRIPTORS
        and total_user_after >= 0
        and total_system_after >= 0
        and max_rss > 0
        and final_current_rss > 0
        and 0 <= final_open_fds <= MAX_FILE_DESCRIPTORS
        and final_open_fds <= startup_open_fds
        and cgroup_memory_peak is not None
        and cgroup_memory_current is not None
        and cgroup_oom_events == 0
    )
    final_invariants_valid = _final_invariants_valid(final, concurrency)
    if not resource_metrics_valid or not final_invariants_valid:
        succeeded = False
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "implementation": "python",
        "runtime_version": sys.version.split()[0],
        "profile": profile,
        "concurrency": concurrency,
        "source_commit": SOURCE_COMMIT,
        "image_identity": IMAGE_IDENTITY,
        "manifest_sha256": manifest_sha256,
        "status": "succeeded" if succeeded else "failed",
        "startup_duration_ms": startup_duration_ms,
        "startup_cpu_user_ms": round(startup_user_after * 1000),
        "startup_cpu_system_ms": round(startup_system_after * 1000),
        "startup_rss_bytes": startup_rss_bytes,
        "startup_open_fds": startup_open_fds,
        "run_duration_ms": round((time.perf_counter() - total_started) * 1000),
        "cpu_user_ms": round(total_user_after * 1000),
        "cpu_system_ms": round(total_system_after * 1000),
        "process_max_rss_bytes": max_rss,
        "cgroup_memory_peak_bytes": cgroup_memory_peak,
        "cgroup_memory_current_bytes": cgroup_memory_current,
        "cgroup_oom_events": cgroup_oom_events,
        "configured": {
            "http_version": "1.1",
            "accept_encoding": "identity",
            "job_timeout_ms": JOB_TIMEOUT_SECONDS * 1000,
            "max_connections": MAX_CONNECTIONS,
            "max_keepalive_connections": MAX_KEEPALIVE_CONNECTIONS,
            "max_aggregate_bytes": MAX_AGGREGATE_BYTES,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "max_requests_per_job": 1,
            "per_origin_concurrency": 1,
            "request_timeout_ms": REQUEST_TIMEOUT_SECONDS * 1000,
        },
        "rounds": rounds,
        "final": final,
    }
    if not succeeded:
        report["error_kind"] = (
            "process_deadline"
            if process_timed_out
            else "resource_metrics_invalid"
            if not resource_metrics_valid
            else "final_invariant"
            if not final_invariants_valid
            else "round_failed"
        )
    return report


def failure_report(error_kind: str, started: float) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "implementation": "python",
        "source_commit": SOURCE_COMMIT,
        "image_identity": IMAGE_IDENTITY,
        "status": "failed",
        "error_kind": error_kind,
        "run_duration_ms": round((time.perf_counter() - started) * 1000),
        "rounds": [],
    }


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    try:
        profile, concurrency = parse_profile(list(sys.argv[1:] if argv is None else argv))
        if not valid_source_identity():
            report = failure_report("source_identity_invalid", started)
        else:
            manifest, manifest_sha256 = load_fixed_manifest()
            report = asyncio.run(run(manifest, manifest_sha256, profile, concurrency))
    except ManifestError as exc:
        kind = "arguments_rejected" if str(exc) == "arguments rejected" else "manifest_invalid"
        report = failure_report(kind, started)
    except Exception:  # noqa: BLE001 - emit one sanitized terminal report
        report = failure_report("internal", started)

    sys.stdout.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
