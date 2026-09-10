"""Inactive Redis queue-v2 candidate.

This package is intentionally disconnected from the crawler CLI and workers.
"""

from __future__ import annotations

from .client import (
    ClaimResult,
    Decision,
    Fence,
    LeaseHandle,
    QueueV2Candidate,
    RouteIdentity,
    TransitionResult,
)

__all__ = [
    "ClaimResult",
    "Decision",
    "Fence",
    "LeaseHandle",
    "QueueV2Candidate",
    "RouteIdentity",
    "TransitionResult",
]
