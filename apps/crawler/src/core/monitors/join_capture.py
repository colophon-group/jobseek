"""Bounded text actually consumed by scheduled Python JOIN pagination."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import structlog

_CAPTURE_DIR = Path("/tmp")
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MAX_BYTES = 8 << 20
log = structlog.get_logger()


def capture_join_text(url: str, text: str) -> None:
    """Save up to four pages for up to four exact selected JOIN slugs."""
    selected = {
        item.strip() for item in os.environ.get("JOIN_CAPTURE_SLUGS", "").split(",") if item.strip()
    }
    if not selected or len(selected) > 4 or not all(_SLUG.fullmatch(item) for item in selected):
        return
    parsed = urlparse(url)
    match = re.fullmatch(r"/companies/([A-Za-z0-9_-]{1,128})/?", parsed.path)
    try:
        port = parsed.port
    except ValueError:
        return
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"join.com", "www.join.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or match is None
        or match.group(1) not in selected
    ):
        return
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"page"} or len(query.get("page", ["1"])) != 1:
        return
    raw_page = query.get("page", ["1"])[0]
    if raw_page not in {"1", "2", "3", "4"}:
        return
    body = text.encode("utf-8")
    if len(body) > _MAX_BYTES:
        return
    slug = match.group(1)
    path = _CAPTURE_DIR / f"jobseek-join-{slug}-{raw_page}.html"
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
        log.warning("join.capture_failed", error_type=type(exc).__name__)
    else:
        log.info(
            "join.response_captured",
            slug=slug,
            page=int(raw_page),
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
