"""Location resolver — matches free-form location strings to GeoNames IDs.

Loads GeoNames data from Postgres into a local SQLite database for fast,
low-memory lookups.  Resolves raw location strings to structured
(location_id, location_type) pairs.

Usage:
    resolver = LocationResolver()
    await resolver.load(pool)
    results = resolver.resolve(["Zurich, Switzerland", "Remote - US"], "onsite")
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

import asyncpg
import structlog

from src.core.enum_normalize import _JOB_LOCATION_TYPE_MAP

log = structlog.get_logger()

# Locales loaded eagerly into memory.  Non-core locale names are fetched
# from the DB on demand and cached for the process lifetime.
_CORE_LOCALES = ("en", "de", "fr", "it", "alt", "")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entry (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER,
    loc_type    TEXT NOT NULL,
    population  INTEGER NOT NULL DEFAULT 0,
    languages   TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS name_index (
    name        TEXT NOT NULL,
    location_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS display_name (
    location_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_name ON name_index(name);
"""


# ── Types ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    location_id: int | None
    location_type: str  # "onsite", "remote", "hybrid"


@dataclass(frozen=True, slots=True)
class _LocationEntry:
    id: int
    parent_id: int | None
    loc_type: str  # "macro", "country", "region", "city"
    population: int
    languages: tuple[str, ...] = ()  # ISO 639-1 codes


# ── Patterns ─────────────────────────────────────────────────────────

# Patterns to detect location type hints in raw strings
_TYPE_HINT_RE = re.compile(
    r"\b(?:"
    r"remote|fully\s+remote|100%\s+remote|telecommute|work\s+from\s+home|wfh"
    r"|hybrid|partially\s+remote|flexible"
    r"|on[\s-]?site|in[\s-]?office|in[\s-]?person"
    r")\b",
    re.IGNORECASE,
)

# Parenthetical hints: "Zurich (remote)", "Berlin (hybrid)"
_PAREN_HINT_RE = re.compile(r"\(([^)]+)\)")

# Strings that are pure remote with no geo content
_PURE_REMOTE_RE = re.compile(
    r"^(?:fully\s+)?remote$|^100%\s+remote$|^telecommute$|^work\s+from\s+home$|^wfh$",
    re.IGNORECASE,
)

# Strings to skip entirely (no location data)
_SKIP_RE = re.compile(
    r"^(?:multiple\s+locations?|various\s+locations?|global|tbd|n/a"
    r"|see\s+description|distributed)$",
    re.IGNORECASE,
)

# "<Country> Locations" pattern — skip (e.g. "India Locations", "Ireland Locations")
_COUNTRY_LOCATIONS_RE = re.compile(r"^(.+?)\s+locations?$", re.IGNORECASE)

# "& Other locations" / "& more" suffix — strip before matching
_OTHER_LOCATIONS_RE = re.compile(r"\s*[&+]\s*(?:other\s+locations?|more|others?).*$", re.IGNORECASE)

# Standalone type words (not locations, just type markers)
_STANDALONE_TYPE_RE = re.compile(
    r"^(?:hybrid|on[\s-]?site|in[\s-]?office|in[\s-]?person)$",
    re.IGNORECASE,
)

# State-prefixed format: "IL-Chicago", "NY-New York", "CA-San Francisco"
_STATE_PREFIX_RE = re.compile(r"^([A-Z]{2})-(.+)$")

# Common city/country aliases (lowercase keys)
_CITY_ALIASES: dict[str, str] = {
    "sf": "San Francisco",
    "nyc": "New York City",
    "ny": "New York City",
    "la": "Los Angeles",
    "dc": "District of Columbia",
    "philly": "Philadelphia",
    "us": "United States",
    "usa": "United States",
    "uk": "United Kingdom",
    "uae": "United Arab Emirates",
    "korea": "South Korea",
    "d.c.": "District of Columbia",
    "anywhere": "Worldwide",
    "northeast": "Northeast US",
    "northeastern": "Northeast US",
    "southeast": "Southeast US",
    "southeastern": "Southeast US",
    "midwest": "Midwest US",
    "midwestern": "Midwest US",
    "central": "Central US",
    "southwest": "Southwest US",
    "southwestern": "Southwest US",
    "pacific northwest": "Pacific Northwest",
    "northwest": "Pacific Northwest",
    "europe": "EMEA",
    "european union": "EU",
    "middle east": "MENA",
    "amer": "Americas",
    "silicon valley": "San Francisco",
}

