"""Import-pure validation for the fixed Lightpanda B0 route identity.

This module deliberately depends only on the Python standard library.  The
networkless dark claimant imports it to prove its future Redis namespace and
route without importing the Redis adapter itself.
"""

from __future__ import annotations

import re
from typing import Final

_SAFE_IDENTIFIER_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_NAMESPACE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_lightpanda_b0_identifier(value: object, name: str) -> str:
    """Return one canonical queue identifier without importing a client."""

    if not isinstance(value, str) or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be 1-128 safe characters")
    return value


def validate_lightpanda_b0_namespace(namespace: object) -> str:
    """Return the canonical Redis hash-tag namespace without importing Redis."""

    if not isinstance(namespace, str) or not _SAFE_NAMESPACE_RE.fullmatch(namespace):
        raise ValueError("namespace must be 1-64 safe key characters")
    return namespace


def validate_lightpanda_b0_shard_id(shard_id: object) -> str:
    """Return the canonical B0 shard identifier without importing Redis."""

    return validate_lightpanda_b0_identifier(shard_id, "shard_id")


__all__ = [
    "validate_lightpanda_b0_identifier",
    "validate_lightpanda_b0_namespace",
    "validate_lightpanda_b0_shard_id",
]
