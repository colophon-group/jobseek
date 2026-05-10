"""Tests for the central :mod:`src.core.enum_normalize` module.

Covers all variants emitted by every monitor / scraper today.  Adding a
new ATS that produces a previously unseen employment-type or
job-location-type spelling should add a parametrised case here as well
as any required entry in ``enum_normalize.py``.
"""

from __future__ import annotations

import pytest

from src.core.enum_normalize import (
    normalize_employment_type,
    normalize_job_location_type,
    normalize_salary_unit,
)

# ── normalize_employment_type ───────────────────────────────────────


class TestNormalizeEmploymentTypeNullish:
    def test_none(self):
        assert normalize_employment_type(None) is None

    def test_empty_string(self):
        assert normalize_employment_type("") is None

    def test_whitespace_only(self):
        assert normalize_employment_type("   \t\n") is None


class TestNormalizeEmploymentTypeCanonicalSet:
    """Every output must be one of the canonical bucket names."""

    _CANONICAL = {
        "full_time",
        "part_time",
        "contract",
        "internship",
        "temporary",
        "volunteer",
        "full_or_part",
    }

    @pytest.mark.parametrize(
        "raw",
        [
            "Full-time",
            "fullTime",
            "全职",
            "INTERN",
            "freelance",
            "Some unknown value",
            "x" * 200,
        ],
    )
    def test_output_is_canonical_or_none(self, raw):
        result = normalize_employment_type(raw)
        assert result in self._CANONICAL


class TestNormalizeEmploymentTypeEnglish:
    @pytest.mark.parametrize(
        "raw",
        [
            "Full-time",
            "Full Time",
            "FULL_TIME",
            "fulltime",
            "full",
            "permanent",
            "Permanent Employment",
            "permanent full-time",
            "regular",
            "graduate",
        ],
    )
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize(
        "raw",
        ["Part-time", "Part Time", "PART_TIME", "parttime", "part"],
    )
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize(
        "raw",
        [
            "Contract",
            "Contractor",
            "freelance",
            "Freelancer",
            "Fixed Term",
            "Fixed Term (Fixed Term)",
            "Fixed term / full-time",
            "fixed_term_contract",
            "contract_temp",
            "contract_to_hire",
        ],
    )
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize(
        "raw",
        [
            "Internship",
            "Intern",
            "INTERN",
            "internship",
            "Trainee",
            "traineeship",
            "Apprenticeship",
            "apprentice",
            "Co-op",
            "Working Student",
        ],
    )
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"

    @pytest.mark.parametrize("raw", ["Temporary", "TEMPORARY", "temp"])
    def test_temporary(self, raw):
        assert normalize_employment_type(raw) == "temporary"

    @pytest.mark.parametrize("raw", ["Volunteer", "VOLUNTEER", "Voluntary"])
    def test_volunteer(self, raw):
        assert normalize_employment_type(raw) == "volunteer"

    @pytest.mark.parametrize(
        "raw",
        [
            "Full time or part time",
            "Full-time, Part-time",
            "Permanent full-time or part-time",
            "Full-time / part-time",
        ],
    )
    def test_full_or_part(self, raw):
        assert normalize_employment_type(raw) == "full_or_part"


class TestNormalizeEmploymentTypeChinese:
    """Mokahr (mainland) and 51job-style traditional Chinese ATSes."""

    @pytest.mark.parametrize("raw", ["全职", "全職"])
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize("raw", ["兼职", "兼職"])
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize("raw", ["实习", "實習", "实习生", "實習生"])
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"

    @pytest.mark.parametrize("raw", ["合同工", "合約", "派遣"])
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize("raw", ["临时", "臨時"])
    def test_temporary(self, raw):
        assert normalize_employment_type(raw) == "temporary"