# ISO 3166-1 alpha-3 → country name (common ones seen in job postings)
_ISO3_TO_COUNTRY: dict[str, str] = {
    "AFG": "Afghanistan",
    "ALB": "Albania",
    "DZA": "Algeria",
    "AND": "Andorra",
    "AGO": "Angola",
    "ARG": "Argentina",
    "ARM": "Armenia",
    "AUS": "Australia",
    "AUT": "Austria",
    "AZE": "Azerbaijan",
    "BHS": "Bahamas",
    "BHR": "Bahrain",
    "BGD": "Bangladesh",
    "BRB": "Barbados",
    "BLR": "Belarus",
    "BEL": "Belgium",
    "BLZ": "Belize",
    "BEN": "Benin",
    "BTN": "Bhutan",
    "BOL": "Bolivia",
    "BIH": "Bosnia and Herzegovina",
    "BWA": "Botswana",
    "BRA": "Brazil",
    "BRN": "Brunei",
    "BGR": "Bulgaria",
    "BFA": "Burkina Faso",
    "BDI": "Burundi",
    "KHM": "Cambodia",
    "CMR": "Cameroon",
    "CAN": "Canada",
    "CPV": "Cape Verde",
    "CAF": "Central African Republic",
    "TCD": "Chad",
    "CHL": "Chile",
    "CHN": "China",
    "COL": "Colombia",
    "COM": "Comoros",
    "COG": "Congo",
    "CRI": "Costa Rica",
    "HRV": "Croatia",
    "CUB": "Cuba",
    "CYP": "Cyprus",
    "CZE": "Czechia",
    "DNK": "Denmark",
    "DJI": "Djibouti",
    "DMA": "Dominica",
    "DOM": "Dominican Republic",
    "ECU": "Ecuador",
    "EGY": "Egypt",
    "SLV": "El Salvador",
    "GNQ": "Equatorial Guinea",
    "ERI": "Eritrea",
    "EST": "Estonia",
    "ETH": "Ethiopia",
    "FJI": "Fiji",
    "FIN": "Finland",
    "FRA": "France",
    "GAB": "Gabon",
    "GMB": "Gambia",
    "GEO": "Georgia",
    "DEU": "Germany",
    "GHA": "Ghana",
    "GRC": "Greece",
    "GTM": "Guatemala",
    "GIN": "Guinea",
    "GUY": "Guyana",
    "HTI": "Haiti",
    "HND": "Honduras",
    "HKG": "Hong Kong",
    "HUN": "Hungary",
    "ISL": "Iceland",
    "IND": "India",
    "IDN": "Indonesia",
    "IRN": "Iran",
    "IRQ": "Iraq",
    "IRL": "Ireland",
    "ISR": "Israel",
    "ITA": "Italy",
    "JAM": "Jamaica",
    "JPN": "Japan",
    "JOR": "Jordan",
    "KAZ": "Kazakhstan",
    "KEN": "Kenya",
    "KWT": "Kuwait",
    "KGZ": "Kyrgyzstan",
    "LAO": "Laos",
    "LVA": "Latvia",
    "LBN": "Lebanon",
    "LSO": "Lesotho",
    "LBR": "Liberia",
    "LBY": "Libya",
    "LIE": "Liechtenstein",
    "LTU": "Lithuania",
    "LUX": "Luxembourg",
    "MDG": "Madagascar",
    "MWI": "Malawi",
    "MYS": "Malaysia",
    "MDV": "Maldives",
    "MLI": "Mali",
    "MLT": "Malta",
    "MRT": "Mauritania",
    "MUS": "Mauritius",
    "MEX": "Mexico",
    "MDA": "Moldova",
    "MCO": "Monaco",
    "MNG": "Mongolia",
    "MNE": "Montenegro",
    "MAR": "Morocco",
    "MOZ": "Mozambique",
    "MMR": "Myanmar",
    "NAM": "Namibia",
    "NPL": "Nepal",
    "NLD": "Netherlands",
    "NZL": "New Zealand",
    "NIC": "Nicaragua",
    "NER": "Niger",
    "NGA": "Nigeria",
    "MKD": "North Macedonia",
    "NOR": "Norway",
    "OMN": "Oman",
    "PAK": "Pakistan",
    "PAN": "Panama",
    "PRY": "Paraguay",
    "PER": "Peru",
    "PHL": "Philippines",
    "POL": "Poland",
    "PRT": "Portugal",
    "QAT": "Qatar",
    "ROU": "Romania",
    "RUS": "Russia",
    "RWA": "Rwanda",
    "SAU": "Saudi Arabia",
    "SEN": "Senegal",
    "SRB": "Serbia",
    "SGP": "Singapore",
    "SVK": "Slovakia",
    "SVN": "Slovenia",
    "SOM": "Somalia",
    "ZAF": "South Africa",
    "KOR": "South Korea",
    "ESP": "Spain",
    "LKA": "Sri Lanka",
    "SDN": "Sudan",
    "SUR": "Suriname",
    "SWE": "Sweden",
    "CHE": "Switzerland",
    "SYR": "Syria",
    "TWN": "Taiwan",
    "TJK": "Tajikistan",
    "TZA": "Tanzania",
    "THA": "Thailand",
    "TGO": "Togo",
    "TTO": "Trinidad and Tobago",
    "TUN": "Tunisia",
    "TUR": "Turkey",
    "TKM": "Turkmenistan",
    "UGA": "Uganda",
    "UKR": "Ukraine",
    "ARE": "United Arab Emirates",
    "GBR": "United Kingdom",
    "USA": "United States",
    "URY": "Uruguay",
    "UZB": "Uzbekistan",
    "VEN": "Venezuela",
    "VNM": "Vietnam",
    "YEM": "Yemen",
    "ZMB": "Zambia",
    "ZWE": "Zimbabwe",
}

