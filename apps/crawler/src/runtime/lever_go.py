"""Default-off exclusive Go rich monitor for explicit Lever board tokens."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from time import monotonic
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitor import MonitorResult, _normalize_discovered
from src.core.monitors import BoardGoneError, DiscoveredJob, all_monitor_types
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

_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_BOOKKEEPING = {
    "scraper_type",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()


class GoLeverMonitorRuntime:
    implementation = "go-lever"

    def __init__(self, binary: str = "/usr/local/bin/lever-monitor-live", *, board_id: str) -> None:
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
        config = monitor_config or {}
        token = config.get("token")
        region = config.get("region") or ""
        parsed_board = urlparse(board_url)
        if (
            monitor_type != "lever"
            or parsed_board.scheme != "https"
            or parsed_board.hostname not in {"jobs.lever.co", "jobs.eu.lever.co"}
            or pw is not None
            or not isinstance(token, str)
            or _TOKEN.fullmatch(token) is None
            or region not in {"", "eu"}
            or (parsed_board.hostname == "jobs.eu.lever.co") != (region == "eu")
            or config.get("scraper_type") != "skip"
            or set(config) - {"token", "region"} - _BOOKKEEPING
        ):
            raise ValueError("Go Lever requires an explicit unchanged rich token configuration")
        api_host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        api_base = f"https://{api_host}/v0/postings/{token}?limit=100&skip="
        api_url = f"{api_base}0"
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--token",
                token,
                "--region",
                region,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 80_000_000:
                raise ValueError("Go Lever output exceeded the selected origin bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Lever response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            byte_count = payload.get("bytes")
            last_skip = payload.get("last_skip")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 1503
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(byte_count) is not int
                or byte_count < 0
                or (responses == 0 and byte_count != 0)
                or type(last_skip) is not int
                or last_skip < 0
                or last_skip > 50_000
                or last_skip % 100 != 0
            ):
                raise ValueError("invalid Go Lever request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(attempts - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", byte_count)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Lever status")
            if responses and (
                not isinstance(final_url, str)
                or not final_url.startswith(api_base)
                or final_url[len(api_base) :] not in {str(skip) for skip in range(0, 50_001, 100)}
                or status == 0
            ):
                raise ValueError("Go Lever returned an unexpected response endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go Lever returned an inconsistent transport outcome")
            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        api_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                if status == 404 and last_skip == 0:
                    raise BoardGoneError(
                        f"Lever board token {token!r} returned 404",
                        url=api_url,
                        status_code=404,
                    )
                if responses:
                    mark_external_response(final_url, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                if status and status != 200:
                    request = httpx.Request("GET", api_url)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Lever list failed: {detail}")
            if status != 200 or responses < 1 or final_url != f"{api_base}{last_skip}":
                raise ValueError("Go Lever success had no HTTP 200 response")
            raw_jobs = payload.get("jobs")
            truncated = payload.get("truncated")
            if not isinstance(raw_jobs, list) or not isinstance(truncated, bool):
                raise ValueError("invalid Go Lever inventory")
            if truncated and len(raw_jobs) < 50_000:
                raise ValueError("invalid Go Lever truncation marker")
            jobs = []
            for index, raw in enumerate(raw_jobs):
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("url"), str)
                    or not raw["url"]
                ):
                    raise ValueError(f"invalid Go Lever job at index {index}")
                jobs.append(DiscoveredJob(**raw))
            digest = hashlib.sha256("\n".join(sorted(job.url for job in jobs)).encode()).hexdigest()
            log.info(
                "go_lever.monitor_complete",
                board_id=self.board_id,
                urls=len(jobs),
                url_sha256=digest,
                requests=attempts,
                responses=responses,
                response_bytes=byte_count,
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