class TestNormalizeEmploymentTypeFrench:
    @pytest.mark.parametrize(
        "raw",
        ["CDI", "Emploi fixe", "Temps plein", "Plein temps", "libéral"],
    )
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize("raw", ["Temps partiel", "mi-temps"])
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize("raw", ["CDD", "Intérim", "intérimaire", "indépendant", "freelance"])
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize("raw", ["Stage", "Alternance", "Apprentissage", "Stagiaire"])
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"


class TestNormalizeEmploymentTypeGerman:
    @pytest.mark.parametrize("raw", ["Festanstellung", "Vollzeit", "Unbefristet", "regulär"])
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize("raw", ["Teilzeit", "MiniJob", "mini_job"])
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize(
        "raw",
        [
            "Praktikum",
            "Praktikant",
            "Werkstudent",
            "Ausbildung",
            "Auszubildende",
            "Lernende",
            "Azubi",
        ],
    )
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"

    @pytest.mark.parametrize("raw", ["Befristet", "Zeitarbeit", "freiberuflich", "Freelancer"])
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize(
        "raw",
        [
            "Vollzeit oder Teilzeit",
            "Voll- oder Teilzeit",
            "Voll-/Teilzeit",
            "Voll- und Teilzeit",
        ],
    )
    def test_full_or_part(self, raw):
        assert normalize_employment_type(raw) == "full_or_part"


class TestNormalizeEmploymentTypeSpanish:
    @pytest.mark.parametrize(
        "raw",
        ["Indefinido", "Contrato indefinido", "Tiempo completo", "Jornada completa"],
    )
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize("raw", ["Tiempo parcial", "Jornada parcial", "Media jornada"])
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize("raw", ["Contrato temporal", "Contrato por obra", "Autónomo"])
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize(
        "raw", ["Becario", "Prácticas", "Practicas", "Contrato de prácticas", "Aprendizaje"]
    )
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"


class TestNormalizeEmploymentTypeItalian:
    @pytest.mark.parametrize(
        "raw",
        ["Tempo pieno", "A tempo pieno", "Tempo indeterminato", "Impiego fisso"],
    )
    def test_full_time(self, raw):
        assert normalize_employment_type(raw) == "full_time"

    @pytest.mark.parametrize("raw", ["Tempo parziale"])
    def test_part_time(self, raw):
        assert normalize_employment_type(raw) == "part_time"

    @pytest.mark.parametrize(
        "raw",
        [
            "Tempo determinato",
            "A tempo determinato",
            "Contratto a termine",
            "Lavoro interinale",
            "Collaborazione",
        ],
    )
    def test_contract(self, raw):
        assert normalize_employment_type(raw) == "contract"

    @pytest.mark.parametrize("raw", ["Tirocinio", "Apprendistato"])
    def test_internship(self, raw):
        assert normalize_employment_type(raw) == "internship"


class TestNormalizeEmploymentTypeJsonLd:
    """schema.org JobPosting employmentType enums."""

    @pytest.mark.parametrize(
        "raw,want",
        [
            ("FULL_TIME", "full_time"),
            ("PART_TIME", "part_time"),
            ("CONTRACTOR", "contract"),
            ("INTERN", "internship"),
            ("TEMPORARY", "temporary"),
            ("VOLUNTEER", "volunteer"),
            ("PER_DIEM", "part_time"),
            # OTHER intentionally falls back to full_time per historic
            # behaviour — see the central map.
            ("OTHER", "full_time"),
        ],
    )
    def test_enum(self, raw, want):
        assert normalize_employment_type(raw) == want


