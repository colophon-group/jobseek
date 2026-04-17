"""IndexNow submission notifier.

Runs periodically on the crawler host. For every company in local
Postgres, derives a content hash over the fields that affect
bot-visible HTML on ``/<locale>/company/<slug>`` pages. Compares
against ``indexnow_submission.content_hash``; URLs whose hash changed
(or that have never been submitted) are POSTed in batches to
``api.indexnow.org``.

A single POST propagates to all participating engines (Bing, Yandex,
Seznam, Naver, Microsoft Yep). Google does NOT participate in
IndexNow and must be left to sitemap discovery.

Design notes:
- **No ``updated_at`` dependency.** ``company.updated_at`` gets bumped
  on every CSV sync regardless of whether any column actually changed,
  so it cannot be used as a "content changed" signal. Instead we hash
  the material fields directly and diff against the last-submitted
  hash. Crash-safe by construction — the diff between current hash and
  last-submitted hash *is* the queue.
- **Ephemeral posting list is deliberately ignored.** The posting list
  is client-rendered and excluded from our SEO surface, so posting
  churn should not trigger IndexNow notifications.
- **Per-locale URLs share one row** in ``indexnow_submission`` —
  company metadata (name, description, logo, etc.) is locale-agnostic
  today. Revisit if we later translate company descriptions.

Run via ``crawler notify-indexnow`` (one-shot) or as a loop container
(``while true; do crawler notify-indexnow; sleep $INDEXNOW_INTERVAL;
done``) alongside ``exporter`` / ``drain``.
"""

from __future__ import annotations

import hashlib
from typing import Any

import asyncpg
import httpx
import structlog

from src.config import settings

log = structlog.get_logger()

# Canonical endpoint — shared across participating engines. Bing's own
# ``www.bing.com/indexnow`` endpoint is an equivalent alias; using the
# generic one keeps the payload engine-neutral.
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"

# Mirror of ``apps/web/src/lib/i18n.ts::locales``. The web app is the
# authoritative source; keep these lists in sync if new locales ship.
LOCALES: tuple[str, ...] = ("en", "de", "fr", "it")

# Maximum URLs per submission (protocol cap).
MAX_URLS_PER_REQUEST = 10_000

# Fields hashed for a company URL. Any change to what
# ``CompanyHead.tsx`` / ``buildOrganizationJsonLd`` renders should be
# mirrored here — otherwise bot-visible content can drift from the last
# submitted hash. Order is locked by the tuple below; do not reorder
# without a migration (or accept a one-off full-resubmit).
_COMPANY_HASH_FIELDS: tuple[str, ...] = (
    "name",
    "website",
    "logo",
    "icon",
    "industry",
    "employee_count_range",
    "founded_year",
)
# Pre-join once — the field tuple is immutable and the select list is
# a trusted constant, never user input. Keeping it out of f-string SQL
# removes any appearance of injection risk.
_COMPANY_SELECT_FIELDS = ", ".join(_COMPANY_HASH_FIELDS)


def compute_company_hash(row: asyncpg.Record | dict[str, Any]) -> str:
    """sha256 hex digest over canonical company fields.

    Accepts either an ``asyncpg.Record`` or a plain dict so callers
    can hash in-memory payloads in tests without constructing records.
    """
    parts: list[str] = []
    for field in _COMPANY_HASH_FIELDS:
        # asyncpg.Record supports .get(), matching dict semantics.
        value = row.get(field, None)
        parts.append("" if value is None else str(value))
    joined = "\x1f".join(parts)  # unit separator — avoids collisions with field values
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def company_urls(slug: str, site_url: str) -> list[str]:
    """Expand a company slug to one absolute URL per supported locale."""
    return [f"{site_url}/{locale}/company/{slug}" for locale in LOCALES]


async def _load_submission_hashes(conn: asyncpg.Connection) -> dict[str, str]:
    """Fetch the last-submitted content hash for every tracked URL."""
    rows = await conn.fetch("SELECT url, content_hash FROM indexnow_submission")
    return {r["url"]: r["content_hash"] for r in rows}


async def _record_submissions(conn: asyncpg.Connection, entries: list[tuple[str, str]]) -> None:
    """Upsert (url, content_hash, now()) for each successfully submitted entry."""
    if not entries:
        return
    await conn.executemany(
        """
        INSERT INTO indexnow_submission (url, content_hash, last_submitted_at)
        VALUES ($1, $2, now())
        ON CONFLICT (url) DO UPDATE
          SET content_hash = EXCLUDED.content_hash,
              last_submitted_at = now()
        """,
        entries,
    )


