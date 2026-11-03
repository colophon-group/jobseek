"""Offline Typesense projection and reconciliation helpers; no polling exporter."""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import asyncpg
import structlog

from src.metrics import (
    export_errors_total,
    typesense_export_docs_total,
    typesense_export_duration_seconds,
)
from src.typesense_candidate_order import (
    candidate_order_fields,
)

__all__ = [
    "PostingSchema",
    "TaxonomyMaps",
    "_POSTING_COLUMNS",
    "_POSTING_UPSERT_SET",
    "_build_typesense_docs",
    "_get_taxonomy_maps",
    "_upsert_to_typesense",
]

log = structlog.get_logger()


_EXPERIENCE_MAX_OPEN_ENDED = 99


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

    min_years = float(cast(Any, exp_min))
    if exp_max is None:
        max_years = float(_EXPERIENCE_MAX_OPEN_ENDED)
        legacy_max = _EXPERIENCE_MAX_OPEN_ENDED
    else:
        max_years = float(cast(Any, exp_max))
        legacy_max = math.floor(max_years)

    legacy_min = math.ceil(min_years)
    return legacy_min, legacy_max, min_years, max_years


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
        # restore the local seed data instead of swallowing the failure.
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
                hint="restore local location_macro_member seed data to enable macro filters",
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
) -> TaxonomyMaps:
    """Get or create taxonomy maps, refreshing if stale."""
    global _taxonomy_maps
    if _taxonomy_maps is None:
        _taxonomy_maps = TaxonomyMaps()
        await _taxonomy_maps.refresh(local_pool)
    elif _taxonomy_maps.stale:
        await _taxonomy_maps.refresh(local_pool)
    return _taxonomy_maps


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
        # Production CDC rows include these fields from a direct company JOIN.
        # Keep the map fallback for callers/tests that build documents from a
        # legacy posting-only row shape.
        company_name = row.get("company_name") or company.get("name", "")
        company_slug = row.get("company_slug") or company.get("slug", "")
        company_icon = row.get("company_icon") or company.get("icon")

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
            occ_ids = sorted(maps.occupation_ancestors.get(occ_id, [occ_id]))

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

        posting_id = str(row["id"])
        # Required candidate reads only search active postings. Clearing these
        # optional fields on delist keeps old postings out of the sort index;
        # a relist upsert restores them before the posting becomes eligible.
        doc: dict = {
            "id": posting_id,
            **candidate_order_fields(row["id"], active=bool(row["is_active"])),
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
            "tdm_reserved": row.get("tdm_reserved", False),
            "location_ids": expanded_location_ids,
            "location_direct_ids": list(raw_location_ids),
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
        if row["salary_min"] is not None:
            doc["salary_min"] = row["salary_min"]
        if row["salary_max"] is not None:
            doc["salary_max"] = row["salary_max"]
        if row["salary_currency"]:
            doc["salary_currency"] = row["salary_currency"]
        if row["salary_period"]:
            doc["salary_period"] = row["salary_period"]
        if row["source_url"]:
            doc["source_url"] = row["source_url"]
        if last_seen_ts is not None:
            doc["last_seen_at"] = last_seen_ts

        docs.append(doc)
    return docs


class _TypesenseAcknowledgementError(RuntimeError):
    """Typesense returned a response that cannot prove the batch was handled."""


def _is_typesense_acknowledgement_parse_failure(exc: BaseException) -> bool:
    """Recognize only JSON decoding failures from the import response parser.

    Typesense Python 2.0 wraps ``JSONDecodeError`` in ``TypesenseClientError``;
    other client versions may propagate the decoder error directly. Walking
    the explicit exception chain handles both without treating unrelated
    ``ValueError`` or client validation errors as acknowledgement failures.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, json.JSONDecodeError):
            return True
        current = current.__cause__ or current.__context__
    return False


def _validate_typesense_acknowledgements(
    docs: list[dict],
    results: object,
) -> list[dict[str, Any]]:
    """Require one explicit boolean acknowledgement per submitted document.

    The CDC cursor may advance only after Typesense has accounted for every
    document in the batch. A malformed response is therefore a target-wide
    protocol failure, not a collection of per-document rejections: none of its
    entries are trusted and the whole batch must be retried idempotently.
    """
    reason: str | None = None
    invalid_index: int | None = None
    if not isinstance(results, list):
        reason = "response_not_list"
    elif len(results) != len(docs):
        reason = "cardinality_mismatch"
    else:
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                reason = "item_not_object"
                invalid_index = index
                break
            if "success" not in result:
                reason = "success_missing"
                invalid_index = index
                break
            if type(result["success"]) is not bool:
                reason = "success_not_boolean"
                invalid_index = index
                break

    if reason is not None:
        acknowledged_count = len(results) if isinstance(results, list) else None
        log.error(
            "exporter.typesense_invalid_acknowledgement",
            reason=reason,
            expected_count=len(docs),
            acknowledged_count=acknowledged_count,
            invalid_index=invalid_index,
            response_type=type(results).__name__,
        )
        raise _TypesenseAcknowledgementError(
            f"Typesense import acknowledgement was invalid: {reason}"
        ) from None

    return cast(list[dict[str, Any]], results)


async def _upsert_to_typesense(
    docs: list[dict],
    *,
    log_rejected_documents: bool = True,
) -> set[str]:
    """Batch upsert documents to Typesense job_posting collection.

    Instruments ``typesense_export_docs_total`` and
    ``typesense_export_duration_seconds``. The typesense client is
    synchronous, so we run it in an executor.

    Returns the set of document IDs that failed to import. The
    Typesense ``import_`` endpoint must return a per-doc result list
    (``[{"success": true|false, "error": "..."}, ...]``) so a single
    bad doc no longer poisons the whole batch (#3180). Each failure is
    logged with the doc id and error string and counted in
    ``export_errors_total{phase="typesense"}``. The caller advances the
    Typesense cursor past failed docs so the exporter doesn't loop on
    them forever.

    A whole-batch transport failure (Typesense unreachable, 5xx, etc.) or
    malformed acknowledgement still raises — neither is a per-doc poison
    pill. The caller keeps the cursor pinned and applies bounded retry
    backoff to the whole batch. Reconciliation disables per-document failure
    logs and emits only an aggregate count; its durable state and telemetry
    must not contain posting IDs or payload details.
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
        documents = cast(Any, client.collections["job_posting"].documents)
        raw_results = await loop.run_in_executor(
            None,
            lambda: documents.import_(docs, {"action": "upsert"}),
        )
        results = _validate_typesense_acknowledgements(docs, raw_results)
    except Exception as exc:
        duration = time.monotonic() - t0
        typesense_export_duration_seconds.observe(duration)
        typesense_export_docs_total.labels(status="error").inc(len(docs))
        if _is_typesense_acknowledgement_parse_failure(exc):
            log.error(
                "exporter.typesense_invalid_acknowledgement",
                reason="response_decode_error",
                expected_count=len(docs),
                acknowledged_count=None,
                invalid_index=None,
                response_type=type(exc).__name__,
            )
            raise _TypesenseAcknowledgementError(
                "Typesense import acknowledgement was invalid: response_decode_error"
            ) from exc
        raise
    duration = time.monotonic() - t0
    typesense_export_duration_seconds.observe(duration)

    # Per-doc result parsing (#3180). The import endpoint returns one
    # dict per submitted doc, in the same order as the input. A typical
    # success entry is ``{"success": true}``; a failure entry is
    # ``{"success": false, "error": "...", "document": "..."}`` — the
    # ``document`` field, when present, is a JSON-encoded copy of the
    # offending input.
    failed_ids: set[str] = set()
    for doc, result in zip(docs, results, strict=True):
        if result["success"] is False:
            doc_id = str(doc.get("id", ""))
            failed_ids.add(doc_id)
            export_errors_total.labels(table="job_posting", phase="typesense").inc()
            if log_rejected_documents:
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
        # ``source_url`` is a mutable outbound link. Durable matching happens
        # on local ``source_identity``; a locale/title publication change must
        # refresh downstream links without replacing the posting UUID.
        "source_url",
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

    @classmethod
    def select_typesense_changed_sql(cls, *extras: str) -> str:
        """Read posting rows with canonical company metadata in one snapshot.

        ``TaxonomyMaps.company_info`` is refreshed every ten minutes. A company
        created between refreshes previously produced posting documents with
        empty denormalized name/slug fields. Joining the authoritative company
        row makes correctness independent of that cache window.
        """
        select_columns = [f"jp.{name}" for name in cls.column_names()]
        select_columns.append("jp.tdm_reserved")
        select_columns.extend(f"jp.{name}" for name in extras)
        select_columns.extend(
            (
                "c.name AS company_name",
                "c.slug AS company_slug",
                "c.icon AS company_icon",
            )
        )
        return (
            "SELECT "
            + ", ".join(select_columns)
            + " FROM job_posting jp JOIN company c ON c.id = jp.company_id"
            + " WHERE (jp.updated_at, jp.id) > ($1, $2)"
            + " AND jp.updated_at < $4 ORDER BY jp.updated_at, jp.id LIMIT $3"
        )


# Explicit offline compatibility aliases for callers that consume the schema SQL.
_POSTING_COLUMNS = PostingSchema.column_list()
_POSTING_UPSERT_SET = PostingSchema.upsert_set()
