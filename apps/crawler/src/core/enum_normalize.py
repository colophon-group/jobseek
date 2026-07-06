"""Normalize employment_type, job_location_type and salary unit to canonical enum values.

Central source of truth for value-mapping helpers.  All scrapers and
monitors should pass their raw upstream values through unchanged and
let the ``normalize_*`` functions here handle the mapping; per-module
local maps duplicate logic and drift over time.

Canonical employment-type values (matches the web filter UI):

- ``full_time``
- ``part_time``
- ``contract``
- ``internship``
- ``temporary``
- ``volunteer``
- ``full_or_part``

Canonical job-location-type values: ``onsite`` / ``remote`` / ``hybrid``.

Canonical salary-unit values: ``year`` / ``month`` / ``week`` / ``day``
/ ``hour``.

Mappings cover EN, DE, FR, IT, ES, ZH variants plus schema.org JSON-LD
enums (``FULL_TIME``, ``PART_TIME``, ``CONTRACTOR``, ``INTERN``, …)
and per-ATS API codes (``SALARIED_FT``, ``fulltime_permanent``, …).
Raw values should also be preserved in R2 ``extras.json`` upstream of
this normalization step.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger()

# ── Employment Type ─────────────────────────────────────────────────
# Canonical: full_time, part_time, contract, internship, temporary,
#            volunteer, full_or_part.

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    # ── Canonical self-mappings (idempotency) ───────────────────────
    # Re-normalising an already-canonical value should be a no-op.
    "full_or_part": "full_or_part",
    # ── English ──────────────────────────────────────────────────────
    "full-time": "full_time",
    "full time": "full_time",
    "full_time": "full_time",
    "fulltime": "full_time",
    "full": "full_time",
    "permanent": "full_time",
    "permanent employment": "full_time",
    "permanent full-time": "full_time",
    "regular": "full_time",
    "employee / full-time": "full_time",
    "eor / full-time": "full_time",
    "graduate": "full_time",
    "other": "full_time",
    "other_employment_type": "full_time",
    "salaried_ft": "full_time",
    "hourly_ft": "full_time",
    "fulltime_permanent": "full_time",
    "fulltime_fixed_term": "full_time",
    "permanent_full_time": "full_time",
    "new": "full_time",  # Korean recruiter.co.kr — 신입 (new graduate)
    "career": "full_time",  # Korean recruiter.co.kr — 경력 (experienced)
    "part-time": "part_time",
    "part time": "part_time",
    "part_time": "part_time",
    "parttime": "part_time",
    "part": "part_time",
    "salaried_pt": "part_time",
    "hourly_pt": "part_time",
    "parttime_permanent": "part_time",
    "parttime_fixed_term": "part_time",
    "permanent_part_time": "part_time",
    "mini_job": "part_time",
    "minijob": "part_time",
    "seasonal": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "freelance": "contract",
    "freelancer": "contract",
    "fixed term": "contract",
    "fixed term (fixed term)": "contract",
    "fixed term / full-time": "contract",
    "fixed_term_contract": "contract",
    "contract_temp": "contract",
    "contract_to_hire": "contract",
    "consultant": "contract",
    "self-employed": "contract",
    "temporary": "temporary",
    "temporary positions": "temporary",
    "temp": "temporary",
    "internship": "internship",
    "intern": "internship",
    "interns": "internship",
    "trainee": "internship",
    "traineeship": "internship",
    "apprentice": "internship",
    "apprenticeship": "internship",
    "co-op": "internship",
    "coop": "internship",
    "working student": "internship",
    "volunteer": "volunteer",
    "voluntary": "volunteer",
    "full time or part time": "full_or_part",
    "full-time, part-time": "full_or_part",
    "permanent full-time or part-time": "full_or_part",
    "temporary positions, full-time": "full_or_part",
    "full_time, part_time": "full_or_part",
    "full-time / part-time": "full_or_part",
    # ── schema.org JSON-LD enums ─────────────────────────────────────
    # The lookup lowercases keys, so FULL_TIME / PART_TIME / CONTRACTOR /
    # INTERN / TEMPORARY / VOLUNTEER / PER_DIEM / OTHER fall through to
    # the entries above (full_time, part_time, contractor, intern, …).
    "per_diem": "part_time",
    # ── German ───────────────────────────────────────────────────────
    "festanstellung": "full_time",
    "unbefristet": "full_time",
    "vollzeit": "full_time",
    "regulär": "full_time",
    "teilzeit": "part_time",
    "werkstudent": "internship",
    "praktikum": "internship",
    "praktikant": "internship",
    "lernende": "internship",
    "ausbildung": "internship",
    "azubi": "internship",
    "auszubildende": "internship",
    "befristet": "contract",
    "zeitarbeit": "contract",
    "freiberuflich": "contract",
    "vollzeit oder teilzeit": "full_or_part",
    "voll- oder teilzeit": "full_or_part",
    "voll-/teilzeit": "full_or_part",
    "voll- und teilzeit": "full_or_part",
    # ── French ───────────────────────────────────────────────────────
    "cdi": "full_time",
    "emploi fixe": "full_time",
    "temps plein": "full_time",
    "plein temps": "full_time",
    "libéral": "full_time",
    "temps partiel": "part_time",
    "mi-temps": "part_time",
    "cdd": "contract",
    "intérim": "contract",
    "intérimaire": "contract",
    "indépendant": "contract",
    "stage": "internship",
    "alternance": "internship",
    "apprentissage": "internship",
    "stagiaire": "internship",
    "temps plein ou partiel": "full_or_part",
    "temps plein / temps partiel": "full_or_part",
    # ── Italian ──────────────────────────────────────────────────────
    "impiego fisso": "full_time",
    "tempo indeterminato": "full_time",
    "tempo pieno": "full_time",
    "a tempo pieno": "full_time",
    "tempo parziale": "part_time",
    "tempo determinato": "contract",
    "a tempo determinato": "contract",
    "contratto a termine": "contract",
    "lavoro interinale": "contract",
    "collaborazione": "contract",
    "tirocinio": "internship",
    "apprendistato": "internship",
    "tempo pieno o parziale": "full_or_part",
    # ── Spanish ──────────────────────────────────────────────────────
    "indefinido": "full_time",
    "contrato indefinido": "full_time",
    "tiempo completo": "full_time",
    "jornada completa": "full_time",
    "tiempo parcial": "part_time",
    "jornada parcial": "part_time",
    "media jornada": "part_time",
    "contrato temporal": "contract",
    "contrato por obra": "contract",
    "autónomo": "contract",
    "becario": "internship",
    "prácticas": "internship",
    "practicas": "internship",
    "contrato de prácticas": "internship",
    "aprendizaje": "internship",
    # ── Czech / Slovak (almacareer ATS) ─────────────────────────────
    "práce na plný úvazek": "full_time",
    "práca na plný úväzok": "full_time",
    "full-time work": "full_time",
    "práce na zkrácený úvazek": "part_time",
    "práca na skrátený úväzok": "part_time",
    "part-time work": "part_time",
    "brigáda": "part_time",
    "dohoda o provedení práce": "contract",
    "dohoda o pracovní činnosti": "contract",
    "externí spolupráce": "contract",
    "živnosť": "contract",
    "dohoda": "contract",
    "stáž": "internship",
    "stáž/prax": "internship",
    # ── Chinese (Mokahr, 51job-style ATSes) ─────────────────────────
    "全职": "full_time",
    "全職": "full_time",
    "兼职": "part_time",
    "兼職": "part_time",
    "实习": "internship",
    "實習": "internship",
    "實習生": "internship",
    "实习生": "internship",
    "合同工": "contract",
    "合約": "contract",
    "派遣": "contract",
    "临时": "temporary",
    "臨時": "temporary",
    # ── Mokahr commitment codes ─────────────────────────────────────
    # ``fullTime``/``partTime``/``intern``/``contract`` lowercased to
    # ``fulltime``/``parttime``/``intern``/``contract`` — already covered
    # above. The Chinese ``全职`` / ``兼职`` / ``实习`` come from the same
    # API when it returns the localised label.
    # ── Polish (traffit, etc.) ───────────────────────────────────────
    "pełny etat": "full_time",
    "pelny etat": "full_time",
    "część etatu": "part_time",
    "czesc etatu": "part_time",
    "umowa zlecenie": "contract",
    "umowa o dzieło": "contract",
    "staż": "internship",
}


# ── Job Location Type ───────────────────────────────────────────────
# Canonical: onsite, remote, hybrid

_JOB_LOCATION_TYPE_MAP: dict[str, str] = {
    # English
    "onsite": "onsite",
    "on-site": "onsite",
    "on_site": "onsite",  # workable / gem snake_case
    "on site": "onsite",
    "office": "onsite",
    "in-office": "onsite",
    "in_office": "onsite",  # gem snake_case
    "in office": "onsite",
    "on-premises": "onsite",
    "in-person": "onsite",
    "in person": "onsite",
    "remote": "remote",
    "telecommute": "remote",
    "work from home": "remote",
    "wfh": "remote",
    "fully remote": "remote",
    "100% remote": "remote",
    "hybrid": "hybrid",
    "office, remote": "hybrid",
    "remote, office": "hybrid",
    "office/remote": "hybrid",
    "remote/office": "hybrid",
    "flexible": "hybrid",
    "partially remote": "hybrid",
    # German
    "vor ort": "onsite",
    "büro": "onsite",
    "homeoffice": "remote",
    "home office": "remote",
    "fernarbeit": "remote",
    "remote arbeit": "remote",
    "teilweise remote": "hybrid",
    "flexibel": "hybrid",
    # French
    "sur site": "onsite",
    "sur place": "onsite",
    "présentiel": "onsite",
    "en présentiel": "onsite",
    "bureau": "onsite",
    "télétravail": "remote",
    "à distance": "remote",
    "travail à distance": "remote",
    "hybride": "hybrid",
    "télétravail partiel": "hybrid",
    # Italian
    "in sede": "onsite",
    "in ufficio": "onsite",
    "in loco": "onsite",
    "da remoto": "remote",
    "lavoro da remoto": "remote",
    "telelavoro": "remote",
    "lavoro a distanza": "remote",
    "ibrido": "hybrid",
}


def normalize_employment_type(raw: str | None) -> str | None:
    """Normalize employment type to canonical enum value.

    Returns ``None`` when *raw* is ``None``, empty/whitespace, or an
    unrecognised token.  Lookup is case-insensitive and trims
    surrounding whitespace.

    A structured warning (``enum_normalize.employment_type.unknown``) is
    emitted on miss so operators can spot new upstream tokens and
    extend the central map.  Per the function name "normalize": the
    caller is free to preserve the raw value, store NULL, or apply its
    own default — silent coercion to ``full_time`` (the pre-#3222
    behaviour) is gone because it conflated unknown tokens with real
    full-time postings.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    mapped = _EMPLOYMENT_TYPE_MAP.get(key)
    if mapped is None:
        log.warning("enum_normalize.employment_type.unknown", raw=raw)
    return mapped


