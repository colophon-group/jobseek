"""DB queries for resume_score table."""

from __future__ import annotations

from typing import Any

import asyncpg

# Fetch jobs that need scoring:
#   - active + enriched + passed cheap filters
#   - not yet scored OR scored with a different resume hash
_FETCH_UNSCORED = """
    SELECT
        jp.id          AS posting_id,
        jp.titles[1]   AS title,
        jp.enrichment,
        c.name         AS company_name,
        jp.first_seen_at
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    LEFT JOIN resume_score rs ON rs.posting_id = jp.id
    WHERE jp.is_active = true
      AND jp.enrichment IS NOT NULL
      AND jp.to_be_enriched = false
      AND jp.titles[1] !~* $1
      AND (jp.experience_max IS NULL OR jp.experience_max <= $2)
      AND (rs.posting_id IS NULL OR rs.resume_hash != $3)
    ORDER BY jp.first_seen_at DESC
"""

_UPSERT_SCORE = """
    INSERT INTO resume_score (posting_id, resume_hash, overlap_score, scored_at)
    VALUES ($1, $2, $3, now())
    ON CONFLICT (posting_id) DO UPDATE
        SET resume_hash   = EXCLUDED.resume_hash,
            overlap_score = EXCLUDED.overlap_score,
            scored_at     = now(),
            explanation   = NULL,
            explained_at  = NULL
"""

_UPSERT_EXPLANATION = """
    UPDATE resume_score
    SET explanation  = $1,
        explained_at = now()
    WHERE posting_id = $2
"""


async def fetch_unscored_jobs(
    conn: asyncpg.Connection,
    *,
    resume_hash: str,
    exclude_title_regex: str,
    experience_max: int | None,
) -> list[dict[str, Any]]:
    """Return unscored (or stale-hash) jobs as plain dicts."""
    cap = experience_max if experience_max is not None else 9999
    rows = await conn.fetch(_FETCH_UNSCORED, exclude_title_regex, cap, resume_hash)
    return [dict(r) for r in rows]


async def upsert_score(
    conn: asyncpg.Connection,
    *,
    posting_id: str,
    resume_hash: str,
    overlap_score: float,
) -> None:
    """Insert or replace a score row. Clears explanation on re-score."""
    await conn.execute(_UPSERT_SCORE, posting_id, resume_hash, overlap_score)


async def upsert_explanation(
    conn: asyncpg.Connection,
    *,
    posting_id: str,
    explanation: str,
) -> None:
    """Write the LLM explanation text and set explained_at."""
    await conn.execute(_UPSERT_EXPLANATION, explanation, posting_id)
