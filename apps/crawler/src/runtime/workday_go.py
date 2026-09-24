"""Exclusive, default-off Go list execution for the first Workday origin."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from urllib.parse import urlparse

import httpx

from src.core.monitor import MonitorResult
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
from src.shared.http import (
    WORKDAY_LIST_TRANSIENT_STATUS_INCIDENT,
    mark_external_response,
    mark_provider_incident,
    mark_reachable_response,
    mark_transient_response_failure,
)
from src.shared.tdm import TDMReservedError

ELEVANCE_BOARD_ID = "bcd90676-101c-4e58-b427-98edd4e09b7d"
_BOARD_URL = "https://elevancehealth.wd1.myworkdayjobs.com/en-US/ANT"
_LIST_URL = "https://elevancehealth.wd1.myworkdayjobs.com/wday/cxs/elevancehealth/ANT/jobs"
_JOB_PREFIX = "https://elevancehealth.wd1.myworkdayjobs.com/ANT/"
_BOOKKEEPING = {
    "scraper_type",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
}


class GoWorkdayMonitorRuntime:
    implementation = "go-workday"

    def __init__(self, binary: str = "/usr/local/bin/workday-monitor-live") -> None:
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
        del http  # Go owns every list HTTP request for this exclusive origin.
        config = monitor_config or {}
        if (
            monitor_type != "workday"
            or board_url != _BOARD_URL
            or pw is not None
            or config.get("company") != "elevancehealth"
            or config.get("wd_instance") != "wd1"
            or config.get("site") != "ANT"
            or config.get("all_sites") is not False
            or set(config) - {"company", "wd_instance", "site", "all_sites"} - _BOOKKEEPING
        ):
            raise ValueError(
                "Go Workday pilot requires the unchanged Elevance one-site configuration"
            )

        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--company",
                "elevancehealth",
                "--instance",
                "wd1",
                "--site",
                "ANT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 512_000:
                raise ValueError("Go Workday output exceeded the selected origin bound")
            payload = json.loads(stdout)
            attempts = payload.get("requests", 0)
            responses = payload.get("responses", 0)
            transport_errors = payload.get("transport_errors", 0)
            byte_count = payload.get("bytes", 0)
            if (
                not all(
                    isinstance(value, int) and value >= 0
                    for value in (attempts, responses, transport_errors, byte_count)
                )
                or attempts != responses + transport_errors
            ):
                raise ValueError("invalid Go Workday request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(transport_errors):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", byte_count)

            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        _LIST_URL, source="header", policy_url=payload.get("tdm_policy")
                    )
                status = payload.get("status", 0)
                if isinstance(status, int) and status > 0:
                    mark_external_response(_LIST_URL, status)
                if status in (303, 429):
                    mark_provider_incident(
                        _LIST_URL, incident=WORKDAY_LIST_TRANSIENT_STATUS_INCIDENT
                    )
                elif status == 0 and payload.get("error_kind") == "transport":
                    mark_transient_response_failure(_LIST_URL, reason="go_workday_list_failure")
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                raise RuntimeError(f"Go Workday list failed: {detail}")

            urls = payload.get("urls", [])
            if not isinstance(urls, list) or any(
                not isinstance(url, str)
                or not url.startswith(_JOB_PREFIX)
                or urlparse(url).hostname != "elevancehealth.wd1.myworkdayjobs.com"
                for url in urls
            ):
                raise ValueError("Go Workday returned a URL outside the selected origin")
            if len(set(urls)) != len(urls) or len(urls) >= 2_000:
                raise ValueError("Go Workday returned a duplicate or unsupported inventory")
            mark_reachable_response(_LIST_URL)
            outcome = "success"
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(urls))
            yield MonitorResult(urls=set(urls))
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
