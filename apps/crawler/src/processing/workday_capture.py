"""One-run response capture for an already scheduled Workday monitor.

The trace is opt-in and local to the selected board. It records only five
allowlisted response headers, no credentials or cookies, and issues no request.
Only a successful, bounded monitor cycle produces a replayable ``.jsonl``.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

_MAX_RESPONSES = 64
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 8_000_000
_TRACE_DIR = Path("/tmp")


class WorkdayCapture:
    def __init__(self, board_id: str, api_url: str, path: Path, output: Any) -> None:
        self.board_id = board_id
        self.api_url = api_url
        self.path = path
        self.output = output
        self.requests = 0
        self.responses = 0
        self.total_bytes = 0
        self.complete = True
        self._write(
            {"schema": "jobseek.workday-replay/v1", "board_id": board_id, "api_url": api_url}
        )

    def _write(self, record: dict[str, object]) -> None:
        self.output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self.output.flush()

    async def capture_request(self, request: httpx.Request) -> None:
        if request.method == "POST" and str(request.url) == self.api_url:
            self.requests += 1

    async def capture_response(self, response: httpx.Response) -> None:
        request = response.request
        if request.method != "POST" or str(request.url) != self.api_url or not self.complete:
            return
        # httpx normally buffers this POST response before the monitor parses
        # it. Reading in the hook changes neither the bytes nor request count.
        body = await response.aread()
        request_body = request.content
        if (
            self.responses >= _MAX_RESPONSES
            or len(body) > _MAX_RESPONSE_BYTES
            or self.total_bytes + len(body) + len(request_body) > _MAX_TOTAL_BYTES
        ):
            self.complete = False
            log.warning("workday.replay_capture_limit", board_id=self.board_id)
            return
        try:
            self._write(
                {
                    "sequence": self.responses,
                    "method": request.method,
                    "url": str(request.url),
                    "request_body_b64": base64.b64encode(request_body).decode("ascii"),
                    "status": response.status_code,
                    "response_body_b64": base64.b64encode(body).decode("ascii"),
                    "content_type": response.headers.get("content-type", ""),
                    "location": response.headers.get("location", ""),
                    "retry_after": response.headers.get("retry-after", ""),
                    "tdm_reservation": response.headers.get("tdm-reservation", ""),
                    "tdm_policy": response.headers.get("tdm-policy", ""),
                }
            )
        except OSError:
            self.complete = False
            log.warning("workday.replay_capture_write_failed", board_id=self.board_id)
            return
        self.responses += 1
        self.total_bytes += len(body) + len(request_body)

    def finish(self, *, monitor_succeeded: bool) -> None:
        try:
            if (
                monitor_succeeded
                and self.complete
                and self.responses
                and self.requests == self.responses
            ):
                self._write(
                    {
                        "complete": True,
                        "requests": self.requests,
                        "responses": self.responses,
                        "bytes": self.total_bytes,
                    }
                )
                os.fsync(self.output.fileno())
                self.output.close()
                self.path.replace(self.path.with_suffix(""))
                log.info(
                    "workday.replay_capture_complete",
                    board_id=self.board_id,
                    responses=self.responses,
                )
            else:
                self.output.close()
        except OSError:
            log.warning("workday.replay_capture_finish_failed", board_id=self.board_id)
            self.output.close()


def start_workday_capture(
    board_id: str, crawler_type: str, metadata: dict
) -> WorkdayCapture | None:
    if crawler_type != "workday" or os.environ.get("WORKDAY_REPLAY_CAPTURE_BOARD_ID") != board_id:
        return None
    if metadata.get("proxy") or metadata.get("skip_ssl") or not metadata.get("ssl_verify", True):
        log.warning("workday.replay_capture_unsupported_transport", board_id=board_id)
        return None
    company = metadata.get("company")
    instance = metadata.get("wd_instance")
    site = metadata.get("site")
    if not all(isinstance(part, str) and part for part in (company, instance, site)):
        log.warning("workday.replay_capture_missing_identity", board_id=board_id)
        return None
    api_url = f"https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
    path = _TRACE_DIR / f"jobseek-workday-{board_id}.jsonl.partial"
    if path.exists() or path.with_suffix("").exists():
        return None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        output = os.fdopen(descriptor, "w", encoding="utf-8")
        return WorkdayCapture(board_id, api_url, path, output)
    except OSError:
        log.warning("workday.replay_capture_open_failed", board_id=board_id)
        return None
