"""Shared Paylocity Recruiting hostname validation."""

from __future__ import annotations


def is_paylocity_recruiting_host(hostname: str | None) -> bool:
    """Return whether *hostname* is a Paylocity public recruiting host.

    Paylocity uses both ``recruiting.paylocity.com`` and shard labels such as
    ``2000recruiting.paylocity.com``.  Compare DNS labels explicitly so the
    vendor name cannot merely appear at an arbitrary position in another
    hostname.
    """
    labels = (hostname or "").lower().rstrip(".").split(".")
    return (
        len(labels) == 3 and labels[1:] == ["paylocity", "com"] and labels[0].endswith("recruiting")
    )