def normalize_job_location_type(raw: str | None, default: str | None = None) -> str | None:
    """Normalize job location type to canonical enum value.

    Returns ``None`` when *raw* is ``None`` or empty/whitespace.

    Lookup is case-insensitive and trims surrounding whitespace.  If the
    trimmed value isn't in the map, emits a structured warning
    (``enum_normalize.job_location_type.unknown``) and returns
    *default* (``None`` since #3222).  Callers that want a last-resort
    bucket can pass ``default="onsite"`` explicitly.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    mapped = _JOB_LOCATION_TYPE_MAP.get(key)
    if mapped is None:
        log.warning("enum_normalize.job_location_type.unknown", raw=raw)
        return default
    return mapped


# ── Salary Unit ─────────────────────────────────────────────────────
# Canonical: year, month, week, day, hour.
#
# Feeds ``JobContent.base_salary.unit`` (R2 ``extras.json`` only — the
# DB ``salary_period`` column is computed independently from description
# text via ``salary_extract.py``).  The web canonical
# ``SalaryPeriod = "yearly|monthly|daily|hourly"`` lives in
# ``apps/web/src/lib/salary.ts`` — the short form here is R2-side only.

_SALARY_UNIT_MAP: dict[str, str] = {
    # ── year family ─────────────────────────────────────────────────
    "year": "year",
    "yr": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
    "per year": "year",
    "per-year": "year",
    "per-year-salary": "year",  # lever
    "yearly_annually": "year",
    # ── month family ────────────────────────────────────────────────
    "month": "month",
    "mo": "month",
    "monthly": "month",
    "per month": "month",
    "per-month": "month",
    "per-month-salary": "month",  # lever
    # ── week family ─────────────────────────────────────────────────
    "week": "week",
    "weekly": "week",
    "per week": "week",
    "per-week": "week",
    "two_weeks": "week",  # pinpoint biweekly cadence
    "biweekly": "week",
    # ── day family ──────────────────────────────────────────────────
    "day": "day",
    "daily": "day",
    "per day": "day",
    "per-day": "day",
    # ── hour family ─────────────────────────────────────────────────
    "hour": "hour",
    "hr": "hour",
    "hourly": "hour",
    "per hour": "hour",
    "per-hour": "hour",
    "per-hour-wage": "hour",  # lever
}

# Substring-fallback ordering matters: ``hour`` is a substring of nothing
# else in the canonical set, but ``year``/``yearly`` and ``month``/
# ``monthly`` overlap.  Match the longer / more specific tokens first.
_SALARY_UNIT_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("two_weeks", "week"),
    ("biweekly", "week"),
    ("hour", "hour"),
    ("month", "month"),
    ("week", "week"),
    ("year", "year"),
    ("annual", "year"),
    ("daily", "day"),
    ("day", "day"),
)


def normalize_salary_unit(raw: str | None) -> str | None:
    """Normalize a salary period string to canonical short form.

    Returns one of ``year|month|week|day|hour``, or ``None`` when *raw*
    is ``None`` / empty / whitespace / an unrecognised token.

    Lookup is case-insensitive and trims surrounding whitespace.  An
    exact match in the map is tried first; if that misses, the
    substring scanners fall back so callers can pass ATS-specific
    composite tokens (e.g. ``"per-hour-wage"`` → ``hour``) without
    pre-tokenising.

    Callers that previously defaulted to ``"month"`` or ``"year"`` when
    the upstream value was missing should keep that default at the
    call site (``normalize_salary_unit(raw) or "year"``) so this
    refactor doesn't change emitted values.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    direct = _SALARY_UNIT_MAP.get(key)
    if direct is not None:
        return direct
    for needle, canonical in _SALARY_UNIT_SUBSTRINGS:
        if needle in key:
            return canonical
    return None
