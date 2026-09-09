"""Narrow registry adapter for the Python sitemap benchmark image.

The production monitor registry eagerly imports every crawler monitor.  That is
appropriate for workers, but would pull Playwright and the full crawler
dependency graph into this read-only comparator.  The image still copies and
runs the production ``core.monitor`` and ``core.monitors.sitemap`` modules;
this adapter supplies only the registry surface those two modules import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_REGISTRY: dict[str, dict[str, Any]] = {}


@dataclass(slots=True)
class DiscoveredJob:
    url: str
    title: str | None = None
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    language: str | None = None
    localizations: dict | None = None
    extras: dict | None = None
    metadata: dict | None = None
    source_identity: str | None = None


def validate_explicit_source_identity(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid source identity")
    return value


def register(
    name: str,
    discover,
    cost: int,
    can_handle=None,
    *,
    rich: bool = False,
    stream=None,
    save_raw=None,
) -> None:
    del cost, can_handle, rich
    _REGISTRY[name] = {
        "discover": discover,
        "stream": stream,
        "save_raw": save_raw,
    }


def get_discoverer(name: str):
    try:
        return _REGISTRY[name]["discover"]
    except KeyError as exc:
        raise ValueError(f"unknown monitor type: {name!r}") from exc


def get_stream_fn(name: str):
    entry = _REGISTRY.get(name)
    return entry["stream"] if entry else None


def get_save_raw(name: str):
    entry = _REGISTRY.get(name)
    return entry["save_raw"] if entry else None
