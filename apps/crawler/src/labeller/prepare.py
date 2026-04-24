"""Load a single posting from the local Postgres + normalize + compute blocks.

Output is the ``input.json`` consumed by every downstream task. Every
subsequent step (splitter, per-section extractors, globals, merge) reads
this file as its source of truth.
"""

from __future__ import annotations

import json
import re
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
    """Load the posting row + the first description row that matches a posting locale.

    The join is deliberately forgiving — posting.locales[] may use short codes
    (``en``) or region-tagged codes (``en_US``) depending on the monitor, and
    ``descriptions.locale`` can be either. We match on the language-prefix
    (first 2 chars) so ``en_US`` in one table matches ``en`` in the other.
    """
    row = await pool.fetchrow(
        """
        WITH p AS (
          SELECT
            id::text AS id,
            source_url,
            company_id,
            board_id,
            first_seen_at,
            COALESCE(titles[1], '') AS title_raw,
            COALESCE(locales[1], 'en') AS locale
          FROM job_posting
          WHERE id = $1
        ),
        d AS (
          SELECT html, locale
          FROM descriptions
          WHERE posting_id = $1
          ORDER BY
            CASE WHEN substr(locale, 1, 2) = substr((SELECT locale FROM p), 1, 2)
                 THEN 0 ELSE 1 END,
            locale
          LIMIT 1
        )
        SELECT
          p.id, p.source_url, p.title_raw, p.locale, p.first_seen_at,
          c.slug AS company_slug,
          c.name AS company_name,
          jb.board_slug,
          jb.crawler_type AS monitor,
          d.html AS description_html
        FROM p
        LEFT JOIN company c ON c.id = p.company_id
        LEFT JOIN job_board jb ON jb.id = p.board_id
        LEFT JOIN d ON true
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
    after_scheme = source_url.split("://", 1)[-1]
    return after_scheme.split("/", 1)[0] or None


def _lang_prefix(locale: str | None) -> str | None:
    if not locale:
        return None
    m = re.match(r"^([a-zA-Z]{2})", locale)
    return m.group(1).lower() if m else None


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
            "description_locale_detected": _lang_prefix(raw.description_locale),
            "description_char_count": len(normalized.text),
            "blocks": blocks_to_json(blocks),
        },
    }


def write_input(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
