"""Load a single posting from the local Postgres + normalize + compute blocks.

Output is the ``input.json`` consumed by every downstream task. Every
subsequent step (splitter, per-section extractors, globals, merge) reads
this file as its source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg

from .blocks import blocks_to_json, extract_blocks
from .normalize import NORMALIZER_VERSION, normalize_html, text_coverage_ratio


@dataclass(frozen=True)
class RawPosting:
    id: str
    source_url: str
    title_raw: str
    description_html_raw: str
    description_locale: str
    company_slug: str | None
    company_name: str | None
    board_slug: str | None
    monitor: str | None
    first_seen_at: datetime | None


async def load_posting(pool: asyncpg.Pool, posting_id: str) -> RawPosting | None:
    row = await pool.fetchrow(
        """
        SELECT
          p.id::text AS id,
          p.source_url,
          COALESCE(p.titles[1], '') AS title_raw,
          COALESCE(p.locales[1], 'en') AS locale,
          p.first_seen_at,
          c.slug AS company_slug,
          c.name AS company_name,
          jb.board_slug,
          jb.crawler_type AS monitor,
          d.html AS description_html
        FROM job_posting p
        LEFT JOIN company c ON c.id = p.company_id
        LEFT JOIN job_board jb ON jb.id = p.board_id
        LEFT JOIN descriptions d
               ON d.posting_id = p.id
              AND d.locale = COALESCE(p.locales[1], 'en')
        WHERE p.id = $1
        """,
        posting_id,
    )
    if row is None or not row["description_html"]:
        return None
    return RawPosting(
        id=row["id"],
        source_url=row["source_url"],
        title_raw=row["title_raw"] or "",
        description_html_raw=row["description_html"],
        description_locale=row["locale"],
        company_slug=row["company_slug"],
        company_name=row["company_name"],
        board_slug=row["board_slug"],
        monitor=row["monitor"],
        first_seen_at=row["first_seen_at"],
    )


def _source_url_host(source_url: str) -> str | None:
    if not source_url:
        return None
    # cheap parse — avoid importing urlparse for one-off
    after_scheme = source_url.split("://", 1)[-1]
    return after_scheme.split("/", 1)[0] or None


def build_input(raw: RawPosting, *, sampled_at: datetime, min_coverage: float = 0.7) -> dict:
    """Normalize + block-split + package the ``input.json`` payload.

    Raises ``ValueError`` if the normalizer coverage ratio is below
    ``min_coverage`` — the posting should be rejected from today's batch.
    """
    normalized = normalize_html(raw.description_html_raw)
    coverage = text_coverage_ratio(raw.description_html_raw, normalized.text)
    if coverage < min_coverage:
        raise ValueError(
            f"normalizer coverage {coverage:.2f} below threshold {min_coverage:.2f}"
            f" — posting {raw.id} skipped"
        )
    blocks = extract_blocks(normalized.html)

    return {
        "id": raw.id,
        "schema_version": 1,
        "normalizer_version": NORMALIZER_VERSION,
        "sampled_at": sampled_at.isoformat(),
        "source": {
            "company_slug": raw.company_slug,
            "company_name": raw.company_name,
            "board_slug": raw.board_slug,
            "monitor": raw.monitor,
            "scraper": None,
            "source_url": raw.source_url,
            "source_url_host": _source_url_host(raw.source_url),
            "first_seen_at": raw.first_seen_at.isoformat() if raw.first_seen_at else None,
        },
        "input": {
            "title_raw": raw.title_raw,
            "description_html_raw": raw.description_html_raw,
            "description_html": normalized.html,
            "description_text": normalized.text,
            "description_locale_detected": raw.description_locale,
            "description_char_count": len(normalized.text),
            "blocks": blocks_to_json(blocks),
        },
    }


def write_input(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