class TestNormalizeEmploymentTypeAtsCodes:
    """Per-ATS API codes that used to live in scraper-local maps."""

    @pytest.mark.parametrize(
        "raw,want",
        [
            # Mokahr camelCase
            ("fullTime", "full_time"),
            ("partTime", "part_time"),
            ("intern", "internship"),
            ("contract", "contract"),
            # Pinpoint
            ("full_time", "full_time"),
            ("permanent_full_time", "full_time"),
            ("part_time", "part_time"),
            ("permanent_part_time", "part_time"),
            ("contract_temp", "contract"),
            ("contract_to_hire", "contract"),
            ("fixed_term_contract", "contract"),
            ("freelance", "contract"),
            ("internship", "internship"),
            ("temporary", "temporary"),
            ("volunteer", "volunteer"),
            ("permanent", "full_time"),
            # Recruitee
            ("fulltime", "full_time"),
            ("fulltime_permanent", "full_time"),
            ("fulltime_fixed_term", "full_time"),
            ("parttime", "part_time"),
            ("parttime_permanent", "part_time"),
            ("parttime_fixed_term", "part_time"),
            ("traineeship", "internship"),
            # Workable
            ("full", "full_time"),
            ("part", "part_time"),
            ("other", "full_time"),
            # Rippling
            ("SALARIED_FT", "full_time"),
            ("SALARIED_PT", "part_time"),
            ("HOURLY_FT", "full_time"),
            ("HOURLY_PT", "part_time"),
            # Ashby PascalCase
            ("FullTime", "full_time"),
            ("PartTime", "part_time"),
            ("Intern", "internship"),
            ("Contract", "contract"),
            ("Temporary", "temporary"),
            # Recruiter.co.kr
            ("NEW", "full_time"),
            ("CAREER", "full_time"),
            ("INTERNSHIP", "internship"),
            ("PARTTIME", "part_time"),
            ("PART_TIME", "part_time"),
            # Almacareer fallback labels (CZ/SK)
            ("práce na plný úvazek", "full_time"),
            ("práca na plný úväzok", "full_time"),
            ("brigáda", "part_time"),
            ("stáž", "internship"),
            ("živnosť", "contract"),
            # BITE
            ("mini_job", "part_time"),
        ],
    )
    def test_ats_code(self, raw, want):
        assert normalize_employment_type(raw) == want


class TestNormalizeEmploymentTypeWhitespaceCase:
    """Lookup is case-insensitive and trims whitespace."""

    def test_strips_whitespace(self):
        assert normalize_employment_type("  Full-time  ") == "full_time"

    def test_uppercase(self):
        assert normalize_employment_type("FULL-TIME") == "full_time"

    def test_lowercase(self):
        assert normalize_employment_type("full-time") == "full_time"

    def test_mixed_case_german(self):
        assert normalize_employment_type("vollzeit") == "full_time"
        assert normalize_employment_type("Vollzeit") == "full_time"
        assert normalize_employment_type("VOLLZEIT") == "full_time"


class TestNormalizeEmploymentTypeIdempotency:
    """Re-normalising a canonical value must be a no-op.

    The exporter, watchlist reconciler and several test fixtures pass
    already-canonical values back through this function — silent
    misclassification (especially of ``full_or_part`` -> ``full_time``)
    would corrupt those flows.
    """

    @pytest.mark.parametrize(
        "canonical",
        [
            "full_time",
            "part_time",
            "contract",
            "internship",
            "temporary",
            "volunteer",
            "full_or_part",
        ],
    )
    def test_canonical_is_idempotent(self, canonical):
        assert normalize_employment_type(canonical) == canonical


class TestNormalizeEmploymentTypeFallback:
    """Unknown values fall back to ``full_time`` historically.

    This is documented behaviour — extending the central map should be
    preferred to relying on it.  These tests pin the fallback in place
    so a future change can't accidentally regress it.
    """

    def test_unknown_value_falls_back_to_full_time(self):
        assert normalize_employment_type("Mystery employment kind") == "full_time"


# ── normalize_job_location_type ─────────────────────────────────────