async def _submit_batch(
    http: httpx.AsyncClient,
    urls: list[str],
) -> bool:
    """POST a single batch. Returns True on 200/202, False otherwise.

    Timeout is short (15s) so we fail fast rather than tying up a pool
    connection during a protocol-level hang. Both transient (5xx,
    network) and permanent (4xx) failures return False — the caller
    does not update the hash table, so the next tick retries. For
    permanent rejections the WARNING→ERROR escalation makes them
    visible in alerts without needing a separate retry policy.
    """
    payload = {
        "host": settings.indexnow_host,
        "key": settings.indexnow_key,
        "keyLocation": settings.indexnow_key_url,
        "urlList": urls,
    }
    try:
        response = await http.post(INDEXNOW_ENDPOINT, json=payload, timeout=15.0)
    except (httpx.HTTPError, TimeoutError) as err:
        log.warning(
            "indexnow.submit.network_error",
            error=type(err).__name__,
            detail=str(err),
            count=len(urls),
        )
        return False

    status = response.status_code
    if status in (200, 202):
        log.info("indexnow.submit.ok", status=status, count=len(urls))
        return True

    # 4xx is our bug — bad key, bad payload, bad host. ERROR so it
    # surfaces in alerting. 5xx is the engine's fault — transient,
    # retry-friendly, stay at WARNING.
    level = log.error if 400 <= status < 500 else log.warning
    level(
        "indexnow.submit.rejected",
        status=status,
        count=len(urls),
        body=response.text[:500],
    )
    return False


async def notify_indexnow(
    local_pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Main entry point: diff → submit → record.

    Returns a stats dict for logging/metrics. When
    ``settings.indexnow_key`` is empty, short-circuits with
    ``{"skipped": ...}`` and makes no HTTP or DB-write calls.
    """
    if not settings.indexnow_key:
        log.info("indexnow.disabled", reason="indexnow_key not set")
        return {"skipped": 1, "submitted": 0, "unchanged": 0}
    if (
        not settings.indexnow_host
        or not settings.indexnow_key_url
        or not settings.indexnow_site_url
    ):
        log.warning(
            "indexnow.misconfigured",
            has_host=bool(settings.indexnow_host),
            has_key_url=bool(settings.indexnow_key_url),
            has_site_url=bool(settings.indexnow_site_url),
        )
        return {"skipped": 1, "submitted": 0, "unchanged": 0}

    # Hold one connection for the whole cycle (read → submit → write).
    # Two separate acquires would let `crawler sync` slip between the
    # read and the write, recording a hash that no longer matches the
    # DB state. Holding the conn trades one pool slot for consistency.
    async with local_pool.acquire() as conn:
        company_rows = await conn.fetch(f"SELECT slug, {_COMPANY_SELECT_FIELDS} FROM company")
        prior = await _load_submission_hashes(conn)

        candidates: list[tuple[str, str]] = []  # (url, content_hash)
        for row in company_rows:
            content_hash = compute_company_hash(row)
            for url in company_urls(row["slug"], settings.indexnow_site_url):
                if prior.get(url) != content_hash:
                    candidates.append((url, content_hash))

        unchanged = len(company_rows) * len(LOCALES) - len(candidates)

        if not candidates:
            log.info("indexnow.nothing_to_submit", unchanged=unchanged)
            return {"submitted": 0, "unchanged": unchanged}

        if dry_run:
            log.info(
                "indexnow.dry_run",
                would_submit=len(candidates),
                unchanged=unchanged,
                sample=[u for u, _ in candidates[:3]],
            )
            return {"submitted": 0, "unchanged": unchanged, "dry_run": len(candidates)}

        # Batch, submit, record only on success. 4xx/5xx leave the row
        # untouched so the next tick retries with the same payload.
        submitted = 0
        for start in range(0, len(candidates), MAX_URLS_PER_REQUEST):
            chunk = candidates[start : start + MAX_URLS_PER_REQUEST]
            urls = [u for u, _ in chunk]
            if await _submit_batch(http, urls):
                await _record_submissions(conn, chunk)
                submitted += len(chunk)

    log.info("indexnow.run.complete", submitted=submitted, unchanged=unchanged)
    return {"submitted": submitted, "unchanged": unchanged}
