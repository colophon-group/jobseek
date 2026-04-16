"""Local-mode enrichment: filter candidates, sync Gemini enrichment, alert query."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4

import asyncpg
import structlog
import yaml
from pydantic import BaseModel, Field, ValidationError

log = structlog.get_logger()


# ── Filter config ──────────────────────────────────────────────────────


class RequireConfig(BaseModel):
    work_permit_support: Literal["yes", "no"] | None = "yes"
    experience_max: int | None = 2


class OutputConfig(BaseModel):
    limit: int = 100


class FilterConfig(BaseModel):
    exclude_title_patterns: list[str] = Field(default_factory=list)
    require: RequireConfig
    output: OutputConfig = Field(default_factory=OutputConfig)


def load_filter_config(path: str) -> FilterConfig:
    """Load and validate ai/filters.yaml. Raises FileNotFoundError or ValidationError."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return FilterConfig.model_validate(raw or {})


# ── Helpers ────────────────────────────────────────────────────────────


def _build_exclude_regex(patterns: list[str]) -> str:
    """Build a case-insensitive regex alternation from a list of patterns.

    Returns '(?!)' (matches nothing) when patterns is empty so SQL !~* is safe.
    """
    if not patterns:
        return "(?!)"
    return "|".join(patterns)


# ── Claim query (local mode — no R2 requirement) ───────────────────────

_CLAIM_PENDING_LOCAL = """
UPDATE job_posting
SET to_be_enriched = false
WHERE id IN (
    SELECT id FROM job_posting
    WHERE is_active = true
      AND to_be_enriched = true
      AND enrichment IS NULL
    ORDER BY first_seen_at DESC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id,
          titles[1]      AS title,
          locales[1]     AS locale,
          employment_type
"""


# ── mark-candidates ────────────────────────────────────────────────────


async def mark_candidates_from_yaml(pool: asyncpg.Pool, yaml_path: str) -> dict:
    """Flag postings that pass cheap filters as to_be_enriched=true.

    Step 1 — Reset all unenriched active postings to candidates.
    Step 2 — Clear the ones that fail title regex or experience cap.

    Returns {"marked": N, "cleared": M}.
    """
    config = load_filter_config(yaml_path)
    exclude_regex = _build_exclude_regex(config.exclude_title_patterns)
    experience_max = config.require.experience_max if config.require.experience_max is not None else 9999

    # Step 1: Reset (idempotent)
    reset_result = await pool.execute(
        "UPDATE job_posting SET to_be_enriched = true "
        "WHERE is_active = true AND enrichment IS NULL"
    )
    marked_count = int(reset_result.split()[-1])

    # Step 2: Clear those that fail cheap filters
    cleared_result = await pool.execute(
        """
        UPDATE job_posting
        SET to_be_enriched = false
        WHERE is_active = true
          AND enrichment IS NULL
          AND (
            (titles[1] IS NOT NULL AND titles[1] ~* $1)
            OR (experience_max IS NOT NULL AND experience_max > $2)
          )
        """,
        exclude_regex,
        experience_max,
    )
    cleared_count = int(cleared_result.split()[-1])

    log.info(
        "mark_candidates.done",
        marked=marked_count,
        cleared=cleared_count,
        exclude_regex=exclude_regex,
        experience_max=experience_max,
    )
    return {"marked": marked_count, "cleared": cleared_count}


# ── fetch HTML from local descriptions table ───────────────────────────


async def fetch_html_local(pool: asyncpg.Pool, posting_id: str, locale: str) -> str | None:
    """Fetch HTML from the local descriptions table."""
    return await pool.fetchval(
        "SELECT html FROM descriptions WHERE posting_id = $1::uuid AND locale = $2 LIMIT 1",
        posting_id,
        locale,
    )


# ── sync enrichment loop ───────────────────────────────────────────────


async def run_sync_enrich(
    pool: asyncpg.Pool,
    provider,
    *,
    batch_size: int = 20,
    rate_limit_rpm: int = 15,
) -> dict:
    """Claim pending postings, enrich via sync Gemini calls, persist results.

    provider — SyncProvider instance (GeminiSyncProvider).
    batch_size — postings per claim iteration (default 20).
    rate_limit_rpm — max Gemini calls per minute (default 15).

    Returns {"enriched": N, "failed": M, "skipped": K}.
    """
    from src.config import settings
    from src.core.enrich.batch import _persist_results
    from src.core.enrich.job import ENRICH_VERSION, SYSTEM_PROMPT, EnrichmentResult, build_user_message

    total_enriched = total_failed = total_skipped = 0

    while True:
        rows = await pool.fetch(_CLAIM_PENDING_LOCAL, batch_size)
        if not rows:
            break

        results: list[tuple[str, dict | None, object | None]] = []
        posting_ids: list[str] = []

        for i, row in enumerate(rows):
            pid = str(row["id"])
            posting_ids.append(pid)
            locale = row["locale"] or "en"

            html = await fetch_html_local(pool, pid, locale)
            if not html:
                log.warning("enrich.local.no_html", posting_id=pid, locale=locale)
                # Re-queue so it can be retried after description is populated
                await pool.execute(
                    "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                    pid,
                )
                results.append((pid, None, None))
                total_skipped += 1
                continue

            # Rate-limit: sleep between calls (not before the first)
            if i > 0:
                await asyncio.sleep(60 / rate_limit_rpm)

            user_msg = build_user_message(
                html,
                title=row["title"],
                locations=None,  # local mode has no denormalized text locations
                employment_type=row["employment_type"],
            )

            try:
                parsed_dict, usage = await provider.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_content=user_msg,
                    response_schema=EnrichmentResult.model_json_schema(),
                )
                log.info("enrich.local.gemini_call", posting_id=pid)
                results.append((pid, parsed_dict, usage))
                total_enriched += 1
            except Exception as exc:
                log.warning("enrich.local.gemini_error", posting_id=pid, error=str(exc))
                # Re-queue for retry
                await pool.execute(
                    "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                    pid,
                )
                results.append((pid, None, None))
                total_failed += 1

        if not results:
            continue

        # Insert synthetic enrich_batch row before calling _persist_results
        # (_persist_results does UPDATE enrich_batch SET status='completed' at the end)
        batch_id = f"local_sync_{uuid4()}"
        await pool.execute(
            """
            INSERT INTO enrich_batch (id, provider, model, status, item_count, posting_ids)
            VALUES ($1, 'gemini', $2, 'submitted', $3, $4::uuid[])
            """,
            batch_id,
            settings.enrich_model or "gemini-2.0-flash",
            len(posting_ids),
            posting_ids,
        )

        await _persist_results(pool, results, batch_id)

        log.info(
            "enrich.local.batch_done",
            batch_id=batch_id,
            enriched=total_enriched,
            failed=total_failed,
            skipped=total_skipped,
        )

    return {"enriched": total_enriched, "failed": total_failed, "skipped": total_skipped}
