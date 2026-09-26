"""Default-off Go owner for direct SuccessFactors Google RSS feeds."""

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
from src.core.monitors.rss import _feed_config
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
_BOOKKEEPING = {
    "scraper_type",
    "scraper_config",
    "suspect_streak",
    "recent_discovered_counts",
    "_monitor_config_fingerprint",
    "_confirmed_drop_candidate",
    "_identity_migration_receipt",
}
_DOWNSTREAM = {"url_allowlist", "url_transform", "url", "job_filter"}


def _eligible(board_url: str, monitor_type: str, config: dict | None, pw: object | None) -> str:
    metadata = config or {}
    if (
        monitor_type != "rss"
        or pw is not None
        or metadata.get("preset") != "successfactors"
        or metadata.get("render")
        or set(metadata) - {"preset", "feed_url"} - _BOOKKEEPING - _DOWNSTREAM
    ):
        raise ValueError("Go SuccessFactors requires a direct default RSS configuration")
    resolved = _feed_config({"board_url": board_url, "metadata": metadata})
    if resolved is None or resolved[0] != "successfactors":
        raise ValueError("Go SuccessFactors could not resolve the configured RSS feed")
    feed_url = resolved[1]
    board, feed = urlparse(board_url), urlparse(feed_url)
    try:
        board_port, feed_port = board.port, feed.port
    except ValueError as exc:
        raise ValueError("Go SuccessFactors requires canonical HTTPS URLs") from exc
    if (
        board.scheme != "https"
        or feed.scheme != "https"
        or not board.hostname
        or board.hostname != feed.hostname
        or board.username is not None
        or board.password is not None
        or feed.username is not None
        or feed.password is not None
        or board_port not in (None, 443)
        or feed_port not in (None, 443)
        or feed.query
        or feed.fragment
        or feed.path.rstrip("/").lower() != "/googlefeed.xml"
    ):
        raise ValueError("Go SuccessFactors requires same-origin HTTPS /googlefeed.xml")
    return feed_url


def percentage_selected(board_id: str, board_url: str, config: dict | None) -> bool:
    raw = os.environ.get("SUCCESSFACTORS_RSS_GO_PERCENT", "0")
    if not re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) or int(raw) == 0:
        return False
    try:
        _eligible(board_url, "rss", config, None)
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


class GoSuccessFactorsRSSMonitorRuntime:
    implementation = "go-successfactors-rss"

    def __init__(
        self, binary: str = "/usr/local/bin/successfactors-rss-monitor-live", *, board_id: str
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
        feed_url = _eligible(board_url, monitor_type, monitor_config, pw)
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary,
                "--feed-url",
                feed_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=8 << 20,
            )

            async def raw_batches() -> AsyncIterator[list[DiscoveredJob] | MonitorResult]:
                assert proc is not None and proc.stdout is not None
                batch: list[DiscoveredJob] = []
                urls: list[str] = []
                summary: dict | None = None
                while line := await proc.stdout.readline():
                    if len(line) > 4 << 20:
                        raise ValueError("Go SuccessFactors job record exceeded 4 MiB")
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("invalid Go SuccessFactors output record")
                    kind = record.get("type")
                    if kind == "job" and summary is None:
                        raw = record.get("job")
                        if (
                            not isinstance(raw, dict)
                            or not isinstance(raw.get("url"), str)
                            or not raw["url"]
                        ):
                            raise ValueError("invalid Go SuccessFactors job")
                        job = DiscoveredJob(**raw)
                        batch.append(job)
                        urls.append(job.url)
                        if len(urls) > 50_000:
                            raise ValueError("Go SuccessFactors exceeded 50,000 jobs")
                        if len(batch) >= 200:
                            yield batch
                            batch = []
                    elif kind == "summary" and summary is None:
                        summary = record
                    else:
                        raise ValueError("unexpected Go SuccessFactors output record")
                await proc.wait()
                if summary is None:
                    raise RuntimeError("Go SuccessFactors exited without a summary")
                attempts, responses, body_bytes = (
                    summary.get("requests"),
                    summary.get("responses"),
                    summary.get("bytes"),
                )
                status, final_url = summary.get("status"), summary.get("final_url")
                if (
                    type(attempts) is not int
                    or not 1 <= attempts <= 3
                    or type(responses) is not int
                    or not 0 <= responses <= attempts
                    or type(body_bytes) is not int
                    or not 0 <= body_bytes <= responses * ((256 << 20) + 1)
                    or type(status) is not int
                    or (status != 0 and not 100 <= status <= 599)
                    or (responses > 0 and (not isinstance(final_url, str) or not final_url))
                    or (responses == 0 and (final_url or status))
                ):
                    raise ValueError("invalid Go SuccessFactors request accounting")
                attribution = current_egress_attribution()
                for _ in range(attempts):
                    record_origin_attempt(attribution, "direct")
                for _ in range(responses):
                    record_origin_outcome(attribution, "direct", "response")
                for _ in range(attempts - responses):
                    record_origin_outcome(attribution, "direct", "transport_error")
                record_response_body_bytes(attribution, "direct", body_bytes)
                error_kind = summary.get("error_kind")
                if error_kind not in {None, "", "tdm"}:
                    raise ValueError("invalid Go SuccessFactors error classification")
                if proc.returncode != 0 or summary.get("error"):
                    if error_kind == "tdm":
                        raise TDMReservedError(
                            feed_url, source="header", policy_url=summary.get("tdm_policy")
                        )
                    if responses:
                        mark_external_response(feed_url, status)
                    detail = str(summary.get("error") or f"exit code {proc.returncode}")[:300]
                    if status and status != 200:
                        request = httpx.Request("GET", feed_url)
                        response = httpx.Response(status, request=request)
                        raise httpx.HTTPStatusError(detail, request=request, response=response)
                    raise RuntimeError(f"Go SuccessFactors RSS failed: {detail}")
                if status != 200 or responses < 1:
                    raise ValueError("Go SuccessFactors success had no HTTP 200 response")
                items, jobs, truncated = (
                    summary.get("items"),
                    summary.get("jobs"),
                    summary.get("truncated"),
                )
                if (
                    type(items) is not int
                    or type(jobs) is not int
                    or items < jobs
                    or items > 1_000_000
                    or jobs != len(urls)
                    or not isinstance(truncated, bool)
                    or truncated != (jobs == 50_000)
                ):
                    raise ValueError("invalid Go SuccessFactors inventory")
                digest = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
                log.info(
                    "go_successfactors_rss.monitor_complete",
                    board_id=self.board_id,
                    urls=jobs,
                    url_sha256=digest,
                    requests=attempts,
                    responses=responses,
                    response_bytes=body_bytes,
                )
                mark_reachable_response(feed_url)
                runtime_output_items_total.labels(
                    stage="monitor", implementation=self.implementation
                ).inc(jobs)
                if batch:
                    yield batch
                if truncated:
                    yield MonitorResult(truncated=True)

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
