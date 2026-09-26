"""Passively save one normal Teamtailor page-zero response for exact replay."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import structlog

_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_MAX_BYTES = 32 << 20
_CAPTURE_DIR = Path("/tmp")
log = structlog.get_logger()


class TeamtailorCapture:
    def __init__(self, *, host: str, path: Path, fd: int) -> None:
        self.host = host
        self.path = path
        self._output = os.fdopen(fd, "wb")
        self._digest = hashlib.sha256()
        self._bytes = 0
        self._committed = False

    def write(self, chunk: bytes) -> None:
        if self._bytes + len(chunk) > _MAX_BYTES:
            raise ValueError("Teamtailor capture exceeded 32 MiB")
        self._output.write(chunk)
        self._digest.update(chunk)
        self._bytes += len(chunk)

    def commit(self) -> None:
        self._output.flush()
        os.fsync(self._output.fileno())
        self._output.close()
        self._committed = True
        log.info(
            "teamtailor_rss.response_captured",
            host=self.host,
            bytes=self._bytes,
            sha256=self._digest.hexdigest(),
        )

    def discard(self) -> None:
        with suppress(OSError):
            self._output.close()
        if not self._committed:
            with suppress(OSError):
                self.path.unlink(missing_ok=True)


def open_teamtailor_capture(feed_url: str) -> TeamtailorCapture | None:
    selected = os.environ.get("TEAMTAILOR_RSS_CAPTURE_HOST", "").strip()
    if not selected or not _HOST.fullmatch(selected):
        return None
    parsed = urlparse(feed_url)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != selected
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.endswith("/jobs.rss")
        or parsed.fragment
        or parse_qs(parsed.query) != {"offset": ["0"], "per_page": ["100"]}
    ):
        return None
    suffix = hashlib.sha256(selected.encode()).hexdigest()[:16]
    path = _CAPTURE_DIR / f"jobseek-teamtailor-{suffix}-page0.rss"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return None
    except OSError as exc:
        log.warning("teamtailor_rss.capture_failed", error_type=type(exc).__name__)
        return None
    return TeamtailorCapture(host=selected, path=path, fd=fd)
