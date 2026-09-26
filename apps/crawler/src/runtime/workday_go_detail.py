"""Exclusive, default-off Go Workday detail fetch for explicitly selected boards."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

import httpx
import structlog

from src.core.job_content import JobContent
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
from src.shared.http import (
    mark_external_response,
    mark_reachable_response,
    mark_transient_response_failure,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()


class GoWorkdayDetailRuntime:
    implementation = "go-workday-detail"

    def __init__(self, binary: str = "/usr/local/bin/workday-detail-live") -> None:
        self.binary = binary

    async def scrape(
        self,
        url: str,
        scraper_type: str,
        scraper_config: dict | None,
        http: httpx.AsyncClient,
        *,
        pw: object | None = None,
        artifact_dir: Path | None = None,
    ) -> JobContent:
        del http, pw, artifact_dir  # Go owns the detail request and its transport.
        if scraper_type != "workday":
            raise ValueError("Go Workday detail requires the Workday scraper")
        config = scraper_config or {}
        aliases = config.get("facility_tenant_aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not alias.strip() for alias in aliases
        ):
            raise ValueError("Go Workday detail requires valid facility tenant aliases")

        started = monotonic()
        outcome = "error"
        proc = None
        try:
            args = [self.binary, "--url", url]
            for alias in aliases:
                args.extend(("--facility-tenant-alias", alias.strip()))
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 1_000_000:
                raise ValueError("Go Workday detail output exceeded the bounded contract")
            payload = json.loads(stdout)
            attempts = payload.get("requests")
            responses = payload.get("responses")
            transport_errors = payload.get("transport_errors")
            byte_count = payload.get("bytes")
            if (
                not all(
                    type(value) is int and value >= 0
                    for value in (attempts, responses, transport_errors, byte_count)
                )
                or attempts != responses + transport_errors
            ):
                raise ValueError("invalid Go Workday detail request accounting")

            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(transport_errors):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", byte_count)

            api_url = url
            status = payload.get("status", 0)
            if type(status) is not int:
                raise ValueError("invalid Go Workday detail status")
            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        api_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                if status:
                    mark_external_response(api_url, status)
                if payload.get("error_kind") == "invalid_payload":
                    mark_transient_response_failure(
                        api_url, reason="workday_invalid_detail_payload"
                    )
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                raise RuntimeError(f"Go Workday detail failed: {detail}")

            if status not in (200, 403, 404):
                raise ValueError("Go Workday detail returned unexpected success status")
            if payload.get("gone"):
                if status not in (403, 404) or payload.get("content") is not None:
                    raise ValueError("invalid Go Workday gone response")
                content = JobContent()
            else:
                if status != 200 or not isinstance(payload.get("content"), dict):
                    raise ValueError("invalid Go Workday detail content")
                content = JobContent(**payload["content"])
            log.info(
                "go_workday.detail_complete",
                host=urlparse(url).hostname,
                gone=bool(payload.get("gone")),
                status=status,
                requests=attempts,
                response_bytes=byte_count,
            )
            mark_reachable_response(api_url)
            runtime_output_items_total.labels(
                stage="scrape", implementation=self.implementation
            ).inc()
            outcome = "success"
            return content
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            if proc is not None and proc.returncode is None:
                proc.terminate()
                await proc.wait()
            from src.core.scrapers import all_scraper_types

            runtime_execution_duration_seconds.labels(
                stage="scrape", implementation=self.implementation
            ).observe(monotonic() - started)
            runtime_executions_total.labels(
                stage="scrape", implementation=self.implementation, outcome=outcome
            ).inc()
            record_runtime_capability(
                stage="scrape",
                implementation=self.implementation,
                capability=scraper_type,
                allowed_capabilities=all_scraper_types(),
                outcome=outcome,
            )
