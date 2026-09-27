"""Go rich monitor for supported direct Ashby configurations."""

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
from src.core.monitors.ashby import _token_from_url
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

_TOKEN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_. -]{0,127}")
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "blast_radius_floor",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()


def direct_ashby_token(board_url: str, config: dict | None) -> str | None:
    """Match Python's token precedence; keep downstream writer/detail settings."""
    config = config or {}
    try:
        parsed = urlparse(board_url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    token = config.get("token")
    if not token:
        if parsed.hostname != "jobs.ashbyhq.com":
            return None
        token = _token_from_url(board_url)
        # Do not accidentally admit Python's partial regex match for a URL
        # containing punctuation or an encoded token without explicit metadata.
        if token is None or parsed.path not in {f"/{token}", f"/{token}/"}:
            return None
    if (
        not isinstance(token, str)
        or _TOKEN.fullmatch(token) is None
        or token != token.strip()
        or config.get("scraper_type") not in ("skip", "json-ld")
        or set(config) - {"token", "org", "board_token"} - _BOOKKEEPING
        or any(key in config and config[key] != token for key in ("org", "board_token"))
        or ("scraper_config" in config and not isinstance(config["scraper_config"], dict))
    ):
        return None
    # These values affect the unchanged writer, never the Ashby API request.
    floor = config.get("blast_radius_floor")
    if "blast_radius_floor" in config and (
        isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 <= floor <= 1
    ):
        return None
    return token


class GoAshbyMonitorRuntime:
    implementation = "go-ashby"

    def __init__(self, binary: str = "/usr/local/bin/ashby-monitor-live", *, board_id: str) -> None:
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
        token = direct_ashby_token(board_url, config)
        if monitor_type != "ashby" or pw is not None or token is None:
            raise ValueError("Go Ashby requires an unchanged rich token configuration")
        api_url = str(
            httpx.URL(f"https://api.ashbyhq.com/posting-api/job-board/{token}").copy_with(
                params={"includeCompensation": "true"}
            )
        )
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--token",
                token,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 80_000_000:
                raise ValueError("Go Ashby output exceeded the selected origin bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Ashby response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            byte_count = payload.get("bytes")
            if (
                type(attempts) is not int
                or attempts != 1
                or type(responses) is not int
                or responses not in (0, 1)
                or type(byte_count) is not int
                or byte_count < 0
                or (responses == 0 and byte_count != 0)
            ):
                raise ValueError("invalid Go Ashby request accounting")
            attribution = current_egress_attribution()
            record_origin_attempt(attribution, "direct")
            record_origin_outcome(
                attribution, "direct", "response" if responses else "transport_error"
            )
            record_response_body_bytes(attribution, "direct", byte_count)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go Ashby status")
            if responses and (final_url != api_url or status == 0):
                raise ValueError("Go Ashby returned an unexpected response endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go Ashby returned an inconsistent transport outcome")
            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        api_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                if status == 404:
                    raise BoardGoneError(
                        f"Ashby board token {token!r} returned 404",
                        url=api_url,
                        status_code=404,
                    )
                if responses:
                    mark_external_response(api_url, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                if status and status != 200:
                    request = httpx.Request("GET", api_url)
                    response = httpx.Response(status, request=request)
                    raise httpx.HTTPStatusError(str(detail), request=request, response=response)
                raise RuntimeError(f"Go Ashby list failed: {detail}")
            if status != 200 or responses != 1:
                raise ValueError("Go Ashby success had no HTTP 200 response")
            raw_jobs = payload.get("jobs")
            truncated = payload.get("truncated")
            if not isinstance(raw_jobs, list) or not isinstance(truncated, bool):
                raise ValueError("invalid Go Ashby inventory")
            if truncated != (len(raw_jobs) > 50_000):
                raise ValueError("invalid Go Ashby truncation marker")
            jobs = []
            for index, raw in enumerate(raw_jobs):
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("url"), str)
                    or not raw["url"]
                ):
                    raise ValueError(f"invalid Go Ashby job at index {index}")
                jobs.append(DiscoveredJob(**raw))
            digest = hashlib.sha256("\n".join(sorted(job.url for job in jobs)).encode()).hexdigest()
            log.info(
                "go_ashby.monitor_complete",
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
