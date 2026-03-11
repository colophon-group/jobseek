"""Normalize employment_type and job_location_type to canonical enum values.

Mappings cover EN, DE, FR, IT variants. Unknown values default to
full_time / onsite respectively. Raw values should be preserved in
R2 extras.json before normalization.
"""

from __future__ import annotations

# ── Employment Type ─────────────────────────────────────────────────
# Canonical: full_time, part_time, contract, internship, full_or_part

_EMPLOYMENT_TYPE_MAP: dict[str, str] = {
    # English
    "full-time": "full_time",
    "full time": "full_time",
    "full_time": "full_time",
    "fulltime": "full_time",
    "permanent": "full_time",
    "permanent employment": "full_time",
    "permanent full-time": "full_time",
    "regular": "full_time",
    "employee / full-time": "full_time",
    "eor / full-time": "full_time",
    "graduate": "full_time",
    "other": "full_time",
    "other_employment_type": "full_time",
    "part-time": "part_time",
    "part time": "part_time",
    "part_time": "part_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "temporary": "contract",
    "temporary positions": "contract",
    "fixed term": "contract",
    "fixed term (fixed term)": "contract",
    "fixed term / full-time": "contract",
    "internship": "internship",
    "intern": "internship",
    "full time or part time": "full_or_part",
    "full-time, part-time": "full_or_part",
    "permanent full-time or part-time": "full_or_part",
    "temporary positions, full-time": "full_or_part",
    "full_time, part_time": "full_or_part",
    # German
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
    "befristet": "contract",
    "zeitarbeit": "contract",
    "freiberuflich": "contract",
    "freelancer": "contract",
    "vollzeit oder teilzeit": "full_or_part",
    "voll- oder teilzeit": "full_or_part",
    "voll-/teilzeit": "full_or_part",
    # French
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
    "freelance": "contract",
    "indépendant": "contract",
    "stage": "internship",
    "alternance": "internship",
    "apprentissage": "internship",
    "stagiaire": "internship",
    "temps plein ou partiel": "full_or_part",
    # Italian
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
}

# ── Job Location Type ───────────────────────────────────────────────
# Canonical: onsite, remote, hybrid

_JOB_LOCATION_TYPE_MAP: dict[str, str] = {
    # English
    "onsite": "onsite",
    "on-site": "onsite",
    "on site": "onsite",
    "office": "onsite",
    "in-office": "onsite",
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

    Returns None if input is None.
    Unknown values default to full_time.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return _EMPLOYMENT_TYPE_MAP.get(key, "full_time")


def normalize_job_location_type(raw: str | None) -> str | None:
    """Normalize job location type to canonical enum value.

    Returns None if input is None.
    Unknown values default to onsite.
    """
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return _JOB_LOCATION_TYPE_MAP.get(key, "onsite")
