"""One bounded existing Pinpoint API response for same-input replay."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import structlog

_CAPTURE_DIR = Path("/tmp")
_TENANT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_MAX_BYTES = 16 << 20
log = structlog.get_logger()


def capture_pinpoint_response(url: str, body: bytes) -> None:
    selected = {
        item.strip()
        for item in os.environ.get("PINPOINT_CAPTURE_TENANTS", "").split(",")
        if item.strip()
    }
    if not selected or len(selected) > 4 or not all(_TENANT.fullmatch(item) for item in selected):
        return
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host.endswith(".pinpointhq.com"):
        return
    tenant = host.removesuffix(".pinpointhq.com")
    try:
        port = parsed.port
    except ValueError:
        return
    if (
        parsed.scheme != "https"
        or tenant not in selected
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != "/postings.json"
        or parsed.query
        or parsed.fragment
        or len(body) > _MAX_BYTES
    ):
        return
    path = _CAPTURE_DIR / f"jobseek-pinpoint-{tenant}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created = False
    try:
        fd = os.open(path, flags, 0o600)
        created = True
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        return
    except OSError as exc:
        if created:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        log.warning("pinpoint.capture_failed", error_type=type(exc).__name__)
    else:
        log.info(
            "pinpoint.response_captured",
            tenant=tenant,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
