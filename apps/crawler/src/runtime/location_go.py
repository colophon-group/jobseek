"""Go location matching over a private, restartable SQLite index.

The transition loader/backfill retains the worker's existing PostgreSQL pool.
Only the parent owns index lifetime; forked children cannot delete its files or
signal its process. Go failures propagate, without per-request Python fallback.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import weakref
from contextlib import suppress
from pathlib import Path

from src.runtime.job_enrichment_go import GoJobEnrichment, enabled

_indexes: weakref.WeakSet[GoLocationIndex] = weakref.WeakSet()


class GoLocationIndex:
    def __init__(self) -> None:
        self.owner_pid = os.getpid()
        self.directory = Path(tempfile.mkdtemp(prefix="jobseek-locations-"))
        self.index_path = self.directory / "locations.sqlite"
        self.client = GoJobEnrichment("/usr/local/bin/location-resolver", self.directory)
        self._negative_identity: tuple[int, int] | None = None
        _indexes.add(self)

    @staticmethod
    def enabled() -> bool:
        return enabled()

    def close(self) -> None:
        self.client.close()
        if self.owner_pid == os.getpid():
            shutil.rmtree(self.directory, ignore_errors=True)

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def resolve(self, raw, fallback, language, *, tracking, negative):
        # The public backfill cache only grows. Re-send it on growth, cache
        # replacement or process restart, rather than sorting the complete
        # negative cache for every posting. A fork/restart rehydrates it.
        identity = (id(negative), len(negative))
        changed = self.client.proc is None or identity != self._negative_identity
        result = self.client.request(
            "location_resolve",
            locations=raw,
            location_type=fallback or "",
            language=language or "",
            tracking=tracking,
            negative=sorted(negative) if changed else None,
        )
        self._negative_identity = identity
        values = result.get("locations")
        misses = result.get("lookup_misses")
        locations = result.get("location_misses")
        if (
            not isinstance(values, list)
            or not isinstance(misses, list)
            or not isinstance(locations, list)
        ):
            raise ValueError("invalid Go location response")
        resolved = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("invalid Go location value")
            lid, kind = value.get("location_id"), value.get("location_type")
            if (lid is not None and (type(lid) is not int or lid <= 0)) or kind not in {
                "onsite",
                "hybrid",
                "remote",
            }:
                raise ValueError("invalid Go location ID or type")
            resolved.append((lid, kind))
        if any(not isinstance(key, str) for key in misses):
            raise ValueError("invalid Go location lookup miss")
        location_misses = []
        for value in locations:
            if not isinstance(value, dict) or any(
                not isinstance(value.get(key), str) for key in ("raw_value", "sample_value")
            ):
                raise ValueError("invalid Go location miss")
            location_misses.append((value["raw_value"], value["sample_value"]))
        return resolved, misses, location_misses

    def ancestors(self, location_id: int) -> list[int]:
        result = self.client.request("location_ancestors", location_id=location_id)
        values = result.get("ancestors")
        if not isinstance(values, list) or any(type(lid) is not int or lid <= 0 for lid in values):
            raise ValueError("invalid Go location ancestor IDs")
        return values

    def display_name(self, location_id: int) -> str | None:
        result = self.client.request("location_display", location_id=location_id)
        if "name" not in result or (
            result["name"] is not None and not isinstance(result["name"], str)
        ):
            raise ValueError("invalid Go location display name")
        return result["name"]


def _after_fork() -> None:
    for index in list(_indexes):
        index.client.after_fork()


def _close() -> None:
    for index in list(_indexes):
        index.close()


os.register_at_fork(after_in_child=_after_fork)
atexit.register(_close)
