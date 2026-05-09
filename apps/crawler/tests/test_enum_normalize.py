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
