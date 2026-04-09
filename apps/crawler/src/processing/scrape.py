"""Scrape processing — single job scraping, fallback chain, batch scrape."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from time import monotonic
from urllib.parse import urlparse

import asyncpg
import httpx
import structlog

import src.queries.lookups as _lookups_mod  # noqa: E402
from src.core.enum_normalize import normalize_employment_type
from src.core.location_resolve import LocationResolver
from src.core.scrapers import (
    JobContent,
    get_scraper,
    scraper_needs_browser,
)
from src.processing.cpu import (
    BatchResult,
    _build_locales,
    _build_titles,
    _coerce_locations,
    _coerce_text,
    _error_message,
    _extract_experience_fields,
    _extract_salary_fields,
    _is_garbage_title,
    _parse_metadata,
    _parse_update_count,
    _resolve_locations_sync,
    _resolve_occupation_seniority,
    _resolve_technology_ids,
)
from src.processing.r2_stage import _stage_r2_pending
from src.queries.scrape import (
    _CLEAR_SCRAPE_FOR_RICH,
    _FETCH_BOARD_SCRAPERS,
    _FETCH_DUE_JOB_POSTINGS,
    _FETCH_POSTING_FOR_ENRICH,
    _RECORD_SCRAPE_FAILURE,
    _RECORD_SCRAPE_SUCCESS,
    _UPDATE_ENRICH_CONTENT,
)
from src.shared.html_normalize import normalize_description_html
from src.shared.langdetect import detect_all_languages, detect_language

log = structlog.get_logger()

_UPSERT_DESCRIPTION = (
    "INSERT INTO descriptions (posting_id, locale, html, hash, r2_uploaded) "
    "VALUES ($1, $2, $3, $4, false) "
    "ON CONFLICT (posting_id, locale) DO UPDATE "
    "SET html = $3, hash = $4, "
    "r2_uploaded = CASE WHEN descriptions.hash IS DISTINCT FROM $4 "
    "THEN false ELSE descriptions.r2_uploaded END, "
    "updated_at = CASE WHEN descriptions.hash IS DISTINCT FROM $4 "
    "THEN now() ELSE descriptions.updated_at END"
)


class _BatchLookups:
    """Late-binding proxy so monkeypatch on src.batch propagates."""

    def __getattr__(self, name):
        import src.batch  # noqa: F811

        return getattr(src.batch, name)


_batch = _BatchLookups()

_SLOW_SCRAPE_SECONDS = 15.0

_JOBCONTENT_FIELDS = frozenset(f.name for f in __import__("dataclasses").fields(JobContent))


@dataclass
class ScrapeResult:
    """Scrape result -> DB writer runs _UPDATE_JOB_CONTENT or _UPDATE_ENRICH_CONTENT."""

    job_posting_id: str
    params: tuple  # positional args for the SQL query
    is_enrich: bool
    staged: tuple[str, str, int] | None = None  # (html, locale, hash) for descriptions table


@dataclass
class ScrapeError:
    """Scrape error -> DB writer runs _RECORD_SCRAPE_FAILURE."""

    job_posting_id: str


@dataclass
class _ScrapeWorkItem:
    """Bundle of ScrapeItem + resolved scraper config for the pipeline worker."""

    item: ScrapeItem
    scraper_type: str
    scraper_config: dict | None
    enrich_fields: list[str] | None
    ssl_verify: bool = True


@dataclass
class ScrapeItem:
    """A job posting claimed from Postgres for scraping."""

    job_posting_id: str
    url: str
    board_id: str = ""
    description_r2_hash: int | None = None


@dataclass
class BoardScraperConfig:
    """Scraper settings for a board (fallback chain lives inside scraper_config)."""

    scraper_type: str
    scraper_config: dict | None
    ssl_verify: bool = True


@dataclass
class _BoardScraperInfo:
    """Scraper info plus whether the board is a rich monitor (no scraping needed)."""

    scrapers: dict[str, BoardScraperConfig]
    rich_board_ids: set[str]  # boards from rich monitors with no explicit scraper


def _board_has_enrich(metadata: dict) -> list[str] | None:
    """Extract the ``enrich`` list from ``metadata["scraper_config"]``, or None."""
    sc = metadata.get("scraper_config")
    if not isinstance(sc, dict):
        return None
    enrich = sc.get("enrich")
    if isinstance(enrich, list) and enrich:
        return enrich
    return None


def _is_skip_no_scrape(metadata: dict, crawler_type: str | None = None) -> bool:
    """Return True if this board is 'rich monitor, no scraping needed'.

    A board is skip-no-scrape when it will never use the scrape pipeline:

    1. ``metadata.scraper_type = "skip"`` with no enrichment — explicit.
    2. ``metadata.scraper_type`` is unset AND ``crawler_type`` auto-resolves
       to ``("skip", None)`` via ``auto_scraper_type()`` AND no enrichment —
       implicit rich monitor that relies on CSV defaults.

    Such boards provide full job data from the monitor and must never be
    sent through the scrape pipeline, or the placeholder ``skip`` scraper
    raises ``RuntimeError("skip scraper called for …")``.

    ``crawler_type`` is optional so legacy callers keep working. Pass it
    whenever you have it (board record, Redis config hash) so implicit
    rich monitors are caught too.

    Use this at every point that writes ``next_scrape_at`` or enqueues a
    scrape task so rich-monitor postings stay out of the scrape loop.
    """
    if _board_has_enrich(metadata) is not None:
        return False
    scraper_type = metadata.get("scraper_type")
    if scraper_type == "skip":
        return True
    # Normalize empty string (can come from Redis hash fields) to None so the
    # implicit-rich branch only fires with a real crawler_type.
    ct = crawler_type or None
    if scraper_type is None and ct:
        # Import here to avoid the workspace._compat dependency at module load
        from src.workspace._compat import auto_skip_crawler_types

        if ct in auto_skip_crawler_types():
            return True
    return False


async def _load_board_scrapers(
    pool: asyncpg.Pool,
    board_ids: set[str],
) -> _BoardScraperInfo:
    """Load scraper type/config by board id from job_board metadata."""
    if not board_ids:
        return _BoardScraperInfo(scrapers={}, rich_board_ids=set())

    rows = await pool.fetch(_FETCH_BOARD_SCRAPERS, list(board_ids))
    resolved: dict[str, BoardScraperConfig] = {}
    rich_board_ids: set[str] = set()

    for row in rows:
        board_id = row["id"]
        metadata = _parse_metadata(row["metadata"])
        crawler_type = row["crawler_type"]
        explicit_scraper = metadata.get("scraper_type")

        enrich_fields = _board_has_enrich(metadata)

        # Determine scraper: explicit > auto-configured > default (json-ld)
        if not explicit_scraper:
            # Check if monitor auto-configures a scraper
            from src.workspace._compat import auto_scraper_type

            auto = auto_scraper_type(crawler_type, metadata)
            if auto and auto[0] == "skip":
                if enrich_fields:
                    # Enrich boards need a scraper -- use json-ld as default
                    scraper_type = "json-ld"
                    auto_config = None
                else:
                    rich_board_ids.add(board_id)
                    continue
            else:
                scraper_type = auto[0] if auto else "json-ld"
                auto_config = auto[1] if auto else None
        elif explicit_scraper == "skip":
            if enrich_fields:
                scraper_type = "json-ld"
                auto_config = None
            else:
                rich_board_ids.add(board_id)
                continue
        else:
            scraper_type = explicit_scraper
            auto_config = None
        scraper_config = metadata.get("scraper_config")
        if not isinstance(scraper_config, dict):
            scraper_config = auto_config

        try:
            get_scraper(scraper_type)
        except Exception:
            log.warning(
                "batch.scrape.invalid_scraper_type",
                board_id=board_id,
                scraper_type=scraper_type,
            )
            scraper_type = "json-ld"
            scraper_config = None

        resolved[board_id] = BoardScraperConfig(
            scraper_type=scraper_type,
            scraper_config=scraper_config,
            ssl_verify=metadata.get("ssl_verify", True),
        )

    return _BoardScraperInfo(scrapers=resolved, rich_board_ids=rich_board_ids)


def _merge_fields(primary: JobContent, fallback: JobContent, fields: list[str]) -> JobContent:
    """Create a merged JobContent, taking listed fields from fallback if not None."""
    from dataclasses import replace

    overrides: dict = {}
    for name in fields:
        if name not in _JOBCONTENT_FIELDS:
            log.warning("batch.fallback.unknown_field", field=name)
            continue
        fb_val = getattr(fallback, name)
        if fb_val is not None:
            overrides[name] = fb_val
    return replace(primary, **overrides) if overrides else primary


def _get_scraper_at_step(
    scraper_type: str, scraper_config: dict | None, step: int
) -> tuple[str, dict]:
    """Walk the fallback chain to the given step. Returns (type, config) for that step."""
    cfg = scraper_config or {}
    current_type = scraper_type
    for _ in range(step):
        fb = cfg.get("fallback")
        if not fb:
            return current_type, cfg  # step beyond chain — just use last
        current_type = fb["type"]
        cfg = fb.get("config") or {}
    return current_type, cfg


def _get_next_fallback(
    scraper_type: str, scraper_config: dict | None, step: int
) -> tuple[str, dict, list[str] | None] | None:
    """Get the fallback at step+1. Returns (type, config, fields) or None."""
    cfg = scraper_config or {}
    for _ in range(step):
        fb = cfg.get("fallback")
        if not fb:
            return None
        cfg = fb.get("config") or {}
    fb = cfg.get("fallback")
    if not fb:
        return None
    return fb["type"], fb.get("config") or {}, fb.get("fields")


def _apply_defaults(content: JobContent, cfg: dict) -> JobContent:
    """Apply constant defaults for fields that are still None after scraping.

    Config example::

        "defaults": {"locations": ["Zurich, Switzerland"], "job_location_type": "onsite"}

    Only fills fields that are ``None`` (or empty list for ``locations``).
    Useful for regional boards where all jobs share a location, or small
    companies with a single office.
    """
    defaults = cfg.get("defaults")
    if not defaults or not isinstance(defaults, dict):
        return content

    for field_name, value in defaults.items():
        if field_name not in _JOBCONTENT_FIELDS:
            log.warning("batch.defaults.unknown_field", field=field_name)
            continue
        current = getattr(content, field_name)
        if current is None or (isinstance(current, list) and not current):
            setattr(content, field_name, value)
    return content


async def _process_one_enrich_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
    enrich_fields: list[str],
    pw=None,
) -> tuple[bool, float]:
    """Run a scrape that only enriches specific fields. Returns (success, duration_s).

    Backfill-on-empty: ``title``, ``employment_type``, and ``locations`` are
    opportunistically filled from the scraper output when the existing
    posting row has them empty. This recovers URL-only stubs inserted when
    a hybrid monitor could not deliver rich data (e.g. the eightfold
    ``awaiting_manual_backfill`` path on Starbucks, which yields sitemap
    URLs without any PCSX data). ``_UPDATE_ENRICH_CONTENT`` uses COALESCE
    on every column, so once PCSX does run, real rich data is preserved
    and never clobbered by a later json-ld scrape.

    Garbage scraped titles (``_is_garbage_title``) are not backfilled — an
    obviously-broken page should not poison a still-empty row that PCSX
    might fill correctly later.
    """
    t0 = monotonic()
    try:
        cfg = scraper_config or {}
        content = await _batch.scrape_one(item.url, scraper_type, scraper_config, http, pw=pw)
        content = _apply_defaults(content, cfg)

        # Normalize before checking -- normalize can strip degenerate HTML to None
        content.description = normalize_description_html(content.description)

        # Success check: at least one enriched field is non-empty
        has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
        if not has_data:
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
            return False, monotonic() - t0

        # Fetch existing row once: used for both backfill detection and
        # R2 staging. ``existing`` may be None in tests or if the row was
        # concurrently deleted; treat that as "don't backfill".
        existing = await pool.fetchrow(_FETCH_POSTING_FOR_ENRICH, item.job_posting_id)

        # Expand enrich_fields with any core fields that are currently empty
        # on the DB row, so the rest of this function naturally populates
        # them via its existing if-branches. Guarded by a garbage-title
        # check so a broken page can't poison a stub row.
        effective_fields = list(enrich_fields)
        if existing:
            existing_titles = existing.get("titles")
            existing_loc_ids = existing.get("location_ids")
            existing_et = existing.get("employment_type")
            scraped_title = _coerce_text(content.title)
            title_is_usable = bool(scraped_title) and not _is_garbage_title(scraped_title)
            if "title" not in effective_fields and not existing_titles and title_is_usable:
                effective_fields.append("title")
            if (
                "locations" not in effective_fields
                and not existing_loc_ids
                and _coerce_locations(content.locations)
            ):
                effective_fields.append("locations")
            if (
                "employment_type" not in effective_fields
                and not existing_et
                and _coerce_text(content.employment_type)
            ):
                effective_fields.append("employment_type")

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        # Compute derived columns only for enriched fields
        loc_resolver = await _batch._get_location_resolver(pool)
        tech_id_map = await _batch._get_technology_ids(pool)
        occ_ids = await _batch._get_occupation_ids(pool)
        sen_ids = await _batch._get_seniority_ids(pool)
        rates = await _batch._get_currency_rates(pool)

        # Default all params to None (COALESCE preserves existing)
        norm_emp_type = None
        all_titles = None
        locales = None
        loc_ids = None
        loc_types = None
        tech_ids = None
        s_min = s_max = s_cur = s_per = s_eur = None
        exp_min = exp_max = None
        occ_id = sen_id = None
        staged = None

        if "employment_type" in effective_fields:
            norm_emp_type = normalize_employment_type(_coerce_text(content.employment_type))

        if "title" in effective_fields:
            title_text = _coerce_text(content.title)
            all_titles = _build_titles(title_text, None) or None
            occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
            # Only overwrite locales if we have real language evidence --
            # _build_locales defaults to ["en"] which would overwrite
            # richer monitor-sourced locale data via COALESCE.
            lang_text = _coerce_text(language)
            if lang_text or content.description:
                detected_langs = (
                    detect_all_languages(content.description) if content.description else []
                )
                built = _build_locales(lang_text, None, detected_languages=detected_langs)
                # Only set if we have data beyond the bare "en" default
                if lang_text or detected_langs:
                    locales = built

        if "locations" in effective_fields:
            lang_text = _coerce_text(language)
            loc_ids, loc_types = await _batch._resolve_locations(
                loc_resolver,
                _coerce_locations(content.locations),
                _coerce_text(content.job_location_type),
                posting_language=lang_text,
            )

        if "description" in effective_fields:
            desc_text = _coerce_text(content.description)
            tech_ids = _resolve_technology_ids(desc_text, tech_id_map)
            s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
            exp_min, exp_max = _extract_experience_fields(desc_text)

            # Reuse the earlier fetch for R2 extras.
            r2_title = None
            if existing:
                titles_arr = existing["titles"]
                if titles_arr:
                    r2_title = titles_arr[0]
            r2_title = r2_title or _coerce_text(content.title)
            r2_locations = _coerce_locations(content.locations)

            staged = _stage_r2_pending(
                title=r2_title,
                description=desc_text,
                language=_coerce_text(language),
                locations=r2_locations,
                localizations=None,
                extras=content.extras,
                metadata=content.metadata,
                date_posted=content.date_posted,
                base_salary=content.base_salary,
                employment_type=_coerce_text(content.employment_type),
                job_location_type=_coerce_text(content.job_location_type),
                current_hash=item.description_r2_hash,
                source="scrape",
                tech_ids=tech_ids,
            )

        async with pool.acquire() as conn:
            await conn.execute(
                _UPDATE_ENRICH_CONTENT,
                item.job_posting_id,
                norm_emp_type,
                all_titles,
                locales,
                loc_ids,
                loc_types,
                tech_ids,
                s_min,
                s_max,
                s_cur,
                s_per,
                s_eur,
                exp_min,
                exp_max,
                occ_id,
                sen_id,
            )
            if staged:
                desc_html, locale, desc_hash = staged
                await conn.execute(
                    _UPSERT_DESCRIPTION,
                    item.job_posting_id,
                    locale,
                    desc_html,
                    desc_hash,
                )
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)

        await _batch._flush_location_misses(loc_resolver, pool)
        elapsed = monotonic() - t0
        log.debug(
            "batch.enrich.success",
            url=item.url,
            fields=enrich_fields,
            duration_s=round(elapsed, 2),
        )
        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        log.error("batch.enrich.error", url=item.url, error=error_msg, duration_s=round(elapsed, 2))
        if _lookups_mod._location_resolver is not None:
            _lookups_mod._location_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
        return False, elapsed


async def _process_one_scrape(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    scraper_type: str,
    scraper_config: dict | None,
    pw=None,
    scrape_step: int = 0,
    scrape_interval: int = 24,
) -> tuple[bool, float]:
    """Run a single scrape step for a job posting. Returns (success, duration_s).

    Each step runs ONE scraper (determined by ``scrape_step`` walking the
    fallback chain).  After saving with COALESCE (never erasing existing
    values), the next fallback step (if any) is enqueued as a separate job.
    """
    from src.redis_queue import enqueue_scrape

    t0 = monotonic()
    try:
        board_cfg = scraper_config or {}

        # Early dispatch for enrich-only scrapes (step 0 only)
        if scrape_step == 0:
            enrich_fields = board_cfg.get("enrich")
            if isinstance(enrich_fields, list) and enrich_fields:
                return await _process_one_enrich_scrape(
                    item, pool, http, scraper_type, scraper_config, enrich_fields, pw=pw
                )

        # Resolve which scraper to run at this step
        step_type, step_cfg = _get_scraper_at_step(scraper_type, scraper_config, scrape_step)

        content = await _batch.scrape_one(item.url, step_type, step_cfg or None, http, pw=pw)
        content = _apply_defaults(content, step_cfg)

        # For step 0, require a usable title; later steps use COALESCE
        if scrape_step == 0 and (not content.title or _is_garbage_title(content.title)):
            if content.title:
                log.info("batch.scrape.garbage_title", url=item.url, title=content.title)
            # Still enqueue next fallback if available
            next_fb = _get_next_fallback(scraper_type, scraper_config, scrape_step)
            if next_fb:
                fb_type, fb_cfg, _fb_fields = next_fb
                needs_browser = scraper_needs_browser(fb_type, fb_cfg)
                domain = urlparse(item.url).hostname or ""
                await enqueue_scrape(
                    domain,
                    item.job_posting_id,
                    time.time(),
                    {
                        "source_url": item.url,
                        "board_id": item.board_id,
                        "description_r2_hash": str(item.description_r2_hash or ""),
                        "scrape_step": str(scrape_step + 1),
                        "scrape_interval_hours": str(scrape_interval),
                    },
                    browser=needs_browser,
                    first_time=True,
                )
                log.info(
                    "batch.scrape.step_failed_enqueue_next",
                    url=item.url,
                    step=scrape_step,
                    next_type=fb_type,
                )
            else:
                async with pool.acquire() as conn:
                    await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
            return False, monotonic() - t0

        content.description = normalize_description_html(content.description)

        # Detect language if not already set
        language = content.language
        if not language and content.description:
            language = detect_language(content.description)

        detected_langs = detect_all_languages(content.description) if content.description else []

        title_text = _coerce_text(content.title)
        desc_text = _coerce_text(content.description)
        lang_text = _coerce_text(language)
        raw_emp_type = _coerce_text(content.employment_type)
        norm_emp_type = normalize_employment_type(raw_emp_type)

        # Resolve locations
        loc_resolver = await _batch._get_location_resolver(pool)
        loc_ids, loc_types = await _batch._resolve_locations(
            loc_resolver,
            _coerce_locations(content.locations),
            _coerce_text(content.job_location_type),
            posting_language=lang_text,
        )

        # Resolve technologies from description
        tech_id_map = await _batch._get_technology_ids(pool)
        tech_ids = _resolve_technology_ids(desc_text, tech_id_map)

        # Resolve occupation + seniority from title
        occ_ids = await _batch._get_occupation_ids(pool)
        sen_ids = await _batch._get_seniority_ids(pool)
        occ_id, sen_id = _resolve_occupation_seniority(title_text, occ_ids, sen_ids)

        # Extract salary + experience from description
        rates = await _batch._get_currency_rates(pool)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)

        # Stage R2 pending data (pure computation, no I/O)
        staged = _stage_r2_pending(
            title=title_text,
            description=desc_text,
            language=lang_text,
            locations=_coerce_locations(content.locations),
            localizations=None,
            extras=content.extras,
            metadata=content.metadata,
            date_posted=content.date_posted,
            base_salary=content.base_salary,
            employment_type=raw_emp_type,
            job_location_type=_coerce_text(content.job_location_type),
            current_hash=item.description_r2_hash,
            source="scrape",
            tech_ids=tech_ids,
        )

        # Always use COALESCE save — never erases existing values
        async with pool.acquire() as conn:
            update_result = await conn.execute(
                _UPDATE_ENRICH_CONTENT,
                item.job_posting_id,
                norm_emp_type,
                _build_titles(title_text, None),
                _build_locales(lang_text, None, detected_languages=detected_langs),
                loc_ids,
                loc_types,
                tech_ids,
                s_min,
                s_max,
                s_cur,
                s_per,
                s_eur,
                exp_min,
                exp_max,
                occ_id,
                sen_id,
            )
            if _parse_update_count(update_result) != 1:
                raise RuntimeError(f"job_posting_not_found:{item.job_posting_id}")
            if staged:
                desc_html, locale, desc_hash = staged
                await conn.execute(
                    _UPSERT_DESCRIPTION,
                    item.job_posting_id,
                    locale,
                    desc_html,
                    desc_hash,
                )
            await conn.execute(_RECORD_SCRAPE_SUCCESS, item.job_posting_id)

        await _batch._flush_location_misses(loc_resolver, pool)

        # Enqueue next fallback step if one exists
        next_fb = _get_next_fallback(scraper_type, scraper_config, scrape_step)
        if next_fb:
            fb_type, fb_cfg, _fb_fields = next_fb
            needs_browser = scraper_needs_browser(fb_type, fb_cfg)
            domain = urlparse(item.url).hostname or ""
            await enqueue_scrape(
                domain,
                item.job_posting_id,
                time.time(),
                {
                    "source_url": item.url,
                    "board_id": item.board_id,
                    "description_r2_hash": str(item.description_r2_hash or ""),
                    "scrape_step": str(scrape_step + 1),
                    "scrape_interval_hours": str(scrape_interval),
                },
                browser=needs_browser,
                first_time=True,
            )
            log.info(
                "batch.scrape.enqueue_next_step",
                url=item.url,
                step=scrape_step,
                next_type=fb_type,
                next_step=scrape_step + 1,
            )

        elapsed = monotonic() - t0
        log.debug(
            "batch.scrape.success",
            url=item.url,
            title=content.title,
            step=scrape_step,
            duration_s=round(elapsed, 2),
        )
        if elapsed >= _SLOW_SCRAPE_SECONDS:
            log.warning("batch.scrape.slow", url=item.url, duration_s=round(elapsed, 2))
        return True, elapsed

    except Exception as exc:
        elapsed = monotonic() - t0
        error_msg = _error_message(exc)
        log.error(
            "batch.scrape.error",
            url=item.url,
            error=error_msg,
            step=scrape_step,
            duration_s=round(elapsed, 2),
        )
        if _lookups_mod._location_resolver is not None:
            _lookups_mod._location_resolver.drain_location_misses()
        with contextlib.suppress(Exception):
            async with pool.acquire() as conn:
                await conn.execute(_RECORD_SCRAPE_FAILURE, item.job_posting_id)
        return False, elapsed


async def _process_one_scrape_insecure(
    item: ScrapeItem,
    pool: asyncpg.Pool,
    scraper_type: str,
    scraper_config: dict | None,
) -> tuple[bool, float]:
    """Wrapper that creates a temporary insecure HTTP client for boards with ssl_verify=False."""
    from src.shared.http import create_http_client

    async with create_http_client(verify=False) as http:
        return await _batch._process_one_scrape(item, pool, http, scraper_type, scraper_config)


async def _do_one_enrich_scrape(
    work: _ScrapeWorkItem,
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> ScrapeResult | ScrapeError:
    """Scrape + enrich inline (no threading). Returns ScrapeResult or ScrapeError.

    Shares the backfill-on-empty semantics documented on
    :func:`_process_one_enrich_scrape`. See that docstring for the rationale.
    """
    item = work.item
    enrich_fields = work.enrich_fields or []
    cfg = work.scraper_config or {}

    content = await _batch.scrape_one(item.url, work.scraper_type, work.scraper_config, http)
    content = _apply_defaults(content, cfg)

    # Normalize before checking
    content.description = normalize_description_html(content.description)

    # Success check: at least one enriched field is non-empty
    has_data = any(getattr(content, f, None) is not None for f in enrich_fields)
    if not has_data:
        return ScrapeError(job_posting_id=item.job_posting_id)

    # Fetch existing row once: used for both backfill detection and R2 staging.
    existing = await pool.fetchrow(_FETCH_POSTING_FOR_ENRICH, item.job_posting_id)

    # Expand enrich_fields with any core fields currently empty on the row
    # (see _process_one_enrich_scrape for rationale).
    effective_fields = list(enrich_fields)
    if existing:
        existing_titles = existing.get("titles")
        existing_loc_ids = existing.get("location_ids")
        existing_et = existing.get("employment_type")
        scraped_title = _coerce_text(content.title)
        title_is_usable = bool(scraped_title) and not _is_garbage_title(scraped_title)
        if "title" not in effective_fields and not existing_titles and title_is_usable:
            effective_fields.append("title")
        if (
            "locations" not in effective_fields
            and not existing_loc_ids
            and _coerce_locations(content.locations)
        ):
            effective_fields.append("locations")
        if (
            "employment_type" not in effective_fields
            and not existing_et
            and _coerce_text(content.employment_type)
        ):
            effective_fields.append("employment_type")

    # Detect language if not already set
    language = content.language
    if not language and content.description:
        language = detect_language(content.description)

    # Default all params to None (COALESCE preserves existing)
    norm_emp_type = None
    all_titles = None
    locales = None
    loc_ids = None
    loc_types = None
    tech_ids = None
    s_min = s_max = s_cur = s_per = s_eur = None
    exp_min = exp_max = None
    occ_id = sen_id = None
    staged = None

    if "employment_type" in effective_fields:
        norm_emp_type = normalize_employment_type(_coerce_text(content.employment_type))

    if "title" in effective_fields:
        title_text = _coerce_text(content.title)
        all_titles = _build_titles(title_text, None) or None
        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
        lang_text = _coerce_text(language)
        if lang_text or content.description:
            detected_langs = (
                detect_all_languages(content.description) if content.description else []
            )
            built = _build_locales(lang_text, None, detected_languages=detected_langs)
            if lang_text or detected_langs:
                locales = built

    if "locations" in effective_fields:
        lang_text = _coerce_text(language)
        loc_ids, loc_types = _resolve_locations_sync(
            loc_resolver,
            _coerce_locations(content.locations),
            _coerce_text(content.job_location_type),
            posting_language=lang_text,
        )

    if "description" in effective_fields:
        desc_text = _coerce_text(content.description)
        tech_ids = _resolve_technology_ids(desc_text, tech_id_map)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)

        # Reuse the earlier fetch for R2 extras.
        r2_title = None
        if existing:
            titles_arr = existing["titles"]
            if titles_arr:
                r2_title = titles_arr[0]
        r2_title = r2_title or _coerce_text(content.title)
        r2_locations = _coerce_locations(content.locations)

        staged = _stage_r2_pending(
            title=r2_title,
            description=desc_text,
            language=_coerce_text(language),
            locations=r2_locations,
            localizations=None,
            extras=content.extras,
            metadata=content.metadata,
            date_posted=content.date_posted,
            base_salary=content.base_salary,
            employment_type=_coerce_text(content.employment_type),
            job_location_type=_coerce_text(content.job_location_type),
            current_hash=item.description_r2_hash,
            source="scrape",
            tech_ids=tech_ids,
        )

    params = (
        item.job_posting_id,
        norm_emp_type,
        all_titles,
        locales,
        loc_ids,
        loc_types,
        tech_ids,
        s_min,
        s_max,
        s_cur,
        s_per,
        s_eur,
        exp_min,
        exp_max,
        occ_id,
        sen_id,
    )

    return ScrapeResult(
        job_posting_id=item.job_posting_id,
        params=params,
        is_enrich=True,
        staged=staged,
    )


async def _do_one_scrape(
    work: _ScrapeWorkItem,
    http: httpx.AsyncClient,
    pool: asyncpg.Pool,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> ScrapeResult | ScrapeError:
    """Scrape + CPU work inline (no threading). Returns ScrapeResult or ScrapeError.

    Modeled on ``_process_one_scrape()`` but returns a result for the DB
    writer instead of writing directly.
    """
    item = work.item
    cfg = work.scraper_config or {}

    # Early dispatch for enrich-only scrapes
    enrich_fields = cfg.get("enrich")
    if isinstance(enrich_fields, list) and enrich_fields:
        return await _do_one_enrich_scrape(
            work, http, pool, loc_resolver, rates, tech_id_map, occ_ids, sen_ids
        )

    content = await _batch.scrape_one(item.url, work.scraper_type, work.scraper_config, http)
    content = _apply_defaults(content, cfg)

    if not content.title or _is_garbage_title(content.title):
        if content.title:
            log.info("pipeline.scrape.garbage_title", url=item.url, title=content.title)
        return ScrapeError(job_posting_id=item.job_posting_id)

    content.description = normalize_description_html(content.description)

    # Detect language if not already set
    language = content.language
    if not language and content.description:
        language = detect_language(content.description)

    detected_langs = detect_all_languages(content.description) if content.description else []

    title_text = _coerce_text(content.title)
    desc_text = _coerce_text(content.description)
    lang_text = _coerce_text(language)
    raw_emp_type = _coerce_text(content.employment_type)
    norm_emp_type = normalize_employment_type(raw_emp_type)

    # Resolve locations (sync -- no threading, no DB backfill)
    loc_ids, loc_types = _resolve_locations_sync(
        loc_resolver,
        _coerce_locations(content.locations),
        _coerce_text(content.job_location_type),
        posting_language=lang_text,
    )

    # Resolve technologies from description
    tech_ids = _resolve_technology_ids(desc_text, tech_id_map)

    # Resolve occupation + seniority from title
    occ_id, sen_id = _resolve_occupation_seniority(title_text, occ_ids, sen_ids)

    # Extract salary + experience from description
    s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
    exp_min, exp_max = _extract_experience_fields(desc_text)

    # Stage R2 pending data (pure computation, no I/O)
    staged = _stage_r2_pending(
        title=title_text,
        description=desc_text,
        language=lang_text,
        locations=_coerce_locations(content.locations),
        localizations=None,
        extras=content.extras,
        metadata=content.metadata,
        date_posted=content.date_posted,
        base_salary=content.base_salary,
        employment_type=raw_emp_type,
        job_location_type=_coerce_text(content.job_location_type),
        current_hash=item.description_r2_hash,
        source="scrape",
        tech_ids=tech_ids,
    )
    params = (
        item.job_posting_id,
        norm_emp_type,
        _build_titles(title_text, None),
        _build_locales(lang_text, None, detected_languages=detected_langs),
        loc_ids,
        loc_types,
        tech_ids,
        s_min,
        s_max,
        s_cur,
        s_per,
        s_eur,
        exp_min,
        exp_max,
        occ_id,
        sen_id,
    )

    return ScrapeResult(
        job_posting_id=item.job_posting_id,
        params=params,
        is_enrich=False,
        staged=staged,
    )


@dataclass
class _PipelineResult:
    succeeded: int = 0
    durations: list[float] = field(default_factory=list)


async def _scrape_pipeline(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, BoardScraperConfig] | None = None,
) -> _PipelineResult:
    """Process scrape items for one domain serially."""
    # Check if any item in this pipeline needs a browser-based scraper
    need_browser = False
    needs_insecure = False
    for item in items:
        if not board_scrapers or item.board_id not in board_scrapers:
            continue
        bsc = board_scrapers[item.board_id]
        if scraper_needs_browser(bsc.scraper_type, bsc.scraper_config):
            need_browser = True
        if not bsc.ssl_verify:
            needs_insecure = True

    return await _run_scrape_items(items, pool, http, board_scrapers, need_browser, needs_insecure)


async def _run_scrape_items(
    items: list[ScrapeItem],
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    board_scrapers: dict[str, BoardScraperConfig] | None,
    need_browser: bool,
    needs_insecure: bool = False,
) -> _PipelineResult:
    """Inner scrape loop, optionally wrapped in a shared Playwright context."""
    pw = None
    pw_ctx = None
    insecure_http = None

    if need_browser:
        try:
            from playwright.async_api import async_playwright

            pw_ctx = async_playwright()
            pw = await pw_ctx.start()
            log.info("batch.scrape.playwright_started")
        except Exception:
            log.warning("batch.scrape.playwright_unavailable", exc_info=True)

    if needs_insecure:
        from src.shared.http import create_http_client

        insecure_http = create_http_client(verify=False)

    try:
        result = _PipelineResult()
        for item in items:
            try:
                scraper_type = "json-ld"
                scraper_config: dict | None = None
                use_insecure = False
                if board_scrapers and item.board_id in board_scrapers:
                    cfg = board_scrapers[item.board_id]
                    scraper_type = cfg.scraper_type
                    scraper_config = cfg.scraper_config
                    use_insecure = not cfg.ssl_verify

                effective_http = insecure_http if use_insecure and insecure_http else http
                ok, elapsed = await _batch._process_one_scrape(
                    item,
                    pool,
                    effective_http,
                    scraper_type,
                    scraper_config,
                    pw=pw,
                )
                result.durations.append(elapsed)
                if ok:
                    result.succeeded += 1
            except Exception:
                log.exception("batch.scrape.pipeline_error", url=item.url)
        return result
    finally:
        if pw_ctx is not None:
            with contextlib.suppress(Exception):
                await pw_ctx.__aexit__(None, None, None)
        if insecure_http is not None:
            await insecure_http.aclose()


async def process_scrape_batch(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    limit: int = 200,
    worker_id: str = "w",
) -> BatchResult:
    """Claim due job postings from Postgres and scrape with domain-parallel pipelines.

    Items targeting the same hostname run serially (respecting per-domain
    throttle).  Different hostnames run concurrently.
    """

    rows = await pool.fetch(_FETCH_DUE_JOB_POSTINGS, limit)

    if not rows:
        return BatchResult()

    all_items = [
        ScrapeItem(
            job_posting_id=str(row["id"]),
            url=row["source_url"],
            board_id=str(row["board_id"]) if row["board_id"] else "",
            description_r2_hash=int(row["description_r2_hash"])
            if row["description_r2_hash"] is not None
            else None,
        )
        for row in rows
    ]

    board_ids = {item.board_id for item in all_items if item.board_id}
    info = await _batch._load_board_scrapers(pool, board_ids)

    # Clear next_scrape_at for postings from rich monitors
    rich_posting_ids = [
        item.job_posting_id for item in all_items if item.board_id in info.rich_board_ids
    ]
    if rich_posting_ids:
        await pool.execute(_CLEAR_SCRAPE_FOR_RICH, rich_posting_ids)
        log.info("batch.scrape.cleared_rich", count=len(rich_posting_ids))

    # Filter out rich-monitor postings
    items = [item for item in all_items if item.board_id not in info.rich_board_ids]

    if not items:
        return BatchResult()

    # Group by scrape domain
    groups: defaultdict[str, list[ScrapeItem]] = defaultdict(list)
    for item in items:
        domain = urlparse(item.url).hostname or "unknown"
        groups[domain].append(item)

    log.info("batch.scrape.start", items=len(items), domains=len(groups))

    # Run domain pipelines concurrently
    t0 = monotonic()
    tasks: list[asyncio.Task[_PipelineResult]] = []
    async with asyncio.TaskGroup() as tg:
        for group_items in groups.values():
            tasks.append(
                tg.create_task(_batch._scrape_pipeline(group_items, pool, http, info.scrapers))
            )

    pipeline_results = [t.result() for t in tasks]
    succeeded = sum(r.succeeded for r in pipeline_results)
    all_durations = [d for r in pipeline_results for d in r.durations]
    elapsed = monotonic() - t0

    return BatchResult(
        processed=len(items),
        succeeded=succeeded,
        failed=len(items) - succeeded,
        duration_s=round(elapsed, 2),
        slow_items=sum(1 for d in all_durations if d >= _SLOW_SCRAPE_SECONDS),
        item_durations=all_durations,
    )
