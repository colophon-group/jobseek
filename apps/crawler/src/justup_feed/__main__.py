"""Export a bounded, acknowledged Jobseek posting feed to private files.

This process only reads the crawler database. It does not contact Justup or
advance a cursor until an operator supplies a receipt from a completed import.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.export_cursor_fence import capture_justup_feed_cutoff

SCHEMA_VERSION = 1
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
BODY_LIMIT = 512 * 1024
PART_LIMIT = 64 * 1024 * 1024
TOTAL_LIMIT = 512 * 1024 * 1024
SPOOL_LIMIT = 8 * 1024 * 1024 * 1024
MAX_ACCEPTED = 4096
RETENTION_DAYS = 7
EXPORT_TIMEOUT_SECONDS = 600
QUERY_TIMEOUT_SECONDS = 60
SNAPSHOT_ROW_LIMIT = 100_000
PAGE_ROW_LIMIT = 100
CHANGE_ROW_LIMIT = 100
PART_ROW_LIMIT = 100
BATCH_RE = re.compile(r"^batch-[0-9a-f]{32}$")
STAGE_RE = re.compile(r"^\.batch-[0-9a-f]{32}\.stage$")
MANIFEST_RE = re.compile(r"^(batch-[0-9a-f]{32})/manifest\.json$")
PART_RE = re.compile(r"^part-[0-9]{4}\.json$")
# NamedTemporaryFile uses eight random characters. The shorter form is from
# earlier producer builds; only state writes ever used it in the spool root.
STATE_TEMP_RE = re.compile(r"^\.feed-(?:state-)?[a-zA-Z0-9_]{8}$")
SELECTOR_LIMIT = 16 * 1024
MAX_COMPANIES = 100

# Keep this query an explicit public-data allowlist. The body is read from the
# authoritative per-locale description row, not a mutable public asset URL.
# Taxonomy display tables are optional in the crawler-owned database. Stable
# IDs remain available without them.
TAXONOMY_TABLE_COUNT_SQL = """SELECT num_nonnulls(
    to_regclass('location_name'), to_regclass('location'),
    to_regclass('occupation_name'), to_regclass('seniority_name'))"""

TAXONOMY_COLUMNS = """
       ARRAY(SELECT n.name FROM unnest(jp.location_ids) WITH ORDINALITY AS x(id, ord)
             LEFT JOIN LATERAL (
                 SELECT name FROM public.location_name
                 WHERE location_id = x.id AND is_display = true
                 ORDER BY (locale = 'en') DESC, locale LIMIT 1
             ) AS n ON true ORDER BY x.ord) AS location_names,
       ARRAY(SELECT l.type FROM unnest(jp.location_ids) WITH ORDINALITY AS x(id, ord)
             LEFT JOIN public.location AS l ON l.id = x.id ORDER BY x.ord) AS location_geo_types,
       (SELECT name FROM public.occupation_name WHERE occupation_id = jp.occupation_id
         AND locale = 'en' AND is_display = true ORDER BY name LIMIT 1) AS occupation_name,
       (SELECT name FROM public.seniority_name WHERE seniority_id = jp.seniority_id
         AND locale = 'en' AND is_display = true ORDER BY name LIMIT 1) AS seniority_name,
"""

NO_TAXONOMY_COLUMNS = """
       ARRAY(SELECT NULL::text FROM unnest(jp.location_ids) WITH ORDINALITY AS x(id, ord)
             ORDER BY x.ord) AS location_names,
       ARRAY(SELECT NULL::text FROM unnest(jp.location_ids) WITH ORDINALITY AS x(id, ord)
             ORDER BY x.ord) AS location_geo_types,
       NULL::text AS occupation_name,
       NULL::text AS seniority_name,
