from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg
import httpx
import structlog
from prometheus_client import Counter, Gauge

from src.config import settings
from src.export_cursor_fence import (
    CursorFenceFactory,
    CutoffFactory,
    capture_cdc_snapshot_cutoff,
    export_cursor_fence,
)
from src.metrics import (
    export_errors_total,
    exporter_export_lag,
    exporter_flush_duration,
    exporter_last_flush_ts,
    exporter_rows_exported,
    local_db_pool_idle,
    local_db_pool_size,
    r2_pending_gauge,
    redis_queue_depth,
    supa_db_pool_idle,
    supa_db_pool_size,
    typesense_backfill_docs_total,
    typesense_export_docs_total,
    typesense_export_duration_seconds,
    typesense_export_lag,
    typesense_memory_bytes,
)
from src.redis_queue import get_queue_depths

# These availability gauges are only ever set by this module (the exporter), so we
# define them here instead of in metrics.py. Defining them at metrics.py's
# module scope would have every crawler container that imports metrics
# export a default-0 sample, which masquerades as "redis disconnected" or
# "typesense unhealthy" in queries that don't filter on instance. Keeping
# them local means only the exporter's /metrics endpoint exposes them.
redis_connected = Gauge(
    "crawler_redis_connected",
    "Redis connection status (1=connected, 0=disconnected)",
)
typesense_healthy = Gauge(
    "crawler_typesense_healthy",
    "Typesense health status (1=healthy, 0=unhealthy)",
)
exporter_downstream_available = Gauge(
    "crawler_exporter_downstream_available",
    "Exporter downstream availability (1=available, 0=in backoff)",
    ["target"],
)
exporter_downstream_backoff_seconds = Gauge(
    "crawler_exporter_downstream_backoff_seconds",
    "Seconds remaining before the exporter retries a downstream",
    ["target"],
)
exporter_downstream_skipped_total = Counter(
    "crawler_exporter_downstream_skipped_total",
    "Exporter downstream attempts skipped by outage backoff",
    ["target"],
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Cursor persistence (exporter_state table)
# ---------------------------------------------------------------------------

_EPOCH = datetime.min.replace(tzinfo=UTC)
_MAX_CDC_CUTOFF = datetime.max.replace(tzinfo=UTC)
_ZERO_UUID = uuid.UUID(int=0)

# Sentinel stamped on Typesense `experience_max` for rows the extractor
# treated as open-ended ("N+ years" → Postgres `experience_max IS NULL`).
# Chosen well above any plausible real requirement so range-overlap
# filters built from the UI (whose pills top out at single-digit years)
# always include these rows when the user's upper bound ≥ N. See #3217.
_EXPERIENCE_MAX_OPEN_ENDED = 99

# Cursor is a (timestamp, id) pair for keyset pagination.
# Stored as "ts_iso|uuid" in exporter_state.
Cursor = tuple[datetime, uuid.UUID]


@dataclass(slots=True)
class _DownstreamBackoff:
    """Bound repeated exporter attempts during a downstream outage.

    The exporter normally runs every second. Without state carried between
    ticks, a refused connection turns one provider incident into a retry
    storm. This circuit only opens for target-wide availability failures;
    deterministic row errors retain their existing isolation path.
    """

    target: str
    base_seconds: float
    max_seconds: float
    consecutive_failures: int = 0
    retry_at: float = 0.0
    outage_started_at: float | None = None

    def __post_init__(self) -> None:
        if self.base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if self.max_seconds < self.base_seconds:
            raise ValueError("max_seconds must be at least base_seconds")
        exporter_downstream_available.labels(target=self.target).set(1)
        exporter_downstream_backoff_seconds.labels(target=self.target).set(0)

    def ready(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        remaining = max(0.0, self.retry_at - current)
        exporter_downstream_backoff_seconds.labels(target=self.target).set(remaining)
        return remaining == 0

    def record_skip(self, now: float | None = None) -> None:
        self.ready(now)
        exporter_downstream_skipped_total.labels(target=self.target).inc()

    def record_failure(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        if self.outage_started_at is None:
            self.outage_started_at = current
        self.consecutive_failures += 1
        # Once the cap is reached there is no reason to keep growing the
        # exponent (and an extremely long outage must not overflow a float).
        cap_exponent = math.ceil(math.log2(self.max_seconds / self.base_seconds))
        exponent = min(self.consecutive_failures - 1, cap_exponent)
        delay = min(self.max_seconds, self.base_seconds * (2**exponent))
        self.retry_at = current + delay
        exporter_downstream_available.labels(target=self.target).set(0)
        exporter_downstream_backoff_seconds.labels(target=self.target).set(delay)
        return delay

    def record_success(self, now: float | None = None) -> tuple[int, float] | None:
        if self.consecutive_failures == 0:
            return None
        current = time.monotonic() if now is None else now
        failures = self.consecutive_failures
        outage_started_at = self.outage_started_at
        outage_seconds = max(
            0.0,
            current - (current if outage_started_at is None else outage_started_at),
        )
        self.consecutive_failures = 0
        self.retry_at = 0.0
        self.outage_started_at = None
        exporter_downstream_available.labels(target=self.target).set(1)
        exporter_downstream_backoff_seconds.labels(target=self.target).set(0)
        return failures, outage_seconds


__all__ = [
    "Cursor",
    "PostingSchema",
    "TaxonomyMaps",
    "_POSTING_COLUMNS",
    "_POSTING_UPSERT_SET",
    "backfill_typesense",
    "redis_connected",
    "run_exporter",
    "typesense_healthy",
]


def _encode_experience_for_typesense(
    exp_min: object,
    exp_max: object,
) -> tuple[int, int, float, float]:
    """Encode decimal-year experience values for Typesense.

    ``experience_min``/``experience_max`` are legacy integer facets retained so
    older documents keep matching during the float-field rollout. They are
    conservative for decimal values: min rounds up and bounded max rounds down,
    preventing the fallback branch from broadening precise float matches.
    """
    if exp_min is None:
        return -1, -1, -1.0, -1.0

    min_years = float(exp_min)
    if exp_max is None:
        max_years = float(_EXPERIENCE_MAX_OPEN_ENDED)
        legacy_max = _EXPERIENCE_MAX_OPEN_ENDED
    else:
        max_years = float(exp_max)
        legacy_max = math.floor(max_years)

    legacy_min = math.ceil(min_years)
    return legacy_min, legacy_max, min_years, max_years


async def _get_cursor(pool: asyncpg.Pool, table: str) -> Cursor:
    """Load the last export cursor from exporter_state."""
    row = await pool.fetchrow(
        "SELECT value FROM exporter_state WHERE key = $1",
        f"last_export_ts:{table}",
    )
    if row:
        val = row["value"]
        if "|" in val:
            ts_str, id_str = val.split("|", 1)
            return datetime.fromisoformat(ts_str), uuid.UUID(id_str)
        # Backward compat: old cursor stored just a timestamp
        return datetime.fromisoformat(val), _ZERO_UUID
    return _EPOCH, _ZERO_UUID


async def _save_cursor(pool: asyncpg.Pool, table: str, cursor: Cursor) -> None:
    """Persist the export cursor to exporter_state."""
    ts, last_id = cursor
    await pool.execute(
        "INSERT INTO exporter_state (key, value, updated_at) "
        "VALUES ($1, $2, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
        f"last_export_ts:{table}",
        f"{ts.isoformat()}|{last_id}",
    )


async def _save_cursors_atomic(
    pool: asyncpg.Pool,
    cursors: list[tuple[str, Cursor]],
) -> None:
    """Persist multiple export cursors in a single transaction.

    Issue #3171: the exporter previously called ``_save_cursor`` twice
    in sequence (Supabase first, Typesense second). A crash between the
    two writes — OOM, SIGKILL, host reboot — left the Supabase cursor
    advanced but the Typesense cursor stale. On restart the entire
    just-exported batch was re-pushed to Typesense (or vice versa),
    burning CPU and re-touching every doc in the batch.

    Wrapping the upserts in a single transaction makes the pair atomic:
    either both cursors land or neither does. If neither lands, the
    next tick just re-fetches the same rows and re-upserts to both
    targets (which is idempotent — Supabase ON CONFLICT and Typesense
    ``import_(..., {"action": "upsert"})``).
    """
    if not cursors:
        return
    async with pool.acquire() as conn, conn.transaction():
        for table, cursor in cursors:
            ts, last_id = cursor
            await conn.execute(
                "INSERT INTO exporter_state (key, value, updated_at) "
                "VALUES ($1, $2, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
                f"last_export_ts:{table}",
                f"{ts.isoformat()}|{last_id}",
            )


# ---------------------------------------------------------------------------
# Taxonomy name maps for Typesense denormalization
# ---------------------------------------------------------------------------

_TAXONOMY_REFRESH_INTERVAL = 600  # 10 minutes


@dataclass
class TaxonomyMaps:
    """In-memory lookup tables for denormalizing Typesense documents.

    Declared as a ``@dataclass`` to make field uniqueness auditable —
    ``dataclasses.fields(TaxonomyMaps)`` returns one entry per logical
    slot, so a regression test can assert there is exactly one
    ``occupation_ancestors`` field. Issue #3124: the prior class-body
    ``__init__`` defined ``self.occupation_ancestors`` twice, and the
    second assignment silently shadowed the first.
    """

    # Name maps (per-locale where applicable)
    location_names: dict[int, dict[str, str]] = field(default_factory=dict)
    location_types: dict[int, str] = field(default_factory=dict)
    company_info: dict[uuid.UUID, dict[str, str | None]] = field(default_factory=dict)
    occupation_names: dict[int, str] = field(default_factory=dict)
    seniority_names: dict[int, str] = field(default_factory=dict)
    technology_names: dict[int, str] = field(default_factory=dict)
    # Ancestor lookup maps for hierarchy-free Typesense filtering
    location_ancestors: dict[int, list[int]] = field(default_factory=dict)
    occupation_ancestors: dict[int, list[int]] = field(default_factory=dict)
    _last_refresh: float = 0.0

    @property
    def stale(self) -> bool:
        return (time.monotonic() - self._last_refresh) > _TAXONOMY_REFRESH_INTERVAL

    async def refresh(
        self,
        local_pool: asyncpg.Pool,
        supa_pool: asyncpg.Pool,
    ) -> None:
        await asyncio.gather(
            self._load_location_names(local_pool),
            self._load_location_geo_types(local_pool),
            self._load_company_info(local_pool),
            self._load_occupation_names(local_pool),
            self._load_occupation_ancestors(local_pool),
            self._load_seniority_names(local_pool),
            self._load_technology_names(local_pool),
            self._load_location_ancestors(local_pool),
        )
        self._last_refresh = time.monotonic()
        log.info(
            "taxonomy_maps.refreshed",
            locations=len(self.location_names),
            companies=len(self.company_info),
            occupations=len(self.occupation_names),
            seniorities=len(self.seniority_names),
            technologies=len(self.technology_names),
        )

    async def _load_location_names(self, pool: asyncpg.Pool) -> None:
        # Filter is_display=true so canonical names win (Los Angeles, Colorado
        # Springs, Maryland) over alternate GeoNames variants (L.A., Colorado
        # Spgs, Old Line State). The location_name table stores aliases and
        # nicknames as separate rows with is_display=false; without this
        # filter, last-write-wins in Postgres heap order picks arbitrary
        # variants for each (location_id, locale) pair. Matches
        # _load_occupation_names / _load_seniority_names below and the
        # location Typesense sync in sync.py:1432.
        rows = await pool.fetch(
            "SELECT location_id, locale, name FROM location_name WHERE is_display = true"
        )
        names: dict[int, dict[str, str]] = {}
        for r in rows:
            loc_id = r["location_id"]
            if loc_id not in names:
                names[loc_id] = {}
            names[loc_id][r["locale"]] = r["name"]
        self.location_names = names

    async def _load_location_geo_types(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT id, type FROM location")
        self.location_types = {r["id"]: r["type"] for r in rows}

    async def _load_company_info(self, pool: asyncpg.Pool) -> None:
        """Load company info from local Postgres (source of truth)."""
        rows = await pool.fetch("SELECT id, name, slug, icon FROM company")
        self.company_info = {
            r["id"]: {"name": r["name"], "slug": r["slug"], "icon": r.get("icon")} for r in rows
        }

    async def _load_occupation_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch(
            "SELECT occupation_id, name FROM occupation_name "
            "WHERE locale = 'en' AND is_display = true"
        )
        self.occupation_names = {r["occupation_id"]: r["name"] for r in rows}

    async def _load_seniority_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch(
            "SELECT seniority_id, name FROM seniority_name "
            "WHERE locale = 'en' AND is_display = true"
        )
        self.seniority_names = {r["seniority_id"]: r["name"] for r in rows}

    async def _load_technology_names(self, pool: asyncpg.Pool) -> None:
        rows = await pool.fetch("SELECT id, name FROM technology")
        self.technology_names = {r["id"]: r["name"] for r in rows}

    async def _load_location_ancestors(
        self,
        local_pool: asyncpg.Pool,
    ) -> None:
        """Build location_id -> [self + all ancestor IDs] map.

        Uses parent_id chain and macro-region membership from local Postgres.
        (location_macro_member links countries to macro regions like EU, DACH.)
        """
        from collections import defaultdict

        # Parent chain from local Postgres
        loc_parents: dict[int, int | None] = {}
        rows = await local_pool.fetch("SELECT id, parent_id FROM location")
        for r in rows:
            loc_parents[r["id"]] = r["parent_id"]

        # Macro-region membership (country_id -> [macro_ids]).
        # Empty / missing -> macros never get stamped onto postings, which
        # silently breaks the EU/EMEA/DACH macro filter (issue #2978). Be
        # loud when the table is empty or unreadable so the operator can
        # re-seed from Supabase, instead of swallowing the failure.
        macro_members: dict[int, list[int]] = defaultdict(list)
        try:
            rows = await local_pool.fetch("SELECT country_id, macro_id FROM location_macro_member")
        except Exception as exc:
            # Table missing or transient DB error — fail open so the
            # exporter keeps running, but make sure we surface it.
            log.warning(
                "exporter.location_macro_member.unreadable",
                error=str(exc),
            )
            rows = []
        for r in rows:
            macro_members[r["country_id"]].append(r["macro_id"])
        if not macro_members:
            # Table exists but is empty: this is the failure mode we hit
            # when the local Postgres has never been seeded with the
            # macro->country links. Postings will be missing macro
            # ancestors in their location_ids until this is fixed.
            log.warning(
                "exporter.location_macro_member.empty",
                hint="seed location_macro_member from Supabase to enable macro filters",
            )

        # Build ancestor map
        ancestors: dict[int, list[int]] = {}
        for lid in loc_parents:
            anc: set[int] = set()
            current: int | None = lid
            while current is not None:
                anc.add(current)
                if current in macro_members:
                    anc.update(macro_members[current])
                current = loc_parents.get(current)
            ancestors[lid] = list(anc)
        self.location_ancestors = ancestors

    async def _load_occupation_ancestors(self, pool: asyncpg.Pool) -> None:
        """Build occupation_id -> [self + all ancestor IDs] map.

        Only occupation IDs belong in ``occupation_ids``. Occupation domains
        use an independent integer identity sequence, so unioning
        ``occupation.domain_id`` into this array makes unrelated values
        collide (for example, Healthcare domain 9 with Data Analyst
        occupation 9). Domain headers are expanded to their first-level
        occupations by the web UI and therefore do not need an index-level
        synthetic ancestor (#3027).
        """
        occ_parents: dict[int, int | None] = {}
        rows = await pool.fetch("SELECT id, parent_id FROM occupation")
        for r in rows:
            occ_parents[r["id"]] = r["parent_id"]

        ancestors: dict[int, list[int]] = {}
        for oid in occ_parents:
            anc: set[int] = set()
            current: int | None = oid
            while current is not None:
                anc.add(current)
                current = occ_parents.get(current)
            ancestors[oid] = list(anc)
        self.occupation_ancestors = ancestors


_taxonomy_maps: TaxonomyMaps | None = None


async def _get_taxonomy_maps(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
) -> TaxonomyMaps:
    """Get or create taxonomy maps, refreshing if stale."""
    global _taxonomy_maps
    if _taxonomy_maps is None:
        _taxonomy_maps = TaxonomyMaps()
        await _taxonomy_maps.refresh(local_pool, supa_pool)
    elif _taxonomy_maps.stale:
        await _taxonomy_maps.refresh(local_pool, supa_pool)
    return _taxonomy_maps


# ---------------------------------------------------------------------------
# Typesense document builder
# ---------------------------------------------------------------------------


def _build_typesense_docs(
    rows: list,
    maps: TaxonomyMaps,
) -> list[dict]:
    """Build Typesense documents from asyncpg rows using taxonomy maps."""
    docs = []
    for row in rows:
        titles = row["titles"]
        title = titles[0] if titles else ""

        company_id = row["company_id"]
        company = maps.company_info.get(company_id, {})
        company_name = company.get("name", "")
        company_slug = company.get("slug", "")
        company_icon = company.get("icon")

        raw_location_ids = row["location_ids"] or []
        location_names = []
        location_geo_types = []
        for loc_id in raw_location_ids:
            loc_name_map = maps.location_names.get(loc_id, {})
            name = loc_name_map.get("en", "")
            if not name and loc_name_map:
                name = next(iter(loc_name_map.values()))
            location_names.append(name)
            location_geo_types.append(maps.location_types.get(loc_id, ""))

        # Expand location_ids with ancestors for hierarchy-free filtering.
        # Leaf IDs come first (aligned with location_names/geo_types arrays),
        # then ancestor-only IDs are appended.  This ensures that filtering
        # by a parent location (e.g. country) matches postings in child
        # locations (e.g. city) even if Postgres only stores leaf IDs.
        ancestor_only: set[int] = set()
        for lid in raw_location_ids:
            ancestor_only.update(maps.location_ancestors.get(lid, [lid]))
        ancestor_only -= set(raw_location_ids)
        expanded_location_ids = list(raw_location_ids) + sorted(ancestor_only)

        occ_id = row["occupation_id"]
        occ_name = maps.occupation_names.get(occ_id) if occ_id else None

        # Expand occupation_id to include ancestors for hierarchy-free filtering
        occ_ids: list[int] | None = None
        if occ_id is not None:
            occ_ids = maps.occupation_ancestors.get(occ_id, [occ_id])

        sen_id = row["seniority_id"]
        sen_name = maps.seniority_names.get(sen_id) if sen_id else None

        tech_ids = row["technology_ids"] or []
        tech_names = [maps.technology_names.get(tid, "") for tid in tech_ids]

        # Experience encoding for Typesense (issues #3217, #3289):
        # - `experience_min_years` / `experience_max_years` are precise float
        #   fields. Sentinel -1.0 means the extractor found no requirement;
        #   open-ended ranges use 99.0 for max.
        # - `experience_min` / `experience_max` remain integer compatibility
        #   fields so existing docs keep matching while the new fields backfill.
        exp_min, exp_max, exp_min_years, exp_max_years = _encode_experience_for_typesense(
            row["experience_min"],
            row["experience_max"],
        )

        locales = row["locales"] or []
        if not locales:
            locales = ["_none"]

        first_seen = row["first_seen_at"]
        first_seen_ts = int(first_seen.timestamp()) if first_seen else 0

        last_seen = row.get("last_seen_at") if hasattr(row, "get") else row["last_seen_at"]
        last_seen_ts = int(last_seen.timestamp()) if last_seen else None

        # `has_content` flag drives the issue #2917 web filter — postings
        # without a usable title or with no description blob in R2 are
        # excluded from search/listing surfaces. Emitted as a boolean on
        # every doc (not gated like `optional` fields) so it always
        # reflects the latest title/description state on update.
        has_content = bool(title and title.strip()) and (row["description_r2_hash"] is not None)

        doc: dict = {
            "id": str(row["id"]),
            # Stable UUID range bucket used by the deploy-independent
            # reconciler. Keeping it in the document avoids whole-index loads
            # and bounds normal scans to 1/256 of the collection.
            "reconciliation_bucket": row["id"].hex[:2],
            "company_id": str(company_id),
            "company_name": company_name,
            "company_slug": company_slug,
            "title": title,
            "is_active": row["is_active"],
            "has_content": has_content,
            "location_ids": expanded_location_ids,
            "location_names": location_names,
            "location_types": list(row["location_types"] or []),
            "location_geo_types": location_geo_types,
            "technology_ids": list(tech_ids),
            "technology_names": tech_names,
            "employment_type": row["employment_type"] or "",
            "experience_min": exp_min,
            "experience_max": exp_max,
            "experience_min_years": exp_min_years,
            "experience_max_years": exp_max_years,
            "locales": list(locales),
            "first_seen_at": first_seen_ts,
        }

        if company_icon:
            doc["company_icon"] = company_icon
        if occ_id is not None:
            doc["occupation_id"] = occ_id
        if occ_ids is not None:
            doc["occupation_ids"] = occ_ids
        if occ_name is not None:
            doc["occupation_name"] = occ_name
        if sen_id is not None:
            doc["seniority_id"] = sen_id
        if sen_name is not None:
            doc["seniority_name"] = sen_name
        if row["salary_eur"] is not None:
            doc["salary_eur"] = row["salary_eur"]
        if row["source_url"]:
            doc["source_url"] = row["source_url"]
        if last_seen_ts is not None:
            doc["last_seen_at"] = last_seen_ts

        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Typesense upsert (instrumented)
# ---------------------------------------------------------------------------


async def _upsert_to_typesense(
    docs: list[dict],
) -> set[str]:
    """Batch upsert documents to Typesense job_posting collection.

    Instruments ``typesense_export_docs_total`` and
    ``typesense_export_duration_seconds``. The typesense client is
    synchronous, so we run it in an executor.

    Returns the set of document IDs that failed to import. The
    Typesense ``import_`` endpoint returns a per-doc result list
    (``[{"success": true|false, "error": "..."}, ...]``) so a single
    bad doc no longer poisons the whole batch (#3180). Each failure is
    logged with the doc id and error string and counted in
    ``export_errors_total{phase="typesense"}``. The caller advances the
    Typesense cursor past failed docs so the exporter doesn't loop on
    them forever.

    A whole-batch transport failure (Typesense unreachable, 5xx, etc.)
    still raises — that's not a per-doc poison-pill, it's a downstream
    incident the caller (``_export_postings_dual``) is expected to
    treat as a leg failure (cursor stays put for that leg).
    """
    from src.typesense_client import get_typesense_client

    # The exporter owns bounded retry timing across ticks. The Typesense
    # client's default three retries are immediate, so leaving them enabled
    # turns one 5s timeout into four back-to-back requests before our cursor
    # safety/backoff code gets control (#5105).
    client = get_typesense_client(num_retries=0)
    if client is None or not docs:
        return set()

    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    try:
        results = await loop.run_in_executor(
            None,
            lambda: client.collections["job_posting"].documents.import_(docs, {"action": "upsert"}),
        )
        duration = time.monotonic() - t0
        typesense_export_duration_seconds.observe(duration)
    except Exception:
        duration = time.monotonic() - t0
        typesense_export_duration_seconds.observe(duration)
        typesense_export_docs_total.labels(status="error").inc(len(docs))
        raise

    # Per-doc result parsing (#3180). The import endpoint returns one
    # dict per submitted doc, in the same order as the input. A typical
    # success entry is ``{"success": true}``; a failure entry is
    # ``{"success": false, "error": "...", "document": "..."}`` — the
    # ``document`` field, when present, is a JSON-encoded copy of the
    # offending input.
    failed_ids: set[str] = set()
    if isinstance(results, list):
        for doc, result in zip(docs, results, strict=False):
            if isinstance(result, dict) and not result.get("success", True):
                doc_id = str(doc.get("id", ""))
                failed_ids.add(doc_id)
                export_errors_total.labels(table="job_posting", phase="typesense").inc()
                # ``[exporter] row dropped`` is the grep anchor for the
                # Loki query ``{app="crawler"} |~ "row dropped"``.
                log.error(
                    "exporter.row_dropped",
                    target="typesense",
                    posting_id=doc_id,
                    error=result.get("error"),
                    code=result.get("code"),
                )

    succeeded = len(docs) - len(failed_ids)
    if succeeded > 0:
        typesense_export_docs_total.labels(status="success").inc(succeeded)
    if failed_ids:
        typesense_export_docs_total.labels(status="error").inc(len(failed_ids))
    return failed_ids


# ---------------------------------------------------------------------------
# Typesense health check
# ---------------------------------------------------------------------------


async def _update_typesense_health(
    backoff: _DownstreamBackoff | None = None,
) -> None:
    """Probe Typesense /health and /metrics.json, update gauges.

    ``client.operations.perform(op)`` in the typesense-python client maps to
    ``POST /operations/{op}`` — not a GET of the named endpoint — so
    ``perform("health")`` hits the non-existent ``POST /operations/health``
    and always returns 404. Use the dedicated convenience methods instead:

    - ``operations.is_healthy()`` → ``GET /health`` (returns ``bool``)
    - ``metrics.retrieve()``      → ``GET /metrics.json`` (returns dict with
      ``typesense_memory_active_bytes`` and friends)

    Note: the memory fields live on ``/metrics.json``, not ``/stats.json`` —
    ``stats.json`` only carries per-second request counts and latencies.
    """
    from src.typesense_client import get_typesense_client

    client = get_typesense_client(num_retries=0)
    if client is None:
        return

    loop = asyncio.get_running_loop()
    try:
        is_healthy = await loop.run_in_executor(
            None,
            client.operations.is_healthy,
        )
        typesense_healthy.set(1 if is_healthy else 0)
        if not is_healthy:
            if backoff is not None:
                retry_in = backoff.record_failure()
                log.warning(
                    "exporter.typesense_unhealthy",
                    retry_in_s=round(retry_in, 2),
                    consecutive_failures=backoff.consecutive_failures,
                )
            return
        if backoff is not None:
            _record_downstream_recovery(backoff)
    except Exception as exc:
        typesense_healthy.set(0)
        fields = _exc_fields(exc)
        if backoff is not None and _is_downstream_unavailable(exc):
            retry_in = backoff.record_failure()
            fields.update(
                retry_in_s=round(retry_in, 2),
                consecutive_failures=backoff.consecutive_failures,
            )
        log.warning("exporter.typesense_health_error", exc_info=True, **fields)
        # Do not immediately follow a timed-out /health request with another
        # request to /metrics.json. That doubled the timeout chain in #5105.
        return

    try:
        metrics = await loop.run_in_executor(
            None,
            client.metrics.retrieve,
        )
        mem = metrics.get("typesense_memory_active_bytes") or metrics.get(
            "typesense_memory_allocated_bytes"
        )
        if mem is not None:
            typesense_memory_bytes.set(int(mem))
    except Exception:
        log.warning("exporter.typesense_metrics_error", exc_info=True)


# ---------------------------------------------------------------------------
# Dual export helpers
# ---------------------------------------------------------------------------


def _cursor_gt(row_ts: datetime, row_id: uuid.UUID, cursor: Cursor) -> bool:
    """Return True if (row_ts, row_id) > cursor."""
    c_ts, c_id = cursor
    return (row_ts, row_id) > (c_ts, c_id)


def _min_cursor(a: Cursor, b: Cursor) -> Cursor:
    """Return the smaller of two cursors."""
    return a if a <= b else b


async def _noop() -> None:
    """No-op coroutine for gather slots."""


def _exc_fields(exc: BaseException) -> dict[str, object]:
    """Structured fields for logging an exception caught by ``asyncio.gather``.

    ``str(exc)`` alone is empty for several common failure modes (CancelledError,
    bare asyncpg errors, httpx errors with no body), which leaves the log line
    useless for diagnosis. See issue #2621.
    """
    fields: dict[str, object] = {
        "error_type": type(exc).__name__,
        "error": str(exc) or repr(exc),
    }
    # PostgresError carries richer fields than str(exc) (which is just message).
    for attr in ("detail", "hint", "sqlstate"):
        value = getattr(exc, attr, None)
        if value:
            fields[attr] = value
    # httpx.HTTPStatusError — surface the status + body snippet.
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None:
            fields["http_status"] = status
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            fields["http_body"] = text[:500]
    return fields


_ROW_POISON_SQLSTATE_CLASSES = frozenset({"21", "22", "23"})
_DOWNSTREAM_UNAVAILABLE_SQLSTATES = frozenset(
    {
        "53300",  # too_many_connections
        "57P03",  # cannot_connect_now
    }
)


def _is_supabase_row_poison(exc: BaseException) -> bool:
    """Return whether *exc* is safe to isolate and skip as one bad row.

    Only PostgreSQL cardinality, data, and integrity violations are known to
    be caused by the row being written.  Timeouts, connection failures,
    resource exhaustion, operator intervention, schema errors, and unknown
    exceptions are target-wide failures: treating any of those as row poison
    would advance the CDC cursor past data that Supabase never acknowledged
    (#5231).

    SQLSTATE class references:
    - 21: cardinality violation (a batch may need per-row isolation)
    - 22: data exception (invalid/out-of-range row value)
    - 23: integrity constraint violation (FK/unique/NOT NULL, etc.)
    """
    sqlstate = getattr(exc, "sqlstate", None)
    return (
        isinstance(exc, asyncpg.PostgresError)
        and isinstance(sqlstate, str)
        and sqlstate[:2] in _ROW_POISON_SQLSTATE_CLASSES
    )


def _is_downstream_unavailable(exc: BaseException) -> bool:
    """Return whether an error represents target-wide unavailability.

    SQLSTATE class 08 covers connection exceptions such as the production
    ``ConnectionFailureError`` (08006). Pool/network timeouts and socket
    failures may arrive as built-in exceptions before asyncpg can attach a
    SQLSTATE. Capacity and startup/shutdown rejections are also target-wide.
    Row-local and unknown failures deliberately do not open the circuit.
    """
    sqlstate = getattr(exc, "sqlstate", None)
    return (
        (isinstance(sqlstate, str) and sqlstate.startswith("08"))
        or sqlstate in _DOWNSTREAM_UNAVAILABLE_SQLSTATES
        or isinstance(exc, (TimeoutError, ConnectionError, OSError, httpx.RequestError))
    )


def _record_downstream_recovery(backoff: _DownstreamBackoff) -> None:
    recovery = backoff.record_success()
    if recovery is None:
        return
    failed_attempts, outage_seconds = recovery
    log.info(
        "exporter.downstream_recovered",
        target=backoff.target,
        failed_attempts=failed_attempts,
        outage_duration_s=round(outage_seconds, 2),
    )


async def _upsert_to_supabase(
    supa_pool: asyncpg.Pool,
    rows: list,
) -> set[uuid.UUID]:
    """Upsert rows to Supabase, falling back to per-row on batch failure.

    Fast path: a single COPY-into-temp-table + ``INSERT ... ON CONFLICT``
    inside one transaction. If any row in the batch trips a constraint
    (FK on company_id/board_id/occupation_id, value-out-of-range on
    salary, unique-index conflict on source_url, etc.), the whole
    transaction rolls back — this is the poison-pill failure mode that
    used to halt CDC forever (#3180).

    Fallback: when PostgreSQL identifies the batch failure as a row-local
    cardinality, data, or integrity violation, re-attempt each row in its
    own transaction. Successful rows commit; deterministic poison rows are
    logged + counted in ``export_errors_total{phase="supabase"}`` and
    returned in the set of failed IDs so the caller advances past them.

    Target-wide and unknown failures escape instead. The caller then keeps
    the cursor pinned and replays the batch on the next tick. This is safe
    even if some fallback rows committed before a transient interruption,
    because every write is an idempotent ``INSERT ... ON CONFLICT UPDATE``.

    The caller is expected to advance the cursor to the last row's
    ``(updated_at, id)`` regardless of which rows failed — the
    successfully-upserted rows are already in Supabase, the failed
    ones are dropped on purpose.
    """
    if not rows:
        return set()

    col_names = PostingSchema.column_names()

    # Fast path: batch INSERT inside a single transaction.
    try:
        async with supa_pool.acquire() as conn, conn.transaction():
            await conn.execute(PostingSchema.temp_table_ddl())

            await conn.copy_records_to_table(
                "_export_postings",
                records=[tuple(r[c] for c in col_names) for r in rows],
                columns=col_names,
            )

            await conn.execute(PostingSchema.insert_from_temp_sql())
        return set()
    except Exception as batch_exc:
        if not _is_supabase_row_poison(batch_exc):
            # A timeout, broken connection, overloaded/shutting-down server,
            # schema error, or unknown exception says nothing about the row.
            # Propagate it so the caller retains the cursor (#5231).
            log.warning(
                "exporter.supabase_batch_retryable",
                batch_size=len(rows),
                **_exc_fields(batch_exc),
            )
            raise

        # A deterministic row-local failure poisoned the whole transaction.
        # Fall back to per-row upserts so the surviving N-1 rows still land
        # in Supabase and the cursor can advance (#3180).
        log.warning(
            "exporter.supabase_batch_failed_falling_back",
            batch_size=len(rows),
            **_exc_fields(batch_exc),
        )

    return await _upsert_to_supabase_per_row(supa_pool, rows)


async def _upsert_to_supabase_per_row(
    supa_pool: asyncpg.Pool,
    rows: list,
) -> set[uuid.UUID]:
    """Per-row Supabase upsert fallback used after a batch failure (#3180).

    Each row gets its own transaction. Successful rows commit; deterministic
    row-local failures are logged, counted in ``export_errors_total``, and
    returned in the failed-ID set so the caller can advance past them.

    A target-wide or unknown failure interrupts the fallback and escapes.
    The cursor remains pinned, and the next tick safely replays any earlier
    rows that committed before the interruption.

    Extracted from ``_upsert_to_supabase`` so it can be unit-tested in
    isolation and so the fast-path COPY stays the steady-state hot path.
    """
    col_names = PostingSchema.column_names()
    insert_sql = PostingSchema.insert_values_sql()

    failed_ids: set[uuid.UUID] = set()
    async with supa_pool.acquire() as conn:
        for row in rows:
            row_id = row["id"]
            values = tuple(row[c] for c in col_names)
            try:
                async with conn.transaction():
                    await conn.execute(insert_sql, *values)
            except Exception as exc:
                if not _is_supabase_row_poison(exc):
                    log.warning(
                        "exporter.supabase_fallback_retryable",
                        posting_id=str(row_id),
                        **_exc_fields(exc),
                    )
                    raise
                failed_ids.add(row_id)
                export_errors_total.labels(table="job_posting", phase="supabase").inc()
                # ``[exporter] row dropped`` is the grep anchor for the
                # Loki query ``{app="crawler"} |~ "row dropped"``.
                log.error(
                    "exporter.row_dropped",
                    target="supabase",
                    posting_id=str(row_id),
                    **_exc_fields(exc),
                )
    return failed_ids


async def _export_postings_dual(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    supa_cursor: Cursor,
    ts_cursor: Cursor,
    maps: TaxonomyMaps,
    supa_backoff: _DownstreamBackoff | None = None,
    ts_backoff: _DownstreamBackoff | None = None,
    *,
    cutoff: datetime = _MAX_CDC_CUTOFF,
) -> tuple[int, Cursor, Cursor]:
    """Fetch changed postings and upsert to both Supabase and Typesense concurrently.

    Uses two-cursor design: SELECT from MIN(supa_cursor, ts_cursor), then
    post-filter rows for each target. Returns (total_fetched, new_supa_cursor, new_ts_cursor).
    """
    supa_ready = supa_backoff is None or supa_backoff.ready()
    ts_ready = ts_backoff is None or ts_backoff.ready()
    if not supa_ready and supa_backoff is not None:
        # Supabase's cursor remains pinned, but reading from it on every
        # one-second tick would replay the same local batch throughout the
        # outage. Read from Typesense's cursor until the retry deadline so
        # that independent search indexing can continue without local churn.
        supa_backoff.record_skip()
    if not ts_ready and ts_backoff is not None:
        ts_backoff.record_skip()

    if not supa_ready and not ts_ready:
        return 0, supa_cursor, ts_cursor
    if supa_ready and ts_ready:
        fetch_cursor = _min_cursor(supa_cursor, ts_cursor)
    elif supa_ready:
        fetch_cursor = supa_cursor
    else:
        fetch_cursor = ts_cursor
    fetch_ts, fetch_id = fetch_cursor

    rows = await local_pool.fetch(
        PostingSchema.select_changed_sql("last_seen_at", "updated_at"),
        fetch_ts,
        fetch_id,
        settings.export_batch_limit,
        cutoff,
    )
    if not rows:
        return 0, supa_cursor, ts_cursor

    supa_rows = (
        [r for r in rows if _cursor_gt(r["updated_at"], r["id"], supa_cursor)] if supa_ready else []
    )
    ts_rows = (
        [r for r in rows if _cursor_gt(r["updated_at"], r["id"], ts_cursor)] if ts_ready else []
    )

    tasks = []

    if supa_rows:
        tasks.append(_upsert_to_supabase(supa_pool, supa_rows))
    else:
        tasks.append(_noop())

    if ts_rows:
        docs = _build_typesense_docs(ts_rows, maps)
        tasks.append(_upsert_to_typesense(docs))
    else:
        tasks.append(_noop())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    new_supa_cursor = supa_cursor
    if supa_rows:
        if isinstance(results[0], BaseException):
            # A bare exception escaping the upsert means the per-row
            # fallback itself blew up (e.g. pool/network unavailable),
            # not a single poison-pill row. Keep the cursor where it is
            # so we retry next tick — same shape as the original
            # implementation. ``_upsert_to_supabase`` swallows per-row
            # poison-pills and returns them as a set instead.
            fields = _exc_fields(results[0])
            if supa_backoff is not None and _is_downstream_unavailable(results[0]):
                retry_in = supa_backoff.record_failure()
                fields.update(
                    retry_in_s=round(retry_in, 2),
                    consecutive_failures=supa_backoff.consecutive_failures,
                )
            log.error("exporter.supabase_upsert_error", **fields)
        else:
            if supa_backoff is not None:
                _record_downstream_recovery(supa_backoff)
            failed_supa_ids: set[uuid.UUID] = results[0] or set()
            last = supa_rows[-1]
            # Advance the cursor past the whole batch even if some rows
            # failed — successful rows are already in Supabase, the
            # failed rows are dropped on purpose (logged + counted in
            # ``export_errors_total``; quarantine table is a follow-up).
            # Not advancing past failures was the poison-pill bug (#3180).
            new_supa_cursor = (last["updated_at"], last["id"])
            # Lifecycle anchor: surface a few sample posting_ids so an
            # operator with the posting_id from a public URL can grep
            # for "was THIS posting in any recent batch?" without
            # filtering by raw counts only (#3192).
            log.info(
                "exporter.exported_postings",
                target="supabase",
                batch_size=len(supa_rows),
                succeeded=len(supa_rows) - len(failed_supa_ids),
                failed=len(failed_supa_ids),
                sample_ids=[str(r["id"]) for r in supa_rows[:5]],
            )

    new_ts_cursor = ts_cursor
    if ts_rows:
        if isinstance(results[1], BaseException):
            # Whole-batch transport failure (Typesense unreachable, 5xx,
            # etc.) — cursor stays put so we retry next tick. Per-doc
            # failures are returned as a set, not raised, so this branch
            # is now strictly for downstream incidents.
            fields = _exc_fields(results[1])
            if ts_backoff is not None and _is_downstream_unavailable(results[1]):
                retry_in = ts_backoff.record_failure()
                fields.update(
                    retry_in_s=round(retry_in, 2),
                    consecutive_failures=ts_backoff.consecutive_failures,
                )
            log.error("exporter.typesense_upsert_error", **fields)
        else:
            if ts_backoff is not None:
                _record_downstream_recovery(ts_backoff)
            failed_ts_ids: set[str] = results[1] or set()
            last = ts_rows[-1]
            # Advance past the whole batch even if some docs failed —
            # see the matching comment on the Supabase leg above.
            new_ts_cursor = (last["updated_at"], last["id"])
            log.info(
                "exporter.exported_postings",
                target="typesense",
                batch_size=len(ts_rows),
                succeeded=len(ts_rows) - len(failed_ts_ids),
                failed=len(failed_ts_ids),
                sample_ids=[str(r["id"]) for r in ts_rows[:5]],
            )

    return len(rows), new_supa_cursor, new_ts_cursor


# ---------------------------------------------------------------------------
# Export: changed job postings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostingColumn:
    name: str
    temp_type: str


class PostingSchema:
    """Single source of truth for job_posting export SQL.

    The exporter reads from local Postgres, COPYs a fixed column subset
    into a Supabase temp table, and upserts from there. Keeping the names,
    temp-table types, and upsert set together avoids silent drift when the
    exported posting shape changes.
    """

    table = "job_posting"
    temp_table = "_export_postings"
    columns: tuple[PostingColumn, ...] = (
        PostingColumn("id", "UUID"),
        PostingColumn("company_id", "UUID"),
        PostingColumn("board_id", "UUID"),
        PostingColumn("source_url", "TEXT"),
        PostingColumn("is_active", "BOOLEAN"),
        PostingColumn("titles", "TEXT[]"),
        PostingColumn("locales", "TEXT[]"),
        PostingColumn("location_ids", "INT[]"),
        PostingColumn("location_types", "TEXT[]"),
        PostingColumn("employment_type", "TEXT"),
        PostingColumn("salary_min", "INT"),
        PostingColumn("salary_max", "INT"),
        PostingColumn("salary_currency", "TEXT"),
        PostingColumn("salary_period", "TEXT"),
        PostingColumn("salary_eur", "INT"),
        PostingColumn("experience_min", "NUMERIC(3,1)"),
        PostingColumn("experience_max", "NUMERIC(3,1)"),
        PostingColumn("occupation_id", "INT"),
        PostingColumn("seniority_id", "INT"),
        PostingColumn("technology_ids", "INT[]"),
        PostingColumn("description_r2_hash", "BIGINT"),
        PostingColumn("first_seen_at", "TIMESTAMPTZ"),
    )
    upsert_columns: tuple[str, ...] = (
        "is_active",
        "titles",
        "locales",
        "location_ids",
        "location_types",
        "employment_type",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_eur",
        "experience_min",
        "experience_max",
        "occupation_id",
        "seniority_id",
        "technology_ids",
        "description_r2_hash",
    )

    @classmethod
    def column_names(cls) -> tuple[str, ...]:
        return tuple(column.name for column in cls.columns)

    @classmethod
    def column_list(cls) -> str:
        return ", ".join(cls.column_names())

    @classmethod
    def select_list(cls, *extras: str) -> str:
        return ", ".join((*cls.column_names(), *extras))

    @classmethod
    def placeholders(cls) -> str:
        return ", ".join("$" + str(idx) for idx in range(1, len(cls.columns) + 1))

    @classmethod
    def upsert_set(cls) -> str:
        return ", ".join(column + " = EXCLUDED." + column for column in cls.upsert_columns)

    @classmethod
    def temp_table_ddl(cls) -> str:
        columns = ", ".join(column.name + " " + column.temp_type for column in cls.columns)
        return "CREATE TEMP TABLE " + cls.temp_table + " (" + columns + ") ON COMMIT DROP"

    @classmethod
    def insert_from_temp_sql(cls) -> str:
        column_list = cls.column_list()
        return (
            "INSERT INTO "
            + cls.table
            + " ("
            + column_list
            + ") SELECT "
            + column_list
            + " FROM "
            + cls.temp_table
            + " ON CONFLICT (id) DO UPDATE SET "
            + cls.upsert_set()
        )

    @classmethod
    def insert_values_sql(cls) -> str:
        return (
            "INSERT INTO "
            + cls.table
            + " ("
            + cls.column_list()
            + ") VALUES ("
            + cls.placeholders()
            + ") ON CONFLICT (id) DO UPDATE SET "
            + cls.upsert_set()
        )

    @classmethod
    def select_changed_sql(cls, *extras: str) -> str:
        return (
            "SELECT "
            + cls.select_list(*extras)
            + " FROM "
            + cls.table
            + " WHERE (updated_at, id) > ($1, $2)"
            + " AND updated_at < $4 ORDER BY updated_at, id LIMIT $3"
        )


# Backward-compatible string aliases for existing tests and diagnostics.
_POSTING_COLUMNS = PostingSchema.column_list()
_POSTING_UPSERT_SET = PostingSchema.upsert_set()


async def _export_changed_postings(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    cursor: Cursor,
    *,
    cutoff: datetime = _MAX_CDC_CUTOFF,
) -> tuple[int, Cursor]:
    """Export job_posting rows changed since cursor to Supabase.

    Uses keyset pagination on (updated_at, id) to avoid skipping rows
    when many share the same updated_at timestamp (e.g. bulk mark-gone).
    Returns (count_exported, new_cursor).

    Delegates to ``_upsert_to_supabase`` so the per-row fallback path
    (#3180) covers the Typesense-disabled deployment too. A bad row no
    longer halts the cursor.
    """
    last_ts, last_id = cursor
    rows = await local_pool.fetch(
        PostingSchema.select_changed_sql("updated_at"),
        last_ts,
        last_id,
        settings.export_batch_limit,
        cutoff,
    )
    if not rows:
        return 0, cursor

    failed_ids = await _upsert_to_supabase(supa_pool, rows)

    # Lifecycle anchor: surface sample posting_ids so an operator can
    # grep "was THIS posting in any recent Supabase batch?" (#3192).
    log.info(
        "exporter.exported_postings",
        target="supabase",
        batch_size=len(rows),
        succeeded=len(rows) - len(failed_ids),
        failed=len(failed_ids),
        sample_ids=[str(r["id"]) for r in rows[:5]],
    )

    last_row = rows[-1]
    # Cursor advances past the entire batch even when some rows were
    # dropped by the per-row fallback (#3180). The failed rows have
    # already been logged and counted in ``export_errors_total``.
    new_cursor = (last_row["updated_at"], last_row["id"])
    return len(rows), new_cursor


# ---------------------------------------------------------------------------
# Export: changed board status
# ---------------------------------------------------------------------------


async def _export_changed_boards(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    cursor: Cursor,
) -> tuple[int, Cursor]:
    """Export job_board status rows changed since cursor to Supabase.

    Row-by-row UPDATE is intentional -- board status changes are rare.
    Returns (count_exported, new_cursor).
    """
    last_ts, last_id = cursor
    rows = await local_pool.fetch(
        "SELECT id, board_status, last_error, is_enabled, updated_at "
        "FROM job_board WHERE (updated_at, id) > ($1, $2) "
        "ORDER BY updated_at, id LIMIT $3",
        last_ts,
        last_id,
        settings.export_batch_limit,
    )
    if not rows:
        return 0, cursor

    async with supa_pool.acquire() as conn:
        for row in rows:
            await conn.execute(
                "UPDATE job_board SET board_status = $2, last_error = $3, "
                "is_enabled = $4, updated_at = $5 WHERE id = $1",
                row["id"],
                row["board_status"],
                row["last_error"],
                row["is_enabled"],
                row["updated_at"],
            )

    last_row = rows[-1]
    new_cursor = (last_row["updated_at"], last_row["id"])
    return len(rows), new_cursor


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


async def _update_metrics(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    posting_cursor: Cursor,
    ts_cursor: Cursor | None = None,
    *,
    probe_typesense_health: bool = True,
    ts_backoff: _DownstreamBackoff | None = None,
) -> None:
    """Update Prometheus gauges with queue depths, export lag, and R2 pending."""
    try:
        depths = await get_queue_depths()
        for key, count in depths.items():
            redis_queue_depth.labels(queue=key).set(count)
        redis_connected.set(1)
    except Exception:
        redis_connected.set(0)
        log.warning("exporter.metrics_redis_error", exc_info=True)

    try:
        last_ts, last_id = posting_cursor
        lag = await local_pool.fetchval(
            "SELECT count(*) FROM job_posting WHERE (updated_at, id) > ($1, $2)",
            last_ts,
            last_id,
        )
        exporter_export_lag.labels(table="job_posting").set(lag or 0)
    except Exception:
        log.warning("exporter.metrics_lag_error", exc_info=True)

    # Typesense export lag
    if ts_cursor is not None:
        try:
            ts_ts, ts_id = ts_cursor
            ts_lag = await local_pool.fetchval(
                "SELECT count(*) FROM job_posting WHERE (updated_at, id) > ($1, $2)",
                ts_ts,
                ts_id,
            )
            typesense_export_lag.set(ts_lag or 0)
        except Exception:
            log.warning("exporter.metrics_typesense_lag_error", exc_info=True)

    try:
        pending = await local_pool.fetchval(
            "SELECT count(*) FROM descriptions WHERE r2_uploaded = false"
        )
        r2_pending_gauge.set(pending or 0)
    except Exception:
        log.warning("exporter.metrics_r2_pending_error", exc_info=True)

    # Typesense health check
    if _typesense_enabled() and probe_typesense_health:
        try:
            if ts_backoff is None or ts_backoff.ready():
                await _update_typesense_health(ts_backoff)
        except Exception:
            log.warning("exporter.metrics_typesense_health_error", exc_info=True)

    # Pool stats
    local_db_pool_size.set(local_pool.get_size())
    local_db_pool_idle.set(local_pool.get_idle_size())
    supa_db_pool_size.set(supa_pool.get_size())
    supa_db_pool_idle.set(supa_pool.get_idle_size())


# ---------------------------------------------------------------------------
# Main export loop
# ---------------------------------------------------------------------------


def _typesense_enabled() -> bool:
    """Check if Typesense integration is enabled."""
    return bool(settings.typesense_admin_key)


async def run_exporter(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
    shutdown_event: asyncio.Event,
    *,
    cursor_fence_factory: CursorFenceFactory = export_cursor_fence,
    cutoff_factory: CutoffFactory = capture_cdc_snapshot_cutoff,
) -> None:
    """Main exporter loop.

    Queries local Postgres for changed rows, COPYs to Supabase,
    and upserts to Typesense when enabled.
    Runs every ``settings.export_interval`` seconds until *shutdown_event*
    is set.
    """
    interval = settings.export_interval
    posting_cursor = await _get_cursor(local_pool, "job_posting")
    supa_backoff = _DownstreamBackoff(
        target="supabase",
        base_seconds=settings.export_downstream_backoff_base_seconds,
        max_seconds=settings.export_downstream_backoff_max_seconds,
    )
    ts_enabled = _typesense_enabled()
    ts_cursor: Cursor = (_EPOCH, _ZERO_UUID)
    maps: TaxonomyMaps | None = None
    ts_backoff: _DownstreamBackoff | None = None
    next_typesense_health_at = 0.0

    if ts_enabled:
        ts_backoff = _DownstreamBackoff(
            target="typesense",
            base_seconds=settings.export_downstream_backoff_base_seconds,
            max_seconds=settings.export_downstream_backoff_max_seconds,
        )
        ts_cursor = await _get_cursor(local_pool, "typesense:job_posting")
        maps = await _get_taxonomy_maps(local_pool, supa_pool)
        log.info("exporter.typesense_enabled")

    while not shutdown_event.is_set():
        t0 = time.monotonic()
        try:
            # Serialize the mutable-row read and cursor save with operator
            # repairs.  Without this fence, a bulk UPDATE can choose its
            # timestamp before this statement snapshot, commit afterwards,
            # and be skipped permanently when this tick advances the cursor.
            async with cursor_fence_factory(local_pool):
                if ts_enabled and maps is not None:
                    # Refresh taxonomy maps if stale
                    if maps.stale:
                        await maps.refresh(local_pool, supa_pool)

                    # Capture a nonblocking clock cutoff no later than the
                    # oldest current writer transaction. Rows stamped by that
                    # writer or a later one stay above the strict upper bound.
                    cutoff = await cutoff_factory(local_pool)

                    # Two-cursor dual export
                    exported, posting_cursor, ts_cursor = await _export_postings_dual(
                        local_pool,
                        supa_pool,
                        posting_cursor,
                        ts_cursor,
                        maps,
                        supa_backoff,
                        ts_backoff,
                        cutoff=cutoff,
                    )
                    # Save both cursors in a single transaction so a crash
                    # between writes cannot leave one cursor advanced while
                    # the other is stale (issue #3171).
                    await _save_cursors_atomic(
                        local_pool,
                        [
                            ("job_posting", posting_cursor),
                            ("typesense:job_posting", ts_cursor),
                        ],
                    )
                else:
                    # Supabase-only export (original path)
                    if not supa_backoff.ready():
                        supa_backoff.record_skip()
                        exported = 0
                    else:
                        try:
                            cutoff = await cutoff_factory(local_pool)
                            exported, posting_cursor = await _export_changed_postings(
                                local_pool,
                                supa_pool,
                                posting_cursor,
                                cutoff=cutoff,
                            )
                        except Exception as exc:
                            if not _is_downstream_unavailable(exc):
                                raise
                            retry_in = supa_backoff.record_failure()
                            log.error(
                                "exporter.supabase_upsert_error",
                                retry_in_s=round(retry_in, 2),
                                consecutive_failures=supa_backoff.consecutive_failures,
                                **_exc_fields(exc),
                            )
                            exported = 0
                        else:
                            _record_downstream_recovery(supa_backoff)
                    await _save_cursor(local_pool, "job_posting", posting_cursor)

            probe_typesense_health = ts_enabled and t0 >= next_typesense_health_at
            if probe_typesense_health:
                next_typesense_health_at = t0 + settings.typesense_health_interval_seconds
            await _update_metrics(
                local_pool,
                supa_pool,
                posting_cursor,
                ts_cursor=ts_cursor if ts_enabled else None,
                probe_typesense_health=probe_typesense_health,
                ts_backoff=ts_backoff,
            )

            duration = time.monotonic() - t0
            exporter_flush_duration.observe(duration)
            exporter_last_flush_ts.set(time.time())
            if exported:
                exporter_rows_exported.labels(table="job_posting").inc(exported)

            log.info(
                "exporter.tick",
                exported=exported,
                duration_s=round(duration, 2),
            )
        except Exception:
            log.exception("exporter.tick_error")

        # Sleep for *interval* seconds, but wake early on shutdown.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)


# ---------------------------------------------------------------------------
# Backfill: full Typesense re-index
# ---------------------------------------------------------------------------


async def _upsert_typesense_backfill_batch(
    docs: list[dict],
    *,
    batch_start: str | None = None,
    max_attempts: int = 5,
    base_delay_s: float = 2.0,
) -> None:
    """Upsert one backfill batch without permitting silent gaps.

    A transport timeout can happen after Typesense has accepted the request,
    so retrying the idempotent upsert is safe.  Exhausted retries and
    per-document import failures both abort the full backfill; its caller must
    not advance or persist the scan cursor past documents that were not
    confirmed written.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            failed_ids = await _upsert_to_typesense(docs)
            if failed_ids:
                raise RuntimeError(
                    f"Typesense rejected {len(failed_ids)} documents in backfill batch"
                )
            return
        except Exception as exc:
            if attempt == max_attempts:
                log.exception(
                    "backfill.typesense_upsert_failed",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    batch_size=len(docs),
                    batch_start=batch_start,
                )
                raise

            delay_s = base_delay_s * (2 ** (attempt - 1))
            log.warning(
                "backfill.typesense_upsert_retry",
                attempt=attempt,
                max_attempts=max_attempts,
                retry_in_s=delay_s,
                batch_size=len(docs),
                batch_start=batch_start,
                **_exc_fields(exc),
            )
            await asyncio.sleep(delay_s)


async def backfill_typesense(
    local_pool: asyncpg.Pool,
    supa_pool: asyncpg.Pool,
) -> None:
    """Iterate ALL job_posting rows and upsert to Typesense.

    Used for initial population or full re-sync. Logs progress every 10K rows.
    """
    maps = await _get_taxonomy_maps(local_pool, supa_pool)

    cursor: Cursor = (_EPOCH, _ZERO_UUID)
    total = 0
    batch_size = settings.export_batch_limit

    while True:
        last_ts, last_id = cursor
        rows = await local_pool.fetch(
            PostingSchema.select_changed_sql("last_seen_at", "updated_at"),
            last_ts,
            last_id,
            batch_size,
        )
        if not rows:
            break

        # Refresh maps periodically during long backfills
        if maps.stale:
            await maps.refresh(local_pool, supa_pool)

        docs = _build_typesense_docs(rows, maps)
        await _upsert_typesense_backfill_batch(docs, batch_start=str(cursor))
        typesense_backfill_docs_total.inc(len(docs))

        last_row = rows[-1]
        cursor = (last_row["updated_at"], last_row["id"])
        total += len(rows)

        if total % 10_000 < batch_size:
            log.info("backfill.progress", total=total)

    # Save the final cursor so the CDC exporter picks up from here
    await _save_cursor(local_pool, "typesense:job_posting", cursor)
    log.info("backfill.completed", total=total)
