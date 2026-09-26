"""Default-off Go owner for the existing Personio XML and HTML monitor."""

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

from src.core.monitor import MonitorResult, postprocess_monitor_stream
from src.core.monitors import DiscoveredJob, all_monitor_types
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
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LANGUAGE = re.compile(r"[a-z]{2}")
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
}
_DOWNSTREAM = {"url_allowlist", "url_transform", "url", "job_filter"}


def _eligible(
    board_url: str,
    monitor_type: str,
    config: dict | None,
    pw: object | None,
) -> tuple[str, str, str, list[str]]:
    metadata = config or {}
    parsed = urlparse(board_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Go Personio requires a canonical board URL") from exc
    host_match = re.fullmatch(
        r"([A-Za-z0-9_-]{1,128})\.jobs\.personio\.(de|com)", parsed.hostname or ""
    )
    slug = metadata.get("slug") or (host_match.group(1) if host_match else None)
    if (
        monitor_type != "personio"
        or pw is not None
        or host_match is None
        or not isinstance(slug, str)
        or _SLUG.fullmatch(slug) is None
        or slug != host_match.group(1)
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or set(metadata) - {"slug", "language", "backfill_languages"} - _BOOKKEEPING - _DOWNSTREAM
    ):
        raise ValueError("Go Personio requires an unchanged direct XML/HTML configuration")
    language = metadata.get("language", "en")
    backfill = metadata.get("backfill_languages")
    if backfill is None:
        backfill = ["de"]
    if (
        not isinstance(language, str)
        or _LANGUAGE.fullmatch(language) is None
        or not isinstance(backfill, list)
        or len(backfill) > 4
        or len(set(map(str, backfill))) != len(backfill)
        or any(not isinstance(item, str) or _LANGUAGE.fullmatch(item) is None for item in backfill)
    ):
        raise ValueError("Go Personio requires bounded language configuration")
    return slug, host_match.group(2), language, backfill


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("PERSONIO_GO_PERCENT", "0")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) or int(raw) == 0:
        return False
    try:
        _eligible(board_url, "personio", config, None)
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


class GoPersonioMonitorRuntime:
    implementation = "go-personio"

    def __init__(
        self, binary: str = "/usr/local/bin/personio-monitor-live", *, board_id: str
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
        del http
        slug, domain, language, backfill = _eligible(board_url, monitor_type, monitor_config, pw)
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--slug",
                slug,
                "--domain",
                domain,
                "--language",
                language,
                "--backfill-languages",
                ",".join(backfill),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 160_000_000:
                raise ValueError("Go Personio output exceeded its selected feed bound")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Personio response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            body_bytes = payload.get("bytes")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 10
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(body_bytes) is not int
                or not 0 <= body_bytes <= responses * ((128 << 20) + 1)
            ):
                raise ValueError("invalid Go Personio request accounting")
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
                raise ValueError("invalid Go Personio HTTP status")
            if responses and (not isinstance(final_url, str) or status == 0):
                raise ValueError("Go Personio returned an inconsistent response")
            if not responses and (final_url or status):
                raise ValueError("Go Personio returned an inconsistent transport outcome")
            error_kind = payload.get("error_kind")
            if error_kind not in {None, "", "tdm"}:
                raise ValueError("invalid Go Personio error classification")
            if proc.returncode != 0 or payload.get("error"):
                if error_kind == "tdm":
                    raise TDMReservedError(
                        board_url, source="header", policy_url=payload.get("tdm_policy")
                    )
                if responses:
                    mark_external_response(board_url, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                raise RuntimeError(f"Go Personio failed: {detail}")
            if status != 200 or responses < 1:
                raise ValueError("Go Personio success had no HTTP 200 response")
            raw_jobs = payload.get("jobs")
            truncated = payload.get("truncated")
            if (
                not isinstance(raw_jobs, list)
                or len(raw_jobs) > 50_000
                or not isinstance(truncated, bool)
                or (truncated and len(raw_jobs) != 50_000)
            ):
                raise ValueError("invalid Go Personio inventory")
            jobs = []
            for index, raw in enumerate(raw_jobs):
                if (
                    not isinstance(raw, dict)
                    or not isinstance(raw.get("url"), str)
                    or not raw["url"]
                ):
                    raise ValueError(f"invalid Go Personio job at index {index}")
                jobs.append(DiscoveredJob(**raw))
            digest = hashlib.sha256("\n".join(sorted(job.url for job in jobs)).encode()).hexdigest()
            log.info(
                "go_personio.monitor_complete",
                board_id=self.board_id,
                urls=len(jobs),
                url_sha256=digest,
                requests=attempts,
                responses=responses,
                response_bytes=body_bytes,
            )
            mark_reachable_response(board_url)
            runtime_output_items_total.labels(
                stage="monitor", implementation=self.implementation
            ).inc(len(jobs))

            async def raw_batches() -> AsyncIterator[list[DiscoveredJob] | MonitorResult]:
                if truncated:
                    yield MonitorResult(
                        urls={job.url for job in jobs},
                        jobs_by_url={job.url: job for job in jobs},
                        truncated=True,
                    )
                else:
                    for offset in range(0, len(jobs), 200):
                        yield jobs[offset : offset + 200]
                    if not jobs:
                        yield []

            async for result in postprocess_monitor_stream(raw_batches(), monitor_config or {}):
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
