"""Exclusive, default-off Go list execution for the first Workday origin."""

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
_BOARD_URL = re.compile(
    r"^https://(?P<company>[A-Za-z0-9_-]+)\."
    r"(?P<instance>wd[0-9]+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[A-Za-z0-9][A-Za-z0-9_-]{0,127})/?$"
)
_BOOKKEEPING = {
    "scraper_type",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()


class GoWorkdayMonitorRuntime:
    implementation = "go-workday"

    def __init__(
        self,
        binary: str = "/usr/local/bin/workday-monitor-live",
        *,
        board_id: str = ELEVANCE_BOARD_ID,
    ) -> None:
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
        del http  # Go owns every list HTTP request for this exclusive origin.
        config = monitor_config or {}
        match = _BOARD_URL.fullmatch(board_url)
        if (
            monitor_type != "workday"
            or match is None
            or pw is not None
            or config.get("company") != match.group("company")
            or config.get("wd_instance") != match.group("instance")
            or config.get("site") != match.group("site")
            or config.get("all_sites") is not False
            or set(config) - {"company", "wd_instance", "site", "all_sites"} - _BOOKKEEPING
        ):
            raise ValueError("Go Workday requires an unchanged direct, single-site configuration")

        company = match.group("company")
        instance = match.group("instance")
        site = match.group("site")
        hostname = f"{company}.{instance}.myworkdayjobs.com"
        list_url = f"https://{hostname}/wday/cxs/{company}/{site}/jobs"
        job_prefix = f"https://{hostname}/{site}/"

        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--company",
                company,
                "--instance",
                instance,
                "--site",
                site,
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
                        list_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                status = payload.get("status", 0)
                if isinstance(status, int) and status > 0:
                    mark_external_response(list_url, status)
                if status in (303, 429):
                    mark_provider_incident(
                        list_url, incident=WORKDAY_LIST_TRANSIENT_STATUS_INCIDENT
                    )
                elif status == 0 and payload.get("error_kind") == "transport":
                    mark_transient_response_failure(list_url, reason="go_workday_list_failure")
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                raise RuntimeError(f"Go Workday list failed: {detail}")

            urls = payload.get("urls", [])
            if not isinstance(urls, list) or any(
                not isinstance(url, str)
                or not url.startswith(job_prefix)
                or urlparse(url).hostname != hostname
                for url in urls
            ):
                raise ValueError("Go Workday returned a URL outside the selected origin")
            if len(set(urls)) != len(urls) or len(urls) >= 2_000:
                raise ValueError("Go Workday returned a duplicate or unsupported inventory")
            log.info(
                "go_workday.monitor_complete",
                board_id=self.board_id,
                urls=len(urls),
                url_sha256=hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest(),
                requests=attempts,
                responses=responses,
                transport_errors=transport_errors,
                response_bytes=byte_count,
            )
            mark_reachable_response(list_url)
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