class TestNormalizeJobLocationType:
    @pytest.mark.parametrize(
        "raw,want",
        [
            ("onsite", "onsite"),
            ("On-site", "onsite"),
            ("In office", "onsite"),
            ("vor ort", "onsite"),
            ("Bureau", "onsite"),
            ("In sede", "onsite"),
            ("remote", "remote"),
            ("Telecommute", "remote"),
            ("Work from home", "remote"),
            ("WFH", "remote"),
            ("Homeoffice", "remote"),
            ("Télétravail", "remote"),
            ("Da remoto", "remote"),
            ("hybrid", "hybrid"),
            ("Hybrid", "hybrid"),
            ("teilweise remote", "hybrid"),
            ("flexibel", "hybrid"),
            ("Hybride", "hybrid"),
            ("Ibrido", "hybrid"),
        ],
    )
    def test_known_value(self, raw, want):
        assert normalize_job_location_type(raw) == want

    def test_none(self):
        assert normalize_job_location_type(None) is None

    def test_empty(self):
        assert normalize_job_location_type("") is None

    def test_unknown_falls_back_to_onsite(self):
        assert normalize_job_location_type("Mystery location type") == "onsite"


class TestNormalizeJobLocationTypeAtsCodes:
    """Per-ATS API tokens that used to live in monitor/scraper-local maps.

    Pinned here so reverting any of these keys in the central
    ``_JOB_LOCATION_TYPE_MAP`` would surface as a parametrised failure
    instead of a silent regression in scraper output.
    """

    @pytest.mark.parametrize(
        "raw,want",
        [
            # ── Workable (snake_case) — pre-#2992 lived in
            #     core/scrapers/workable.py::_WORKPLACE_MAP ────────────
            ("remote", "remote"),
            ("hybrid", "hybrid"),
            ("onsite", "onsite"),
            ("on_site", "onsite"),  # NEW central key (was workable-only)
            # ── Workday remoteType — pre-#2992 lived in
            #     core/scrapers/workday.py::_parse_location_type ──────
            ("Remote", "remote"),
            ("Flexible", "hybrid"),
            ("Hybrid", "hybrid"),
            # ── Ashby workplaceType (PascalCase) — pre-#2992 lived in
            #     core/monitors/ashby.py::_WORKPLACE_TYPE_MAP ─────────
            ("Remote", "remote"),
            ("Hybrid", "hybrid"),
            ("OnSite", "onsite"),
            # ── Pinpoint workplace_type — pre-#2992 lived in
            #     core/monitors/pinpoint.py::_WORKPLACE_MAP ───────────
            ("remote", "remote"),
            ("hybrid", "hybrid"),
            ("onsite", "onsite"),
            # ── Gem location_type — pre-#2992 lived in
            #     core/monitors/gem.py::_LOCATION_TYPE_MAP.
            #     ``in_office`` was previously gem-only, now central. ─
            ("remote", "remote"),
            ("hybrid", "hybrid"),
            ("in_office", "onsite"),  # NEW central key
            ("on_site", "onsite"),
            ("onsite", "onsite"),
        ],
    )
    def test_ats_token(self, raw, want):
        # ATS tokens take precedence over the default fallback —
        # pre-#2992 the local maps returned ``None`` on miss; we use
        # ``default=None`` at the call site to preserve that.
        assert normalize_job_location_type(raw, default=None) == want


class TestNormalizeJobLocationTypeDefault:
    """The ``default`` parameter (added in #2992) lets per-ATS callers
    preserve their pre-#2992 None-on-miss behaviour while keeping the
    public default at ``"onsite"`` for the rare callers that want a
    last-resort bucket."""

    def test_default_onsite_is_public_default(self):
        assert normalize_job_location_type("Mystery") == "onsite"

    def test_default_none_returns_none_on_miss(self):
        assert normalize_job_location_type("Mystery", default=None) is None

    def test_default_passes_arbitrary_string(self):
        # The default is not validated — callers should pass a canonical
        # bucket or ``None`` only.  Pinned so we don't accidentally
        # tighten the type contract.
        assert normalize_job_location_type("Mystery", default="hybrid") == "hybrid"

    def test_default_does_not_apply_to_known_values(self):
        # Known values always resolve regardless of ``default`` — the
        # parameter only kicks in on miss.
        assert normalize_job_location_type("Remote", default=None) == "remote"
        assert normalize_job_location_type("Remote", default="hybrid") == "remote"

    def test_default_does_not_apply_to_none_input(self):
        # ``None`` / empty / whitespace input always returns ``None``,
        # never the default.  This preserves the
        # ``no-upstream-value -> JobContent.job_location_type=None``
        # invariant that DB ingestion relies on.
        assert normalize_job_location_type(None, default="onsite") is None
        assert normalize_job_location_type("", default="onsite") is None
        assert normalize_job_location_type("   ", default="onsite") is None