# ISO 3166-1 alpha-2 → country name
# Only codes that do NOT collide with US state abbreviations, plus codes
# where the country meaning dominates in job postings (handled via context).
_ISO2_TO_COUNTRY: dict[str, str] = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AM": "Armenia",
    "AO": "Angola",
    "AT": "Austria",
    "AU": "Australia",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CD": "Congo",
    "CF": "Central African Republic",
    "CG": "Congo",
    "CH": "Switzerland",
    "CI": "Ivory Coast",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cape Verde",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FR": "France",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GH": "Ghana",
    "GM": "Gambia",
    "GN": "Guinea",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GT": "Guatemala",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "IE": "Ireland",
    "IS": "Iceland",
    "IT": "Italy",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KZ": "Kazakhstan",
    "LB": "Lebanon",
    "LC": "Saint Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MG": "Madagascar",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MR": "Mauritania",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PE": "Peru",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PT": "Portugal",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SE": "Sweden",
    "SG": "Singapore",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SV": "El Salvador",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TD": "Chad",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TM": "Turkmenistan",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VE": "Venezuela",
    "VN": "Vietnam",
    "YE": "Yemen",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
    # Colliding with US states — included so multi-token context can disambiguate.
    # Standalone use prefers the US state; multi-token prefers whichever ancestor matches.
    "AL": "Albania",
    "AR": "Argentina",
    "CA": "Canada",
    "CO": "Colombia",
    "DE": "Germany",
    "GA": "Gabon",
    "ID": "Indonesia",
    "IL": "Israel",
    "IN": "India",
    "LA": "Laos",
    "MA": "Morocco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MN": "Mongolia",
    "MT": "Malta",
    "NE": "Niger",
    "PA": "Panama",
    "SC": "Seychelles",
}

# US state abbreviation to full name (for disambiguation)
_US_STATE_ABBREV: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DC": "District of Columbia",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}

# Token split pattern: comma, dash, slash, pipe, semicolon, bullet, ampersand (spaced)
_SPLIT_RE = re.compile(r"[,/|;]\s*|\s+-\s+|\s+–\s+|\s*[•·]\s*|\s+&\s+")

# Trailing postal/zip code pattern: ", DE, 10557" or ", AT, 1090"
_POSTAL_CODE_RE = re.compile(r",\s*\d{4,5}\s*$")

# "The " prefix for country names: "The Netherlands" → also index as "Netherlands"
_THE_PREFIX_RE = re.compile(r"^the\s+", re.IGNORECASE)

# " City" suffix: "Makati City" → also index as "Makati"
_CITY_SUFFIX_RE = re.compile(r"\s+city$", re.IGNORECASE)


_COMBINING_RE = re.compile(r"[\u0300-\u036f]+")


def _strip_accents(s: str) -> str:
    """Remove diacritics/accents: 'São Paulo' → 'Sao Paulo', 'Zürich' → 'Zurich'."""
    return _COMBINING_RE.sub("", unicodedata.normalize("NFD", s))


# Trailing office/HQ/campus suffix: "Zurich HQ", "Singapore Office"
_OFFICE_SUFFIX_RE = re.compile(r"\s+(?:HQ|Office|Campus|Branch|Headquarters)\s*$", re.IGNORECASE)


