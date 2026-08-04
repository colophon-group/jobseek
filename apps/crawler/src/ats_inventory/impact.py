"""Bounded, last-known-good impact derivation from published job artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from src.ats_inventory.compat import compatibility_for
from src.ats_inventory.locking import exclusive_run_lock
from src.ats_inventory.models import CompanyImpact, ImpactSnapshot, InventoryRow, InventorySnapshot
from src.ats_inventory.source import assert_trusted_artifact_url
from src.ats_inventory.tenant_keys import normalize_identity, tenant_key

_STATE_VERSION = 1
_ALGORITHM_VERSION = 3
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCATION_HASH_RE = re.compile(r"^[0-9a-f]{16}$")
_REQUIRED_COLUMNS = frozenset(
    {"url", "company", "ats_id", "location", "country_iso", "is_remote", "posted_at"}
)
_DIRECT_TENANT_COLUMN = "tenant_key"
_MAX_DERIVED_OBJECT_BYTES = 128 * 1024 * 1024
_MAX_LOCATION_IDENTITIES = 5_000
_MAX_COUNTRY_CODES = 250


class ImpactValidationError(RuntimeError):
    """A new impact input cannot safely replace the last-known-good snapshot."""


@dataclass(frozen=True, slots=True)
class _Artifact:
    family: str
    url: str
    sha256: str
    size: int
    rows: int


@dataclass(frozen=True, slots=True)
class _Bucket:
    kind: str
    key: str
    name_keys: tuple[str, ...]
    active_jobs: int
    remote_jobs: int
    location_hashes: tuple[str, ...]
    country_codes: tuple[str, ...]
    latest_posted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "name_keys": list(self.name_keys),
            "active_jobs": self.active_jobs,
            "remote_jobs": self.remote_jobs,
            "location_hashes": list(self.location_hashes),
            "country_codes": list(self.country_codes),
            "latest_posted_at": self.latest_posted_at,
        }


@dataclass(slots=True)
class _MutableBucket:
    kind: str
    key: str
    active_jobs: int = 0
    remote_jobs: int = 0
    names: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    latest_posted_at: str | None = None

    def add(
        self,
        *,
        name: object,
        location: object,
        country: object,
        is_remote: object,
        posted_at: object,
    ) -> None:
        self.active_jobs += 1
        if is_remote is True:
            self.remote_jobs += 1
        if (name_key := normalize_identity(name)) is not None:
            self.names.add(name_key)
        if (
            len(self.locations) < _MAX_LOCATION_IDENTITIES
            and (location_key := normalize_identity(location, max_length=1_000)) is not None
        ):
            self.locations.add(location_key)
        if len(self.countries) < _MAX_COUNTRY_CODES:
            country_code = normalize_identity(country, max_length=2)
            if country_code is not None and len(country_code) == 2 and country_code.isalpha():
                self.countries.add(country_code.upper())
        if posted_at is not None:
            value = str(posted_at).strip()
            if (
                value
                and len(value) <= 100
                and (self.latest_posted_at is None or value > self.latest_posted_at)
            ):
                self.latest_posted_at = value

    def freeze(self) -> _Bucket:
        location_hashes = tuple(
            sorted(
                hashlib.sha256(f"location\0{location}".encode()).hexdigest()[:16]
                for location in self.locations
            )
        )
        return _Bucket(
            kind=self.kind,
            key=self.key,
            name_keys=tuple(sorted(self.names)),
            active_jobs=self.active_jobs,
            remote_jobs=self.remote_jobs,
            location_hashes=location_hashes,
            country_codes=tuple(sorted(self.countries)),
            latest_posted_at=self.latest_posted_at,
        )


@dataclass(frozen=True, slots=True)
class _FamilySummary:
    family: str
    artifact_sha256: str
    artifact_size: int
    artifact_rows: int
    derived_at: str
    buckets: tuple[_Bucket, ...]

    def to_dict(self) -> dict[str, Any]:
        keyed_jobs = sum(
            bucket.active_jobs for bucket in self.buckets if bucket.kind != "unmatched"
        )
        return {
            "schema_version": _STATE_VERSION,
            "algorithm_version": _ALGORITHM_VERSION,
            "family": self.family,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size": self.artifact_size,
            "artifact_rows": self.artifact_rows,
            "derived_at": self.derived_at,
            "bucket_count": len(self.buckets),
            "keyed_jobs": keyed_jobs,
            "unkeyed_jobs": self.artifact_rows - keyed_jobs,
            "buckets": [bucket.to_dict() for bucket in self.buckets],
        }


@dataclass(slots=True)
class _CompanyAccumulator:
    active_jobs: int = 0
    remote_jobs: int = 0
    locations: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    latest_posted_at: str | None = None

    def add(self, bucket: _Bucket) -> None:
        self.active_jobs += bucket.active_jobs
        self.remote_jobs += bucket.remote_jobs
        self.locations.update(bucket.location_hashes)
        self.countries.update(bucket.country_codes)
        if bucket.latest_posted_at is not None and (
            self.latest_posted_at is None or bucket.latest_posted_at > self.latest_posted_at
        ):
            self.latest_posted_at = bucket.latest_posted_at


class ImpactCache:
    """Derive a compact snapshot while never executing upstream scraper code."""

    def __init__(
        self,
        cache_dir: Path,
        client: httpx.AsyncClient,
        *,
        max_cache_bytes: int = 768 * 1024 * 1024,
        max_artifact_bytes: int = 512 * 1024 * 1024,
        min_free_bytes: int = 512 * 1024 * 1024,
        retention: int = 3,
        max_age_days: int = 30,
    ) -> None:
        if max_cache_bytes < 1024 * 1024:
            raise ValueError("max_cache_bytes must be at least 1 MiB")
        if max_artifact_bytes < 1 or max_artifact_bytes > max_cache_bytes:
            raise ValueError("max_artifact_bytes must fit within max_cache_bytes")
        if min_free_bytes < 0:
            raise ValueError("min_free_bytes cannot be negative")
        if retention < 1:
            raise ValueError("retention must be at least 1")
        self.cache_dir = cache_dir
        self.client = client
        self.max_cache_bytes = max_cache_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.min_free_bytes = min_free_bytes
        self.retention = retention
        self.max_age_days = max_age_days

    @property
    def current_path(self) -> Path:
        return self.cache_dir / "current.json"

    async def sync(self, inventory: InventorySnapshot) -> ImpactSnapshot:
        """Refresh changed families and atomically publish a complete snapshot."""

        self._prepare_directories()
        with exclusive_run_lock(self.cache_dir / "impact.lock"):
            return await self._sync_locked(inventory)

    async def _sync_locked(self, inventory: InventorySnapshot) -> ImpactSnapshot:
        family_rows = _eligible_rows(inventory.rows)
        artifacts = {
            family: _job_artifact(inventory.manifest, family) for family in sorted(family_rows)
        }
        family_artifacts = {
            family: None if artifact is None else artifact.sha256
            for family, artifact in artifacts.items()
        }

        previous = self._try_load_current()
        if previous is not None and _same_inputs(previous, inventory, family_artifacts):
            self._prune_cache()
            return _with_runtime(previous, changed=False, downloads=0)

        summaries: dict[str, _FamilySummary | None] = {}
        downloads = 0
        for family, artifact in artifacts.items():
            if artifact is None:
                summaries[family] = None
                continue
            summary, downloaded = await self._ensure_family_summary(artifact)
            summaries[family] = summary
            downloads += int(downloaded)

        companies, reports = _resolve_companies(family_rows, summaries)
        snapshot_payload = {
            "schema_version": _STATE_VERSION,
            "algorithm_version": _ALGORITHM_VERSION,
            "manifest_sha256": inventory.manifest_sha256,
            "inventory_sha256": inventory.inventory_sha256,
            "generated_at": inventory.generated_at,
            "derived_at": _now_iso(),
            "family_artifacts": family_artifacts,
            "family_reports": reports,
            "companies": [company.to_dict() for company in companies],
        }
        snapshot_body = _json_bytes(snapshot_payload)
        if len(snapshot_body) > _MAX_DERIVED_OBJECT_BYTES:
            raise ImpactValidationError("compact impact snapshot exceeds byte limit")
        projected_size = _directory_size(self.cache_dir) + len(snapshot_body) + 16 * 1024
        if projected_size > self.max_cache_bytes:
            raise ImpactValidationError("compact impact snapshot cannot fit configured cache")
        snapshot_sha256 = hashlib.sha256(snapshot_body).hexdigest()
        # Validate the exact serialized representation before it can become current.
        _decode_snapshot(
            json.loads(snapshot_body),
            snapshot_sha256,
            changed=True,
            downloads=downloads,
        )
        snapshot_path = self.cache_dir / "snapshots" / f"{snapshot_sha256}.json"
        self._write_atomic_bytes(snapshot_path, snapshot_body)
        pointer = {
            "schema_version": _STATE_VERSION,
            "algorithm_version": _ALGORITHM_VERSION,
            "snapshot_sha256": snapshot_sha256,
            "manifest_sha256": inventory.manifest_sha256,
            "inventory_sha256": inventory.inventory_sha256,
            "generated_at": inventory.generated_at,
            "validated_at": snapshot_payload["derived_at"],
        }
        self._write_atomic_bytes(self.current_path, _json_bytes(pointer, pretty=True))
        published = self._load_current(changed=True, downloads=downloads)
        self._prune_cache()
        return published

    def load_current(self) -> ImpactSnapshot:
        """Load and fully validate the compact last-known-good snapshot."""

        return self._load_current(changed=False, downloads=0)

    async def _ensure_family_summary(self, artifact: _Artifact) -> tuple[_FamilySummary, bool]:
        path = self._family_path(artifact.family, artifact.sha256)
        try:
            return _load_family_summary(path, artifact), False
        except ImpactValidationError:
            pass

        self._assert_download_fits(artifact)
        temp_path = self._temporary_download_path()
        try:
            await self._download_artifact(artifact, temp_path)
            summary = _derive_family(temp_path, artifact)
            self._write_atomic_bytes(path, _json_bytes(summary.to_dict()))
            # Validate the serialized form, not just the in-memory producer.
            return _load_family_summary(path, artifact), True
        except Exception:
            # A newly written invalid derived object must never poison retries.
            try:
                _load_family_summary(path, artifact)
            except ImpactValidationError:
                path.unlink(missing_ok=True)
            raise
        finally:
            temp_path.unlink(missing_ok=True)

    async def _download_artifact(self, artifact: _Artifact, target: Path) -> None:
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as output:
                async with self.client.stream(
                    "GET",
                    artifact.url,
                    headers={"Accept": "application/vnd.apache.parquet"},
                ) as response:
                    response.raise_for_status()
                    assert_trusted_artifact_url(str(response.url))
                    declared = _content_length(response)
                    if declared is not None and declared != artifact.size:
                        raise ImpactValidationError(
                            "job artifact Content-Length does not match manifest"
                        )
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > artifact.size or size > self.max_artifact_bytes:
                            raise ImpactValidationError("job artifact exceeds byte limit")
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            raise ImpactValidationError("cannot persist job artifact download") from exc
        if size != artifact.size:
            raise ImpactValidationError(
                f"job artifact size mismatch: expected {artifact.size}, received {size}"
            )
        if digest.hexdigest() != artifact.sha256:
            raise ImpactValidationError("job artifact SHA-256 mismatch")

    def _assert_download_fits(self, artifact: _Artifact) -> None:
        if artifact.size > self.max_artifact_bytes:
            raise ImpactValidationError(
                f"{artifact.family} artifact exceeds configured per-artifact byte limit"
            )
        cache_size = _directory_size(self.cache_dir)
        overhead = 2 * 1024 * 1024
        if cache_size + artifact.size + overhead > self.max_cache_bytes:
            raise ImpactValidationError(
                f"{artifact.family} artifact cannot fit configured impact cache"
            )
        try:
            free = shutil.disk_usage(self.cache_dir).free
        except OSError as exc:
            raise ImpactValidationError("cannot determine free disk space") from exc
        if free - artifact.size < self.min_free_bytes:
            raise ImpactValidationError(
                f"{artifact.family} artifact would violate free-space reserve"
            )

    def _load_current(self, *, changed: bool, downloads: int) -> ImpactSnapshot:
        try:
            pointer = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImpactValidationError("impact current pointer is missing or corrupt") from exc
        if not isinstance(pointer, dict):
            raise ImpactValidationError("impact current pointer must be an object")
        _validate_versions(pointer)
        snapshot_sha256 = _required_digest(pointer, "snapshot_sha256")
        path = self.cache_dir / "snapshots" / f"{snapshot_sha256}.json"
        body = _read_bounded(path, _MAX_DERIVED_OBJECT_BYTES)
        if hashlib.sha256(body).hexdigest() != snapshot_sha256:
            raise ImpactValidationError("compact impact snapshot checksum mismatch")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImpactValidationError("compact impact snapshot is invalid JSON") from exc
        snapshot = _decode_snapshot(payload, snapshot_sha256, changed=changed, downloads=downloads)
        for key in ("manifest_sha256", "inventory_sha256", "generated_at"):
            if pointer.get(key) != getattr(snapshot, key):
                raise ImpactValidationError(f"impact pointer {key} does not match snapshot")
        return snapshot

    def _try_load_current(self) -> ImpactSnapshot | None:
        if not self.current_path.exists():
            return None
        try:
            return self._load_current(changed=False, downloads=0)
        except ImpactValidationError:
            return None

    def _prepare_directories(self) -> None:
        (self.cache_dir / "families").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "downloads").mkdir(parents=True, exist_ok=True)
        for path in (self.cache_dir / "downloads").iterdir():
            if path.is_file():
                path.unlink(missing_ok=True)

    def _family_path(self, family: str, digest: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", family):
            raise ImpactValidationError("invalid family cache key")
        if not _SHA256_RE.fullmatch(digest):
            raise ImpactValidationError("invalid family artifact checksum")
        directory = self.cache_dir / "families" / family
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.json"

    def _temporary_download_path(self) -> Path:
        fd, raw_path = tempfile.mkstemp(prefix="job-artifact-", dir=self.cache_dir / "downloads")
        os.close(fd)
        return Path(raw_path)

    def _write_atomic_bytes(self, target: Path, body: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, target)
            _fsync_directory(target.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    def _prune_cache(self) -> None:
        now = datetime.now(UTC)
        cutoff = now - timedelta(days=self.max_age_days)
        snapshots: list[tuple[datetime, Path, dict[str, Any]]] = []
        current_sha: str | None = None
        try:
            pointer = json.loads(self.current_path.read_text(encoding="utf-8"))
            if isinstance(pointer, dict):
                current_sha = pointer.get("snapshot_sha256")
        except (OSError, json.JSONDecodeError):
            pass
        for path in (self.cache_dir / "snapshots").glob("*.json"):
            try:
                body = _read_bounded(path, _MAX_DERIVED_OBJECT_BYTES)
                if hashlib.sha256(body).hexdigest() != path.stem:
                    raise ImpactValidationError("snapshot checksum mismatch")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ImpactValidationError("snapshot is not an object")
                derived_at = _parse_datetime(_required_string(payload, "derived_at"))
            except (ImpactValidationError, UnicodeDecodeError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            snapshots.append((derived_at, path, payload))
        snapshots.sort(key=lambda item: item[0], reverse=True)
        kept: list[tuple[datetime, Path, dict[str, Any]]] = []
        for item in snapshots:
            is_current = item[1].stem == current_sha
            if is_current or (len(kept) < self.retention and item[0] >= cutoff):
                kept.append(item)
            else:
                item[1].unlink(missing_ok=True)
        referenced: dict[str, set[str]] = defaultdict(set)
        for _date, _path, payload in kept:
            artifacts = payload.get("family_artifacts")
            if not isinstance(artifacts, dict):
                continue
            for family, digest in artifacts.items():
                if isinstance(family, str) and isinstance(digest, str):
                    referenced[family].add(digest)
        for family_dir in (self.cache_dir / "families").iterdir():
            if not family_dir.is_dir():
                continue
            for path in family_dir.glob("*.json"):
                if path.stem not in referenced.get(family_dir.name, set()):
                    path.unlink(missing_ok=True)


def _eligible_rows(rows: tuple[InventoryRow, ...]) -> dict[str, tuple[InventoryRow, ...]]:
    grouped: dict[str, list[InventoryRow]] = defaultdict(list)
    for row in rows:
        compatibility = compatibility_for(row.ats)
        if compatibility is not None and compatibility.candidate_eligible:
            grouped[row.ats].append(row)
    return {family: tuple(items) for family, items in grouped.items()}


def _job_artifact(manifest: dict[str, Any], family: str) -> _Artifact | None:
    by_ats = manifest.get("by_ats")
    if not isinstance(by_ats, dict):
        raise ImpactValidationError("validated manifest lost by_ats")
    value = by_ats.get(family)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ImpactValidationError(f"invalid {family} job artifact metadata")
    url = value.get("parquet")
    digest = value.get("parquet_sha256")
    size = value.get("parquet_size_bytes")
    rows = value.get("rows")
    if url is None:
        return None
    if not isinstance(url, str) or not isinstance(digest, str):
        raise ImpactValidationError(f"invalid {family} Parquet metadata")
    if not _SHA256_RE.fullmatch(digest):
        raise ImpactValidationError(f"invalid {family} Parquet checksum")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ImpactValidationError(f"invalid {family} Parquet size")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
        raise ImpactValidationError(f"invalid {family} Parquet row count")
    try:
        assert_trusted_artifact_url(url)
    except Exception as exc:
        raise ImpactValidationError(f"untrusted {family} Parquet URL") from exc
    return _Artifact(family=family, url=url, sha256=digest, size=size, rows=rows)


def _derive_family(path: Path, artifact: _Artifact) -> _FamilySummary:
    try:
        scan = pl.scan_parquet(path)
        columns = set(scan.collect_schema().names())
        missing = sorted(_REQUIRED_COLUMNS - columns)
        if missing:
            raise ImpactValidationError(
                f"{artifact.family} Parquet is missing columns: {', '.join(missing)}"
            )
        selected = sorted(_REQUIRED_COLUMNS)
        if _DIRECT_TENANT_COLUMN in columns:
            selected.append(_DIRECT_TENANT_COLUMN)
        expressions: list[pl.Expr] = []
        for column in selected:
            if column == "is_remote":
                expressions.append(
                    pl.col(column)
                    .cast(pl.String, strict=False)
                    .str.to_lowercase()
                    .is_in(["true", "1", "yes"])
                    .alias(column)
                )
            else:
                expressions.append(pl.col(column).cast(pl.String, strict=False))
        frame = scan.select(expressions).collect(engine="streaming")
    except ImpactValidationError:
        raise
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ImpactValidationError(f"{artifact.family} Parquet is unreadable") from exc
    if frame.height != artifact.rows:
        raise ImpactValidationError(
            f"{artifact.family} row mismatch: expected {artifact.rows}, got {frame.height}"
        )

    has_direct = _DIRECT_TENANT_COLUMN in frame.columns
    buckets: dict[tuple[str, str, str], _MutableBucket] = {}
    for raw in frame.iter_rows(named=True):
        direct = normalize_identity(raw.get(_DIRECT_TENANT_COLUMN)) if has_direct else None
        extracted = tenant_key(artifact.family, raw.get("url"), raw.get("ats_id"))
        name_key = normalize_identity(raw.get("company"))
        if direct is not None:
            identity = ("direct", direct, name_key or "")
        elif extracted is not None:
            # A few detail URLs expose only a shared route such as /j/<job>.
            # Preserve the company identity so a misleading URL key cannot
            # collapse unrelated tenants before conservative fallback matching.
            identity = ("tenant", extracted, name_key or "")
        elif name_key is not None:
            identity = ("name", name_key, name_key)
        else:
            identity = ("unmatched", "", "")
        bucket = buckets.setdefault(identity, _MutableBucket(kind=identity[0], key=identity[1]))
        bucket.add(
            name=raw.get("company"),
            location=raw.get("location"),
            country=raw.get("country_iso"),
            is_remote=raw.get("is_remote"),
            posted_at=raw.get("posted_at"),
        )
    frozen = tuple(buckets[identity].freeze() for identity in sorted(buckets))
    return _FamilySummary(
        family=artifact.family,
        artifact_sha256=artifact.sha256,
        artifact_size=artifact.size,
        artifact_rows=artifact.rows,
        derived_at=_now_iso(),
        buckets=frozen,
    )


def _resolve_companies(
    family_rows: dict[str, tuple[InventoryRow, ...]],
    summaries: dict[str, _FamilySummary | None],
) -> tuple[tuple[CompanyImpact, ...], dict[str, dict[str, Any]]]:
    companies: list[CompanyImpact] = []
    reports: dict[str, dict[str, Any]] = {}
    for family in sorted(family_rows):
        rows = family_rows[family]
        summary = summaries[family]
        if summary is None:
            companies.extend(_unknown_company(row) for row in rows)
            reports[family] = {
                "status": "artifact_missing",
                "companies": len(rows),
                "known_companies": 0,
                "matched_jobs": 0,
                "unmatched_jobs": 0,
            }
            continue

        tenant_map = _unique_index(
            (tenant_key(family, row.url), index) for index, row in enumerate(rows)
        )
        slug_map = _unique_index(
            (normalize_identity(row.slug), index) for index, row in enumerate(rows)
        )
        slug_root_map = _unique_index(
            (normalize_identity(row.slug.split("/", 1)[0]), index) for index, row in enumerate(rows)
        )
        name_map = _unique_index(
            (normalize_identity(row.name), index) for index, row in enumerate(rows)
        )
        accumulators = [_CompanyAccumulator() for _row in rows]
        matched_jobs = 0
        unmatched_jobs = 0
        for bucket in summary.buckets:
            index: int | None = None
            if bucket.kind == "direct":
                index = slug_map.get(bucket.key)
                if index is None:
                    index = tenant_map.get(bucket.key)
            elif bucket.kind == "tenant":
                index = tenant_map.get(bucket.key)
            if index is None:
                name_matches = set()
                for name in bucket.name_keys:
                    if name in name_map:
                        name_matches.add(name_map[name])
                    if name in slug_map:
                        name_matches.add(slug_map[name])
                    if name in slug_root_map:
                        name_matches.add(slug_root_map[name])
                if len(name_matches) == 1:
                    index = name_matches.pop()
            if index is None:
                unmatched_jobs += bucket.active_jobs
                continue
            accumulators[index].add(bucket)
            matched_jobs += bucket.active_jobs

        known_companies = 0
        for index, row in enumerate(rows):
            accumulator = accumulators[index]
            # Without a source-published company-stats row, absence is not
            # proof of zero: a job-detail URL may omit the tenant and its name
            # may drift. Only an actually matched active bucket is known.
            impact_unknown = accumulator.active_jobs == 0
            known_companies += int(not impact_unknown)
            companies.append(
                CompanyImpact(
                    ats=row.ats,
                    name=row.name,
                    slug=row.slug,
                    url=row.url,
                    impact_unknown=impact_unknown,
                    active_jobs=accumulator.active_jobs,
                    remote_jobs=accumulator.remote_jobs,
                    location_count=len(accumulator.locations),
                    country_codes=tuple(sorted(accumulator.countries)),
                    latest_posted_at=accumulator.latest_posted_at,
                )
            )
        reports[family] = {
            "status": "derived",
            "companies": len(rows),
            "known_companies": known_companies,
            "artifact_rows": summary.artifact_rows,
            "buckets": len(summary.buckets),
            "matched_jobs": matched_jobs,
            "unmatched_jobs": unmatched_jobs,
        }
    return tuple(companies), reports


def _unknown_company(row: InventoryRow) -> CompanyImpact:
    return CompanyImpact(
        ats=row.ats,
        name=row.name,
        slug=row.slug,
        url=row.url,
        impact_unknown=True,
        active_jobs=0,
        remote_jobs=0,
        location_count=0,
        country_codes=(),
        latest_posted_at=None,
    )


def _unique_index(values) -> dict[str, int]:  # type: ignore[no-untyped-def]
    grouped: dict[str, list[int]] = defaultdict(list)
    for key, index in values:
        if key is not None:
            grouped[key].append(index)
    return {key: indexes[0] for key, indexes in grouped.items() if len(indexes) == 1}


def _load_family_summary(path: Path, artifact: _Artifact) -> _FamilySummary:
    body = _read_bounded(path, _MAX_DERIVED_OBJECT_BYTES)
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImpactValidationError("family summary is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ImpactValidationError("family summary must be an object")
    _validate_versions(value)
    if value.get("family") != artifact.family:
        raise ImpactValidationError("family summary family mismatch")
    if value.get("artifact_sha256") != artifact.sha256:
        raise ImpactValidationError("family summary checksum mismatch")
    if value.get("artifact_size") != artifact.size or value.get("artifact_rows") != artifact.rows:
        raise ImpactValidationError("family summary metadata mismatch")
    derived_at = _required_string(value, "derived_at")
    _parse_datetime(derived_at)
    raw_buckets = value.get("buckets")
    if not isinstance(raw_buckets, list) or len(raw_buckets) > artifact.rows + 1:
        raise ImpactValidationError("family summary buckets are invalid")
    buckets = tuple(_decode_bucket(raw) for raw in raw_buckets)
    identities = [(bucket.kind, bucket.key, bucket.name_keys) for bucket in buckets]
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        raise ImpactValidationError("family summary bucket identities are invalid")
    if sum(bucket.active_jobs for bucket in buckets) != artifact.rows:
        raise ImpactValidationError("family summary job counts do not match artifact")
    if value.get("bucket_count") != len(buckets):
        raise ImpactValidationError("family summary bucket count mismatch")
    return _FamilySummary(
        family=artifact.family,
        artifact_sha256=artifact.sha256,
        artifact_size=artifact.size,
        artifact_rows=artifact.rows,
        derived_at=derived_at,
        buckets=buckets,
    )


def _decode_bucket(value: object) -> _Bucket:
    if not isinstance(value, dict):
        raise ImpactValidationError("family bucket must be an object")
    kind = value.get("kind")
    key = value.get("key")
    if kind not in {"direct", "tenant", "name", "unmatched"} or not isinstance(key, str):
        raise ImpactValidationError("family bucket identity is invalid")
    if len(key) > 4096 or (kind == "unmatched") != (key == ""):
        raise ImpactValidationError("family bucket key is invalid")
    names = _string_tuple(value.get("name_keys"), max_items=1_000, max_length=500)
    active = _nonnegative_int(value, "active_jobs")
    remote = _nonnegative_int(value, "remote_jobs")
    location_hashes = _string_tuple(
        value.get("location_hashes"),
        max_items=_MAX_LOCATION_IDENTITIES,
        max_length=16,
    )
    if remote > active or any(
        not _LOCATION_HASH_RE.fullmatch(location_hash) for location_hash in location_hashes
    ):
        raise ImpactValidationError("family bucket counters are invalid")
    countries = _string_tuple(
        value.get("country_codes"), max_items=_MAX_COUNTRY_CODES, max_length=2
    )
    if any(len(code) != 2 or not code.isalpha() or code != code.upper() for code in countries):
        raise ImpactValidationError("family bucket country codes are invalid")
    latest = value.get("latest_posted_at")
    if latest is not None and (not isinstance(latest, str) or not latest or len(latest) > 100):
        raise ImpactValidationError("family bucket latest_posted_at is invalid")
    return _Bucket(
        kind=kind,
        key=key,
        name_keys=names,
        active_jobs=active,
        remote_jobs=remote,
        location_hashes=location_hashes,
        country_codes=countries,
        latest_posted_at=latest,
    )


def _decode_snapshot(
    value: object,
    snapshot_sha256: str,
    *,
    changed: bool,
    downloads: int,
) -> ImpactSnapshot:
    if not isinstance(value, dict):
        raise ImpactValidationError("compact impact snapshot must be an object")
    _validate_versions(value)
    manifest_sha256 = _required_digest(value, "manifest_sha256")
    inventory_sha256 = _required_digest(value, "inventory_sha256")
    generated_at = _required_string(value, "generated_at")
    derived_at = _required_string(value, "derived_at")
    _parse_datetime(generated_at)
    _parse_datetime(derived_at)
    raw_artifacts = value.get("family_artifacts")
    if not isinstance(raw_artifacts, dict):
        raise ImpactValidationError("impact family_artifacts must be an object")
    family_artifacts: dict[str, str | None] = {}
    for family, digest in raw_artifacts.items():
        if not isinstance(family, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,63}", family):
            raise ImpactValidationError("invalid impact family key")
        if digest is not None and (not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)):
            raise ImpactValidationError("invalid impact family checksum")
        family_artifacts[family] = digest
    raw_reports = value.get("family_reports")
    if not isinstance(raw_reports, dict) or set(raw_reports) != set(family_artifacts):
        raise ImpactValidationError("impact family reports are invalid")
    reports: dict[str, dict[str, Any]] = {}
    for family, report in raw_reports.items():
        if not isinstance(report, dict):
            raise ImpactValidationError("impact family report must be an object")
        reports[family] = report
    raw_companies = value.get("companies")
    if not isinstance(raw_companies, list) or len(raw_companies) > 1_000_000:
        raise ImpactValidationError("impact companies are invalid")
    companies = tuple(_decode_company(raw) for raw in raw_companies)
    if any(company.ats not in family_artifacts for company in companies):
        raise ImpactValidationError("impact company has an unlisted family")
    identities = [(company.ats, company.url) for company in companies]
    if len(identities) != len(set(identities)):
        raise ImpactValidationError("impact snapshot has duplicate companies")
    return ImpactSnapshot(
        snapshot_sha256=snapshot_sha256,
        manifest_sha256=manifest_sha256,
        inventory_sha256=inventory_sha256,
        generated_at=generated_at,
        derived_at=derived_at,
        companies=companies,
        family_artifacts=family_artifacts,
        family_reports=reports,
        changed=changed,
        downloads=downloads,
    )


def _decode_company(value: object) -> CompanyImpact:
    if not isinstance(value, dict):
        raise ImpactValidationError("impact company must be an object")
    ats = _bounded_string(value, "ats", 64)
    name = _bounded_string(value, "name", 500)
    slug = _bounded_string(value, "slug", 500, allow_empty=True)
    url = _bounded_string(value, "url", 4096)
    impact_unknown = value.get("impact_unknown")
    if not isinstance(impact_unknown, bool):
        raise ImpactValidationError("impact_unknown must be boolean")
    active = _nonnegative_int(value, "active_jobs")
    remote = _nonnegative_int(value, "remote_jobs")
    locations = _nonnegative_int(value, "location_count")
    if remote > active:
        raise ImpactValidationError("remote job count exceeds active count")
    countries = _string_tuple(value.get("country_codes"), max_items=250, max_length=2)
    latest = value.get("latest_posted_at")
    if latest is not None and (not isinstance(latest, str) or not latest or len(latest) > 100):
        raise ImpactValidationError("company latest_posted_at is invalid")
    if impact_unknown and any((active, remote, locations, countries, latest)):
        raise ImpactValidationError("unknown company cannot carry impact counters")
    return CompanyImpact(
        ats=ats,
        name=name,
        slug=slug,
        url=url,
        impact_unknown=impact_unknown,
        active_jobs=active,
        remote_jobs=remote,
        location_count=locations,
        country_codes=countries,
        latest_posted_at=latest,
    )


def _same_inputs(
    previous: ImpactSnapshot,
    inventory: InventorySnapshot,
    family_artifacts: dict[str, str | None],
) -> bool:
    return (
        previous.manifest_sha256 == inventory.manifest_sha256
        and previous.inventory_sha256 == inventory.inventory_sha256
        and previous.generated_at == inventory.generated_at
        and previous.family_artifacts == family_artifacts
    )


def _with_runtime(snapshot: ImpactSnapshot, *, changed: bool, downloads: int) -> ImpactSnapshot:
    return ImpactSnapshot(
        snapshot_sha256=snapshot.snapshot_sha256,
        manifest_sha256=snapshot.manifest_sha256,
        inventory_sha256=snapshot.inventory_sha256,
        generated_at=snapshot.generated_at,
        derived_at=snapshot.derived_at,
        companies=snapshot.companies,
        family_artifacts=snapshot.family_artifacts,
        family_reports=snapshot.family_reports,
        changed=changed,
        downloads=downloads,
    )


def _validate_versions(value: dict[str, Any]) -> None:
    if value.get("schema_version") != _STATE_VERSION:
        raise ImpactValidationError("unsupported impact cache schema")
    if value.get("algorithm_version") != _ALGORITHM_VERSION:
        raise ImpactValidationError("unsupported impact derivation algorithm")


def _required_digest(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not _SHA256_RE.fullmatch(result):
        raise ImpactValidationError(f"impact {key} is invalid")
    return result


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ImpactValidationError(f"impact {key} is invalid")
    return result


def _bounded_string(
    value: dict[str, Any], key: str, max_length: int, *, allow_empty: bool = False
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or len(result) > max_length or (not result and not allow_empty):
        raise ImpactValidationError(f"impact company {key} is invalid")
    return result


def _nonnegative_int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise ImpactValidationError(f"impact {key} must be a nonnegative integer")
    return result


def _string_tuple(value: object, *, max_items: int, max_length: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ImpactValidationError("impact string list is invalid")
    if any(not isinstance(item, str) or not item or len(item) > max_length for item in value):
        raise ImpactValidationError("impact string list item is invalid")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise ImpactValidationError("impact string list is not sorted and unique")
    return result


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImpactValidationError("impact timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ImpactValidationError("impact timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            raise ImpactValidationError("impact cache object exceeds byte limit")
        return path.read_bytes()
    except OSError as exc:
        raise ImpactValidationError("impact cache object is missing or unreadable") from exc


def _json_bytes(value: dict[str, Any], *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ImpactValidationError("job artifact Content-Length is invalid") from exc
    if value < 0:
        raise ImpactValidationError("job artifact Content-Length is invalid")
    return value


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for candidate in path.rglob("*"):
            if candidate.is_file():
                total += candidate.stat().st_size
    except OSError as exc:
        raise ImpactValidationError("cannot measure impact cache size") from exc
    return total


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
