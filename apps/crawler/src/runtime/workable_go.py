"""Default-off Go owner for canonical Workable list monitors."""

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

log = structlog.get_logger()
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_BOOKKEEPING = {
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}


def _eligible(board_url: str, monitor_type: str, config: dict | None, pw: object | None) -> str:
    metadata = config or {}
    parsed = urlparse(board_url)
    parts = parsed.path.strip("/").split("/")
    slug = parts[0] if len(parts) == 1 else ""
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Go Workable requires a canonical hosted URL") from exc
    if (
        monitor_type != "workable"
        or pw is not None
        or parsed.scheme != "https"
        or parsed.hostname != "apply.workable.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or _SLUG.fullmatch(slug) is None
        or metadata.get("token") != slug
        or metadata.get("scraper_type") != "workable"
        or set(metadata) - {"token", "scraper_type"} - _BOOKKEEPING
    ):
        raise ValueError("Go Workable requires an unchanged direct list configuration")
    return slug


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("WORKABLE_GO_PERCENT", "0")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) or int(raw) == 0:
        return False
    try:
        _eligible(board_url, "workable", config, None)
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
    return bucket < int(raw) * 100


class GoWorkableMonitorRuntime:
    implementation = "go-workable"

    def __init__(self, binary: str = "/usr/local/bin/workable-monitor-live", *, board_id: str):
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
        slug = _eligible(board_url, monitor_type, monitor_config, pw)
        api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
        allowed_endpoints = {
            api_url,
            f"https://apply.workable.com/{slug}/llms.txt",
            f"https://apply.workable.com/{slug}/jobs.md",
            f"https://www.workable.com/api/accounts/{slug}",
        }
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--slug",
                slug,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if len(stdout) > 8 << 20:
                raise ValueError("Go Workable output exceeded the monitor bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Workable response")
            attempts, responses, body_bytes = (
                payload.get("requests"),
                payload.get("responses"),
                payload.get("bytes"),
            )
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 3_000
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= responses * ((64 << 20) + 1)
            ):
                raise ValueError("invalid Go Workable request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(attempts - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", body_bytes)
            status, final_url = payload.get("status"), payload.get("final_url")
            if (
                type(status) is not int
                or (status != 0 and not 100 <= status <= 599)
                or (responses > 0 and final_url not in allowed_endpoints)
                or (responses == 0 and (final_url or status))
            ):
                raise ValueError("invalid Go Workable HTTP outcome")
            resolved_url = final_url if isinstance(final_url, str) else api_url
            error_kind = payload.get("error_kind")
            if error_kind not in {None, "", "tdm"}:
                raise ValueError("invalid Go Workable error classification")
            if proc.returncode != 0 or payload.get("error"):
                if error_kind == "tdm":
                    raise TDMReservedError(
                        resolved_url,
                        source="header",
                        policy_url=payload.get("tdm_policy"),
                    )
                if responses and status:
                    mark_external_response(resolved_url, status)
                detail = str(payload.get("error") or f"exit code {proc.returncode}")[:300]
                if status and status != 200:
                    request = httpx.Request("POST" if final_url == api_url else "GET", resolved_url)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(detail, request=request, response=response)
                raise RuntimeError(f"Go Workable inventory failed: {detail}")
            raw_urls = payload.get("urls")
            truncated = payload.get("truncated")
            verified_empty = payload.get("verified_empty")
            prefix = f"https://apply.workable.com/{slug}/j/"
            if (
                status != 200
                or responses == 0
                or not isinstance(raw_urls, list)
                or len(raw_urls) > 50_100
                or any(
                    not isinstance(url, str)
                    or not url.startswith(prefix)
                    or re.fullmatch(r"[A-Za-z0-9_-]+/", url[len(prefix) :]) is None
                    for url in raw_urls
                )
                or len(set(raw_urls)) != len(raw_urls)
                or type(truncated) is not bool
                or type(verified_empty) is not bool
                or (verified_empty and (raw_urls or truncated))
                or (truncated and len(raw_urls) < 50_000)
            ):
                raise ValueError("invalid Go Workable URL inventory")
            urls = set(raw_urls)
            digest = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
            log.info(
                "go_workable.monitor_complete",
                board_id=self.board_id,
                urls=len(urls),
                url_sha256=digest,
                truncated=truncated,
                verified_empty=verified_empty,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(resolved_url)
            outcome = "success"
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(urls))
            if verified_empty:
                yield MonitorResult(
                    urls=set(),
                    verified_empty_reason="Workable llms.txt advertises zero current openings",
                )
            else:
                yield MonitorResult(urls=urls, truncated=truncated)
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
