"""Capture selected scheduled Workable detail responses without another request."""

from __future__ import annotations

import hashlib
import os
import re
from contextlib import suppress
from pathlib import Path

import structlog

log = structlog.get_logger()
_JOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}/[A-Za-z0-9_]{1,128}")
_CAPTURE_DIR = Path("/tmp")
_MAX_BYTES = 2 << 20


def capture_workable_detail(slug: str, shortcode: str, kind: str, body: bytes) -> None:
    selected = {
        item.strip()
        for item in os.environ.get("WORKABLE_DETAIL_CAPTURE_JOBS", "").split(",")
        if item.strip()
    }
    identity = f"{slug}/{shortcode}"
    if (
        not 1 <= len(selected) <= 4
        or any(_JOB.fullmatch(item) is None for item in selected)
        or identity not in selected
        or kind not in {"api", "markdown"}
        or len(body) > _MAX_BYTES
    ):
        return
    suffix = "json" if kind == "api" else "md"
    path = _CAPTURE_DIR / f"jobseek-workable-detail-{slug}-{shortcode}-{kind}.{suffix}"
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
        log.warning("workable.detail_capture_failed", error_type=type(exc).__name__)
    else:
        log.info(
            "workable.detail_captured",
            slug=slug,
            shortcode=shortcode,
            kind=kind,
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
