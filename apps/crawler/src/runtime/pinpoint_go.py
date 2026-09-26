"""Default-off exclusive Go Pinpoint rich monitor for direct tenant boards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator
from time import monotonic
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitor import MonitorResult, _normalize_discovered
from src.core.monitors import DiscoveredJob, all_monitor_types
from src.metrics import (
    runtime_execution_duration_seconds,
    runtime_executions_total,
    runtime_output_items_total,
)
from src.shared.egress import (
    current_egress_attribution,
    record_origin_attempt,
    record_origin_outcome,
    record_response_body_bytes,
    record_runtime_capability,
)
from src.shared.http import mark_external_response, mark_reachable_response
from src.shared.tdm import TDMReservedError

_TENANT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
    "jobs",
}
_IGNORED = {"token", "company", "company_slug"}
log = structlog.get_logger()


def _eligible(board_url: str, monitor_type: str, config: dict | None, pw: object | None) -> str:
    metadata = config or {}
    parsed = urlparse(board_url)
    hostname = parsed.hostname or ""
    if not hostname.endswith(".pinpointhq.com"):
        raise ValueError("Go Pinpoint requires a direct hosted tenant")
    tenant = hostname.removesuffix(".pinpointhq.com")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Go Pinpoint requires a canonical hosted URL") from exc
    if (
        monitor_type != "pinpoint"
        or pw is not None
        or _TENANT.fullmatch(tenant) is None
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or metadata.get("slug", tenant) != tenant
        or metadata.get("scraper_type") != "skip"
        or set(metadata) - {"slug"} - _IGNORED - _BOOKKEEPING
    ):
        raise ValueError("Go Pinpoint requires an unchanged hosted rich configuration")
    return tenant


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("PINPOINT_GO_PERCENT", "0")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw):
        return False
    percent = int(raw)
    if percent == 0:
        return False
    try:
        _eligible(board_url, "pinpoint", config, None)
    except ValueError:
        return False
    recent = (config or {}).get("recent_discovered_counts")
    if (
        not isinstance(recent, list)
        or len(recent) < 3
        or not all(type(count) is int and 1 <= count <= 500 for count in recent[-3:])
    ):
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < percent * 100


class GoPinpointMonitorRuntime:
    implementation = "go-pinpoint"

    def __init__(self, binary: str = "/usr/local/bin/pinpoint-monitor-live", *, board_id: str):
        self.binary = binary
        self.board_id = board_id

    async def stream(
        self,
        board_url: str,
        monitor_type: str,
        monitor_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
    ) -> AsyncIterator[MonitorResult]:
        del http
        tenant = _eligible(board_url, monitor_type, monitor_config, pw)
        api_url = f"https://{tenant}.pinpointhq.com/postings.json"
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--tenant",
                tenant,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 80_000_000:
                raise ValueError("Go Pinpoint output exceeded its selected origin bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Pinpoint response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            body_bytes = payload.get("bytes")
            if (
                attempts != 1
                or type(attempts) is not int
                or type(responses) is not int
                or responses not in (0, 1)
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= (16 << 20) + 1
                or (responses == 0 and body_bytes != 0)
            ):
                raise ValueError("invalid Go Pinpoint request accounting")
            attribution = current_egress_attribution()
            record_origin_attempt(attribution, "direct")
            record_origin_outcome(
                attribution, "direct", "response" if responses else "transport_error"
            )
            record_response_body_bytes(attribution, "direct", body_bytes)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Pinpoint HTTP status")
            if responses and (final_url != api_url or status == 0):
                raise ValueError("Go Pinpoint returned an unexpected endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go Pinpoint returned an inconsistent transport outcome")
            error_kind = payload.get("error_kind")
            if error_kind not in {None, "", "tdm"}:
                raise ValueError("invalid Go Pinpoint error classification")
            if proc.returncode != 0 or payload.get("error"):
                if error_kind == "tdm":
                    raise TDMReservedError(
                        api_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                if responses:
                    mark_external_response(api_url, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                if status and status != 200:
                    request = httpx.Request("GET", api_url)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Pinpoint inventory failed: {detail}")
            if status != 200 or responses != 1:
                raise ValueError("Go Pinpoint success had no HTTP 200 response")
            raw_jobs = payload.get("jobs")
            truncated = payload.get("truncated")
            if not isinstance(raw_jobs, list) or not isinstance(truncated, bool):
                raise ValueError("invalid Go Pinpoint inventory")
            if truncated != (len(raw_jobs) > 50_000):
                raise ValueError("invalid Go Pinpoint truncation marker")
            jobs = []
            for index, raw in enumerate(raw_jobs):
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("url"), str)
                    or not raw["url"]
                ):
                    raise ValueError(f"invalid Go Pinpoint job at index {index}")
                jobs.append(DiscoveredJob(**raw))
            digest = hashlib.sha256("\n".join(sorted(job.url for job in jobs)).encode()).hexdigest()
            log.info(
                "go_pinpoint.monitor_complete",
                board_id=self.board_id,
                urls=len(jobs),
                url_sha256=digest,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(api_url)
            outcome = "success"
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(jobs))
            if truncated:
                yield MonitorResult(
                    urls={job.url for job in jobs},
                    jobs_by_url={job.url: job for job in jobs},
                    truncated=True,
                )
            else:
                for offset in range(0, len(jobs), 200):
                    yield _normalize_discovered(jobs[offset : offset + 200])
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                await proc.wait()
            runtime_execution_duration_seconds.labels(
                stage="monitor", implementation=self.implementation
            ).observe(monotonic() - started)
            runtime_executions_total.labels(
                stage="monitor", implementation=self.implementation, outcome=outcome
            ).inc()
            record_runtime_capability(
                stage="monitor",
                implementation=self.implementation,
                capability=monitor_type,
                allowed_capabilities=all_monitor_types(),
                outcome=outcome,
            )
