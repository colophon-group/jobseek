"""CPU-intensive job processing — normalize, detect language, resolve, extract."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime

from src.core.enum_normalize import normalize_employment_type
from src.core.experience_extract import extract_experience
from src.core.location_resolve import LocationResolver
from src.core.occupation_resolve import match_occupation
from src.core.salary_extract import extract_salary_unified
from src.core.scrapers import enrich_description
from src.core.seniority_resolve import match_seniority
from src.core.technology_resolve import match_technologies
from src.shared.html_normalize import normalize_description_html
from src.shared.langdetect import detect_all_languages, detect_language


@dataclass
class JobCPUResult:
    """CPU-processed job data ready for INSERT."""

    url: str
    insert_record: tuple  # positional params for _INSERT_RICH_JOB
    r2_staging_args: dict  # kwargs for _stage_r2_pending
    tech_ids: list[int] | None


@dataclass
class BatchResult:
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    duration_s: float = 0.0
    slow_items: int = 0
    item_durations: list[float] = field(default_factory=list)


# Titles that indicate a broken scrape (auth wall, CAPTCHA, etc.)
_GARBAGE_TITLES = frozenset(
    s.lower()
    for s in (
        "Not Logged In",
        "Log in to Career Profile",
        "Access Denied",
        "Just a moment...",
        "Page Not Found",
        "404",
        "403 Forbidden",
        "Sign In",
        "Login",
        "Redirecting",
    )
)


def _is_garbage_title(title: str) -> bool:
    """Return True if the title is a known broken-scrape artifact."""
    return title.strip().lower() in _GARBAGE_TITLES


def _resolve_occupation_seniority(
    titles: list[str] | str | None,
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> tuple[int | None, int | None]:
    """Resolve occupation_id and seniority_id from job title(s).

    Tries each title individually and returns the first match for each.
    This handles multilingual titles correctly (e.g. German title may
    match seniority while English title matches occupation).
    """
    if not titles:
        return None, None
    if isinstance(titles, str):
        titles = [titles]

    occ_id: int | None = None
    sen_id: int | None = None
    for title in titles:
        if not title or not isinstance(title, str):
            continue
        title = title.strip()
        if not title:
            continue
        if occ_id is None:
            slug = match_occupation(title)
            if slug:
                occ_id = occ_ids.get(slug)
        if sen_id is None:
            slug = match_seniority(title)
            if slug:
                sen_id = sen_ids.get(slug)
        if occ_id is not None and sen_id is not None:
            break
    return occ_id, sen_id


def _resolve_technology_ids(description: str | None, tech_ids: dict[str, int]) -> list[int] | None:
    """Extract technology IDs from description text. Returns None if no matches."""
    if not description:
        return None
    slugs = match_technologies(description)
    if not slugs:
        return None
    ids = sorted({tech_ids[s] for s in slugs if s in tech_ids})
    return ids or None


def _extract_salary_fields(
    html: str | None,
    rates: dict[str, float],
) -> tuple[int | None, int | None, str | None, str | None, int | None]:
    """Extract salary from HTML and store raw values.

    Returns (salary_min, salary_max, salary_currency, salary_period, salary_eur).
    salary_min/max are the raw values in the original period and currency.
    salary_eur is the annualized EUR equivalent for index-based filtering;
    refreshed daily by refresh_currency_rates.py when exchange rates change.
    """
    if not html:
        return None, None, None, None, None
    sr = extract_salary_unified(html)
    if sr is None:
        return None, None, None, None, None

    # Store raw values in original period (always integers for DB)
    # Hourly values are stored in cents (e.g. $25.50/hr -> 2550)
    sal_min = sr.min
    sal_max = sr.max

    # Annualize only for the EUR filter column.
    # Source of truth for the hourly→yearly multiplier: 2080 = 52 × 40 (US
    # convention, ignoring holidays). `apps/web/src/lib/salary.ts::TO_YEARLY`
    # mirrors this constant so display-side conversions match the salary
    # filter cutoffs computed here. See issue #3194.
    if sr.period == "hourly":
        annual_min = round(sr.min / 100 * 2080)
    elif sr.period == "monthly":
        annual_min = sr.min * 12
    else:
        annual_min = sr.min

    to_eur = rates.get(sr.currency, 0)
    salary_eur = round(annual_min * to_eur) if to_eur > 0 else None

    return sal_min, sal_max, sr.currency, sr.period, salary_eur


def _extract_experience_fields(html: str | None) -> tuple[int | None, int | None]:
    """Extract experience requirement from HTML.

    Returns (experience_min, experience_max). max is None for open-ended ("5+ years").
    """
    if not html:
        return None, None
    result = extract_experience(html)
    if result is None:
        return None, None
    return result.min_years, result.max_years


def _resolve_locations_sync(
    resolver: LocationResolver,
    locations: list[str] | None,
    job_location_type: str | None,
    posting_language: str | None = None,
) -> tuple[list[int] | None, list[str] | None]:
    """Synchronous location resolution (cache only, no DB backfill).

    Used by threaded batch processing.  Call ``resolver.backfill_misses()``
    after the thread completes to handle cache misses.

    Returns parallel arrays of (location_ids, location_types) — leaf IDs only.
    Ancestor expansion for Typesense happens at indexing time in the exporter.
    """
    results = resolver.resolve(locations, job_location_type, posting_language)
    if not results:
        return None, None
    loc_ids: list[int] = []
    loc_types: list[str] = []
    for r in results:
        if r.location_id is not None:
            loc_ids.append(r.location_id)
            loc_types.append(r.location_type)
    return loc_ids or None, loc_types or None


# ── Helpers ──────────────────────────────────────────────────────────


def _jsonb(val: dict | None) -> str | None:
    return json.dumps(val) if val is not None else None


def _error_message(exc: Exception, max_len: int = 500) -> str:
    """Return a non-empty, bounded error message for logs/DB fields."""
    text = str(exc).strip()
    message = text or type(exc).__name__
    if len(message) > max_len:
        return message[:max_len]
    return message


def _coerce_text(val: object | None) -> str | None:
    """Normalize scalars/lists to a single text value for Postgres text columns."""
    if val is None:
        return None
    if isinstance(val, str):
        stripped = val.strip()
        return stripped or None
    if isinstance(val, (list, tuple, set)):
        parts: list[str] = []
        for item in val:
            part = _coerce_text(item)
            if part:
                parts.append(part)
        if not parts:
            return None
        return ", ".join(dict.fromkeys(parts))
    if isinstance(val, dict):
        return json.dumps(val, sort_keys=True)
    return str(val)


def _coerce_locations(val: object | None) -> list[str] | None:
    """Normalize values to a Postgres text[] payload."""
    if val is None:
        return None
    if isinstance(val, str):
        text = val.strip()
        return [text] if text else None
    if isinstance(val, (list, tuple, set)):
        parts: list[str] = []
        for item in val:
            part = _coerce_text(item)
            if part:
                parts.append(part)
        if not parts:
            return None
        return list(dict.fromkeys(parts))
    text = _coerce_text(val)
    return [text] if text else None


def _coerce_datetime(val: object | None) -> datetime | None:
    """Normalize common monitor/scraper timestamp formats for timestamptz columns."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, date):
        return datetime.combine(val, time.min, tzinfo=UTC)
    if isinstance(val, (int, float)):
        with contextlib.suppress(Exception):
            return datetime.fromtimestamp(val, tz=UTC)
        return None
    if not isinstance(val, str):
        return None

    raw = val.strip()
    if not raw:
        return None

    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    if raw.endswith(" UTC"):
        candidates.append(raw[:-4] + "+00:00")

    for candidate in candidates:
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    with contextlib.suppress(ValueError):
        parsed_date = date.fromisoformat(raw)
        return datetime.combine(parsed_date, time.min, tzinfo=UTC)

    with contextlib.suppress(Exception):
        parsed = parsedate_to_datetime(raw)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    return None


