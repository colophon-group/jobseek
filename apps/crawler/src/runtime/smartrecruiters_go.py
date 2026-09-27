"""Go owner for direct SmartRecruiters publication and localized monitors."""

from __future__ import annotations

import asyncio
import dataclasses
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

from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob, all_monitor_types
from src.core.monitors.smartrecruiters import (
    _canonical_identity_mode,
    _canonical_template,
    _language_preference,
    _token_from_url,
)
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
from src.shared.http_retry import PaginationFetchError, ResponseBodyTooLargeError
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_OPTIONS = {
    "token",
    "company",
    "company_identifier",
    "canonical_identity",
    "canonical_job_id_url_template",
    "language_preference",
}
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
_MAX_OUTPUT_BYTES = 256 << 20


def eligible(board_url: str, monitor_type: str, config: dict | None, pw=None) -> str:
    meta = config or {}
    parsed = urlparse(board_url)
    token = meta.get("token") or _token_from_url(board_url)
    if (
        monitor_type != "smartrecruiters"
        or pw is not None
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not isinstance(token, str)
        or _TOKEN.fullmatch(token) is None
        or set(meta) - _OPTIONS - _BOOKKEEPING
    ):
        raise ValueError("Go SmartRecruiters requires a direct unchanged provider configuration")
    identity = _canonical_identity_mode(meta)
    template = _canonical_template(meta)
    if identity and template is not None:
        raise ValueError("conflicting SmartRecruiters identity modes")
    if identity == "job-v1" or template is not None:
        _language_preference(meta)
    return token


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("SMARTRECRUITERS_GO_PERCENT", "100")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) or int(raw) == 0:
        return False
    try:
        eligible(board_url, "smartrecruiters", config)
    except ValueError:
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < int(raw) * 100


async def run_go(binary: str, request: dict, *, limit: int = _MAX_OUTPUT_BYTES) -> tuple[dict, int]:
    """Bound the child output while reading, and reap on every exit path."""
    body = json.dumps(request).encode()
    if len(body) > 65_536:
        raise ValueError("SmartRecruiters configuration exceeds bound")
    proc = await asyncio.create_subprocess_exec(
        binary,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(body)
        await proc.stdin.drain()
        proc.stdin.close()
        chunks = []
        size = 0
        while chunk := await proc.stdout.read(min(1 << 20, limit + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise ValueError("Go SmartRecruiters output exceeds bound")
        returncode = await proc.wait()
        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict):
            raise ValueError("invalid Go SmartRecruiters envelope")
        return payload, returncode
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()
                await proc.wait()


class GoSmartRecruitersMonitorRuntime:
    implementation = "go-smartrecruiters"

    def __init__(
        self, binary: str = "/usr/local/bin/smartrecruiters-monitor-live", *, board_id: str
    ):
        self.binary = binary
        self.board_id = board_id

    async def stream(
        self,
        board_url: str,
        monitor_type: str,
        monitor_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw=None,
    ) -> AsyncIterator[MonitorResult]:
        del http
        token = eligible(board_url, monitor_type, monitor_config, pw)
        config = monitor_config or {}
        api_url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
        rich = bool(config.get("canonical_identity") or config.get("canonical_job_id_url_template"))
        started = monotonic()
        outcome = "error"
        try:
            payload, returncode = await run_go(
                self.binary,
                {
                    "board_url": board_url,
                    "metadata": {k: v for k, v in config.items() if k in _OPTIONS},
                },
            )
            if not isinstance(payload, dict):
                raise ValueError("invalid Go SmartRecruiters envelope")
            attempts, responses, body_bytes = (
                payload.get(k) for k in ("requests", "responses", "bytes")
            )
            if (
                type(attempts) is not int
                or not 0 <= attempts <= 610_000
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= (540 << 20)
                or (responses == 0 and body_bytes != 0)
            ):
                raise ValueError("invalid Go SmartRecruiters request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(attempts - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", body_bytes)
            if returncode or payload.get("error"):
                failure = payload.get("failure") or {}
                if not isinstance(failure, dict):
                    raise ValueError("invalid Go SmartRecruiters failure")
                endpoint = failure.get("url", api_url)
                parsed = urlparse(endpoint)
                if (
                    parsed.scheme != "https"
                    or parsed.netloc != "api.smartrecruiters.com"
                    or not (
                        parsed.path == f"/v1/companies/{token}/postings"
                        or parsed.path.startswith(f"/v1/companies/{token}/postings/")
                    )
                ):
                    raise ValueError("unexpected Go SmartRecruiters failure endpoint")
                kind = failure.get("kind")
                if kind == "tdm":
                    if failure.get("source") not in {"header", "meta"}:
                        raise ValueError("invalid publisher policy source")
                    raise TDMReservedError(
                        endpoint, source=failure["source"], policy_url=failure.get("policy")
                    )
                if kind in {"body_limit", "run_body_limit"}:
                    raise ResponseBodyTooLargeError(endpoint, failure["max_bytes"])
                if kind == "pagination":
                    status = failure.get("status")
                    if status:
                        mark_external_response(endpoint, status)
                    raise PaginationFetchError(
                        endpoint,
                        failure["attempts"],
                        last_status=status,
                        last_error=None if status else "GoTransportOrJSONError",
                        last_location=failure.get("location"),
                    )
                raise ValueError("Go SmartRecruiters inventory failed")
            urls, raw_jobs, truncated, total = (
                payload.get(k) for k in ("urls", "jobs", "truncated", "total_found")
            )
            if (
                not isinstance(urls, list)
                or not all(isinstance(u, str) and u for u in urls)
                or len(set(urls)) != len(urls)
                or type(truncated) is not bool
                or type(total) is not int
                or total < 0
                or responses == 0
            ):
                raise ValueError("invalid Go SmartRecruiters inventory")
            jobs = []
            if rich:
                if not isinstance(raw_jobs, list):
                    raise ValueError("missing rich inventory")
                jobs = [DiscoveredJob(**item) for item in raw_jobs]
                if len(jobs) != len(urls) or {j.url for j in jobs} != set(urls):
                    raise ValueError("rich inventory URL mismatch")
                publications = []
                for job in jobs:
                    ids = (job.metadata or {}).get("smartrecruiters_publication_ids")
                    if (
                        not isinstance(ids, list)
                        or not ids
                        or not all(isinstance(i, str) and i for i in ids)
                    ):
                        raise ValueError("missing publication membership")
                    publications.extend(ids)
                if (
                    len(set(publications)) != len(publications)
                    or (not truncated and len(publications) != total)
                    or (truncated and not 50_000 <= len(publications) < total)
                ):
                    raise ValueError("rich inventory publication boundary mismatch")
            elif (
                raw_jobs is not None
                or (not truncated and len(urls) != total)
                or (truncated and not 50_000 <= len(urls) < total)
            ):
                raise ValueError("invalid publication inventory boundary")
            url_hash = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
            fields = hashlib.sha256()
            for job in sorted(jobs, key=lambda j: j.url):
                fields.update(
                    json.dumps(
                        dataclasses.asdict(job),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                    + b"\n"
                )
            log.info(
                "go_smartrecruiters.monitor_complete",
                board_id=self.board_id,
                urls=len(urls),
                url_sha256=url_hash,
                fields_sha256=fields.hexdigest() if rich else None,
                truncated=truncated,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(api_url)
            outcome = "success"
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(urls))
            yield MonitorResult(
                urls=set(urls), jobs_by_url={j.url: j for j in jobs}, truncated=truncated
            )
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
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
