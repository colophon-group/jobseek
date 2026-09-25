"""Save one existing Workable list response per page for exact offline replay."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import structlog

log = structlog.get_logger()
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_MAX_BYTES = 64 << 20
_CAPTURE_DIR = Path("/tmp")


def capture_workable_response(slug: str, page: int, url: str, body: bytes) -> None:
    selected = {
        item.strip()
        for item in os.environ.get("WORKABLE_CAPTURE_SLUGS", "").split(",")
        if item.strip()
    }
    if (
        len(selected) == 0
        or len(selected) > 4
        or any(_SLUG.fullmatch(item) is None for item in selected)
        or slug not in selected
        or not 0 <= page <= 500
        or len(body) > _MAX_BYTES
    ):
        return
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return
    if (
        parsed.scheme != "https"
        or parsed.hostname != "apply.workable.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != f"/api/v3/accounts/{slug}/jobs"
        or parsed.query
        or parsed.fragment
    ):
        return
    path = _CAPTURE_DIR / f"jobseek-workable-{slug}-page{page}.json"
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
        log.warning("workable.capture_failed", error_type=type(exc).__name__)
    else:
        log.info(
            "workable.response_captured",
            slug=slug,
            page=page,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
