"""Go-owned public HTTP transport for direct DOM detail documents."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from time import monotonic
from urllib.parse import urlsplit

import httpx
import structlog

from src.metrics import runtime_execution_duration_seconds, runtime_executions_total
from src.runtime.jsonld_go_detail import run_child
from src.shared.egress import (
    current_egress_attribution,
    record_origin_attempt,
    record_origin_outcome,
    record_response_body_bytes,
)
from src.shared.http import mark_external_response, mark_reachable_response
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()
_BINARY = "/usr/local/bin/dom-detail-fetch"
_LIMIT = 16 << 20


def eligible(url: str, config: dict, http: httpx.AsyncClient | None = None) -> bool:
    if os.environ.get("DOM_GO_HTTP_ENABLED", "1") != "1":
        return False
    if http is not None and getattr(http, "_jobseek_verified_direct_http", False) is not True:
        return False
    if any(config.get(k) for k in ("render", "actions", "proxy", "skip_ssl")):
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 443 if parsed.scheme == "https" else 80}
            and len(url) <= 8192
        )
    except ValueError:
        return False


async def fetch_response(
    url: str,
    *,
    headers: dict[str, str],
    retry_limits: dict[int, int],
    same_origin_redirects: bool,
    binary: str | None = None,
) -> httpx.Response:
    """Return native-fetched bytes; normal callers retain response semantics.

    The response wrapper performs no network I/O. Redirects, status retries,
    cookie handshakes, body bounds, public DNS and TDM checks are native.
    Configured encoding, gone/challenge handling and document conversion remain
    with the existing caller, then the installed Go parser consumes its HTML.
    """
    started, outcome = monotonic(), "error"
    try:
        async with asyncio.timeout(610):
            payload, returncode = await run_child(
                binary or _BINARY,
                {
                    "url": url,
                    "options": {
                        "headers": headers,
                        "retry_limits": {str(k): v for k, v in retry_limits.items()},
                        "same_origin": same_origin_redirects,
                        "public_headers": bool(headers),
                    },
                },
            )
        requests, responses, size, status = (
            payload.get(k) for k in ("requests", "responses", "bytes", "status")
        )
        final_url = payload.get("final_url", "")
        if (
            type(requests) is not int
            or not 0 <= requests <= 21_021
            or type(responses) is not int
            or not 0 <= responses <= requests
            or type(size) is not int
            or not 0 <= size <= responses * (_LIMIT + 1)
            or type(status) is not int
            or (status != 0 and not 100 <= status <= 599)
            or (not responses and (status or size or final_url))
        ):
            raise ValueError("invalid Go DOM HTTP accounting")
        if responses and not eligible_endpoint(final_url):
            raise ValueError("invalid Go DOM HTTP final endpoint")
        attribution = current_egress_attribution()
        for _ in range(requests):
            record_origin_attempt(attribution, "direct")
        for _ in range(responses):
            record_origin_outcome(attribution, "direct", "response")
        for _ in range(requests - responses):
            record_origin_outcome(attribution, "direct", "transport_error")
        record_response_body_bytes(attribution, "direct", size)
        kind = payload.get("error_kind")
        if kind not in {None, "", "tdm", "config"}:
            raise ValueError("invalid Go DOM HTTP error classification")
        if returncode or payload.get("error"):
            if kind == "tdm":
                if payload.get("tdm_source") not in {"header", "meta"}:
                    raise ValueError("invalid Go DOM HTTP policy source")
                raise TDMReservedError(
                    final_url or url,
                    source=payload["tdm_source"],
                    policy_url=payload.get("tdm_policy") or None,
                )
            if responses:
                mark_external_response(final_url, status)
            raise RuntimeError("Go DOM HTTP fetch failed")
        if not responses:
            raise ValueError("Go DOM HTTP success requires a response")
        encoded = payload.get("body_base64")
        if not isinstance(encoded, str) or len(encoded) > ((_LIMIT + 2) // 3) * 4:
            raise ValueError("invalid Go DOM HTTP response bytes")
        body = base64.b64decode(encoded, validate=True)
        if len(body) > _LIMIT or len(body) > size:
            raise ValueError("invalid Go DOM HTTP body accounting")
        content_type = payload.get("content_type")
        if not isinstance(content_type, str) or len(content_type) > 8192:
            raise ValueError("invalid Go DOM HTTP content type")
        mark_external_response(final_url, status)
        if 200 <= status < 300:
            mark_reachable_response(final_url)
        log.info(
            "go_dom.fetch_complete",
            url=url,
            final_url=final_url,
            status=status,
            raw_body_sha256=hashlib.sha256(body).hexdigest(),
            requests=requests,
            responses=responses,
            response_bytes=size,
        )
        outcome = "success"
        return httpx.Response(
            status,
            content=body,
            headers={"Content-Type": content_type},
            request=httpx.Request("GET", final_url),
        )
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        runtime_execution_duration_seconds.labels(
            stage="fetch", implementation="go-dom-http"
        ).observe(monotonic() - started)
        runtime_executions_total.labels(
            stage="fetch", implementation="go-dom-http", outcome=outcome
        ).inc()


def eligible_endpoint(url: str) -> bool:
    """Validate protocol evidence independently of rollout configuration."""
    # The native transport enforces public DNS/IP on every redirect. Here only
    # validate its protocol envelope, without doing a second DNS/origin read.
    try:
        parts = urlsplit(url)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and parts.port in {None, 443 if parts.scheme == "https" else 80}
            and len(url) <= 8192
        )
    except (ValueError, TypeError):
        return False