"""

BASE_SELECT_TEMPLATE = """
SELECT jp.id::text AS id, jp.company_id::text AS company_id,
       c.name AS company_name, c.slug AS company_slug,
       c.website AS company_website, jp.titles,
       jp.source_url, jp.is_active, jp.tdm_reserved, jp.first_seen_at,
       jp.last_seen_at, jp.updated_at, jp.locales, jp.location_ids,
       jp.description_r2_hash,
       jp.location_types, jp.employment_type, jp.salary_min, jp.salary_max,
       jp.salary_currency, jp.salary_period, jp.experience_min,
       jp.experience_max, jp.occupation_id, jp.seniority_id,
{taxonomy_columns}
       d.locale AS body_locale, d.hash AS body_hash,
       d.body_bytes, d.html AS body_html
FROM public.job_posting AS jp
LEFT JOIN public.company AS c ON c.id = jp.company_id
LEFT JOIN LATERAL (
    SELECT locale, hash, octet_length(html) AS body_bytes,
           CASE WHEN octet_length(html) <= $4::int THEN html END AS html
    FROM justup_feed.uploaded_description
    WHERE posting_id = jp.id
      AND locale = ANY(jp.locales) AND locale <> '__none'
    ORDER BY array_position(jp.locales, locale), locale LIMIT 1
) AS d ON true
"""

BASE_SELECT = BASE_SELECT_TEMPLATE.format(taxonomy_columns=TAXONOMY_COLUMNS)
BASE_SELECT_NO_TAXONOMY = BASE_SELECT_TEMPLATE.format(taxonomy_columns=NO_TAXONOMY_COLUMNS)

SNAPSHOT_FILTER = """
WHERE jp.is_active IS TRUE AND jp.first_seen_at >= $1::timestamptz
  AND jp.updated_at < $2::timestamptz
  AND (jp.updated_at, jp.id) > ($3::timestamptz, $5::uuid)
  AND ($7::uuid[] IS NULL OR jp.company_id = ANY($7::uuid[]))
ORDER BY jp.updated_at, jp.id LIMIT $6::int
"""
SNAPSHOT_SQL = BASE_SELECT + SNAPSHOT_FILTER
SNAPSHOT_SQL_NO_TAXONOMY = BASE_SELECT_NO_TAXONOMY + SNAPSHOT_FILTER

CHANGES_FILTER = """
WHERE (jp.updated_at, jp.id) > ($1::timestamptz, $2::uuid)
  AND jp.updated_at < $3::timestamptz
  AND ($6::uuid[] IS NULL OR jp.company_id = ANY($6::uuid[]))
