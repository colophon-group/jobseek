"""Exclusive Go execution for an explicitly selected cached XML sitemap."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from time import monotonic
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitor import MonitorResult, postprocess_monitor_stream
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
from src.shared.http import mark_reachable_response
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()


def eligible(board_url: str, monitor_type: str | None, config: dict | None) -> bool:
    """Only explicit, direct HTTPS sitemaps can use the bounded Go runner."""
    if monitor_type != "sitemap" or not isinstance(config, dict):
        return False
    sitemap_url = config.get("sitemap_url")
    if not isinstance(sitemap_url, str) or not sitemap_url:
        return False
    if config.get("xml_attempts", 1) != 1:
        return False
    if config.get("proxy") or config.get("skip_ssl") or config.get("ssl_verify") is False:
        return False
    try:
        page = urlparse(board_url)
        sitemap = urlparse(sitemap_url)
        return (
            page.scheme == sitemap.scheme == "https"
            and page.hostname is not None
            and page.hostname == sitemap.hostname
            and page.port is None
            and sitemap.port is None
            and sitemap.username is None
            and sitemap.password is None
            and not sitemap.fragment
        )
    except ValueError:
        return False


class GoSitemapMonitorRuntime:
    implementation = "go-sitemap"

    def __init__(self, *, board_id: str, binary: str = "/usr/local/bin/sitemap-monitor-live"):
        self.board_id = board_id
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
        if pw is not None or not eligible(board_url, monitor_type, config):
            raise ValueError("Go sitemap requires an explicit unchanged HTTPS sitemap")
        sitemap_url = config["sitemap_url"]
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--sitemap-url",
                sitemap_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=75)
            if len(stdout) > 12_000_000:
                raise ValueError("Go sitemap output exceeded the selected bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go sitemap response")
            requests = payload.get("requests")
            responses = payload.get("responses")
            wire_attempts = payload.get("wire_attempts")
            response_bytes = payload.get("response_bytes")
            if (
                type(requests) is not int
                or not 0 <= requests <= 3
                or type(responses) is not int
                or not 0 <= responses <= requests
                or type(wire_attempts) is not int
                or not 0 <= wire_attempts <= requests
                or type(response_bytes) is not int
                or not 0 <= response_bytes <= 55 * 1024 * 1024
            ):
                raise ValueError("invalid Go sitemap request accounting")
            attribution = current_egress_attribution()
            for attempt in range(requests):
                record_origin_attempt(attribution, "direct")
                record_origin_outcome(
                    attribution,
                    "direct",
                    "response" if attempt < responses else "transport_error",
                )
            record_response_body_bytes(attribution, "direct", response_bytes)
            error = payload.get("error")
            if proc.returncode != 0 or error:
                if error == "tdm_reservation":
                    raise TDMReservedError(sitemap_url, source="header")
                detail = error or stderr.decode(errors="replace")[:200]
                raise RuntimeError(f"Go sitemap failed: {detail}")
            raw_urls = payload.get("urls")
            truncated = payload.get("truncated")
            if (
                not isinstance(raw_urls, list)
                or len(raw_urls) > 50_000
                or not all(isinstance(url, str) and url for url in raw_urls)
                or type(truncated) is not bool
                or responses < 1
            ):
                raise ValueError("invalid Go sitemap URL inventory")
            mark_reachable_response(sitemap_url)

            async def raw_batches() -> AsyncIterator[MonitorResult]:
                yield MonitorResult(urls=set(raw_urls), truncated=truncated)

            async for result in postprocess_monitor_stream(raw_batches(), config):
                digest = hashlib.sha256("\n".join(sorted(result.urls)).encode()).hexdigest()
                log.info(
                    "go_sitemap.monitor_complete",
                    board_id=self.board_id,
                    urls=len(result.urls),
                    url_sha256=digest,
                    requests=requests,
                    responses=responses,
                    response_bytes=response_bytes,
                )
                runtime_output_items_total.labels(
                    stage="monitor", implementation=self.implementation
                ).inc(len(result.urls))
                yield result
            outcome = "success"
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
