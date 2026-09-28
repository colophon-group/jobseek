"""Native JSON-LD extraction for direct HTTP and retained Lightpanda HTML."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.job_content import JobContent
from src.core.scrapers import all_scraper_types
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
_BINARY = "/usr/local/bin/jsonld-detail-live"
_MAX_OUTPUT = 64 << 20
_ALLOWED = frozenset(
    {
        "render",
        "proxy",
        "skip_ssl",
        "ignore_locations",
        "ignore_address_region",
        "ignore_date_posted",
        "ignore_valid_through",
        "defaults_by_url",
        "defaults",
        "enrich",
        "fallback",
        "transport_attempts",
        "description_selector",
        "request_headers",
        "wait",
        "actions",
        "channel",
        "stealth",
        "timeout",
        "headless",
        "persistent_context",
        "wait_fallback",
        "browser_backend",
        "routing_revision",
    }
)


def _json_keys(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON-LD config keys must be strings")
        for child in value.values():
            _json_keys(child)
    elif isinstance(value, list):
        for child in value:
            _json_keys(child)


def eligible(url: str, scraper_type: str, config: dict | None, pw=None) -> None:
    del pw  # Mixed domain batches may provide an idle browser handle.
    meta = config or {}
    parsed = urlsplit(url)
    expected_port = 443 if parsed.scheme == "https" else 80
    if (
        scraper_type != "json-ld"
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, expected_port}
        or set(meta) - _ALLOWED
        or any(meta.get(k) for k in ("render", "proxy", "skip_ssl"))
    ):
        raise ValueError("unsupported direct JSON-LD configuration")
    _json_keys(meta)
    attempts = meta.get("transport_attempts")
    if attempts is not None and (type(attempts) is not int or not 1 <= attempts <= 5):
        raise ValueError("JSON-LD transport_attempts must be an integer from 1 to 5")
    headers = meta.get("request_headers") or {}
    if not isinstance(headers, dict) or any(
        not isinstance(v, str) or not k or any(c in k + v for c in "\r\n\0")
        for k, v in headers.items()
    ):
        raise ValueError("invalid JSON-LD request headers")
    selector = meta.get("description_selector")
    if selector is not None and (
        not isinstance(selector, str)
        or not selector.strip()
        or len(selector) > 256
        or "\0" in selector
    ):
        raise ValueError("invalid JSON-LD description_selector")


def percentage_selected(board_id: str, url: str, config: dict | None) -> bool:
    raw = os.environ.get("JSONLD_GO_DETAIL_PERCENT", "100")
    if re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) is None or raw == "0":
        return False
    try:
        eligible(url, "json-ld", config)
    except ValueError:
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < int(raw) * 100


async def run_child(binary: str, request: dict, *, parse: bool = False) -> tuple[dict, int]:
    _json_keys(request)
    body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
    limit = 64 << 20 if parse else 64 << 10
    if len(body) > limit:
        raise ValueError("JSON-LD input exceeds limit")
    proc = await asyncio.create_subprocess_exec(
        binary,
        *(["--parse"] if parse else []),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None

        # Read and write concurrently: retained HTML may exceed either pipe's
        # buffer, and a rejecting child must not deadlock the writer.
        async def write():
            assert proc.stdin is not None
            try:
                proc.stdin.write(body)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()

        writer = asyncio.create_task(write())
        try:
            chunks = []
            size = 0
            while chunk := await proc.stdout.read(min(1 << 20, _MAX_OUTPUT + 1 - size)):
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_OUTPUT:
                    raise ValueError("JSON-LD output exceeds limit")
            await writer
            await proc.wait()
        finally:
            if not writer.done():
                writer.cancel()
                with suppress(asyncio.CancelledError):
                    await writer
        assert proc.returncode is not None
        if parse and proc.returncode:
            raise ValueError("Go JSON-LD parse failed")
        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict):
            raise ValueError("invalid Go JSON-LD response")
        return payload, proc.returncode
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()


def _content(raw) -> JobContent:
    if not isinstance(raw, dict) or set(raw) != {f.name for f in dataclasses.fields(JobContent)}:
        raise ValueError("invalid Go JSON-LD content shape")
    return JobContent(**raw)


async def parse_rendered_html(
    url: str, config: dict | None, html: str, *, binary: str = _BINARY
) -> JobContent:
    """Parse only the validated retained HTML; this invocation cannot fetch."""
    raw, _ = await run_child(binary, {"url": url, "config": config or {}, "html": html}, parse=True)
    return _content(raw)


class GoJsonLdDetailRuntime:
    implementation = "go-jsonld-detail"

    def __init__(self, binary: str = _BINARY) -> None:
        self.binary = binary

    async def scrape(
        self,
        url: str,
        scraper_type: str,
        scraper_config: dict | None,
        http,
        *,
        pw=None,
        artifact_dir: Path | None = None,
    ) -> JobContent:
        del http, artifact_dir
        eligible(url, scraper_type, scraper_config, pw)
        started, outcome = monotonic(), "error"
        try:
            payload, returncode = await run_child(
                self.binary, {"url": url, "config": scraper_config or {}}
            )
            requests, responses, size, status = (
                payload.get(k) for k in ("requests", "responses", "bytes", "status")
            )
            final_url = payload.get("final_url", "")
            # Two content passes, each with an optional iframe; each fetch can
            # retry one 403 and two Avature 406s, each with at most 20 redirects.
            if (
                type(requests) is not int
                or not 0 <= requests <= 336
                or type(responses) is not int
                or not 0 <= responses <= requests
                or type(size) is not int
                or not 0 <= size <= responses * ((16 << 20) + 1)
                or type(status) is not int
                or status != 0
                and not 100 <= status <= 599
                or not responses
                and (status or size or final_url)
            ):
                raise ValueError("invalid Go JSON-LD accounting")
            if responses:
                if not isinstance(final_url, str):
                    raise ValueError("invalid Go JSON-LD endpoint")
                eligible(final_url, "json-ld", None)
            attribution = current_egress_attribution()
            for _ in range(requests):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(requests - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", size)
            kind = payload.get("error_kind")
            if kind not in {None, "", "tdm", "status", "parse"}:
                raise ValueError("invalid Go JSON-LD error classification")
            if returncode or payload.get("error"):
                if kind == "tdm":
                    if payload.get("tdm_source") not in {"header", "meta"}:
                        raise ValueError("invalid Go JSON-LD policy source")
                    raise TDMReservedError(
                        final_url or url,
                        source=payload["tdm_source"],
                        policy_url=payload.get("tdm_policy") or None,
                    )
                if responses:
                    mark_external_response(final_url, status)
                if kind == "status":
                    response = httpx.Response(status, request=httpx.Request("GET", final_url))
                    response.raise_for_status()
                    raise ValueError("invalid Go JSON-LD status failure")
                if kind == "parse":
                    raise ValueError("Go JSON-LD extraction failed")
                raise RuntimeError("Go JSON-LD fetch failed")
            if not responses or not 200 <= status < 300:
                raise ValueError("Go JSON-LD success requires a successful response")
            content = _content(payload.get("content"))
            mark_external_response(final_url, status)
            mark_reachable_response(final_url)
            fields_hash = hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(content),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            log.info(
                "go_jsonld.detail_complete",
                url=url,
                status=status,
                fields_sha256=fields_hash,
                requests=requests,
                response_bytes=size,
            )
            runtime_output_items_total.labels(
                stage="scrape", implementation=self.implementation
            ).inc()
            outcome = "success"
            return content
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
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
