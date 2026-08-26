from __future__ import annotations

import hashlib


def redact(scope: str, value: str) -> str:
    """Return the normative deterministic v1 pseudonym for a sensitive value."""

    digest = hashlib.sha256(scope.encode() + b"\0" + value.encode()).hexdigest()
    return f"redacted-sha256:{digest}"


def redact_email(scope: str, value: str) -> str:
    return f"person-{redact(scope, value).removeprefix('redacted-sha256:')}@redacted.invalid"