class TestNormalizeJobLocationTypeWhitespaceCase:
    """Lookup is case-insensitive and trims whitespace — pinned because
    Ashby emits PascalCase (``Remote``/``Hybrid``/``OnSite``) and the
    Workable/Pinpoint/Gem snake_case keys arrive lowercase already."""

    def test_strips_whitespace(self):
        assert normalize_job_location_type("  Remote  ", default=None) == "remote"

    def test_pascal_case(self):
        assert normalize_job_location_type("OnSite", default=None) == "onsite"

    def test_upper_case(self):
        assert normalize_job_location_type("REMOTE", default=None) == "remote"


# ── normalize_salary_unit ──────────────────────────────────────────


class TestNormalizeSalaryUnitNullish:
    def test_none(self):
        assert normalize_salary_unit(None) is None

    def test_empty(self):
        assert normalize_salary_unit("") is None

    def test_whitespace_only(self):
        assert normalize_salary_unit("   \t\n") is None

    def test_unknown_returns_none(self):
        # The central function returns ``None`` for unknown — callers
        # apply their own per-ATS default at the call site.
        assert normalize_salary_unit("Mystery period") is None
        assert normalize_salary_unit("fortnightly") is None


class TestNormalizeSalaryUnitYear:
    @pytest.mark.parametrize(
        "raw",
        [
            "year",
            "Year",
            "YEAR",
            "yr",
            "Yr",
            "yearly",
            "Yearly",
            "annual",
            "annually",
            "Annually",
            "per year",
            "Per Year",
            "per-year",
            "per-year-salary",  # lever
            "yearly_annually",
        ],
    )
    def test_year(self, raw):
        assert normalize_salary_unit(raw) == "year"


class TestNormalizeSalaryUnitMonth:
    @pytest.mark.parametrize(
        "raw",
        [
            "month",
            "Month",
            "MONTH",
            "mo",
            "Mo",
            "monthly",
            "Monthly",
            "per month",
            "Per Month",
            "per-month",
            "per-month-salary",  # lever
        ],
    )
    def test_month(self, raw):
        assert normalize_salary_unit(raw) == "month"


class TestNormalizeSalaryUnitWeek:
    @pytest.mark.parametrize(
        "raw",
        [
            "week",
            "Week",
            "WEEK",
            "weekly",
            "Weekly",
            "per week",
            "Per Week",
            "per-week",
            "two_weeks",  # pinpoint biweekly cadence — pre-#2993 only
            #               pinpoint knew this token
            "biweekly",
        ],
    )
    def test_week(self, raw):
        assert normalize_salary_unit(raw) == "week"


class TestNormalizeSalaryUnitDay:
    @pytest.mark.parametrize(
        "raw",
        [
            "day",
            "Day",
            "DAY",
            "daily",
            "Daily",
            "per day",
            "Per Day",
            "per-day",
        ],
    )
    def test_day(self, raw):
        # Pre-#2993 ``daily`` silently fell through to the per-ATS
        # default (``year``/``month``) because ``"daily".find("day")``
        # is -1 in Python — d-a-i-l-y has no ``day`` substring, only
        # d-a-y does.  Centralisation fixes this.
        assert normalize_salary_unit(raw) == "day"


