"""Exclusive Go Pinpoint rich monitor for direct tenant boards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from time import monotonic
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitor import MonitorResult, postprocess_monitor_stream
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
    "identity_migration",
    "rescrape_policy",
    "min_jobs",
    "blast_radius_floor",
}
_IGNORED = {"token", "company", "company_slug"}
_DOWNSTREAM = {"url_filter", "url_allowlist", "url_transform", "url", "job_filter"}
_MAX_OUTPUT_BYTES = 320_000_000
_CHILD_STOP_SECONDS = 5
log = structlog.get_logger()


def _eligible(board_url: str, monitor_type: str, config: dict | None, pw: object | None) -> str:
    metadata = config or {}
    if (
        monitor_type != "pinpoint"
        or pw is not None
        or set(metadata) - {"slug"} - _IGNORED - _BOOKKEEPING - _DOWNSTREAM
    ):
        raise ValueError("Go Pinpoint requires a supported direct configuration")
    from src.core.monitors.pinpoint import _slug_from_url

    tenant = metadata.get("slug") or _slug_from_url(board_url)
    if not isinstance(tenant, str) or _TENANT.fullmatch(tenant) is None:
        raise ValueError("Go Pinpoint requires a canonical configured slug")
    return tenant


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("PINPOINT_GO_PERCENT", "100")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw):
        return False
    percent = int(raw)
    if percent == 0:
        return False
    try:
        _eligible(board_url, "pinpoint", config, None)
    except ValueError:
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
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert proc.stdout is not None
            chunks = []
            size = 0
            while chunk := await proc.stdout.read(min(1 << 20, _MAX_OUTPUT_BYTES + 1 - size)):
                size += len(chunk)
                if size > _MAX_OUTPUT_BYTES:
                    raise ValueError("Go Pinpoint output exceeded its response bound")
                chunks.append(chunk)
            await proc.wait()
            payload = json.loads(b"".join(chunks))
            del chunks
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Pinpoint response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            body_bytes = payload.get("bytes")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 21
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or attempts - responses > 1
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= (64 << 20) + 1
                or (responses == 0 and body_bytes != 0)
            ):
                raise ValueError("invalid Go Pinpoint request accounting")
            attribution = current_egress_attribution()
            for attempt in range(attempts):
                record_origin_attempt(attribution, "direct")
                record_origin_outcome(
                    attribution, "direct", "response" if attempt < responses else "transport_error"
                )
            record_response_body_bytes(attribution, "direct", body_bytes)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Pinpoint HTTP status")
            final = urlparse(final_url) if isinstance(final_url, str) else None
            if responses and (
                final is None
                or final.scheme != "https"
                or not final.hostname
                or final.username is not None
                or final.password is not None
                or final.port not in (None, 443)
                or final.fragment
                or (responses == 1 and final_url != api_url)
                or status == 0
            ):
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
                detail = payload.get("error") or "native process failed"
                if responses == attempts and status != 200:
                    request = httpx.Request("GET", api_url)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Pinpoint inventory failed: {detail}")
            if status != 200 or responses != attempts:
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
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(jobs))

            async def raw_batches() -> AsyncIterator[list[DiscoveredJob] | MonitorResult]:
                if truncated:
                    yield MonitorResult(
                        urls={job.url for job in jobs},
                        jobs_by_url={job.url: job for job in jobs},
                        truncated=True,
                    )
                else:
                    for offset in range(0, len(jobs), 200):
                        yield jobs[offset : offset + 200]

            canonical_urls: set[str] = set()
            async for result in postprocess_monitor_stream(raw_batches(), monitor_config or {}):
                canonical_urls.update(result.urls)
                yield result
            log.info(
                "go_pinpoint.monitor_postprocessed",
                board_id=self.board_id,
                urls=len(canonical_urls),
                url_sha256=hashlib.sha256("\n".join(sorted(canonical_urls)).encode()).hexdigest(),
                truncated=truncated,
            )
            outcome = "success"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if proc is not None and proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), _CHILD_STOP_SECONDS)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        proc.kill()
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
