"""IndexNow submission notifier.

**Retired as of #2821 — kept for revival.** Companies are no longer
indexed (``/{locale}/company/{slug}`` is ``noindex,follow`` and
excluded from the sitemap), so there are no URLs left for this
notifier to push. The compose service was removed from
``apps/crawler/docker-compose.yml``; the ``notify-indexnow`` CLI
subcommand still exists but no scheduler invokes it. The module is
preserved so an indexable company surface can be brought back without
re-implementing the hash-diff state machine.

For every company × locale pair, derives a content hash over the
fields that affect bot-visible HTML on ``/<locale>/company/<slug>``
pages. Compares against ``indexnow_submission.content_hash``; URLs
whose hash changed (or that have never been submitted) are POSTed in
batches to ``api.indexnow.org``.

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
- **Per-locale hashing.** Company descriptions are localized via the
  ``company_description`` table — a German-only description edit must
  re-notify ``/de/company/...`` without touching the English URL.
  Stable fields (name / logo / website / industry / employees / founded
  year) are hashed once per company; the locale-specific description
  is appended, so each of the four URLs carries its own hash.
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

# Hash scheme version — bumped when the hashed-field set or layout
# changes, so stored hashes from an older layout force a one-off
# full-resubmit on the next tick instead of silently looking current.
# v2 = per-locale description included in the hash.
_HASH_VERSION = "v2"

# Stable company columns hashed for every URL regardless of locale.
# Any change to what ``CompanyHead.tsx`` renders should be mirrored
# here — otherwise bot-visible content can drift from the last
# submitted hash. Order is locked; do not reorder without a version
# bump (or accept a one-off full-resubmit).
_COMPANY_STABLE_FIELDS: tuple[str, ...] = (
    "name",
    "website",
    "logo",
    "icon",
    "industry",
    "employee_count_range",
    "founded_year",
)
# Select list for the driving query, rendered once — fields are a
# trusted constant, never user input. Kept out of f-string SQL to
# remove any appearance of injection risk.
_COMPANY_SELECT_FIELDS = ", ".join(f"c.{f}" for f in _COMPANY_STABLE_FIELDS)


def compute_company_locale_hash(
    row: asyncpg.Record | dict[str, Any],
    description: str | None,
) -> str:
    """sha256 hex digest for one (company, locale) URL.

    Includes the stable company fields plus the locale-specific
    description (``None`` when the company has no entry for that
    locale — hashed as empty string). The ``_HASH_VERSION`` prefix
    lets older stored hashes invalidate automatically when the scheme
    changes.
    """
    parts: list[str] = []
    for field in _COMPANY_STABLE_FIELDS:
        value = row.get(field, None)
        parts.append("" if value is None else str(value))
    parts.append("" if description is None else description)
    joined = "\x1f".join(parts)  # unit separator — avoids collisions with field values
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"{_HASH_VERSION}:{digest}"


def _url_for(slug: str, locale: str, site_url: str) -> str:
    return f"{site_url}/{locale}/company/{slug}"


def company_urls(slug: str, site_url: str) -> list[str]:
    """Expand a company slug to one absolute URL per supported locale."""
    return [_url_for(slug, locale, site_url) for locale in LOCALES]


async def _load_submission_hashes(conn: asyncpg.Connection) -> dict[str, str]:
    """Fetch the last-submitted content hash for every tracked URL."""
    rows = await conn.fetch("SELECT url, content_hash FROM indexnow_submission")
    return {r["url"]: r["content_hash"] for r in rows}


async def _load_descriptions(
    conn: asyncpg.Connection,
) -> dict[str, dict[str, str]]:
    """Build ``{company_id: {locale: description}}`` for the supported locales.

    A company with no ``company_description`` row for a locale simply
    has no entry in the inner dict — callers treat the missing entry
    as ``None`` (hashed as empty, same as an explicit NULL). Rows for
    locales we don't support are filtered at query time.
    """
    rows = await conn.fetch(
        """
        SELECT c.id::text AS company_id, cd.locale, cd.description
        FROM company c
        JOIN company_description cd ON cd.company_id = c.id
        WHERE cd.locale = ANY($1::text[])
        """,
        list(LOCALES),
    )
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        cid = r["company_id"]
        out.setdefault(cid, {})[r["locale"]] = r["description"]
    return out


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
        company_rows = await conn.fetch(
            f"SELECT c.id::text AS id, c.slug, {_COMPANY_SELECT_FIELDS} FROM company c"
        )
        descriptions_by_company = await _load_descriptions(conn)
        prior = await _load_submission_hashes(conn)

        candidates: list[tuple[str, str]] = []  # (url, content_hash)
        for row in company_rows:
            per_locale = descriptions_by_company.get(row["id"], {})
            for locale in LOCALES:
                description = per_locale.get(locale)
                content_hash = compute_company_locale_hash(row, description)
                url = _url_for(row["slug"], locale, settings.indexnow_site_url)
                if prior.get(url) != content_hash:
                    candidates.append((url, content_hash))

        unchanged = len(company_rows) * len(LOCALES) - len(candidates)

        if not candidates:
            log.info("indexnow.nothing_to_submit", unchanged=unchanged)
            return {"submitted": 0, "unchanged": unchanged, "deferred": 0}

        # Per-tick cap: a first-fill or _HASH_VERSION bump otherwise
        # hands ~N_companies × 4 URLs to five search engines in a single
        # blast, inviting a synchronized recrawl that hammers Vercel
        # image transforms. Deterministic sort-then-slice means a
        # failing batch retries on the same prefix rather than letting
        # alphabetically-later companies leapfrog. Leftover URLs stay
        # hash-mismatched and return next tick.
        total_candidates = len(candidates)
        deferred = 0
        cap = settings.indexnow_max_urls_per_tick
        if cap and total_candidates > cap:
            candidates.sort(key=lambda c: c[0])
            deferred = total_candidates - cap
            candidates = candidates[:cap]
            log.info(
                "indexnow.throttled",
                total=total_candidates,
                submitting=len(candidates),
                deferred=deferred,
            )

        if dry_run:
            log.info(
                "indexnow.dry_run",
                would_submit=len(candidates),
                unchanged=unchanged,
                deferred=deferred,
                sample=[u for u, _ in candidates[:3]],
            )
            return {
                "submitted": 0,
                "unchanged": unchanged,
                "deferred": deferred,
                "dry_run": len(candidates),
            }

        # Batch, submit, record only on success. 4xx/5xx leave the row
        # untouched so the next tick retries with the same payload.
        submitted = 0
        for start in range(0, len(candidates), MAX_URLS_PER_REQUEST):
            chunk = candidates[start : start + MAX_URLS_PER_REQUEST]
            urls = [u for u, _ in chunk]
            if await _submit_batch(http, urls):
                await _record_submissions(conn, chunk)
                submitted += len(chunk)

    log.info(
        "indexnow.run.complete",
        submitted=submitted,
        unchanged=unchanged,
        deferred=deferred,
    )
    return {"submitted": submitted, "unchanged": unchanged, "deferred": deferred}
