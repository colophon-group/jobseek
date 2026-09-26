"""Exclusive, default-off Go Workable detail fetch for selected boards."""

from __future__ import annotations

import asyncio
import json
import re
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
from src.shared.http import mark_external_response, mark_reachable_response
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()
_PATH = re.compile(r"/[A-Za-z0-9][A-Za-z0-9_-]{0,127}/j/[A-Za-z0-9_]+/?")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


class GoWorkableDetailRuntime:
    implementation = "go-workable-detail"

    def __init__(self, binary: str = "/usr/local/bin/workable-detail-live") -> None:
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
        del http, artifact_dir
        config = scraper_config or {}
        parsed = urlparse(url)
        token = config.get("token", "")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Go Workable detail requires a canonical direct URL") from exc
        if (
            scraper_type != "workable"
            or pw is not None
            or parsed.scheme != "https"
            or parsed.hostname != "apply.workable.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
            or _PATH.fullmatch(parsed.path) is None
            or not isinstance(token, str)
            or (token and _SLUG.fullmatch(token) is None)
            or set(config) - {"token"}
        ):
            raise ValueError("Go Workable detail requires a canonical direct URL and config")
        parts = parsed.path.strip("/").split("/")
        slug = token or parts[0]
        shortcode = parts[2]
        api_url = f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"
        markdown_url = f"https://apply.workable.com/{slug}/jobs/view/{shortcode}.md"
        started = monotonic()
        outcome = "error"
        proc = None
        try:
            args = [self.binary, "--url", url]
            if token:
                args.extend(("--token", token))
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if len(stdout) > 1_000_000:
                raise ValueError("Go Workable detail output exceeded the bounded contract")
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("invalid Go Workable detail response")
            attempts = payload.get("requests")
            responses = payload.get("responses")
            byte_count = payload.get("bytes")
            status = payload.get("status")
            final_url = payload.get("final_url", "")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= 2
                or type(responses) is not int
                or not 0 <= responses <= attempts
                or type(byte_count) is not int
                or byte_count < 0
                or type(status) is not int
                or (status != 0 and not 100 <= status <= 599)
                or not isinstance(final_url, str)
                or (final_url and final_url not in {api_url, markdown_url})
            ):
                raise ValueError("invalid Go Workable detail request accounting")
            attribution = current_egress_attribution()
            for _ in range(attempts):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(attempts - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", byte_count)
            if proc.returncode != 0 or payload.get("error"):
                if payload.get("error") == "tdm-reservation=1":
                    raise TDMReservedError(
                        final_url or api_url,
                        source="header",
                        policy_url=payload.get("tdm_policy"),
                    )
                if status and final_url:
                    mark_external_response(final_url, status)
                detail = payload.get("error") or stderr.decode(errors="replace")[:300]
                raise RuntimeError(f"Go Workable detail failed: {detail}")
            if responses < 1 or status == 0 or not final_url:
                raise ValueError("Go Workable detail success has no HTTP response")
            raw_content = payload.get("content")
            if raw_content is not None and (status != 200 or not isinstance(raw_content, dict)):
                raise ValueError("invalid Go Workable detail content")
            content = JobContent(**raw_content) if raw_content is not None else JobContent()
            log.info(
                "go_workable.detail_complete",
                slug=slug,
                content=raw_content is not None,
                status=status,
                requests=attempts,
                response_bytes=byte_count,
            )
            mark_reachable_response(final_url)
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