class LocationResolver:
    """Location resolver backed by a local SQLite database.

    GeoNames data is pulled from Postgres and stored in SQLite for fast,
    low-memory lookups.  Non-core locale names are fetched from Postgres
    on cache miss and added to SQLite.
    """

    def __init__(self) -> None:
        self._db: sqlite3.Connection | None = None
        self._entry_cache: dict[int, _LocationEntry | None] = {}
        self._pool: asyncpg.Pool | None = None
        self._loaded = False
        self._tracking = False
        self._misses: set[str] = set()
        self._negative: set[str] = set()
        self._posting_language: str | None = None

    def _init_db(self, path: str = ":memory:") -> None:
        """Create SQLite schema. Use ':memory:' for tests."""
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)

    @property
    def entry_count(self) -> int:
        if self._db is None:
            return 0
        row = self._db.execute("SELECT COUNT(*) FROM entry").fetchone()
        return row[0] if row else 0

    def _get_entry(self, loc_id: int) -> _LocationEntry | None:
        """Look up a location entry, with per-instance cache."""
        cached = self._entry_cache.get(loc_id)
        if cached is not None:
            return cached
        if loc_id in self._entry_cache:  # cached as None
            return None
        assert self._db is not None
        row = self._db.execute(
            "SELECT id, parent_id, loc_type, population, languages FROM entry WHERE id = ?",
            (loc_id,),
        ).fetchone()
        if not row:
            self._entry_cache[loc_id] = None
            return None
        entry = _LocationEntry(
            id=row[0],
            parent_id=row[1],
            loc_type=row[2],
            population=row[3],
            languages=tuple(row[4].split(",")) if row[4] else (),
        )
        self._entry_cache[loc_id] = entry
        return entry

    def _lookup_name(self, key: str) -> list[int]:
        """Look up name→location IDs from SQLite, tracking misses for backfill."""
        assert self._db is not None
        rows = self._db.execute(
            "SELECT location_id FROM name_index WHERE name = ?", (key,)
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
        if self._tracking and key and key not in self._negative:
            self._misses.add(key)
        return []

    @staticmethod
    def _name_variants(name: str) -> list[str]:
        """Return all index variants for a lowercased name."""
        variants = [name]
        stripped = _strip_accents(name)
        if stripped != name:
            variants.append(stripped)
        for base in (name, stripped) if stripped != name else (name,):
            # Names are already lowercased — use string ops instead of regex
            if base.startswith("the "):
                variants.append(base[4:])
            if base.endswith(" city"):
                variants.append(base[:-5])
        return variants

    async def load(self, pool: asyncpg.Pool) -> None:
        """Pull GeoNames data from Postgres into a local SQLite database."""
        self._pool = pool
        self._init_db()
        assert self._db is not None
        cur = self._db.cursor()

        # Phase 1: location entries
        async with pool.acquire() as conn:
            loc_rows = await conn.fetch(
                "SELECT id, parent_id, type::text AS type, "
                "COALESCE(population, 0) AS population, "
                "COALESCE(languages, '{}') AS languages "
                "FROM location"
            )
        cur.executemany(
            "INSERT OR REPLACE INTO entry (id, parent_id, loc_type, population, languages) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["id"],
                    row["parent_id"],
                    row["type"],
                    row["population"],
                    ",".join(row["languages"]),
                )
                for row in loc_rows
            ],
        )
        del loc_rows

        # Phase 2: core-locale name index (with variants)
        async with pool.acquire() as conn:
            name_rows = await conn.fetch(
                "SELECT location_id, lower(name) AS name FROM location_name WHERE locale = ANY($1)",
                list(_CORE_LOCALES),
            )
        name_pairs: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for row in name_rows:
            loc_id = row["location_id"]
            for variant in self._name_variants(row["name"]):
                pair = (variant, loc_id)
                if pair not in seen:
                    seen.add(pair)
                    name_pairs.append(pair)
        cur.executemany(
            "INSERT INTO name_index (name, location_id) VALUES (?, ?)",
            name_pairs,
        )
        del name_rows, name_pairs, seen

        # Phase 3: English display names
        async with pool.acquire() as conn:
            en_name_rows = await conn.fetch(
                "SELECT location_id, name, "
                "COALESCE(is_display, false) AS is_display "
                "FROM location_name WHERE locale = 'en'"
            )
        # First pass: any en name. Second pass: override with is_display.
        display: dict[int, str] = {}
        for row in en_name_rows:
            if row["location_id"] not in display:
                display[row["location_id"]] = row["name"]
        for row in en_name_rows:
            if row["is_display"]:
                display[row["location_id"]] = row["name"]
        cur.executemany(
            "INSERT OR REPLACE INTO display_name (location_id, name) VALUES (?, ?)",
            list(display.items()),
        )
        del en_name_rows, display

        # Create indexes after bulk insert and commit
        self._db.executescript(_INDEXES)
        self._db.commit()
        self._loaded = True
        self._tracking = True

    async def backfill_misses(self) -> bool:
        """Batch-query Postgres for names not found in SQLite.

        Inserts results into SQLite so subsequent ``resolve()`` calls hit.
        Returns True if new names were added.
        """
        if not self._pool or not self._misses or self._db is None:
            return False

        missed = list(self._misses)
        self._misses.clear()

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT location_id, lower(name) AS name "
                "FROM location_name WHERE lower(name) = ANY($1::text[])",
                missed,
            )

        if not rows:
            self._negative.update(missed)
            return False

        matched_keys: set[str] = set()
        name_pairs: list[tuple[str, int]] = []
        for row in rows:
            matched_keys.add(row["name"])
            for variant in self._name_variants(row["name"]):
                name_pairs.append((variant, row["location_id"]))

        self._db.executemany(
            "INSERT OR IGNORE INTO name_index (name, location_id) VALUES (?, ?)",
            name_pairs,
        )
        self._db.commit()
        self._negative.update(set(missed) - matched_keys)

        log.info(
            "location_resolver.backfill",
            queried=len(missed),
            matched=len(matched_keys),
        )
        return True

    def display_name(self, location_id: int) -> str | None:
        """Return the English display name for a location ID, or None."""
        assert self._db is not None
        row = self._db.execute(
            "SELECT name FROM display_name WHERE location_id = ?", (location_id,)
        ).fetchone()
        return row[0] if row else None

    def resolve(
        self,
        raw_locations: list[str] | None,
        job_location_type: str | None = None,
        posting_language: str | None = None,
    ) -> list[ResolvedLocation]:
        """Resolve raw location strings to structured locations.

        Args:
            raw_locations: List of free-form location strings from scraper.
            job_location_type: Global job location type (fallback).
            posting_language: ISO 639-1 language code of the job posting
                (used as disambiguation hint).

        Returns:
            List of ResolvedLocation(location_id, location_type).
        """
        if not raw_locations:
            return []

        self._posting_language = posting_language
        seen: set[tuple[int | None, str]] = set()
        results: list[ResolvedLocation] = []
        for raw in raw_locations:
            # Split semicolon-separated multi-location strings first
            # so each segment resolves independently:
            # "Amsterdam, Netherlands; Berlin, Germany" → two resolutions
            segments = [s.strip() for s in raw.split(";") if s.strip()]
            for segment in segments:
                for resolved in self._resolve_one(segment, job_location_type):
                    key = (resolved.location_id, resolved.location_type)
                    if key not in seen:
                        seen.add(key)
                        results.append(resolved)
        self._posting_language = None
        return results

    def _resolve_one(
        self,
        raw: str,
        fallback_type: str | None,
    ) -> list[ResolvedLocation]:
        """Resolve a single raw location string.

        Returns a list because comma-separated multi-city inputs
        (e.g. "Dublin, London, Berlin") produce multiple results.
        """
        if not raw:
            return []

        # Normalize whitespace (double spaces, tabs)
        raw = " ".join(raw.split())

        # Strip "& Other locations" suffix early
        raw = _OTHER_LOCATIONS_RE.sub("", raw).strip()

        # Strip trailing postal/zip codes (e.g. "Berlin, DE, 10557" → "Berlin, DE")
        raw = _POSTAL_CODE_RE.sub("", raw).strip()

        # Strip trailing office/HQ suffix (e.g. "Zurich HQ" → "Zurich")
        raw = _OFFICE_SUFFIX_RE.sub("", raw).strip()

        if not raw:
            return []

        # Skip non-location strings
        if _SKIP_RE.match(raw):
            return []

        # "<Country> Locations" → try to resolve the country part
        m_country_loc = _COUNTRY_LOCATIONS_RE.match(raw)
        if m_country_loc:
            country_part = m_country_loc.group(1).strip()
            loc_type = _normalize_type(fallback_type) or "onsite"
            for loc_id in self._match_geo(country_part):
                return [ResolvedLocation(location_id=loc_id, location_type=loc_type)]
            return []

        # Pure remote — no geo content
        if _PURE_REMOTE_RE.match(raw):
            return [ResolvedLocation(location_id=None, location_type="remote")]

        # Standalone type markers (e.g. "Hybrid", "On-site", "In-Office")
        if _STANDALONE_TYPE_RE.match(raw):
            loc_type = _normalize_type(raw)
            if loc_type:
                return [ResolvedLocation(location_id=None, location_type=loc_type)]
            return []

        # Extract type hint from parenthetical or inline markers
        loc_type = self._extract_type_hint(raw)
        # Strip parenthetical hints from the geo string
        geo_str = _PAREN_HINT_RE.sub("", raw).strip()

        # Strip known type markers from the geo string
        geo_str = _TYPE_HINT_RE.sub("", geo_str).strip()
        geo_str = geo_str.strip(" -–/,")

        if not geo_str:
            # Had type hint but no geo content → e.g. "(Remote)"
            if loc_type:
                return [ResolvedLocation(location_id=None, location_type=loc_type)]
            return []

        # Determine final type
        if not loc_type:
            loc_type = _normalize_type(fallback_type) or "onsite"

        # Try to match the geo string (may return multiple for multi-city)
        location_ids = self._match_geo(geo_str)
        if location_ids:
            return [
                ResolvedLocation(location_id=lid, location_type=loc_type) for lid in location_ids
            ]

        return []

    def _extract_type_hint(self, raw: str) -> str | None:
        """Extract location type from parenthetical or inline markers."""
        # Check parenthetical first: "Zurich (remote)"
        paren = _PAREN_HINT_RE.search(raw)
        if paren:
            hint = paren.group(1).strip().lower()
            normalized = _JOB_LOCATION_TYPE_MAP.get(hint)
            if normalized:
                return normalized

        # Check inline markers
        match = _TYPE_HINT_RE.search(raw)
        if match:
            hint = match.group(0).strip().lower()
            normalized = _JOB_LOCATION_TYPE_MAP.get(hint)
            if normalized:
                return normalized

        return None

    def _match_geo(self, geo_str: str) -> list[int]:
        """Match a geo string to location ID(s).

        Returns a list because comma-separated multi-city inputs
        (e.g. "Dublin, London, Berlin") can produce multiple IDs.
        """
        # Handle prefix format: "IL-Chicago", "NL-Amsterdam", "GB-London"
        sp = _STATE_PREFIX_RE.match(geo_str)
        if sp:
            prefix = sp.group(1)
            rest = sp.group(2).strip()
            city_part = rest.split(",")[0].strip()
            if prefix in _US_STATE_ABBREV:
                state_name = _US_STATE_ABBREV[prefix]
                result = self._match_multi_tokens([city_part, state_name])
                if result:
                    return result
                eid = self._exact_match(city_part)
                if eid is not None:
                    return [eid]
            elif prefix in _ISO2_TO_COUNTRY:
                country_name = _ISO2_TO_COUNTRY[prefix]
                result = self._match_multi_tokens([city_part, country_name])
                if result:
                    return result
                eid = self._exact_match(city_part)
                if eid is not None:
                    return [eid]

        # For short strings (2-3 chars), check ISO country codes before name index
        # to avoid e.g. "BRA" → Bra (Italy) instead of Brazil
        if len(geo_str) in (2, 3) and geo_str.isalpha():
            upper = geo_str.upper()
            country_name = None
            if len(geo_str) == 3:
                country_name = _ISO3_TO_COUNTRY.get(upper)
            if not country_name and len(geo_str) == 2:
                country_name = _ISO2_TO_COUNTRY.get(upper)
            if country_name:
                result = self._exact_match(country_name)
                if result is not None:
                    return [result]

        # First try full string exact match
        full_match = self._exact_match(geo_str)
        if full_match is not None:
            return [full_match]

        # Split into tokens and try matching
        tokens = [t.strip() for t in _SPLIT_RE.split(geo_str) if t.strip()]
        if not tokens:
            return []

        if len(tokens) == 1:
            result = self._match_single_token(tokens[0])
            if result is not None:
                return [result]
            # Compound fallback: "Bremen Germany" → city + country
            if " " in tokens[0]:
                words = tokens[0].split()
                if len(words) >= 2:
                    cid = self._match_compound(words)
                    if cid is not None:
                        return [cid]
            return []

        # Multiple tokens — may return multiple IDs for multi-city lists
        return self._match_multi_tokens(tokens)

    def _exact_match(self, text: str) -> int | None:
        """Try exact case-insensitive match, disambiguate by population."""
        # Check aliases first
        alias = _CITY_ALIASES.get(text.lower())
        if alias:
            text = alias

        key = text.lower()
        ids = self._lookup_name(key)
        # Try accent-stripped fallback
        if not ids:
            ids = self._lookup_name(_strip_accents(key))
        # Try "The " prefix stripped from input ("The Netherlands" → "Netherlands")
        if not ids:
            no_the = _THE_PREFIX_RE.sub("", key)
            if no_the != key:
                ids = self._lookup_name(no_the)
                if not ids:
                    ids = self._lookup_name(_strip_accents(no_the))
        # Try " City" suffix stripped from input ("Singapore City" → "Singapore")
        if not ids:
            no_city = _CITY_SUFFIX_RE.sub("", key)
            if no_city != key:
                ids = self._lookup_name(no_city)
                if not ids:
                    ids = self._lookup_name(_strip_accents(no_city))
        if not ids:
            return None

        if len(ids) == 1:
            return ids[0]

        # Country names take priority for standalone matches
        # ("Mexico" → country, not Mexico City)
        countries = [
            lid
            for lid in ids
            if self._get_entry(lid) and self._get_entry(lid).loc_type == "country"
        ]
        if countries:
            return self._best_by_population(countries)

        # Disambiguate city vs region sharing a name:
        cities = [
            lid for lid in ids if self._get_entry(lid) and self._get_entry(lid).loc_type == "city"
        ]
        regions = [
            lid
            for lid in ids
            if self._get_entry(lid) and self._get_entry(lid).loc_type in ("region", "macro")
        ]
        if cities and regions:
            # City inside its namesake region → prefer city (more specific)
            # e.g. Geneva city inside Geneva canton
            region_set = set(regions)
            cities_in_region = [
                cid for cid in cities if self._is_descendant_of_any(cid, region_set)
            ]
            if cities_in_region:
                best_cir = self._best_by_population(cities_in_region)
                best_reg = self._best_by_population(regions)
                cir_pop = self._get_entry(best_cir).population if best_cir else 0
                reg_pop = self._get_entry(best_reg).population if best_reg else 0
                # If the best region is overwhelmingly larger, the region
                # is the more notable entity (Montana US state 1.1M vs
                # Montana Bulgarian city 47k). But Geneva city (201k)
                # inside Geneva canton (1.5M) still prefers the city.
                if cir_pop > 0 and cir_pop * 10 < reg_pop:
                    return best_reg
                return best_cir
            # Unrelated city/region (e.g. Manchester UK vs Manchester Jamaica)
            # → prefer whichever has higher population
            best_city = self._best_by_population(cities)
            best_region = self._best_by_population(regions)
            city_pop = self._get_entry(best_city).population if best_city else 0
            region_pop = self._get_entry(best_region).population if best_region else 0
            return best_city if city_pop >= region_pop else best_region

        if cities:
            return self._best_by_population(cities)
        return self._best_by_population(ids)

    def _match_single_token(self, token: str) -> int | None:
        """Match a single token."""
        upper = token.upper()

        if len(token) == 2:
            # Check aliases first (US, UK, etc.)
            alias = _CITY_ALIASES.get(token.lower())
            if alias:
                result = self._exact_match(alias)
                if result:
                    return result

            is_state = upper in _US_STATE_ABBREV
            is_iso2 = upper in _ISO2_TO_COUNTRY

            if is_state and not is_iso2:
                # Unambiguous US state
                full_name = _US_STATE_ABBREV[upper]
                result = self._exact_match(full_name)
                if result:
                    return result
            elif is_iso2 and not is_state:
                # Unambiguous ISO2 country (SG, CH, PL, etc.)
                country_name = _ISO2_TO_COUNTRY[upper]
                result = self._exact_match(country_name)
                if result:
                    return result
            elif is_state and is_iso2:
                # Collision (IN, DE, CA, etc.) — prefer US state for standalone
                full_name = _US_STATE_ABBREV[upper]
                result = self._exact_match(full_name)
                if result:
                    return result

        # Check ISO 3166-1 alpha-3 country codes (3-letter)
        if len(token) == 3 and upper in _ISO3_TO_COUNTRY:
            country_name = _ISO3_TO_COUNTRY[upper]
            result = self._exact_match(country_name)
            if result:
                return result

        return self._exact_match(token)

    def _resolve_2letter_token(self, token: str) -> list[int]:
        """Resolve a 2-letter token to candidate location IDs.

        For colliding codes (e.g. IN = Indiana / India), returns IDs for BOTH
        interpretations so that ancestor-based disambiguation can pick the right one.
        """
        upper = token.upper()
        ids: list[int] = []

        # Check aliases first (US, UK, etc.)
        alias = _CITY_ALIASES.get(token.lower())
        if alias:
            ids.extend(self._lookup_name(alias.lower()))
            if ids:
                return ids

        # Collect candidates from both US state and ISO2 interpretations
        if upper in _US_STATE_ABBREV:
            full_name = _US_STATE_ABBREV[upper]
            ids.extend(self._lookup_name(full_name.lower()))

        if upper in _ISO2_TO_COUNTRY:
            country_name = _ISO2_TO_COUNTRY[upper]
            country_ids = self._lookup_name(country_name.lower())
            # Avoid duplicates
            existing = set(ids)
            ids.extend(cid for cid in country_ids if cid not in existing)

        return ids

    def _get_token_ids(self, token: str) -> list[int]:
        """Resolve a single token to all candidate location IDs."""
        alias = _CITY_ALIASES.get(token.lower())
        if alias:
            ids = self._lookup_name(alias.lower())
            if ids:
                return ids

        upper = token.upper()

        if len(token) == 2 and upper in (_US_STATE_ABBREV | _ISO2_TO_COUNTRY):
            return self._resolve_2letter_token(token)

        if len(token) == 3 and upper in _ISO3_TO_COUNTRY:
            country_name = _ISO3_TO_COUNTRY[upper]
            ids = self._lookup_name(country_name.lower())
            if ids:
                return ids

        ids = self._lookup_name(token.lower())
        if not ids:
            ids = self._lookup_name(_strip_accents(token.lower()))
        return ids

    def _match_multi_tokens(self, tokens: list[str]) -> list[int]:
        """Match tokens assuming left-to-right: city, [region], country.

        Process right-to-left to build a context chain, then resolve
        the leftmost token(s) as the target within that context.
        When no context is found (e.g. "Dublin, London, Berlin"),
        returns each matched city independently (multi-location).
        """
        tokens = [t for t in tokens if not t.isdigit()]
        if not tokens:
            return []

        # Resolve each token to candidate IDs
        token_ids: list[list[int]] = [self._get_token_ids(t) for t in tokens]

        # Build context chain right-to-left
        ctx_set: set[int] = set()
        target_end = len(tokens)

        # Always leave tokens[0] as the target (the most specific entity).
        for i in range(len(tokens) - 1, 0, -1):
            ids = token_ids[i]
            if not ids:
                continue  # skip unrecognized tokens (addresses, etc.)

            ctx_entries = [
                lid
                for lid in ids
                if self._get_entry(lid)
                and self._get_entry(lid).loc_type in ("country", "region", "macro")
            ]
            city_entries = [
                lid
                for lid in ids
                if self._get_entry(lid) and self._get_entry(lid).loc_type == "city"
            ]

            if not ctx_set:
                # First context token: accept if purely context (no cities),
                # or if at least one left-side token is a descendant (confirming
                # this token is context, not part of a multi-city list).
                if ctx_entries and not city_entries:
                    ctx_set.update(ctx_entries)
                    target_end = i
                    continue
                elif ctx_entries and city_entries:
                    ctx_candidate = set(ctx_entries)
                    has_descendant = any(
                        self._is_descendant_of_any(lid, ctx_candidate)
                        for ids in token_ids[:i]
                        for lid in ids
                        if ids
                    )
                    if has_descendant:
                        ctx_set.update(ctx_entries)
                        target_end = i
                        continue
                break
            else:
                # Subsequent: must narrow existing context
                narrowed = [lid for lid in ctx_entries if self._is_descendant_of_any(lid, ctx_set)]
                if narrowed:
                    ctx_set.update(narrowed)
                    target_end = i
                    continue
                else:
                    break

        if not ctx_set:
            # Before treating as multi-city, scan for any pure-context token
            # (only region/country/macro, no city entries). Handles reversed
            # formats like "CH - Geneva" or "Switzerland, Geneva, Zurich".
            pure_ctx: set[int] = set()
            target_groups: list[list[int]] = []
            for ids in token_ids:
                if not ids:
                    continue
                t_ctx = [
                    lid
                    for lid in ids
                    if self._get_entry(lid)
                    and self._get_entry(lid).loc_type in ("country", "region", "macro")
                ]
                t_city = [
                    lid
                    for lid in ids
                    if self._get_entry(lid) and self._get_entry(lid).loc_type == "city"
                ]
                if t_ctx and not t_city:
                    pure_ctx.update(t_ctx)
                else:
                    target_groups.append(ids)

            if pure_ctx and target_groups:
                ctx_results: list[int] = []
                for ids in target_groups:
                    in_ctx = [lid for lid in ids if self._is_descendant_of_any(lid, pure_ctx)]
                    if in_ctx:
                        tid = self._pick_target(in_ctx)
                        if tid is not None and tid not in ctx_results:
                            ctx_results.append(tid)
                    else:
                        best = self._best_by_population(ids)
                        if best is not None and best not in ctx_results:
                            ctx_results.append(best)
                if ctx_results:
                    return ctx_results

            # No context found → multi-city list: resolve each token independently
            results: list[int] = []
            for ids in token_ids:
                if ids:
                    best = self._pick_target(ids)
                    if best is not None and best not in results:
                        results.append(best)
            return results

        # Collect target IDs from tokens[0:target_end]
        target_ids: list[int] = []
        for ids in token_ids[:target_end]:
            target_ids.extend(ids)

        if not target_ids:
            # All tokens consumed as context → return narrowest
            nid = self._pick_narrowest(ctx_set)
            return [nid] if nid else []

        # Resolve target within context — prefer narrowest context first
        narrowest_id = self._pick_narrowest(ctx_set)
        narrowest_set = {narrowest_id} if narrowest_id else ctx_set

        in_narrowest = [lid for lid in target_ids if self._is_descendant_of_any(lid, narrowest_set)]
        if not in_narrowest:
            # Try broader context
            in_narrowest = [lid for lid in target_ids if self._is_descendant_of_any(lid, ctx_set)]

        if in_narrowest:
            tid = self._pick_target(in_narrowest)
            return [tid] if tid else []

        # Target has IDs but none are in context — check if target itself
        # is a region/country within context (e.g. "Montana, USA")
        ctx_regions = [
            lid
            for lid in target_ids
            if self._get_entry(lid)
            and self._get_entry(lid).loc_type in ("country", "region", "macro")
            and self._is_descendant_of_any(lid, ctx_set)
        ]
        if ctx_regions:
            rid = self._best_by_population(ctx_regions)
            return [rid] if rid else []

        # Nothing matched context — return narrowest context as fallback
        nid = self._pick_narrowest(ctx_set)
        return [nid] if nid else []

    def _pick_narrowest(self, ctx_set: set[int]) -> int | None:
        """Return the most specific (narrowest) context location."""
        _SPEC = {"region": 3, "country": 2, "macro": 1}
        best_id = None
        best_spec = -1
        best_pop = -1
        for lid in ctx_set:
            entry = self._get_entry(lid)
            if not entry:
                continue
            spec = _SPEC.get(entry.loc_type, 0)
            if spec > best_spec or (spec == best_spec and entry.population > best_pop):
                best_id = lid
                best_spec = spec
                best_pop = entry.population
        return best_id

    def _pick_target(self, ids: list[int]) -> int | None:
        """Pick the best target from IDs in context.

        When both cities and same-named regions match, prefer cities that
        are inside the matching region. If no city is inside the region,
        the target IS the region (e.g. "Missouri, USA" → Missouri state).
        """
        cities = [
            lid for lid in ids if self._get_entry(lid) and self._get_entry(lid).loc_type == "city"
        ]
        regions = [
            lid
            for lid in ids
            if self._get_entry(lid)
            and self._get_entry(lid).loc_type in ("country", "region", "macro")
        ]

        if cities and regions:
            # Check if any city is actually inside one of the matching regions
            region_set = set(regions)
            cities_in_region = [
                cid for cid in cities if self._is_descendant_of_any(cid, region_set)
            ]
            if cities_in_region:
                return self._best_by_population(cities_in_region)
            # No city is inside the named region → target is the region
            return self._best_by_population(regions)

        if cities:
            return self._best_by_population(cities)
        if regions:
            return self._best_by_population(regions)
        return self._best_by_population(ids)

    def _match_compound(self, words: list[str]) -> int | None:
        """Try to split space-separated words into city + country/region.

        For strings like "Bremen Germany" or "Riyadh Saudi Arabia" where
        city and country are space-separated without delimiters.
        Tries from right: last N words as country context, rest as city.
        """
        for split_pos in range(len(words) - 1, 0, -1):
            right = " ".join(words[split_pos:])

            # Look up right part — use _get_token_ids for alias/abbreviation support
            # (e.g. "DC" → "District of Columbia", "DEU" → "Germany")
            right_ids = self._get_token_ids(right)
            if not right_ids:
                right_key = right.lower()
                right_ids = self._lookup_name(right_key)
                if not right_ids:
                    right_ids = self._lookup_name(_strip_accents(right_key))

            context_ids: set[int] = set()
            for loc_id in right_ids:
                entry = self._get_entry(loc_id)
                if entry and entry.loc_type in ("country", "region", "macro"):
                    context_ids.add(loc_id)

            if not context_ids:
                continue

            # Look up left part
            left = " ".join(words[:split_pos])
            left_key = left.lower()
            left_ids = self._lookup_name(left_key)
            if not left_ids:
                left_ids = self._lookup_name(_strip_accents(left_key))

            if not left_ids:
                continue

            # Prefer candidates that are descendants of the context
            filtered = [lid for lid in left_ids if self._is_descendant_of_any(lid, context_ids)]
            if filtered:
                return self._best_by_population(filtered)

            # Left matched but isn't a descendant — still return best match
            # (the context confirms this is a real location string)
            return self._best_by_population(left_ids)

        return None

    def _is_descendant_of_any(self, loc_id: int, ancestor_ids: set[int]) -> bool:
        """Check if loc_id is a descendant of any ID in ancestor_ids."""
        current = loc_id
        depth = 0
        while current is not None and depth < 5:
            entry = self._get_entry(current)
            if entry is None:
                return False
            if entry.parent_id in ancestor_ids:
                return True
            current = entry.parent_id
            depth += 1
        return False

    def _best_by_population(self, ids: list[int]) -> int | None:
        """Pick the location with highest population from a list of IDs.

        When a posting_language is set, candidates where that language is
        spoken are preferred over those where it is not. Within each group,
        higher population wins; ties broken by type specificity.
        """
        if not ids:
            return None

        _TYPE_RANK = {"city": 3, "region": 2, "country": 1, "macro": 0}
        lang = self._posting_language

        # If language hint is available, try to filter by it
        if lang and len(ids) > 1:
            lang_match = [
                lid
                for lid in ids
                if self._get_entry(lid) and lang in self._get_entry(lid).languages
            ]
            if lang_match and len(lang_match) < len(ids):
                # Language narrows the candidates — use only matching ones
                ids = lang_match

        best_id = ids[0]
        best_pop = -1
        best_rank = -1
        for loc_id in ids:
            entry = self._get_entry(loc_id)
            if not entry:
                continue
            rank = _TYPE_RANK.get(entry.loc_type, 0)
            # Prefer higher population; break ties by type specificity
            if (entry.population, rank) > (best_pop, best_rank):
                best_pop = entry.population
                best_rank = rank
                best_id = loc_id
        return best_id


def _normalize_type(raw: str | None) -> str | None:
    """Normalize a job_location_type string."""
    if not raw:
        return None
    key = raw.strip().lower()
    return _JOB_LOCATION_TYPE_MAP.get(key)
