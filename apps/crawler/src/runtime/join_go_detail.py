"""Go owner for direct JOIN Next.js detail extraction with configured mappings."""

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
from urllib.parse import urlparse

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
_SPECS = {
    "title": {"title"},
    "description": {
        "description",
        "schemaDescription",
        "schemaDescription || unifiedDescription || description",
    },
    "locations": {"city.cityName"},
    "employment_type": {"employmentType.googleType", "employmentType.name"},
    "job_location_type": {"workplaceType"},
    "date_posted": {"createdAt"},
}
_MAX_OUTPUT = 64 << 20


def eligible(url: str, scraper_type: str, config: dict | None, pw=None) -> None:
    # A domain batch can provide an idle Playwright handle for another board.
    # This direct config never uses it; render/actions are rejected below.
    del pw
    meta = config or {}
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("unsupported JOIN detail URL") from exc
    if (
        scraper_type != "nextdata"
        or parsed.scheme != "https"
        or parsed.hostname not in {"join.com", "www.join.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or re.fullmatch(r"/companies/[A-Za-z0-9_-]{1,128}/.+", parsed.path) is None
        or set(meta) - {"path", "fields"}
    ):
        raise ValueError("unsupported direct JOIN detail configuration")
    fields = meta.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("unsupported JOIN detail fields")
    if not fields:
        return
    if meta.get("path") != "props.pageProps.initialState.job":
        raise ValueError("unsupported JOIN detail path")
    for target, spec in fields.items():
        if target == "locations" and spec == ["=Switzerland"]:
            continue
        if not isinstance(spec, str) or spec not in _SPECS.get(target, set()):
            raise ValueError("unsupported JOIN field mapping")


def percentage_selected(board_id: str, url: str, config: dict | None) -> bool:
    raw = os.environ.get("JOIN_GO_DETAIL_PERCENT", "100")
    if re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) is None or raw == "0":
        return False
    try:
        eligible(url, "nextdata", config)
    except ValueError:
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < int(raw) * 100


async def run_detail(binary: str, request: dict) -> tuple[dict, int]:
    body = json.dumps(request, ensure_ascii=False).encode()
    if len(body) > 64 << 10:
        raise ValueError("JOIN detail input exceeds 64 KiB")
    proc = await asyncio.create_subprocess_exec(
        binary,
        "--detail",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(body)
        await proc.stdin.drain()
        proc.stdin.close()
        chunks = []
        size = 0
        while chunk := await proc.stdout.read(min(1 << 20, _MAX_OUTPUT + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > _MAX_OUTPUT:
                raise ValueError("JOIN detail output exceeds 64 MiB")
        await proc.wait()
        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict):
            raise ValueError("invalid Go JOIN detail response")
        assert proc.returncode is not None
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


class GoJoinDetailRuntime:
    implementation = "go-join-nextdata-detail"

    def __init__(self, binary: str = "/usr/local/bin/join-monitor-live") -> None:
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
        started = monotonic()
        outcome = "error"
        try:
            payload, returncode = await run_detail(
                self.binary, {"url": url, "config": scraper_config or {}}
            )
            requests, responses, size, status = (
                payload.get(k) for k in ("requests", "responses", "bytes", "status")
            )
            final_url = payload.get("final_url", "")
            has_fields = bool((scraper_config or {}).get("fields"))
            if (
                type(requests) is not int
                or not (1 <= requests <= 21 if has_fields else requests == 0)
                or type(responses) is not int
                or not 0 <= responses <= requests
                or type(size) is not int
                or not 0 <= size <= responses * ((16 << 20) + 1)
                or type(status) is not int
                or (status != 0 and not 100 <= status <= 599)
                or (not responses and (status or size or final_url))
            ):
                raise ValueError("invalid Go JOIN detail accounting")
            if responses:
                if not isinstance(final_url, str):
                    raise ValueError("invalid Go JOIN detail endpoint")
                endpoint = urlparse(final_url)
                if (
                    endpoint.scheme != "https"
                    or not endpoint.hostname
                    or endpoint.username is not None
                    or endpoint.password is not None
                    or endpoint.port not in {None, 443}
                    or endpoint.fragment
                ):
                    raise ValueError("invalid Go JOIN detail endpoint")
            attribution = current_egress_attribution()
            for _ in range(requests):
                record_origin_attempt(attribution, "direct")
            for _ in range(responses):
                record_origin_outcome(attribution, "direct", "response")
            for _ in range(requests - responses):
                record_origin_outcome(attribution, "direct", "transport_error")
            record_response_body_bytes(attribution, "direct", size)
            if payload.get("error_kind") not in {None, "", "tdm"}:
                raise ValueError("invalid Go JOIN detail error classification")
            if returncode or payload.get("error"):
                if payload.get("error_kind") == "tdm":
                    if payload.get("tdm_source") not in {"header", "meta"}:
                        raise ValueError("invalid Go JOIN publisher policy source")
                    raise TDMReservedError(
                        final_url or url,
                        source=payload["tdm_source"],
                        policy_url=payload.get("tdm_policy"),
                    )
                if responses:
                    mark_external_response(final_url, status)
                raise RuntimeError("Go JOIN detail fetch failed")
            raw = payload.get("content")
            expected_keys = {field.name for field in dataclasses.fields(JobContent)}
            if (
                not isinstance(raw, dict)
                or set(raw) != expected_keys
                or any(raw[key] is not None for key in expected_keys - _SPECS.keys())
                or any(
                    value is not None
                    and not isinstance(value, str)
                    and not (isinstance(value, list) and all(isinstance(v, str) for v in value))
                    for value in raw.values()
                )
                or (raw["locations"] is not None and not isinstance(raw["locations"], list))
                or (status != 200 and any(v is not None for v in raw.values()))
                or (has_fields and (not responses or not status))
            ):
                raise ValueError("invalid Go JOIN detail content")
            content = JobContent(**raw)
            if responses:
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
                "go_join.detail_complete",
                url=url,
                status=status,
                content=any(v is not None for v in raw.values()),
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
