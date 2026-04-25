"""Load a posting from the local Postgres and turn it into the ``input.json``
that every downstream task reads.

The routine runs in two stages, with a Sonnet normalize call in between:

    stage A  — load raw HTML from the DB; write raw_input.json (title + raw_html)
               and render the normalize task prompt.
    (LLM)    — Sonnet normalizer reads the prompt, writes clean HTML.
    stage B  — read the normalized HTML, run deterministic tail-cleanup + block
               extraction, write the final input.json that split_sections and
               every per-section extractor consume.

Splitting it this way keeps the deterministic code free of network calls while
letting the LLM take the brittle, HTML-specific judgement calls (paragraph
inference on plaintext, bullet-marker detection, heading classification,
scraping-corruption repair).
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

NORMALIZED_FILE = "normalized.html"
RAW_INPUT_FILE = "raw_input.json"


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
    """Load the posting row + the first description that matches the posting locale.

    The join is forgiving — ``posting.locales[]`` may use short codes (``en``)
    or region-tagged codes (``en_US``) and ``descriptions.locale`` can be
    either. We match on language-prefix (first 2 chars).
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


def build_raw_input(raw: RawPosting, *, sampled_at: datetime) -> dict:
    """Stage-A payload: the metadata + raw HTML that feeds the normalize prompt.

    This is NOT the final input.json consumed by the downstream tasks — it only
    carries the fields the LLM normalizer needs (title, raw_html) plus the
    source / identity fields that stage B will copy forward.
    """
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
            "description_locale_detected": _lang_prefix(raw.description_locale),
        },
    }


def finalize_input(raw_input: dict, normalized_html: str, *, min_coverage: float = 0.7) -> dict:
    """Stage-B payload: combine raw_input + LLM-normalized HTML into input.json.

    The deterministic normalizer runs once more as a defensive tail-pass
    (strips stray attributes, unwraps anything the LLM left in, wraps naked
    text) so the final output is guaranteed to conform to the allowed tag
    subset. Blocks are extracted from the tail-pass output.
    """
    tail = normalize_html(normalized_html)
    raw_text = (raw_input.get("input", {}) or {}).get("description_html_raw") or ""
    coverage = text_coverage_ratio(raw_text, tail.text)
    if coverage < min_coverage:
        raise ValueError(
            f"normalizer coverage {coverage:.2f} below threshold {min_coverage:.2f}"
            f" — posting {raw_input.get('id', '?')} skipped"
        )
    blocks = extract_blocks(tail.html)
    out = dict(raw_input)
    out["input"] = dict(raw_input["input"])
    out["input"]["description_html"] = tail.html
    out["input"]["description_text"] = tail.text
    out["input"]["description_char_count"] = len(tail.text)
    out["input"]["blocks"] = blocks_to_json(blocks)
    return out


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
