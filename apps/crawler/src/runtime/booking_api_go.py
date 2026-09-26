"""Default-off Go HTTP inventory for Booking's existing page-one DOM result."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from time import monotonic

import httpx
import structlog

from src.core.monitor import MonitorResult
from src.core.monitors import all_monitor_types
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

BOARD_ID = "d9730d93-3a2a-4003-a99e-4a8f0830aa2b"
BOARD_URL = "https://jobs.booking.com/booking/jobs"
API_URL = (
    "https://jobs.booking.com/api/jobs?page=1&sortBy=relevance&descending=false"
    "&internal=false&tags1=Booking.com%20Company%20Hierarchy%7CTransport%20Company%20Hierarchy"
)
_URL = re.compile(r"https://jobs[.]booking[.]com/booking/jobs/[1-9][0-9]{0,11}[?]lang=en-us\Z")
_BOOKKEEPING = {
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()


def _eligible(board_id: str, board_url: str, monitor_type: str, config: dict | None) -> None:
    metadata = config or {}
    if (
        board_id != BOARD_ID
        or board_url != BOARD_URL
        or monitor_type != "dom"
        or metadata.get("render") is not True
        or metadata.get("url_filter") != {"include": r"jobs\.booking\.com/booking/jobs/\d+"}
        or metadata.get("scraper_type") != "json-ld"
        or set(metadata) - {"render", "url_filter", "scraper_type"} - _BOOKKEEPING
    ):
        raise ValueError("Go Booking API requires the unchanged selected DOM configuration")


class GoBookingAPIMonitorRuntime:
    implementation = "go-booking-api"

    def __init__(
        self, binary: str = "/usr/local/bin/booking-api-monitor-live", *, board_id: str
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
        del http, pw
        _eligible(self.board_id, board_url, monitor_type, monitor_config)
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
            if len(stdout) > 100_000:
                raise ValueError("Go Booking API output exceeded its page-one bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Booking API response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            body_bytes = payload.get("bytes")
            if (
                type(attempts) is not int
                or attempts != 1
                or type(responses) is not int
                or responses not in (0, 1)
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= (2 << 20) + 1
                or (responses == 0 and body_bytes != 0)
            ):
                raise ValueError("invalid Go Booking API request accounting")
            attribution = current_egress_attribution()
            record_origin_attempt(attribution, "direct")
            record_origin_outcome(
                attribution, "direct", "response" if responses else "transport_error"
            )
            record_response_body_bytes(attribution, "direct", body_bytes)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Booking API HTTP status")
            if responses and (final_url != API_URL or status == 0):
                raise ValueError("Go Booking API returned an unexpected endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go Booking API returned an inconsistent transport outcome")
            error_kind = payload.get("error_kind")
            if error_kind not in {None, "", "tdm"}:
                raise ValueError("invalid Go Booking API error classification")
            if proc.returncode != 0 or payload.get("error"):
                if error_kind == "tdm":
                    raise TDMReservedError(
                        API_URL, source="header", policy_url=payload.get("tdm_policy")
                    )
                if responses:
                    mark_external_response(API_URL, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                if status and status != 200:
                    request = httpx.Request("GET", API_URL)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Booking API inventory failed: {detail}")
            raw_urls = payload.get("urls")
            advertised = payload.get("advertised")
            if (
                status != 200
                or responses != 1
                or not isinstance(raw_urls, list)
                or len(raw_urls) > 100
                or any(not isinstance(url, str) or _URL.fullmatch(url) is None for url in raw_urls)
                or len(set(raw_urls)) != len(raw_urls)
                or type(advertised) is not int
                or not len(raw_urls) <= advertised <= 1_000_000
            ):
                raise ValueError("invalid Go Booking API URL inventory")
            urls = set(raw_urls)
            digest = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
            log.info(
                "go_booking_api.monitor_complete",
                board_id=self.board_id,
                urls=len(urls),
                advertised=advertised,
                url_sha256=digest,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(API_URL)
            outcome = "success"
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(urls))
            yield MonitorResult(urls=urls)
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
