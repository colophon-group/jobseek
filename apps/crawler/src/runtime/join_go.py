"""Exclusive Go JOIN inventory runtime, selected per configured board."""

from __future__ import annotations

import asyncio
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
from src.core.monitors import BoardGoneError, all_monitor_types
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

_SLUG = re.compile(r"[A-Za-z0-9_-]{1,128}")
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
log = structlog.get_logger()
_MAX_OUTPUT_BYTES = 64 << 20


def _eligible(board_url: str, monitor_type: str, config: dict | None, pw: object | None) -> str:
    metadata = config or {}
    parsed = urlparse(board_url)
    slug = metadata.get("slug")
    if not slug:
        match = re.fullmatch(r"/companies/([A-Za-z0-9_-]{1,128})/?", parsed.path)
        slug = match.group(1) if match is not None else None
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Go JOIN requires a canonical direct board URL") from exc
    if (
        monitor_type != "join"
        or pw is not None
        or not isinstance(slug, str)
        or _SLUG.fullmatch(slug) is None
        or parsed.scheme != "https"
        or parsed.hostname not in {"join.com", "www.join.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {f"/companies/{slug}", f"/companies/{slug}/"}
        or set(metadata) - {"slug"} - _BOOKKEEPING
    ):
        raise ValueError("Go JOIN requires an unchanged slug-only board configuration")
    return slug


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    """Choose a stable share of the supported provider configurations."""
    raw = os.environ.get("JOIN_GO_PERCENT", "100")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw):
        return False
    percent = int(raw)
    if percent == 0:
        return False
    try:
        _eligible(board_url, "join", config, None)
    except ValueError:
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < percent * 100


class GoJoinMonitorRuntime:
    implementation = "go-join"

    def __init__(self, binary: str = "/usr/local/bin/join-monitor-live", *, board_id: str) -> None:
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
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--board-url",
                board_url,
                "--slug",
                slug,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert proc.stdout is not None
            chunks = []
            size = 0
            while chunk := await proc.stdout.read(min(1 << 20, _MAX_OUTPUT_BYTES + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_OUTPUT_BYTES:
                    raise ValueError("Go JOIN output exceeded its inventory bound")
            await proc.wait()
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict):
                raise ValueError("invalid Go JOIN response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            body_bytes = payload.get("bytes")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 630_000
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= responses * ((16 << 20) + 1)
                or (responses == 0 and body_bytes != 0)
            ):
                raise ValueError("invalid Go JOIN request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(attempts - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", body_bytes)
            status = payload.get("status")
            final_url = payload.get("final_url")
            if type(status) is not int or (status != 0 and not 100 <= status <= 599):
                raise ValueError("invalid Go JOIN HTTP status")
            if responses:
                if not isinstance(final_url, str):
                    raise ValueError("Go JOIN returned an unexpected endpoint")
                endpoint = urlparse(final_url)
                try:
                    port = endpoint.port
                except ValueError as exc:
                    raise ValueError("Go JOIN returned an unexpected endpoint") from exc
                if (
                    endpoint.scheme != "https"
                    or not endpoint.hostname
                    or endpoint.username is not None
                    or endpoint.password is not None
                    or port not in (None, 443)
                    or endpoint.fragment
                ):
                    raise ValueError("Go JOIN returned an unexpected endpoint")
            if not responses and (final_url or status):
                raise ValueError("Go JOIN returned an inconsistent transport outcome")
            error_kind = payload.get("error_kind")
            if error_kind not in {None, "", "gone", "tdm"}:
                raise ValueError("invalid Go JOIN error classification")
            if proc.returncode != 0 or payload.get("error"):
                if error_kind == "tdm":
                    if payload.get("tdm_source") not in {"header", "meta"}:
                        raise ValueError("invalid Go JOIN publisher policy source")
                    raise TDMReservedError(
                        board_url,
                        source=payload["tdm_source"],
                        policy_url=payload.get("tdm_policy"),
                    )
                if error_kind == "gone" and status in {404, 410}:
                    raise BoardGoneError(
                        f"JOIN board {slug!r} returned {status}",
                        url=board_url,
                        status_code=status,
                    )
                if responses:
                    mark_external_response(final_url or board_url, status)
                detail = payload.get("error") or "child process failed"
                raise RuntimeError(f"Go JOIN inventory failed: {detail}")
            raw_urls = payload.get("urls")
            prefix = f"https://join.com/companies/{slug}/"
            if (
                status != 200
                or not isinstance(raw_urls, list)
                or len(raw_urls) > 50_000
                or any(not isinstance(url, str) or not url.startswith(prefix) for url in raw_urls)
                or len(set(raw_urls)) != len(raw_urls)
            ):
                raise ValueError("invalid Go JOIN URL inventory")
            urls = set(raw_urls)
            digest = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
            log.info(
                "go_join.monitor_complete",
                board_id=self.board_id,
                urls=len(urls),
                url_sha256=digest,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(board_url)
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
                with suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        proc.kill()
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