def _parse_metadata(raw: object) -> dict:
    """Normalize job_board.metadata values from asyncpg to plain dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    return {}


def _parse_update_count(result: object) -> int:
    """Extract rowcount from asyncpg command status (e.g. ``UPDATE 1``)."""
    if not isinstance(result, str):
        return 0
    parts = result.rsplit(" ", 1)
    if len(parts) != 2:
        return 0
    with contextlib.suppress(ValueError):
        return int(parts[1])
    return 0


def _build_titles(title: str | None, localizations: dict | None) -> list[str]:
    """Build titles array from primary title + localizations."""
    titles: list[str] = []
    if title:
        titles.append(title)
    if localizations and isinstance(localizations, dict):
        for loc_data in localizations.values():
            if isinstance(loc_data, dict):
                loc_title = loc_data.get("title")
                if loc_title and loc_title not in titles:
                    titles.append(loc_title)
    return titles


def _build_locales(
    language: str | None,
    localizations: dict | None,
    *,
    detected_languages: list[str] | None = None,
) -> list[str]:
    """Build locales array from primary language + localization keys + detected."""
    locales: list[str] = []
    primary = language or "en"
    locales.append(primary)
    if localizations and isinstance(localizations, dict):
        for locale in localizations:
            if locale not in locales:
                locales.append(locale)
    if detected_languages:
        for lang in detected_languages:
            if lang not in locales:
                locales.append(lang)
    return locales


def _process_jobs_cpu(
    jobs_by_url: dict,
    company_id: str,
    board_id: str,
    loc_resolver: LocationResolver,
    rates: dict[str, float],
    tech_id_map: dict[str, int],
    occ_ids: dict[str, int],
    sen_ids: dict[str, int],
) -> dict[str, JobCPUResult]:
    """Pure CPU work: normalize, detect language, resolve, extract.

    Runs inline on the event loop.  No async, no DB.
    Returns a dict of ``{url: JobCPUResult}``.
    """
    results: dict[str, JobCPUResult] = {}
    for url, j in jobs_by_url.items():
        j.description = normalize_description_html(j.description)
        enrich_description(j)
        if not j.language and j.description:
            j.language = detect_language(j.description)

        loc_ids_r, loc_types_r = _resolve_locations_sync(
            loc_resolver,
            _coerce_locations(j.locations),
            _coerce_text(j.job_location_type),
            _coerce_text(j.language),
        )
        desc_text = _coerce_text(j.description)
        s_min, s_max, s_cur, s_per, s_eur = _extract_salary_fields(desc_text, rates)
        exp_min, exp_max = _extract_experience_fields(desc_text)
        t_ids = _resolve_technology_ids(desc_text, tech_id_map)
        title_text = _coerce_text(j.title)
        all_titles = _build_titles(title_text, j.localizations)
        occ_id, sen_id = _resolve_occupation_seniority(all_titles, occ_ids, sen_ids)
        detected_langs = detect_all_languages(j.description) if j.description else []

        insert_record = (
            company_id,
            board_id,
            normalize_employment_type(_coerce_text(j.employment_type)),
            j.url,
            all_titles,
            _build_locales(
                _coerce_text(j.language),
                j.localizations,
                detected_languages=detected_langs,
            ),
            loc_ids_r,
            loc_types_r,
            s_min,
            s_max,
            s_cur,
            s_per,
            s_eur,
            exp_min,
            exp_max,
            t_ids,
            occ_id,
            sen_id,
        )

        r2_staging_args = dict(
            title=_coerce_text(j.title),
            description=_coerce_text(j.description),
            language=_coerce_text(j.language),
            locations=_coerce_locations(j.locations),
            localizations=j.localizations,
            extras=j.extras,
            metadata=j.metadata,
            date_posted=j.date_posted,
            base_salary=j.base_salary,
            employment_type=_coerce_text(j.employment_type),
            job_location_type=_coerce_text(j.job_location_type),
            source="monitor",
            tech_ids=t_ids,
        )

        results[url] = JobCPUResult(
            url=url,
            insert_record=insert_record,
            r2_staging_args=r2_staging_args,
            tech_ids=t_ids,
        )
    return results
