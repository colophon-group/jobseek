"""Enrichment batch processor.

Claims pending postings, fetches HTML from R2, builds LLM prompts,
submits batches, collects results, and persists enrichment data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg
import structlog

from src.config import settings
from src.core.description_store import get_description_html
from src.core.enrich.job import (
    ENRICH_VERSION,
    SYSTEM_PROMPT,
    EnrichmentResult,
    build_user_message,
)
from src.core.enrich.providers import BatchProvider, BatchRequest
from src.core.enrich.taxonomy import TaxonomyMiss, resolve_taxonomy

log = structlog.get_logger()


# ── Claim and prepare ─────────────────────────────────────────────────

_CLAIM_PENDING = """
UPDATE job_posting
SET to_be_enriched = false
WHERE id IN (
    SELECT id FROM job_posting
    WHERE is_active = true
      AND to_be_enriched = true
      AND description_r2_hash IS NOT NULL
    ORDER BY first_seen_at DESC
    LIMIT $1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, titles[1] AS title, locales[1] AS locale,
          locations, employment_type
"""


async def _fetch_html(posting_id: str, locale: str) -> str | None:
    """Fetch the latest HTML description from R2."""
    return await get_description_html(posting_id, locale)


async def prepare_batch(
    pool: asyncpg.Pool,
    limit: int,
) -> tuple[list[BatchRequest], list[str]] | None:
    """Claim pending postings and prepare LLM requests.

    Returns (requests, posting_ids) or None if nothing to claim.
    """
    rows = await pool.fetch(_CLAIM_PENDING, limit)
    if not rows:
        return None

    requests: list[BatchRequest] = []
    posting_ids: list[str] = []
    failed_ids: list[str] = []

    for row in rows:
        pid = str(row["id"])
        locale = row["locale"] or "en"
        html = await _fetch_html(pid, locale)

        if not html:
            log.warning("enrich.no_html", posting_id=pid, locale=locale)
            failed_ids.append(pid)
            continue

        user_msg = build_user_message(
            html,
            title=row["title"],
            locations=row["locations"],
            employment_type=row["employment_type"],
        )

        requests.append(
            BatchRequest(
                custom_id=pid,
                system_prompt=SYSTEM_PROMPT,
                user_content=user_msg,
            )
        )
        posting_ids.append(pid)

    # Re-queue postings that had no HTML
    if failed_ids:
        await pool.execute(
            "UPDATE job_posting SET to_be_enriched = true WHERE id = ANY($1::uuid[])",
            failed_ids,
        )

    if not requests:
        return None

    return requests, posting_ids


# ── Submit ────────────────────────────────────────────────────────────


async def submit_batch(
    pool: asyncpg.Pool,
    provider: BatchProvider,
    requests: list[BatchRequest],
    posting_ids: list[str],
) -> str:
    """Submit batch to provider and record in DB."""
    schema = EnrichmentResult.model_json_schema()
    batch_id = await provider.submit_batch(requests, schema)

    # Estimate cost
    est_input_tokens = sum(len(r.user_content) // 4 for r in requests)
    est_output_tokens = len(requests) * 500  # ~500 tokens per response
    est_cost = (
        est_input_tokens * settings.enrich_input_price_per_m / 1_000_000
        + est_output_tokens * settings.enrich_output_price_per_m / 1_000_000
    )

    await pool.execute(
        """
        INSERT INTO enrich_batch (id, provider, model, status, item_count,
                                  posting_ids, estimated_cost_usd)
        VALUES ($1, $2, $3, 'submitted', $4, $5::uuid[], $6)
        """,
        batch_id,
        settings.enrich_provider,
        settings.enrich_model,
        len(requests),
        posting_ids,
        round(est_cost, 4),
    )

    return batch_id


# ── Collect results ───────────────────────────────────────────────────


async def collect_completed_batches(pool: asyncpg.Pool, provider: BatchProvider) -> int:
    """Check submitted batches and persist results. Returns count of completed batches."""
    batches = await pool.fetch(
        "SELECT id, posting_ids FROM enrich_batch WHERE status = 'submitted'"
    )

    completed = 0
    for batch in batches:
        batch_id = batch["id"]
        try:
            status = await provider.check_batch(batch_id)
        except Exception:
            log.exception("enrich.check_error", batch_id=batch_id)
            continue

        if status == "completed":
            try:
                results = await provider.collect_results(batch_id)
                await _persist_results(pool, results, batch_id)
                completed += 1
            except Exception:
                log.exception("enrich.collect_error", batch_id=batch_id)
                await _handle_batch_failure(pool, batch_id, batch["posting_ids"])

        elif status in ("failed", "expired"):
            log.warning("enrich.batch_failed", batch_id=batch_id, status=status)
            await _handle_batch_failure(pool, batch_id, batch["posting_ids"])

    return completed


async def _upsert_taxonomy_misses(pool: asyncpg.Pool, misses: list[TaxonomyMiss]) -> None:
    """Batch upsert taxonomy misses. Increments hit_count for pending entries."""
    if not misses:
        return

    taxonomies = [m.taxonomy for m in misses]
    raw_values = [m.raw_value for m in misses]
    sample_values = [m.sample_value for m in misses]

    await pool.execute(
        """
        INSERT INTO taxonomy_miss (taxonomy, raw_value, sample_value)
        SELECT * FROM unnest($1::text[], $2::text[], $3::text[])
        ON CONFLICT (taxonomy, raw_value) DO UPDATE SET
            hit_count = taxonomy_miss.hit_count + 1,
            last_seen_at = now()
        WHERE taxonomy_miss.status = 'pending'
        """,
        taxonomies,
        raw_values,
        sample_values,
    )


async def _persist_results(
    pool: asyncpg.Pool,
    results: list[tuple[str, dict | None, object | None]],
    batch_id: str,
) -> None:
    """Write enrichment data to job_posting rows."""
    total_input = total_output = 0
    succeeded = 0
    occupation_resolved = 0
    seniority_resolved = 0
    technology_resolved = 0
    all_misses: list[TaxonomyMiss] = []
    now_iso = datetime.now(UTC).isoformat()

    for custom_id, parsed, usage in results:
        if parsed is not None:
            # Validate against schema
            try:
                EnrichmentResult.model_validate(parsed)
            except Exception:
                log.warning("enrich.validation_error", posting_id=custom_id)
                await pool.execute(
                    "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                    custom_id,
                )
                continue

            # Resolve taxonomy FKs
            tax = await resolve_taxonomy(pool, parsed)
            if tax.occupation_id is not None:
                occupation_resolved += 1
            if tax.seniority_id is not None:
                seniority_resolved += 1
            if tax.technology_ids:
                technology_resolved += 1
            all_misses.extend(tax.misses)

            tech_ids = tax.technology_ids or None

            enrichment = {"v": ENRICH_VERSION, "extracted_at": now_iso, **parsed}
            await pool.execute(
                """
                UPDATE job_posting
                SET enrichment = $2::jsonb,
                    enrich_version = $3,
                    last_enriched_at = now(),
                    to_be_enriched = false,
                    occupation_id = COALESCE($4, occupation_id),
                    seniority_id = COALESCE($5, seniority_id),
                    technology_ids = COALESCE($6, technology_ids)
                WHERE id = $1::uuid
                """,
                custom_id,
                json.dumps(enrichment),
                ENRICH_VERSION,
                tax.occupation_id,
                tax.seniority_id,
                tech_ids,
            )
            succeeded += 1
        else:
            # Individual item failed — re-queue
            await pool.execute(
                "UPDATE job_posting SET to_be_enriched = true WHERE id = $1::uuid",
                custom_id,
            )

        if usage:
            total_input += usage.input_tokens
            total_output += usage.output_tokens

    # Batch upsert taxonomy misses
    await _upsert_taxonomy_misses(pool, all_misses)

    await pool.execute(
        """
        UPDATE enrich_batch
        SET status = 'completed', completed_at = now(),
            input_tokens = $2, output_tokens = $3
        WHERE id = $1
        """,
        batch_id,
        total_input,
        total_output,
    )

    log.info(
        "enrich.batch_completed",
        batch_id=batch_id,
        succeeded=succeeded,
        total=len(results),
        occupation_resolved=occupation_resolved,
        seniority_resolved=seniority_resolved,
        technology_resolved=technology_resolved,
        taxonomy_misses=len(all_misses),
        input_tokens=total_input,
        output_tokens=total_output,
    )


async def _handle_batch_failure(
    pool: asyncpg.Pool,
    batch_id: str,
    posting_ids: list,
) -> None:
    """Re-queue all postings from a failed batch."""
    await pool.execute(
        "UPDATE job_posting SET to_be_enriched = true WHERE id = ANY($1::uuid[])",
        posting_ids,
    )
    await pool.execute(
        "UPDATE enrich_batch SET status = 'failed', completed_at = now() WHERE id = $1",
        batch_id,
    )
    log.info("enrich.batch_failure_handled", batch_id=batch_id, requeued=len(posting_ids))


# ── Budget check ──────────────────────────────────────────────────────


async def check_daily_budget(pool: asyncpg.Pool) -> bool:
    """Check if daily spend is under the cap."""
    spent = await pool.fetchval(
        """
        SELECT COALESCE(SUM(estimated_cost_usd), 0)
        FROM enrich_batch
        WHERE submitted_at >= CURRENT_DATE
        """
    )
    if float(spent) >= settings.enrich_daily_spend_cap_usd:
        log.warning(
            "enrich.budget_exceeded",
            spent=float(spent),
            cap=settings.enrich_daily_spend_cap_usd,
        )
        return False
    return True