ORDER BY jp.updated_at, jp.id LIMIT $5::int
"""
CHANGES_SQL = BASE_SELECT + CHANGES_FILTER
CHANGES_SQL_NO_TAXONOMY = BASE_SELECT_NO_TAXONOMY + CHANGES_FILTER

SOURCE_COUNT_SQL = """SELECT count(*) FROM public.job_posting AS jp
WHERE jp.is_active IS TRUE AND jp.first_seen_at >= $1::timestamptz
  AND jp.updated_at < $2::timestamptz
  AND ($3::uuid[] IS NULL OR jp.company_id = ANY($3::uuid[]))"""


class FeedError(Exception):
    """Operator-facing error that never includes posting content or credentials."""


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_stamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise FeedError("Invalid feed cursor time.") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise FeedError("Feed cursor time must be UTC.")
    return result


def cursor(time: datetime, identifier: str = ZERO_UUID) -> dict[str, str]:
    return {"updatedAt": stamp(time), "id": identifier}


def validate_cursor(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"updatedAt", "id"}:
        raise FeedError("Invalid feed cursor.")
    parse_stamp(value["updatedAt"])
    try:
        if str(UUID(value["id"])) != value["id"]:
            raise ValueError
    except (TypeError, ValueError, AttributeError) as exc:
        raise FeedError("Invalid feed cursor ID.") from exc
    return value


def body_evidence(row: asyncpg.Record) -> tuple[dict[str, str] | None, str]:
    if row["body_locale"] is None:
        return None, "missing"
    if row["body_bytes"] is None or row["body_bytes"] > BODY_LIMIT:
        return None, "too_large"
    html = row["body_html"]
    if not isinstance(html, str):
        return None, "missing"
    digest = hashlib.sha256(html.encode("utf-8")).digest()
    source_hash = struct.unpack(">q", digest[:8])[0]
    if source_hash != row["body_hash"]:
        return None, "hash_mismatch"
    return {"locale": row["body_locale"], "html": html, "sha256": digest.hex()}, "ready"


def public_row(row: asyncpg.Record) -> dict[str, Any]:
    if not row["company_name"] or not row["source_url"]:
        raise FeedError("A posting lacks required public identity fields.")
    body, status = body_evidence(row)
    titles = row["titles"] or []
    title = next((item.strip() for item in titles if isinstance(item, str) and item.strip()), None)
    location_ids = row["location_ids"] or []
    location_types = row["location_types"]
    if location_types is None:
        location_types = [None] * len(location_ids)
    elif len(location_types) != len(location_ids):
        raise FeedError("A posting has misaligned location types.")
    return {
        "id": row["id"],
        "companyId": row["company_id"],
        "companyName": row["company_name"],
        "companySlug": row["company_slug"],
        "companyWebsite": row["company_website"],
        "title": title,
        "sourceUrl": row["source_url"],
        "active": row["is_active"],
        "reserved": row["tdm_reserved"],
        "hasContent": bool(title and (row["description_r2_hash"] is not None or body is not None)),
        "firstSeenAt": stamp(row["first_seen_at"]),
        "lastSeenAt": stamp(row["last_seen_at"]) if row["last_seen_at"] else None,
        "updatedAt": stamp(row["updated_at"]),
        "locales": row["locales"] or [],
        "locationDirectIds": location_ids,
        "locationNames": row["location_names"] or [],
        "locationTypes": location_types,
        "locationGeoTypes": row["location_geo_types"] or [],
        "employmentType": row["employment_type"],
        "salaryMin": row["salary_min"],
        "salaryMax": row["salary_max"],
        "salaryCurrency": row["salary_currency"],
        "salaryPeriod": row["salary_period"],
        "experienceMin": float(row["experience_min"])
        if row["experience_min"] is not None
        else None,
        "experienceMax": float(row["experience_max"])
        if row["experience_max"] is not None
        else None,
        "occupationId": row["occupation_id"],
        "occupationName": row["occupation_name"],
        "seniorityId": row["seniority_id"],
        "seniorityName": row["seniority_name"],
        "body": body,
        "bodyStatus": status,
    }


def encode(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def selected_companies(path: Path | None) -> tuple[list[UUID] | None, dict[str, Any]]:
    if path is None:
        return None, {"kind": "all_eligible"}
    if path.is_symlink() or path.stat().st_size > SELECTOR_LIMIT:
        raise FeedError("Company selector is unsafe or exceeds 16 KiB.")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise FeedError("Company selector has duplicate keys.")
            value[key] = item
        return value

    try:
        selector = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise FeedError("Company selector cannot be read.") from exc
    if (
        not isinstance(selector, dict)
        or set(selector) != {"schemaVersion", "companyIds"}
        or type(selector["schemaVersion"]) is not int
        or selector["schemaVersion"] != SCHEMA_VERSION
        or not isinstance(selector["companyIds"], list)
        or not 1 <= len(selector["companyIds"]) <= MAX_COMPANIES
    ):
        raise FeedError("Company selector requires schemaVersion 1 and 1..100 IDs.")
    raw = selector["companyIds"]
    try:
        if any(not isinstance(item, str) or str(UUID(item)) != item for item in raw) or len(
            set(raw)
        ) != len(raw):
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise FeedError("Company selector requires unique canonical UUIDs.") from exc
    names = sorted(raw)
    digest = hashlib.sha256(json.dumps(names, separators=(",", ":")).encode()).hexdigest()
    return [UUID(item) for item in names], {
        "kind": "company_ids",
        "requestedCount": len(names),
        "requestedSetSha256": digest,
    }


def private_directory(path: Path) -> Path:
    directory = path.expanduser().resolve()
    checkout = Path(__file__).resolve().parents[4]
    if not directory.is_dir() or directory == checkout or checkout in directory.parents:
        raise FeedError("Use an existing private directory outside the checkout.")
    stat = directory.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise FeedError("Feed directory must be owned by this user and mode 0700.")
    return directory


def write_atomic(path: Path, data: bytes) -> None:
    temporary: Path | None = None
    try:
        prefix = ".feed-state-" if path.name == "state.json" else ".feed-"
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=prefix, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def private_file_digest(path: Path, limit: int) -> str:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > limit
    ):
        raise FeedError("A private feed file is missing or unsafe.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def check_receipt(path: Path) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o022
        or path.stat().st_size > 8192
    ):
        raise FeedError("Feed receipt is missing or unsafe.")


def batch_directory(directory: Path, manifest_name: str) -> Path:
    match = MANIFEST_RE.fullmatch(manifest_name)
    if match is None:
        raise FeedError("Feed manifest path is invalid.")
    path = directory / match.group(1)
    if (
        path.is_symlink()
        or not path.is_dir()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
    ):
        raise FeedError("Private batch directory is missing or unsafe.")
    return path


def verify_pending(directory: Path, pending: dict[str, Any]) -> None:
    batch = batch_directory(directory, pending["manifest"])
    path = batch / "manifest.json"
    if private_file_digest(path, 1024 * 1024) != pending["sha256"]:
        raise FeedError("Pending manifest changed.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FeedError("Pending manifest cannot be read.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("toCursor") != pending["toCursor"]
        or manifest.get("scope") != pending["scope"]
        or not isinstance(manifest.get("parts"), list)
        or len(manifest["parts"]) > SNAPSHOT_ROW_LIMIT // PART_ROW_LIMIT + 1
    ):
        raise FeedError("Pending manifest does not match feed state.")
    counted = 0
    for part in manifest["parts"]:
        if (
            not isinstance(part, dict)
            or set(part) != {"file", "sha256", "rows"}
            or not isinstance(part["file"], str)
            or not PART_RE.fullmatch(part["file"])
            or not isinstance(part["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", part["sha256"])
            or type(part["rows"]) is not int
            or not 1 <= part["rows"] <= PART_ROW_LIMIT
        ):
            raise FeedError("Pending part manifest is invalid.")
        if private_file_digest(batch / part["file"], PART_LIMIT) != part["sha256"]:
            raise FeedError("Pending part changed.")
        counted += part["rows"]
    if counted != manifest.get("rows"):
        raise FeedError("Pending row count is inconsistent.")


@contextmanager
def state_lock(directory: Path):
    lock_path = directory / ".state.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_mode & 0o077:
            raise FeedError("Feed lock has unsafe permissions.")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_state(directory: Path) -> dict[str, Any]:
    path = directory / "state.json"
    if not path.exists():
        return {
            "schemaVersion": SCHEMA_VERSION,
            "cursor": None,
            "scope": None,
            "pending": None,
            "accepted": [],
        }
    if (
        path.is_symlink()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
        or path.stat().st_size > 1024 * 1024
    ):
        raise FeedError("Feed state file is unsafe.")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FeedError("Feed state cannot be read.") from exc
    if (
        not isinstance(state, dict)
        or set(state) != {"schemaVersion", "cursor", "scope", "pending", "accepted"}
        or state["schemaVersion"] != SCHEMA_VERSION
        or not isinstance(state["accepted"], list)
        or len(state["accepted"]) > MAX_ACCEPTED
    ):
        raise FeedError("Feed state schema is invalid.")
    if state["cursor"] is not None:
        validate_cursor(state["cursor"])
    pending = state["pending"]
    for scope in (state["scope"], pending.get("scope") if isinstance(pending, dict) else None):
        if scope is not None and (
            not isinstance(scope, dict)
            or scope.get("kind") not in ("all_eligible", "company_ids")
            or (scope["kind"] == "all_eligible" and set(scope) != {"kind"})
            or (
                scope["kind"] == "company_ids"
                and (
                    set(scope) != {"kind", "requestedCount", "requestedSetSha256"}
                    or type(scope["requestedCount"]) is not int
                    or not 1 <= scope["requestedCount"] <= MAX_COMPANIES
                    or not isinstance(scope["requestedSetSha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", scope["requestedSetSha256"])
                )
            )
        ):
            raise FeedError("Feed scope is invalid.")
    if pending is not None and (
        not isinstance(pending, dict)
        or set(pending) != {"manifest", "sha256", "toCursor", "scope"}
        or not isinstance(pending["manifest"], str)
        or not MANIFEST_RE.fullmatch(pending["manifest"])
        or not isinstance(pending["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", pending["sha256"])
    ):
        raise FeedError("Feed pending state is invalid.")
    if pending is not None:
        if pending["scope"] is None:
            raise FeedError("Pending feed scope is missing.")
        validate_cursor(pending["toCursor"])
    seen = set()
    for item in state["accepted"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"manifest", "sha256", "acceptedAt", "backupVerifiedAt"}
            or not isinstance(item["manifest"], str)
            or not MANIFEST_RE.fullmatch(item["manifest"])
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            or not isinstance(item["acceptedAt"], str)
            or item["manifest"] in seen
        ):
            raise FeedError("Accepted feed state is invalid.")
        parse_stamp(item["acceptedAt"])
        if item["backupVerifiedAt"] is not None:
            parse_stamp(item["backupVerifiedAt"])
        seen.add(item["manifest"])
    if pending is not None and pending["manifest"] in seen:
        raise FeedError("Pending feed batch is already accepted.")
    return state


def sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_private_batch(path: Path) -> None:
    if (
        not (BATCH_RE.fullmatch(path.name) or STAGE_RE.fullmatch(path.name))
        or path.is_symlink()
        or not path.is_dir()
        or path.stat().st_uid != os.getuid()
        or path.stat().st_mode & 0o077
    ):
        raise FeedError("Unreferenced batch directory is unsafe.")
    for child in path.iterdir():
        if (
            child.is_dir()
            or child.is_symlink()
            or not (
                child.name == "manifest.json"
                or PART_RE.fullmatch(child.name)
                or child.name.startswith(".feed-")
            )
        ):
            raise FeedError("Unreferenced batch contains an unexpected file.")
        child.unlink()
    path.rmdir()
    sync_directory(path.parent)


def recover_unreferenced(directory: Path, state: dict[str, Any]) -> None:
    referenced = {
        item["manifest"].split("/", 1)[0]
        for item in [*state["accepted"], *([state["pending"]] if state["pending"] else [])]
    }
    removed_temp = False
    for child in directory.iterdir():
        if STATE_TEMP_RE.fullmatch(child.name):
            if (
                child.is_symlink()
                or not child.is_file()
                or child.stat().st_uid != os.getuid()
                or child.stat().st_mode & 0o077
            ):
                raise FeedError("Interrupted state temp file is unsafe.")
            child.unlink()
            removed_temp = True
            continue
        if STAGE_RE.fullmatch(child.name) or (
            BATCH_RE.fullmatch(child.name) and child.name not in referenced
        ):
            remove_private_batch(child)
    if removed_temp:
        sync_directory(directory)


def spool_bytes(directory: Path) -> int:
    total = 0
    for batch in directory.iterdir():
        if not BATCH_RE.fullmatch(batch.name):
            continue
        if (
            batch.is_symlink()
            or not batch.is_dir()
            or batch.stat().st_uid != os.getuid()
            or batch.stat().st_mode & 0o077
        ):
            raise FeedError("Feed spool contains an unsafe batch directory.")
        for item in batch.iterdir():
            if (
                item.is_symlink()
                or not item.is_file()
                or item.stat().st_uid != os.getuid()
                or item.stat().st_mode & 0o077
                or not (item.name == "manifest.json" or PART_RE.fullmatch(item.name))
            ):
                raise FeedError("Feed spool contains an unexpected file.")
            total += item.stat().st_size
            if total > SPOOL_LIMIT:
                raise FeedError("Feed spool exceeds its 8 GiB limit.")
    return total


def prune_eligible(directory: Path, state: dict[str, Any], now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    keep, remove = [], []
    for item in state["accepted"]:
        accepted = parse_stamp(item["acceptedAt"])
        if item["backupVerifiedAt"] is not None and accepted <= now - timedelta(
            days=RETENTION_DAYS
        ):
            remove.append(item)
        else:
            keep.append(item)
    if not remove:
        return 0
    # Persist the completed retention decision first. If deletion is interrupted,
    # the next locked run sees an unreferenced directory and finishes cleanup.
    state["accepted"] = keep
    write_atomic(directory / "state.json", encode(state))
    for item in remove:
        remove_private_batch(batch_directory(directory, item["manifest"]))
    return len(remove)


class Parts:
    def __init__(self, directory: Path, existing_bytes: int):
        self.directory = directory
        self.existing_bytes = existing_bytes
        self.parts: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self.size = len(encode({"schemaVersion": SCHEMA_VERSION, "rows": []}))
        self.total = 0

    def add(self, row: dict[str, Any]) -> None:
        size = len(encode(row))
        if size + self.size > PART_LIMIT or len(self.rows) == PART_ROW_LIMIT:
            self.flush()
        if size + self.size > PART_LIMIT:
            raise FeedError("One posting exceeds the private part limit.")
        self.rows.append(row)
        self.size += size

    def flush(self) -> None:
        if not self.rows:
            return
        name = f"part-{len(self.parts) + 1:04d}.json"
        data = encode({"schemaVersion": SCHEMA_VERSION, "rows": self.rows})
        if len(data) > PART_LIMIT or self.total + len(data) > TOTAL_LIMIT:
            raise FeedError("Feed export exceeds its private size limit.")
        if self.existing_bytes + self.total + len(data) > SPOOL_LIMIT:
            raise FeedError("Feed spool would exceed its 8 GiB limit.")
        write_atomic(self.directory / name, data)
        self.total += len(data)
        self.parts.append(
            {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "rows": len(self.rows)}
        )
        self.rows = []
        self.size = len(encode({"schemaVersion": SCHEMA_VERSION, "rows": []}))


async def build_batch(
    pool: asyncpg.Pool,
    directory: Path,
    state: dict[str, Any],
    reconcile: bool,
    companies: list[UUID] | None,
    scope: dict[str, Any],
) -> dict[str, Any]:
    # Capture the writer floor BEFORE the repeatable-read user-table snapshot.
    # Reversing these calls can omit a writer that commits between them while
    # still advancing past its updated_at position.
    cutoff = await capture_justup_feed_cutoff(pool)
    previous = state["cursor"]
    if previous is not None and parse_stamp(previous["updatedAt"]) >= cutoff:
        raise FeedError("CDC cutoff has not advanced beyond the acknowledged cursor.")
    if previous is not None and state["scope"] != scope and not reconcile:
        raise FeedError("A changed company scope requires a full --reconcile snapshot.")
    kind = "snapshot" if previous is None or reconcile else "changes"
    batch_id = uuid4().hex
    stage = directory / f".batch-{batch_id}.stage"
    published = directory / f"batch-{batch_id}"
    existing_bytes = spool_bytes(directory)
    stage.mkdir(mode=0o700)
    parts = Parts(stage, existing_bytes)
    last = previous
    count = 0
    complete = True
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        taxonomy_table_count = await conn.fetchval(TAXONOMY_TABLE_COUNT_SQL)
        if type(taxonomy_table_count) is not int or taxonomy_table_count not in (0, 4):
            raise FeedError("Taxonomy display schema is partially present or unknown.")
        snapshot_sql = SNAPSHOT_SQL if taxonomy_table_count == 4 else SNAPSHOT_SQL_NO_TAXONOMY
        changes_sql = CHANGES_SQL if taxonomy_table_count == 4 else CHANGES_SQL_NO_TAXONOMY
        source_row_count = await conn.fetchval(
            SOURCE_COUNT_SQL, cutoff - timedelta(days=14), cutoff, companies
        )
        if not isinstance(source_row_count, int) or source_row_count < 0:
            raise FeedError("Invalid source scope count.")
        if kind == "snapshot" and source_row_count > SNAPSHOT_ROW_LIMIT:
            raise FeedError("Snapshot exceeds the 100,000-row source cap.")
        if kind == "snapshot":
            after_time = datetime(1, 1, 1, tzinfo=UTC)
            after_id = ZERO_UUID
            while True:
                rows = await conn.fetch(
                    snapshot_sql,
                    cutoff - timedelta(days=14),
                    cutoff,
                    after_time,
                    BODY_LIMIT,
                    after_id,
                    PAGE_ROW_LIMIT,
                    companies,
                )
                for row in rows:
                    parts.add(public_row(row))
                    count += 1
                    if count > SNAPSHOT_ROW_LIMIT:
                        raise FeedError("Snapshot exceeds the 100,000-row source cap.")
                    after_time, after_id = row["updated_at"], row["id"]
                if len(rows) < PAGE_ROW_LIMIT:
                    break
            last = cursor(cutoff)
        else:
            validated = validate_cursor(previous)
            after_time = parse_stamp(validated["updatedAt"])
            rows = await conn.fetch(
                changes_sql,
                after_time,
                UUID(validated["id"]),
                cutoff,
                BODY_LIMIT,
                CHANGE_ROW_LIMIT + 1,
                companies,
            )
            complete = len(rows) <= CHANGE_ROW_LIMIT
            for row in rows[:CHANGE_ROW_LIMIT]:
                parts.add(public_row(row))
                last = cursor(row["updated_at"], row["id"])
                count += 1
            if complete:
                last = cursor(cutoff)
    parts.flush()
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "fromCursor": None if kind == "snapshot" else previous,
        "toCursor": last,
        "cutoffAt": stamp(cutoff),
        "complete": complete,
        "scope": scope,
        "windowDays": 14,
        "sourceScopeRowCount": source_row_count,
        "rows": count,
        "parts": parts.parts,
    }
    manifest_name = f"batch-{batch_id}/manifest.json"
    data = encode(manifest)
    digest = hashlib.sha256(data).hexdigest()
    if existing_bytes + parts.total + len(data) > SPOOL_LIMIT:
        raise FeedError("Feed spool would exceed its 8 GiB limit.")
    write_atomic(stage / "manifest.json", data)
    os.rename(stage, published)
    sync_directory(directory)
    state["pending"] = {
        "manifest": manifest_name,
        "sha256": digest,
        "toCursor": last,
        "scope": scope,
    }
    write_atomic(directory / "state.json", encode(state))
    return state["pending"]


async def export(directory: Path, reconcile: bool, selector: Path | None) -> dict[str, Any]:
    with state_lock(directory):
        companies, scope = selected_companies(selector)
        state = load_state(directory)
        recover_unreferenced(directory, state)
        prune_eligible(directory, state)
        if state["pending"] is not None:
            pending = state["pending"]
            if pending["scope"] != scope:
                raise FeedError("Pending feed batch has a different company scope.")
            verify_pending(directory, pending)
            return pending
        if len(state["accepted"]) >= MAX_ACCEPTED:
            raise FeedError(
                "Feed has too many retained accepted batches; verify backups and prune."
            )
        dsn = os.environ.get("LOCAL_DATABASE_URL")
        if not dsn:
            raise FeedError("LOCAL_DATABASE_URL is required for read-only export.")
        pool = await asyncpg.create_pool(
            dsn,
            min_size=1,
            max_size=2,
            timeout=10,
            command_timeout=QUERY_TIMEOUT_SECONDS,
            server_settings={
                "default_transaction_read_only": "on",
                "statement_timeout": str(QUERY_TIMEOUT_SECONDS * 1000),
                "idle_in_transaction_session_timeout": "120000",
            },
        )
        try:
            async with asyncio.timeout(EXPORT_TIMEOUT_SECONDS):
                return await build_batch(pool, directory, state, reconcile, companies, scope)
        finally:
            try:
                async with asyncio.timeout(30):
                    await pool.close()
            except TimeoutError:
                pool.terminate()


def acknowledge(directory: Path, receipt_path: Path) -> dict[str, Any]:
    with state_lock(directory):
        state = load_state(directory)
        recover_unreferenced(directory, state)
        pending = state["pending"]
        if pending is None:
            raise FeedError("There is no pending feed batch.")
        check_receipt(receipt_path)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FeedError("Import receipt cannot be read.") from exc
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"schemaVersion", "manifestSha256"}
            or receipt["schemaVersion"] != SCHEMA_VERSION
            or receipt["manifestSha256"] != pending["sha256"]
        ):
            raise FeedError("Import receipt does not match the pending batch.")
        verify_pending(directory, pending)
        if len(state["accepted"]) >= MAX_ACCEPTED:
            raise FeedError(
                "Feed has too many retained accepted batches; verify backups and prune."
            )
        state["cursor"] = pending["toCursor"]
        state["scope"] = pending["scope"]
        state["accepted"].append(
            {
                "manifest": pending["manifest"],
                "sha256": pending["sha256"],
                "acceptedAt": stamp(datetime.now(UTC)),
                "backupVerifiedAt": None,
            }
        )
        state["pending"] = None
        write_atomic(directory / "state.json", encode(state))
        return {"cursor": state["cursor"]}


def mark_backup(directory: Path, receipt_path: Path, now: datetime | None = None) -> dict[str, Any]:
    with state_lock(directory):
        state = load_state(directory)
        recover_unreferenced(directory, state)
        check_receipt(receipt_path)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FeedError("Backup receipt cannot be read.") from exc
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"schemaVersion", "manifestSha256", "backupVerified"}
            or receipt["schemaVersion"] != SCHEMA_VERSION
            or receipt["backupVerified"] is not True
            or not isinstance(receipt["manifestSha256"], str)
        ):
            raise FeedError("Backup receipt is invalid.")
        matching = [
            item for item in state["accepted"] if item["sha256"] == receipt["manifestSha256"]
        ]
        if len(matching) != 1:
            raise FeedError("Backup receipt does not match one accepted batch.")
        item = matching[0]
        if item["backupVerifiedAt"] is None:
            item["backupVerifiedAt"] = stamp(now or datetime.now(UTC))
            write_atomic(directory / "state.json", encode(state))
        removed = prune_eligible(directory, state, now)
        return {"backupRecorded": True, "pruned": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export_parser = sub.add_parser("export")
    export_parser.add_argument("--directory", required=True, type=Path)
    export_parser.add_argument(
        "--reconcile", action="store_true", help="publish a full verified 14-day snapshot"
    )
    export_parser.add_argument(
        "--company-ids-file", type=Path, help="private exact company selector; no fallback"
    )
    ack_parser = sub.add_parser("ack")
    ack_parser.add_argument("--directory", required=True, type=Path)
    ack_parser.add_argument("--receipt", required=True, type=Path)
    backup_parser = sub.add_parser("backup-verified")
    backup_parser.add_argument("--directory", required=True, type=Path)
    backup_parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        directory = private_directory(args.directory)
        if args.command == "export":
            result = asyncio.run(export(directory, args.reconcile, args.company_ids_file))
            public_receipt = {"manifest": result["manifest"], "sha256": result["sha256"]}
        elif args.command == "ack":
            result = acknowledge(directory, args.receipt)
            public_receipt = {
                "acknowledged": True,
                "cursorUpdatedAt": result["cursor"]["updatedAt"],
            }
        else:
            public_receipt = mark_backup(directory, args.receipt)
        print(json.dumps(public_receipt, separators=(",", ":")))
    except Exception as exc:
        # Driver and unexpected errors may contain DSNs or source content.
        message = str(exc) if isinstance(exc, FeedError) else "Read-only feed operation failed."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
