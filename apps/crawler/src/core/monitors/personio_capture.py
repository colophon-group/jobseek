"""Save exact bytes from one naturally scheduled Personio XML response per language."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path

import structlog

log = structlog.get_logger()
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LANGUAGE = re.compile(r"[a-z]{2}")
_CAPTURE_DIR = Path("/tmp")
_MAX_BYTES = 128 << 20


def capture_personio_response(slug: str, domain: str, language: str, body: bytes) -> None:
    """Capture without issuing another origin request or changing monitor output."""
    selected = os.environ.get("PERSONIO_CAPTURE_SLUG", "").strip()
    if (
        not selected
        or selected != slug
        or not _SLUG.fullmatch(selected)
        or domain not in {"de", "com"}
        or not _LANGUAGE.fullmatch(language)
        or len(body) > _MAX_BYTES
    ):
        return
    suffix = hashlib.sha256(slug.encode()).hexdigest()[:16]
    path = _CAPTURE_DIR / f"jobseek-personio-{suffix}-{domain}-{language}.xml"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        log.warning("personio.capture_failed", slug=slug, error_type=type(exc).__name__)
        return
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        log.info(
            "personio.response_captured",
            slug=slug,
            domain=domain,
            language=language,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
    except OSError as exc:
        with suppress(OSError):
            path.unlink(missing_ok=True)
        log.warning("personio.capture_failed", slug=slug, error_type=type(exc).__name__)
