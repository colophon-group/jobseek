"""Native extraction from an already fetched DOM detail document."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile
from contextlib import suppress
from time import monotonic

import structlog

from src.core.job_content import JobContent
from src.metrics import runtime_execution_duration_seconds, runtime_executions_total
from src.runtime.jsonld_go_detail import _content, _json_keys

log = structlog.get_logger()
_BINARY = "/usr/local/bin/dom-detail-parse"
_LIMIT = 64 << 20
_TIMEOUT = 45
_CAPTURE_PREFIX = "/tmp/jobseek-dom-go-parse"


def enabled() -> bool:
    """The installed extractor owns DOM parsing unless explicitly reversed."""
    return os.environ.get("DOM_GO_PARSE_ENABLED", "1") == "1"


def _request(html: str, config: dict, url: str | None) -> bytes:
    request = {"mode": "parse", "html": html, "config": config}
    if url is not None:
        request["url"] = url
    _json_keys(request)
    body = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > _LIMIT:
        raise ValueError("DOM extraction input exceeds limit")
    return body


def _decode(body: bytes, returncode: int) -> JobContent:
    if returncode:
        raise ValueError("Go DOM extraction failed")
    if len(body) > _LIMIT:
        raise ValueError("DOM extraction output exceeds limit")
    return _content(json.loads(body))


def parse_html(html: str, config: dict, *, binary: str | None = None) -> JobContent:
    """Synchronous, offline parser for previews and linked detail HTML."""
    body = _request(html, config, None)
    # Use a private temporary file so a defective child cannot allocate
    # unbounded parent memory before the output bound is checked.
    with tempfile.TemporaryFile() as output:
        child = subprocess.run(
            [binary or _BINARY],
            input=body,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=_TIMEOUT,
        )
        output.seek(0)
        return _decode(output.read(_LIMIT + 1), child.returncode)


def _capture(body: bytes) -> None:
    """Retain at most eight small normal inputs per worker; never fetch again."""
    if len(body) > 2 << 20:
        return
    for slot in range(8):
        path = f"{_CAPTURE_PREFIX}-{slot}.json"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        return


async def parse_fetched_html(
    html: str, config: dict, url: str, *, binary: str | None = None
) -> JobContent:
    """Parse the normal transport's exact document without origin I/O."""
    body = _request(html, config, url)
    started, outcome = monotonic(), "error"
    child = None
    try:
        async with asyncio.timeout(_TIMEOUT):
            child = await asyncio.create_subprocess_exec(
                binary or _BINARY,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert child.stdin is not None and child.stdout is not None

            async def write() -> None:
                assert child is not None and child.stdin is not None
                try:
                    child.stdin.write(body)
                    await child.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    child.stdin.close()

            writer = asyncio.create_task(write())
            try:
                chunks, size = [], 0
                while chunk := await child.stdout.read(min(1 << 20, _LIMIT + 1 - size)):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > _LIMIT:
                        raise ValueError("DOM extraction output exceeds limit")
                await writer
                returncode = await child.wait()
            finally:
                if not writer.done():
                    writer.cancel()
                    with suppress(asyncio.CancelledError):
                        await writer
            content = _decode(b"".join(chunks), returncode)
        fields_hash = hashlib.sha256(
            json.dumps(
                dataclasses.asdict(content),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with suppress(OSError):
            _capture(body)
        log.info(
            "go_dom.parse_complete",
            url=url,
            html_sha256=hashlib.sha256(html.encode()).hexdigest(),
            fields_sha256=fields_hash,
        )
        outcome = "success"
        return content
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        if child is not None and child.returncode is None:
            with suppress(ProcessLookupError):
                child.terminate()
            try:
                await asyncio.wait_for(child.wait(), 5)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    child.kill()
                await child.wait()
        runtime_execution_duration_seconds.labels(stage="extract", implementation="go-dom").observe(
            monotonic() - started
        )
        runtime_executions_total.labels(
            stage="extract", implementation="go-dom", outcome=outcome
        ).inc()
