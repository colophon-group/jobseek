# apps/crawler/src/queries/alert.py
"""Alert query: enriched jobs passing visa + experience + title filters."""

from __future__ import annotations

from typing import Any

import asyncpg

_ALERT_QUERY = """
    SELECT
        jp.id,
        jp.titles[1]                            AS title,
        jp.source_url,
        jp.first_seen_at,
        jp.experience_max,
        jp.enrichment->>'work_permit_support'   AS work_permit_support,
        jp.enrichment->>'seniority'             AS seniority,
        jp.enrichment                           AS enrichment_json,
        c.name                                  AS company_name,
        c.slug                                  AS company_slug
    FROM job_posting jp
    JOIN company c ON c.id = jp.company_id
    WHERE jp.is_active = true
      AND jp.enrichment IS NOT NULL
      AND ($4::text IS NULL OR jp.enrichment->>'work_permit_support' = $4)
      AND (jp.experience_max IS NULL OR jp.experience_max <= $1)
      AND (jp.titles[1] IS NULL OR jp.titles[1] !~* $2)
    ORDER BY jp.first_seen_at DESC
    LIMIT $3
"""


async def run_alert_query(
    conn: asyncpg.Connection,
    *,
    experience_max: int,
    exclude_title_regex: str,
    limit: int,
    work_permit_support: str | None = "yes",
) -> list[dict[str, Any]]:
    """Return jobs matching all alert filters as plain dicts."""
    rows = await conn.fetch(_ALERT_QUERY, experience_max, exclude_title_regex, limit, work_permit_support)
    return [dict(r) for r in rows]
