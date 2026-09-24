"""Default-off Go rich monitor for the exact Elastic Greenhouse board."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from time import monotonic

import httpx
import structlog

from src.core.monitor import MonitorResult, _normalize_discovered
from src.core.monitors import BoardGoneError, DiscoveredJob
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
)
from src.shared.http import mark_external_response, mark_reachable_response
from src.shared.tdm import TDMReservedError

ELASTIC_BOARD_ID = "0b0b0ae8-3635-47b3-929d-79e439879598"
_BOARD_URL = "https://job-boards.greenhouse.io/elastic"
_API_URL = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
_BOOKKEEPING = {
    "scraper_type",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()


class GoGreenhouseMonitorRuntime:
    implementation = "go-greenhouse"

    def __init__(self, binary: str = "/usr/local/bin/greenhouse-monitor-live") -> None:
        self.binary = binary

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
        if (
            monitor_type != "greenhouse"
            or board_url != _BOARD_URL
            or pw is not None
            or config.get("token") != "elastic"
            or config.get("scraper_type") != "skip"
            or set(config) - {"token"} - _BOOKKEEPING
        ):
            raise ValueError("Go Greenhouse pilot requires the unchanged Elastic configuration")

        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 80_000_000:
                raise ValueError("Go Greenhouse output exceeded the selected origin bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Greenhouse response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            byte_count = payload.get("bytes")
            if (
                type(attempts) is not int
                or attempts != 1
                or type(responses) is not int
                or responses not in (0, 1)
                or not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count < 0
                or (responses == 0 and byte_count != 0)
            ):
                raise ValueError("invalid Go Greenhouse request accounting")
            attribution = current_egress_attribution()
            record_origin_attempt(attribution, "direct")
            record_origin_outcome(
                attribution, "direct", "response" if responses else "transport_error"
            )
            record_response_body_bytes(attribution, "direct", byte_count)

            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Greenhouse status")
            if responses and (final_url != _API_URL or status == 0):
                raise ValueError("Go Greenhouse returned an unexpected response endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go Greenhouse returned an inconsistent transport outcome")
            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        _API_URL, source="header", policy_url=payload.get("tdm_policy")
                    )
                if status == 404:
                    raise BoardGoneError(
                        "Greenhouse board token 'elastic' returned 404",
                        url=_API_URL,
                        status_code=404,
                    )
                if responses:
                    mark_external_response(_API_URL, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                if status != 0 and status != 200:
                    request = httpx.Request("GET", _API_URL)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Greenhouse list failed: {detail}")

            if status != 200 or responses != 1:
                raise ValueError("Go Greenhouse success had no HTTP 200 response")
            raw_jobs = payload.get("jobs")
            truncated = payload.get("truncated")
            if not isinstance(raw_jobs, list) or not isinstance(truncated, bool):
                raise ValueError("invalid Go Greenhouse inventory")
            if truncated != (len(raw_jobs) > 50_000):
                raise ValueError("invalid Go Greenhouse truncation marker")
            jobs = []
            for index, raw in enumerate(raw_jobs):
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("url"), str)
                    or not raw["url"]
                ):
                    raise ValueError(f"invalid Go Greenhouse job at index {index}")
                jobs.append(
                    DiscoveredJob(
                        url=raw["url"],
                        title=raw.get("title"),
                        description=raw.get("description"),
                        locations=raw.get("locations"),
                        date_posted=raw.get("date_posted"),
                        language=raw.get("language"),
                        metadata=raw.get("metadata"),
                    )
                )
            fields_digest = hashlib.sha256()
            for job in sorted(jobs, key=lambda item: item.url):
                fields_digest.update(
                    json.dumps(
                        {
                            "url": job.url,
                            "title": job.title,
                            "description": job.description,
                            "locations": job.locations,
                            "date_posted": job.date_posted,
                            "language": job.language,
                            "metadata": job.metadata,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                )
                fields_digest.update(b"\n")
            log.info(
                "go_greenhouse.monitor_complete",
                board_id=ELASTIC_BOARD_ID,
                urls=len(jobs),
                url_sha256=hashlib.sha256(
                    "\n".join(sorted(job.url for job in jobs)).encode()
                ).hexdigest(),
                fields_sha256=fields_digest.hexdigest(),
                requests=attempts,
                responses=responses,
                response_bytes=byte_count,
            )
            mark_reachable_response(_API_URL)
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
