"""Validated, last-known-good ingestion of ats-scrapers public data files."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.ats_inventory.compat import COMPATIBILITY
from src.ats_inventory.locking import exclusive_run_lock
from src.ats_inventory.models import Coverage, InventoryRow, InventorySnapshot

DEFAULT_MANIFEST_URL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
_TRUSTED_HOST = "storage.stapply.ai"
_TRUSTED_PATH_PREFIX = "/jobhive/v1/"
_MANIFEST_LIMIT = 2 * 1024 * 1024
_INVENTORY_LIMIT = 64 * 1024 * 1024
_FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_HEADER = ("ats", "name", "slug", "url")
_STATE_VERSION = 1


class SourceValidationError(RuntimeError):
    """The new source cannot safely replace the last-known-good snapshot."""


@dataclass(frozen=True, slots=True)
class _ManifestDetails:
    generated_at: str
    inventory_url: str
    inventory_sha256: str
    inventory_size: int
    inventory_rows: int
    family_counts: dict[str, int]


class InventorySource:
    """Fetch and validate a checksum-addressed company inventory snapshot."""

    def __init__(
        self,
        cache_dir: Path,
        client: httpx.AsyncClient,
        *,
        manifest_url: str = DEFAULT_MANIFEST_URL,
        retention: int = 7,
        max_snapshot_age_days: int = 30,
        max_cache_bytes: int = 256 * 1024 * 1024,
        max_total_drop: float = 0.10,
        max_family_drop: float = 0.30,
        family_drop_min_rows: int = 20,
        minimum_candidate_coverage_pct: float = 99.0,
    ) -> None:
        if retention < 1:
            raise ValueError("retention must be at least 1")
        if max_cache_bytes <= _MANIFEST_LIMIT:
            raise ValueError("max_cache_bytes is too small")
        _assert_trusted_artifact_url(manifest_url)
        self.cache_dir = cache_dir
        self.client = client
        self.manifest_url = manifest_url
        self.retention = retention
        self.max_snapshot_age_days = max_snapshot_age_days
        self.max_cache_bytes = max_cache_bytes
        self.max_total_drop = max_total_drop
        self.max_family_drop = max_family_drop
        self.family_drop_min_rows = family_drop_min_rows
        self.minimum_candidate_coverage_pct = minimum_candidate_coverage_pct

    @property
    def current_path(self) -> Path:
        return self.cache_dir / "current.json"

    async def sync(self) -> InventorySnapshot:
        """Refresh the source, atomically publishing only a fully valid snapshot."""

        self._prepare_directories()
        with exclusive_run_lock(self.cache_dir / "source.lock"):
            return await self._sync_locked()

    async def _sync_locked(self) -> InventorySnapshot:
        recovery_state: dict[str, Any] | None = None
        try:
            previous_state = self._read_current_state()
        except SourceValidationError:
            # A damaged pointer is not useful as an LKG. Keep it in place until
            # a fully validated unconditional refresh atomically supersedes it.
            previous_state = None
        previous_etag = None if previous_state is None else previous_state.get("manifest_etag")
        manifest_bytes, manifest_etag, not_modified = await self._fetch_manifest(previous_etag)

        if not_modified:
            if previous_state is None:
                raise SourceValidationError("source returned 304 without a cached snapshot")
            try:
                snapshot = self._load_snapshot(
                    previous_state,
                    changed=False,
                    etag_revalidated=True,
                    new_families=(),
                    removed_families=(),
                    changed_urls=0,
                )
            except SourceValidationError as cache_error:
                # Do not get trapped in an endless 304 loop when a local object
                # is lost or corrupted. Refetch both published artifacts and
                # publish only after the normal full validation path succeeds.
                manifest_bytes, manifest_etag, not_modified = await self._fetch_manifest(None)
                if not_modified or manifest_bytes is None:
                    raise SourceValidationError(
                        "source returned 304 during unconditional cache recovery"
                    ) from cache_error
                recovery_state = previous_state
                previous_state = None
            else:
                previous_state["last_checked_at"] = _now_iso()
                if manifest_etag is not None:
                    previous_state["manifest_etag"] = manifest_etag
                self._write_atomic_json(self.current_path, previous_state)
                self._prune_cache(current_manifest_sha256=snapshot.manifest_sha256)
                return snapshot

        if manifest_bytes is None:  # pragma: no cover - narrowed by not_modified
            raise AssertionError("manifest response missing body")
        manifest_sha256 = _sha256_bytes(manifest_bytes)
        manifest = _decode_manifest(manifest_bytes)
        details = _validate_manifest(manifest)
        previous: InventorySnapshot | None = None
        if previous_state is not None:
            try:
                previous = self._load_snapshot(
                    previous_state,
                    changed=False,
                    etag_revalidated=False,
                    new_families=(),
                    removed_families=(),
                    changed_urls=0,
                )
            except SourceValidationError:
                # A complete newly fetched snapshot can safely repair a broken
                # local cache, but cannot use the broken state as a shrink or
                # monotonicity baseline.
                previous_state = None
            else:
                if _parse_datetime(details.generated_at) < _parse_datetime(previous.generated_at):
                    raise SourceValidationError(
                        "refusing to replace current inventory with an older manifest"
                    )
        elif recovery_state is not None:
            recovered_generated_at = _state_string(recovery_state, "generated_at")
            if _parse_datetime(details.generated_at) < _parse_datetime(recovered_generated_at):
                raise SourceValidationError(
                    "refusing to replace recovering inventory with an older manifest"
                )
        manifest_path = self._object_path(manifest_sha256)
        manifest_existed = _valid_object(
            manifest_path,
            manifest_sha256,
            expected_size=len(manifest_bytes),
            max_bytes=_MANIFEST_LIMIT,
        )
        self._store_bytes_object(manifest_sha256, manifest_bytes)

        inventory_path = self._object_path(details.inventory_sha256)
        inventory_existed = _valid_object(
            inventory_path,
            details.inventory_sha256,
            expected_size=details.inventory_size,
            max_bytes=_INVENTORY_LIMIT,
        )
        try:
            if not inventory_existed:
                await self._fetch_object(
                    details.inventory_url,
                    details.inventory_sha256,
                    details.inventory_size,
                    _INVENTORY_LIMIT,
                )

            rows, family_counts = _validate_inventory(inventory_path, details)
            previous_rows: tuple[InventoryRow, ...] = ()
            previous_counts: dict[str, int] = {}
            if previous is not None:
                previous_rows = previous.rows
                previous_counts = previous.family_counts
                self._validate_shrink(previous_counts, family_counts)
            elif recovery_state is not None:
                previous_counts = _state_family_counts(recovery_state)
                self._validate_shrink(previous_counts, family_counts)
        except Exception:
            if not inventory_existed:
                inventory_path.unlink(missing_ok=True)
            if not manifest_existed:
                manifest_path.unlink(missing_ok=True)
            raise

        coverage = _coverage(
            family_counts,
            minimum_candidate_coverage_pct=self.minimum_candidate_coverage_pct,
        )
        validated_at = _now_iso()
        baseline_state = previous_state or recovery_state
        changed = baseline_state is None or (
            baseline_state.get("manifest_sha256") != manifest_sha256
            or baseline_state.get("inventory_sha256") != details.inventory_sha256
        )
        new_families = tuple(sorted(set(family_counts) - set(previous_counts)))
        removed_families = tuple(sorted(set(previous_counts) - set(family_counts)))
        changed_urls = _count_changed_urls(previous_rows, rows) if previous_state else 0
        state: dict[str, Any] = {
            "schema_version": _STATE_VERSION,
            "manifest_sha256": manifest_sha256,
            "manifest_etag": manifest_etag,
            "inventory_sha256": details.inventory_sha256,
            "inventory_size": details.inventory_size,
            "generated_at": details.generated_at,
            "validated_at": validated_at,
            "last_checked_at": validated_at,
            "family_counts": dict(sorted(family_counts.items())),
        }

        # The live pair itself must fit before it can become current. Previous
        # snapshots may temporarily coexist and are pruned only after the
        # atomic pointer changes, so disk pressure can never destroy the LKG.
        live_bytes = len(manifest_bytes) + details.inventory_size
        metadata_bytes = len(json.dumps(state).encode()) * 2 + 4096
        if live_bytes + metadata_bytes > self.max_cache_bytes:
            if not inventory_existed:
                inventory_path.unlink(missing_ok=True)
            if not manifest_existed:
                manifest_path.unlink(missing_ok=True)
            raise SourceValidationError(
                f"validated live snapshot needs {live_bytes + metadata_bytes} bytes, "
                f"over cache limit {self.max_cache_bytes}"
            )

        snapshot_path = self.cache_dir / "snapshots" / f"{manifest_sha256}.json"
        self._write_atomic_json(snapshot_path, state)
        self._write_atomic_json(self.current_path, state)
        self._prune_cache(current_manifest_sha256=manifest_sha256)

        return InventorySnapshot(
            manifest_sha256=manifest_sha256,
            manifest_etag=manifest_etag,
            inventory_sha256=details.inventory_sha256,
            generated_at=details.generated_at,
            validated_at=validated_at,
            manifest=manifest,
            rows=rows,
            family_counts=family_counts,
            coverage=coverage,
            changed=changed,
            etag_revalidated=False,
            new_families=new_families,
            removed_families=removed_families,
            changed_urls=changed_urls,
        )

    def load_current(self) -> InventorySnapshot:
        """Load and revalidate the current snapshot without network access."""

        state = self._read_current_state()
        if state is None:
            raise SourceValidationError("no current inventory snapshot")
        return self._load_snapshot(
            state,
            changed=False,
            etag_revalidated=False,
            new_families=(),
            removed_families=(),
            changed_urls=0,
        )

    def _prepare_directories(self) -> None:
        (self.cache_dir / "objects").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "snapshots").mkdir(parents=True, exist_ok=True)

    def _object_path(self, digest: str) -> Path:
        if not _SHA256_RE.fullmatch(digest):
            raise SourceValidationError("invalid object checksum")
        return self.cache_dir / "objects" / digest

    async def _fetch_manifest(self, etag: object) -> tuple[bytes | None, str | None, bool]:
        headers = {"Accept": "application/json"}
        if etag is not None:
            if not isinstance(etag, str) or not _safe_etag(etag):
                raise SourceValidationError("cached manifest ETag is invalid")
            headers["If-None-Match"] = etag
        async with self.client.stream("GET", self.manifest_url, headers=headers) as response:
            _assert_trusted_artifact_url(str(response.url))
            if response.status_code == 304:
                response_etag = response.headers.get("etag")
                if response_etag is not None and not _safe_etag(response_etag):
                    raise SourceValidationError("manifest ETag is invalid")
                return (
                    None,
                    response_etag or (etag if isinstance(etag, str) else None),
                    True,
                )
            response.raise_for_status()
            body = await _read_bounded_response(response, _MANIFEST_LIMIT)
            response_etag = response.headers.get("etag")
            if response_etag is not None and not _safe_etag(response_etag):
                raise SourceValidationError("manifest ETag is invalid")
            return body, response_etag, False

    async def _fetch_object(
        self,
        url: str,
        expected_sha256: str,
        expected_size: int,
        max_bytes: int,
    ) -> Path:
        _assert_trusted_artifact_url(url)
        objects_dir = self.cache_dir / "objects"
        fd, raw_temp_path = tempfile.mkstemp(prefix="download-", dir=objects_dir)
        temp_path = Path(raw_temp_path)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output:
                async with self.client.stream(
                    "GET", url, headers={"Accept": "text/csv"}
                ) as response:
                    response.raise_for_status()
                    _assert_trusted_artifact_url(str(response.url))
                    declared_size = _content_length(response)
                    if declared_size is not None and declared_size > max_bytes:
                        raise SourceValidationError("artifact Content-Length exceeds byte limit")
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise SourceValidationError("artifact exceeds byte limit")
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != expected_size:
                raise SourceValidationError(
                    f"artifact size mismatch: expected {expected_size}, received {size}"
                )
            if digest.hexdigest() != expected_sha256:
                raise SourceValidationError("artifact SHA-256 mismatch")
            target = self._object_path(expected_sha256)
            os.replace(temp_path, target)
            _fsync_directory(objects_dir)
            return target
        finally:
            temp_path.unlink(missing_ok=True)

    def _store_bytes_object(self, digest: str, body: bytes) -> Path:
        target = self._object_path(digest)
        if _valid_object(target, digest, expected_size=len(body), max_bytes=_MANIFEST_LIMIT):
            return target
        self._write_atomic_bytes(target, body)
        return target

    def _read_current_state(self) -> dict[str, Any] | None:
        if not self.current_path.exists():
            return None
        try:
            value = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceValidationError("current cache pointer is corrupt") from exc
        if not isinstance(value, dict) or value.get("schema_version") != _STATE_VERSION:
            raise SourceValidationError("current cache pointer has an unsupported schema")
        return value

    def _load_snapshot(
        self,
        state: dict[str, Any],
        *,
        changed: bool,
        etag_revalidated: bool,
        new_families: tuple[str, ...],
        removed_families: tuple[str, ...],
        changed_urls: int,
    ) -> InventorySnapshot:
        manifest_sha256 = _state_digest(state, "manifest_sha256")
        inventory_sha256 = _state_digest(state, "inventory_sha256")
        manifest_path = self._object_path(manifest_sha256)
        manifest_bytes = _read_valid_object(manifest_path, manifest_sha256, _MANIFEST_LIMIT)
        manifest = _decode_manifest(manifest_bytes)
        details = _validate_manifest(manifest)
        if details.inventory_sha256 != inventory_sha256:
            raise SourceValidationError("current pointer does not match manifest inventory")
        if state.get("generated_at") != details.generated_at:
            raise SourceValidationError("current pointer generated_at does not match manifest")
        inventory_path = self._object_path(inventory_sha256)
        if not _valid_object(
            inventory_path,
            inventory_sha256,
            expected_size=details.inventory_size,
            max_bytes=_INVENTORY_LIMIT,
        ):
            raise SourceValidationError("current inventory cache object is corrupt")
        rows, family_counts = _validate_inventory(inventory_path, details)
        expected_counts = state.get("family_counts")
        if expected_counts != dict(sorted(family_counts.items())):
            raise SourceValidationError("current family counts do not match cached inventory")
        coverage = _coverage(
            family_counts,
            minimum_candidate_coverage_pct=self.minimum_candidate_coverage_pct,
        )
        return InventorySnapshot(
            manifest_sha256=manifest_sha256,
            manifest_etag=state.get("manifest_etag")
            if isinstance(state.get("manifest_etag"), str)
            else None,
            inventory_sha256=inventory_sha256,
            generated_at=details.generated_at,
            validated_at=_state_string(state, "validated_at"),
            manifest=manifest,
            rows=rows,
            family_counts=family_counts,
            coverage=coverage,
            changed=changed,
            etag_revalidated=etag_revalidated,
            new_families=new_families,
            removed_families=removed_families,
            changed_urls=changed_urls,
        )

    def _validate_shrink(
        self,
        previous: dict[str, int],
        current: dict[str, int],
    ) -> None:
        previous_total = sum(previous.values())
        current_total = sum(current.values())
        if previous_total and current_total < previous_total * (1 - self.max_total_drop):
            raise SourceValidationError(
                f"inventory shrank unexpectedly from {previous_total} to {current_total} rows"
            )
        for family, old_count in previous.items():
            if old_count < self.family_drop_min_rows:
                continue
            new_count = current.get(family, 0)
            if new_count < old_count * (1 - self.max_family_drop):
                raise SourceValidationError(
                    f"inventory family {family!r} shrank unexpectedly from "
                    f"{old_count} to {new_count} rows"
                )

    def _write_atomic_json(self, target: Path, value: dict[str, Any]) -> None:
        body = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        self._write_atomic_bytes(target, body)

    def _write_atomic_bytes(self, target: Path, body: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_temp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(raw_temp_path)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, target)
            _fsync_directory(target.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def _prune_cache(self, *, current_manifest_sha256: str) -> None:
        snapshot_dir = self.cache_dir / "snapshots"
        for path in self.cache_dir.glob(".current.json.*"):
            path.unlink(missing_ok=True)
        for path in snapshot_dir.glob(".*.json.*"):
            path.unlink(missing_ok=True)
        now = datetime.now(UTC)
        candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
        for path in snapshot_dir.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                validated_at = _parse_datetime(_state_string(state, "validated_at"))
                _state_digest(state, "manifest_sha256")
                _state_digest(state, "inventory_sha256")
            except (OSError, json.JSONDecodeError, SourceValidationError):
                path.unlink(missing_ok=True)
                continue
            candidates.append((validated_at, path, state))
        candidates.sort(key=lambda item: item[0], reverse=True)

        keep: list[tuple[datetime, Path, dict[str, Any]]] = []
        cutoff = now - timedelta(days=self.max_snapshot_age_days)
        for item in candidates:
            is_current = item[2].get("manifest_sha256") == current_manifest_sha256
            if is_current or (len(keep) < self.retention and item[0] >= cutoff):
                keep.append(item)
            else:
                item[1].unlink(missing_ok=True)

        referenced = {
            digest
            for _validated_at, _path, state in keep
            for digest in (state.get("manifest_sha256"), state.get("inventory_sha256"))
            if isinstance(digest, str) and _SHA256_RE.fullmatch(digest)
        }
        for path in (self.cache_dir / "objects").iterdir():
            if path.is_file() and path.name not in referenced:
                path.unlink(missing_ok=True)

        # Retention normally keeps usage far below the ceiling. Under unusual
        # churn, discard oldest non-current snapshots until it is bounded.
        while _directory_size(self.cache_dir) > self.max_cache_bytes:
            removable = [
                item for item in keep if item[2].get("manifest_sha256") != current_manifest_sha256
            ]
            if not removable:
                break
            oldest = min(removable, key=lambda item: item[0])
            oldest[1].unlink(missing_ok=True)
            keep.remove(oldest)
            referenced = {
                digest
                for _validated_at, _path, state in keep
                for digest in (state.get("manifest_sha256"), state.get("inventory_sha256"))
                if isinstance(digest, str) and _SHA256_RE.fullmatch(digest)
            }
            for path in (self.cache_dir / "objects").iterdir():
                if path.is_file() and path.name not in referenced:
                    path.unlink(missing_ok=True)


def _validate_manifest(manifest: dict[str, Any]) -> _ManifestDetails:
    if manifest.get("version") != "2.0":
        raise SourceValidationError("unsupported manifest version")
    generated_at = manifest.get("generated_at")
    if not isinstance(generated_at, str):
        raise SourceValidationError("manifest generated_at is missing")
    _parse_datetime(generated_at)
    companies = manifest.get("companies")
    by_family = manifest.get("by_ats_companies")
    stats = manifest.get("stats")
    if not isinstance(companies, dict) or not isinstance(by_family, dict):
        raise SourceValidationError("manifest company artifacts are missing")
    if not isinstance(stats, dict):
        raise SourceValidationError("manifest stats are missing")
    inventory_url = _manifest_url(companies, "csv")
    inventory_sha256 = _manifest_digest(companies, "sha256")
    inventory_size = _manifest_int(companies, "size_bytes", minimum=1)
    inventory_rows = _manifest_int(companies, "rows", minimum=1)
    if inventory_size > _INVENTORY_LIMIT:
        raise SourceValidationError("company inventory exceeds byte limit")

    family_counts: dict[str, int] = {}
    for family, artifact in by_family.items():
        if not isinstance(family, str) or not _FAMILY_RE.fullmatch(family):
            raise SourceValidationError(f"invalid inventory family {family!r}")
        if not isinstance(artifact, dict):
            raise SourceValidationError(f"invalid artifact for family {family!r}")
        _manifest_url(artifact, "csv")
        _manifest_digest(artifact, "sha256")
        _manifest_int(artifact, "size_bytes", minimum=1)
        family_counts[family] = _manifest_int(artifact, "rows", minimum=1)
    if sum(family_counts.values()) != inventory_rows:
        raise SourceValidationError("per-family row counts do not match aggregate inventory")
    if stats.get("total_companies") != inventory_rows:
        raise SourceValidationError("manifest stats total_companies does not match inventory")

    # Later ranking stages consume by_ats from this validated manifest. Validate
    # its trust-sensitive artifact references now without downloading them.
    by_ats = manifest.get("by_ats")
    if not isinstance(by_ats, dict):
        raise SourceValidationError("manifest by_ats artifacts are missing")
    for family, artifact in by_ats.items():
        if not isinstance(family, str) or not _FAMILY_RE.fullmatch(family):
            raise SourceValidationError(f"invalid job artifact family {family!r}")
        if not isinstance(artifact, dict):
            raise SourceValidationError(f"invalid job artifact for family {family!r}")
        _manifest_int(artifact, "rows", minimum=0)
        for url_key, digest_key, size_key in (
            ("csv", "sha256", "size_bytes"),
            ("parquet", "parquet_sha256", "parquet_size_bytes"),
        ):
            if url_key in artifact:
                _manifest_url(artifact, url_key)
                _manifest_digest(artifact, digest_key)
                _manifest_int(artifact, size_key, minimum=0)

    return _ManifestDetails(
        generated_at=generated_at,
        inventory_url=inventory_url,
        inventory_sha256=inventory_sha256,
        inventory_size=inventory_size,
        inventory_rows=inventory_rows,
        family_counts=family_counts,
    )


def _validate_inventory(
    path: Path, details: _ManifestDetails
) -> tuple[tuple[InventoryRow, ...], dict[str, int]]:
    rows: list[InventoryRow] = []
    family_counts: Counter[str] = Counter()
    seen_urls: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, strict=True)
            if tuple(reader.fieldnames or ()) != _EXPECTED_HEADER:
                raise SourceValidationError(
                    f"company inventory header must be {','.join(_EXPECTED_HEADER)}"
                )
            for line_number, raw in enumerate(reader, start=2):
                if None in raw or set(raw) != set(_EXPECTED_HEADER):
                    raise SourceValidationError(f"invalid CSV shape on line {line_number}")
                ats = _clean_field(raw["ats"], "ats", line_number, max_length=64)
                name = _clean_field(
                    raw["name"],
                    "name",
                    line_number,
                    max_length=500,
                    trim=True,
                )
                slug = _optional_clean_field(raw["slug"], "slug", line_number, max_length=500)
                url = _clean_field(raw["url"], "url", line_number, max_length=4096)
                if not _FAMILY_RE.fullmatch(ats):
                    raise SourceValidationError(f"invalid ATS family on line {line_number}")
                if ats not in details.family_counts:
                    raise SourceValidationError(
                        f"unlisted ATS family {ats!r} on line {line_number}"
                    )
                normalized_url = _normalize_inventory_url(url, line_number)
                if normalized_url in seen_urls:
                    raise SourceValidationError(f"duplicate normalized URL on line {line_number}")
                seen_urls.add(normalized_url)
                rows.append(InventoryRow(ats=ats, name=name, slug=slug, url=url))
                family_counts[ats] += 1
                if len(rows) > details.inventory_rows:
                    raise SourceValidationError("company inventory has more rows than declared")
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise SourceValidationError("company inventory CSV is unreadable") from exc
    if len(rows) != details.inventory_rows:
        raise SourceValidationError(
            f"company row count mismatch: expected {details.inventory_rows}, got {len(rows)}"
        )
    if dict(family_counts) != details.family_counts:
        raise SourceValidationError("company family row counts do not match manifest")
    return tuple(rows), dict(family_counts)


def _coverage(family_counts: dict[str, int], *, minimum_candidate_coverage_pct: float) -> Coverage:
    total_rows = sum(family_counts.values())
    unsupported_families = tuple(sorted(set(family_counts) - set(COMPATIBILITY)))
    excluded_families = tuple(
        sorted(
            family
            for family in family_counts
            if (compat := COMPATIBILITY.get(family)) is not None and not compat.candidate_eligible
        )
    )
    unsupported_rows = sum(family_counts[family] for family in unsupported_families)
    excluded_rows = sum(family_counts[family] for family in excluded_families)
    supported_rows = total_rows - unsupported_rows - excluded_rows
    candidate_rows = total_rows - excluded_rows
    candidate_pct = 100.0 if not candidate_rows else supported_rows / candidate_rows * 100
    classified_pct = 100.0 if not total_rows else (total_rows - unsupported_rows) / total_rows * 100
    return Coverage(
        total_rows=total_rows,
        supported_rows=supported_rows,
        unsupported_rows=unsupported_rows,
        excluded_rows=excluded_rows,
        candidate_rows=candidate_rows,
        supported_candidate_rows=supported_rows,
        classified_coverage_pct=round(classified_pct, 6),
        candidate_coverage_pct=round(candidate_pct, 6),
        unsupported_families=unsupported_families,
        excluded_families=excluded_families,
        candidate_generation_allowed=candidate_pct >= minimum_candidate_coverage_pct,
    )


def _count_changed_urls(
    previous: tuple[InventoryRow, ...], current: tuple[InventoryRow, ...]
) -> int:
    def grouped(rows: tuple[InventoryRow, ...]) -> dict[tuple[str, str], set[str]]:
        values: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in rows:
            identity = f"slug:{row.slug}" if row.slug else f"name:{row.name.casefold()}"
            values[(row.ats, identity)].add(row.url)
        return values

    old = grouped(previous)
    new = grouped(current)
    return sum(old[key] != new[key] for key in old.keys() & new.keys())


def _decode_manifest(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceValidationError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SourceValidationError("manifest root must be an object")
    return value


async def _read_bounded_response(response: httpx.Response, max_bytes: int) -> bytes:
    declared_size = _content_length(response)
    if declared_size is not None and declared_size > max_bytes:
        raise SourceValidationError("response Content-Length exceeds byte limit")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise SourceValidationError("response exceeds byte limit")
    return bytes(body)


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise SourceValidationError("invalid Content-Length") from exc
    if value < 0:
        raise SourceValidationError("invalid Content-Length")
    return value


def _assert_trusted_artifact_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError("invalid artifact URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != _TRUSTED_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(_TRUSTED_PATH_PREFIX)
    ):
        raise SourceValidationError(f"untrusted artifact URL: {url!r}")


def _normalize_inventory_url(url: str, line_number: int) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError(f"invalid company URL on line {line_number}") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SourceValidationError(f"unsafe company URL on line {line_number}")
    host = parsed.hostname.lower().rstrip(".")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, parsed.query, ""))


def _clean_field(
    value: object,
    field: str,
    line: int,
    *,
    max_length: int,
    trim: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SourceValidationError(f"invalid {field} on line {line}")
    cleaned = value.strip() if trim else value
    if not cleaned or (not trim and cleaned != value):
        raise SourceValidationError(f"invalid {field} on line {line}")
    if len(cleaned) > max_length or any(ord(char) < 32 for char in cleaned):
        raise SourceValidationError(f"invalid {field} on line {line}")
    return cleaned


def _optional_clean_field(value: object, field: str, line: int, *, max_length: int) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise SourceValidationError(f"invalid {field} on line {line}")
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        raise SourceValidationError(f"invalid {field} on line {line}")
    return value


def _safe_etag(value: str) -> bool:
    return bool(value) and len(value) <= 200 and all(32 <= ord(char) < 127 for char in value)


def _manifest_url(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str):
        raise SourceValidationError(f"manifest artifact is missing {key}")
    _assert_trusted_artifact_url(value)
    return value


def _manifest_digest(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SourceValidationError(f"manifest artifact has invalid {key}")
    return value


def _manifest_int(container: dict[str, Any], key: str, *, minimum: int) -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceValidationError(f"manifest artifact has invalid {key}")
    return value


def _state_digest(state: dict[str, Any], key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SourceValidationError(f"current pointer has invalid {key}")
    return value


def _state_string(state: dict[str, Any], key: str) -> str:
    value = state.get(key)
    if not isinstance(value, str):
        raise SourceValidationError(f"current pointer has invalid {key}")
    return value


def _state_family_counts(state: dict[str, Any]) -> dict[str, int]:
    value = state.get("family_counts")
    if not isinstance(value, dict):
        raise SourceValidationError("current pointer has invalid family_counts")
    result: dict[str, int] = {}
    for family, count in value.items():
        if (
            not isinstance(family, str)
            or not _FAMILY_RE.fullmatch(family)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
        ):
            raise SourceValidationError("current pointer has invalid family_counts")
        result[family] = count
    return result


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceValidationError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise SourceValidationError(f"timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)


def _read_valid_object(path: Path, digest: str, max_bytes: int) -> bytes:
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise SourceValidationError(f"cached object {digest} is missing") from exc
    if len(body) > max_bytes or _sha256_bytes(body) != digest:
        raise SourceValidationError(f"cached object {digest} is corrupt")
    return body


def _valid_object(path: Path, digest: str, *, expected_size: int, max_bytes: int) -> bool:
    try:
        if path.stat().st_size != expected_size or expected_size > max_bytes:
            return False
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest() == digest
    except OSError:
        return False


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total