class TestNormalizeSalaryUnitHour:
    @pytest.mark.parametrize(
        "raw",
        [
            "hour",
            "Hour",
            "HOUR",
            "hr",
            "Hr",
            "hourly",
            "Hourly",
            "per hour",
            "Per Hour",
            "per-hour",
            "per-hour-wage",  # lever
        ],
    )
    def test_hour(self, raw):
        assert normalize_salary_unit(raw) == "hour"


class TestNormalizeSalaryUnitSubstring:
    """Some upstream values arrive as composite strings (Pinpoint sends
    ``per_hour`` / ``per_day`` as ``compensation_frequency`` underscore
    tokens).  Substring fallback lets the central function resolve
    these without per-ATS pre-tokenising — and keeps Lever/Amazon
    composite strings working unchanged."""

    @pytest.mark.parametrize(
        "raw,want",
        [
            ("per_hour", "hour"),
            ("per_day", "day"),
            ("per_month", "month"),
            ("per_year", "year"),
            ("per_week", "week"),
            ("salary_per_year", "year"),
        ],
    )
    def test_substring(self, raw, want):
        assert normalize_salary_unit(raw) == want


class TestNormalizeSalaryUnitJsonLd:
    """schema.org uses uppercase tokens (``YEAR``/``MONTH``/...) for
    ``unitText``.  Used by jsonld scraper (#2990 P3) and bite scraper
    via the same vocabulary."""

    @pytest.mark.parametrize(
        "raw,want",
        [
            ("YEAR", "year"),
            ("MONTH", "month"),
            ("WEEK", "week"),
            ("DAY", "day"),
            ("HOUR", "hour"),
        ],
    )
    def test_jsonld_unit_text(self, raw, want):
        assert normalize_salary_unit(raw) == want


class TestNormalizeSalaryUnitPerAtsCanonicalSet:
    """Spot-check: every output is one of the 5 canonical buckets, or
    ``None``.  Catches a future map-extension that adds (e.g.) a
    ``per_task`` value unintentionally — Mokahr's ``per_task`` is
    explicitly KEPT-LOCAL per #2976 because it has structural numeric
    encoding.
    """

    _CANONICAL = {"year", "month", "week", "day", "hour", None}

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "year",
            "Yearly",
            "MONTH",
            "per-hour-wage",
            "two_weeks",
            "Mystery",
            "fortnightly",
        ],
    )
    def test_output_is_canonical_or_none(self, raw):
        result = normalize_salary_unit(raw)
        assert result in self._CANONICAL


class TestNormalizeSalaryUnitWhitespaceCase:
    def test_strips_whitespace(self):
        assert normalize_salary_unit("  yearly  ") == "year"

    def test_case_insensitive(self):
        assert normalize_salary_unit("YEARLY") == "year"
        assert normalize_salary_unit("Yearly") == "year"
        assert normalize_salary_unit("yearly") == "year"


class TestNormalizeSalaryUnitJsonLdScraperRegression:
    """Concrete regression payloads from real schema.org JobPosting
    blocks — the P3 callout in #2976 / #2990 was that
    core/scrapers/jsonld.py:186 used to lowercase ``unitText`` and
    emit raw, which happened to work for the canonical schema.org
    set (``MONTH``/``HOUR``/``DAY``/``WEEK``/``YEAR``) but would
    drift on edge cases.  Routed through ``normalize_salary_unit``
    in #2990 — pin equivalence here."""

    @pytest.mark.parametrize(
        "unit_text,expected_unit",
        [
            ("HOUR", "hour"),
            ("hour", "hour"),
            ("YEAR", "year"),
            ("year", "year"),
            ("MONTH", "month"),
        ],
    )
    def test_jsonld_unit_text_resolves(self, unit_text, expected_unit):
        # Pre-#2990 the jsonld scraper did
        #   "unit": value.get("unitText", "").lower() or None
        # which gave the same answer for canonical inputs.  The
        # central function gives identical output for these.
        assert normalize_salary_unit(unit_text) == expected_unit
