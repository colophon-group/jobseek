"""One-run capture of an already scheduled Kandou JSON-LD detail render."""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
from pathlib import Path

import structlog

log = structlog.get_logger()

_KANDOU_URL = re.compile(r"https://kandou\.bamboohr\.com/careers/([0-9]+)")
_TRACE_DIR = Path("/tmp")
_MAX_HTML_BYTES = 1_000_000
_MAX_ATTEMPTS = 2


class KandouJsonLdCapture:
    def __init__(self, url: str, job_id: str) -> None:
        self.url = url
        self.path = _TRACE_DIR / f"jobseek-kandou-jsonld-{job_id}.jsonl"
        self.records: list[dict[str, object]] = []
        self.complete = True

    def record(self, attempt: int, html: str, *, title_found: bool) -> None:
        try:
            encoded = html.encode("utf-8")
        except UnicodeError:
            self.complete = False
            log.warning("kandou.jsonld_capture_encode_failed", url=self.url)
            return
        if not self.complete or attempt > _MAX_ATTEMPTS or len(encoded) > _MAX_HTML_BYTES:
            self.complete = False
            log.warning("kandou.jsonld_capture_limit", url=self.url)
            return
        self.records.append(
            {
                "attempt": attempt,
                "html_b64": base64.b64encode(encoded).decode("ascii"),
                "title_found": title_found,
            }
        )

    def finish(self) -> None:
        if not self.complete or not self.records or self.path.exists():
            return
        partial = self.path.with_suffix(".jsonl.partial")
        try:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        except OSError:
            log.warning("kandou.jsonld_capture_open_failed", url=self.url)
            return
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                for record in (
                    {"schema": "jobseek.kandou-jsonld-replay/v1", "url": self.url},
                    *self.records,
                    {"complete": True, "attempts": len(self.records)},
                ):
                    output.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                output.flush()
                os.fsync(output.fileno())
            partial.replace(self.path)
            log.info("kandou.jsonld_capture_complete", url=self.url, attempts=len(self.records))
        except OSError:
            log.warning("kandou.jsonld_capture_write_failed", url=self.url)
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)


def start_kandou_jsonld_capture(url: str, config: dict) -> KandouJsonLdCapture | None:
    selected = os.environ.get("KANDOU_JSONLD_CAPTURE_URL")
    match = _KANDOU_URL.fullmatch(url)
    if selected != url or match is None or config.get("render") is not True:
        return None
    capture = KandouJsonLdCapture(url, match.group(1))
    if capture.path.exists() or capture.path.with_suffix(".jsonl.partial").exists():
        return None
    return capture
