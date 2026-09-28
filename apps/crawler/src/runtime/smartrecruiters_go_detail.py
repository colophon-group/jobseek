"""Go owner for scheduled direct SmartRecruiters detail extraction."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse

import structlog

from src.core.job_content import JobContent
from src.metrics import (
    runtime_execution_duration_seconds,
    runtime_executions_total,
    runtime_output_items_total,
)
from src.runtime.smartrecruiters_go import run_go
from src.shared.egress import (
    current_egress_attribution,
    record_origin_attempt,
    record_origin_outcome,
    record_response_body_bytes,
    record_runtime_capability,
)
from src.shared.http import mark_external_response, mark_reachable_response
from src.shared.http_retry import ResponseBodyTooLargeError
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

_ORDINARY = re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)/([\w-]+)")
_ONECLICK = re.compile(
    r"(?:jobs|careers)\.smartrecruiters\.com/oneclick-ui/company/([\w-]+)/"
    r"(?:publication|job)/([\w-]+)"
)


def eligible(url: str, scraper_type: str, config: dict | None) -> tuple[str, str]:
    parsed = urlparse(url)
    if (
        scraper_type != "smartrecruiters"
        or config
        or parsed.scheme != "https"
        or parsed.params
        or parsed.netloc not in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}
    ):
        raise ValueError("Go SmartRecruiters detail requires a direct unchanged configuration")
    parts = parsed.path.strip("/").split("/")
    if (
        len(parts) == 5
        and parts[:2] == ["oneclick-ui", "company"]
        and parts[3] in {"publication", "job"}
    ):
        token, posting_id = parts[2], parts[4]
    elif len(parts) == 2:
        token, posting_id = parts
    else:
        raise ValueError("unsupported SmartRecruiters detail path")
    # Preserve the existing parser's identity, including oneclick precedence.
    # Encoded segments and nested URLs must not silently change the API target.
    historical = _ONECLICK.search(url) or _ORDINARY.search(url)
    if (
        historical is None
        or historical.groups() != (token, posting_id)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token) is None
        or re.fullmatch(r"[\w-]{1,128}", posting_id) is None
    ):
        raise ValueError("unsupported SmartRecruiters detail identity")
    return token, posting_id


def percentage_selected(board_id: str, url: str, config: dict | None) -> bool:
    raw = os.environ.get("SMARTRECRUITERS_GO_DETAIL_PERCENT", "100")
    if re.fullmatch(r"(?:0|[1-9][0-9]?|100)", raw) is None or raw == "0":
        return False
    try:
        eligible(url, "smartrecruiters", config)
    except ValueError:
        return False
    bucket = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], "big") % 10_000
    return bucket < int(raw) * 100


class GoSmartRecruitersDetailRuntime:
    implementation = "go-smartrecruiters-detail"

    def __init__(
        self, binary: str = "/usr/local/bin/smartrecruiters-monitor-live", *, board_id: str = ""
    ):
        self.binary = binary
        self.board_id = board_id

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
        del http, artifact_dir, pw  # Mixed domain batches may carry an idle browser.
        token, posting_id = eligible(url, scraper_type, scraper_config)
        api_url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}"
        started = monotonic()
        outcome = "error"
        try:
            payload, returncode = await run_go(
                self.binary,
                {"mode": "detail", "url": url, "capture_board_id": self.board_id},
                limit=8 << 20,
            )
            requests, responses, size, status = (
                payload.get(k) for k in ("requests", "responses", "bytes", "status")
            )
            if (
                type(requests) is not int
                or requests != 1
                or type(responses) is not int
                or not 0 <= responses <= 1
                or type(size) is not int
                or not 0 <= size <= (1 << 20) + 1
                or type(status) is not int
                or (status != 0 and not 100 <= status <= 599)
                or payload.get("final_url") != api_url
                or (responses == 0 and (status != 0 or size != 0))
            ):
                raise ValueError("invalid Go SmartRecruiters detail accounting")
            attribution = current_egress_attribution()
            record_origin_attempt(attribution, "direct")
            record_origin_outcome(
                attribution, "direct", "response" if responses else "transport_error"
            )
            record_response_body_bytes(attribution, "direct", size)
            if returncode or payload.get("error"):
                failure = payload.get("failure") or {}
                if not isinstance(failure, dict) or (failure and failure.get("url") != api_url):
                    raise ValueError("invalid Go detail failure")
                if failure.get("kind") == "tdm":
                    if failure.get("source") not in {"header", "meta"}:
                        raise ValueError("invalid publisher policy source")
                    raise TDMReservedError(
                        api_url, source=failure["source"], policy_url=failure.get("policy")
                    )
                if failure.get("kind") == "body_limit":
                    raise ResponseBodyTooLargeError(api_url, 1 << 20)
                raise RuntimeError("Go SmartRecruiters detail fetch failed")
            raw = payload.get("content")
            if (
                responses != 1
                or status == 0
                or (status == 200 and not isinstance(raw, dict))
                or (status != 200 and raw is not None)
            ):
                raise ValueError("invalid Go detail content")
            content = JobContent(**raw) if raw is not None else JobContent()
            mark_external_response(api_url, status)
            mark_reachable_response(api_url)
            fields_hash = hashlib.sha256(
                json.dumps(
                    dataclasses.asdict(content),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            log.info(
                "go_smartrecruiters.detail_complete",
                url=url,
                status=status,
                content=raw is not None,
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
