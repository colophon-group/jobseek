"""One-time Swiss employer discovery from job-room.ch.

Fetches a sample of job postings from www.job-room.ch's public search backend
(POST /jobadservice/api/jobAdvertisements/_search), extracts employer metadata
per posting, deduplicates per employer, classifies external apply URLs against
ATS / aggregator / recruiter patterns, reconciles against the existing
companies.csv, and writes apps/crawler/data/discovered_employers_ch.csv.

The postings themselves are NOT copied — only employer-identifying facts (name,
website, canton, ATS signature). The output CSV is meant as a discovery seed
for the existing ws batch-add pipeline; postings are still ingested via direct
ATS crawls of the employer's own career page.

See ~/dev/job-room/strategy.md and ~/dev/job-room/api.md for background.

Run from the crawler directory so that ``src.shared.*`` resolves:

    cd apps/crawler
    uv run python ../../scripts/jobroom-discover.py [--max-postings N] [--all-cantons]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import structlog

from src.shared.csv_io import read_csv, write_csv
from src.shared.http import create_http_client
from src.shared.logging import setup_logging
from src.shared.slug import slugify

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

API_URL = "https://www.job-room.ch/jobadservice/api/jobAdvertisements/_search"
LANG_NG = "ZW4="  # base64("en"); the SPA includes this on every backend call

# 26 Swiss cantons (ISO 3166-2:CH).
SWISS_CANTONS: tuple[str, ...] = (
    "ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS",
    "BL", "SH", "AR", "AI", "SG", "GR", "AG", "TG", "TI", "VD", "VS", "NE",
    "GE", "JU",
)

# All-defaults filter body (as captured from the SPA).
DEFAULT_FILTER: dict = {
    "workloadPercentageMin": 10,
    "workloadPercentageMax": 100,
    "permanent": None,
    "companyName": None,
    "onlineSince": 60,
    "displayRestricted": False,
    "professionCodes": [],
    "keywords": [],
    "communalCodes": [],
    "cantonCodes": [],
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "apps" / "crawler" / "data" / "discovered_employers_ch.csv"
DEFAULT_CACHE = Path("/tmp/jobroom_raw.jsonl")
COMPANIES_CSV = REPO_ROOT / "apps" / "crawler" / "data" / "companies.csv"

# Per-page sleep — be polite, single-threaded crawl.
PAGE_DELAY_SECONDS = 1.5

# Server-side hard cap on (page * size) is 10_000. Catch HTTP 412 to detect.
HARD_OFFSET_CAP = 10_000

# Legal-entity suffixes to strip before slugifying for name dedup.
# Includes Swiss-subsidiary markers like "(Schweiz)" / "(Suisse)" so that
# "Hilti (Schweiz) AG" normalizes to the same key as parent "Hilti AG".
# The strip loop runs twice to handle chained suffixes.
_LEGAL_SUFFIX_PATTERN = re.compile(
    r"\s*("
    # Country / subsidiary markers (must match before AG/SA)
    r"\(?\s*(?:schweiz|suisse|svizzera|switzerland|swiss)\s*\)?|"
    # Legal-entity forms
    r"genossenschaft|stiftung|vereinigung|verein|"
    r"s\.?\s*a\.?\s*r\.?\s*l\.?|"
    r"sa\s*rl|sàrl|sarl|sagl|"
    r"holding\s*ag|"
    r"\(?ag\)?|s\.?\s*a\.?|"
    r"gmbh|"
    r"ltd\.?|inc\.?|llc|llp|"
    r"plc|n\.?v\.?|b\.?v\.?|"
    r"co\.?(\s*kg)?|kg|"
    r"sa/nv|nv/sa"
    r")\s*$",
    re.IGNORECASE,
)

CSV_COLUMNS = [
    "employer_name",
    "website",
    "website_host",
    "company_city",
    "canton_codes",
    "languages",
    "detected_ats",
    "source_kind",
    "external_url_sample",
    "posting_count",
    "first_seen_date",
    "last_seen_date",
    "avam_occupation_codes",
    "matched_company_slug",
    "matched_by",
]

# ---------------------------------------------------------------------------
# ATS / aggregator / recruiter pattern table
# ---------------------------------------------------------------------------

# Each tuple: (compiled_regex, ats_name, source_kind)
# Order matters: aggregators are matched BEFORE generic patterns.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Aggregators / syndication channels — must match before ATS patterns
    # because some employers post via these and the URL is unhelpful for
    # detecting the employer's actual ATS.
    (re.compile(r"(?:^|\.)jobs\.ch$", re.I), "jobs_ch", "aggregator"),
    (re.compile(r"(?:^|\.)jobup\.ch$", re.I), "jobup_ch", "aggregator"),
    (re.compile(r"(?:^|\.)jobscout24\.ch$", re.I), "jobscout24", "aggregator"),
    (re.compile(r"(?:^|\.)indeed\.(?:ch|com)$", re.I), "indeed", "aggregator"),
    (re.compile(r"(?:^|\.)linkedin\.com$", re.I), "linkedin", "aggregator"),
    (re.compile(r"(?:^|\.)stepstone\.(?:ch|de)$", re.I), "stepstone", "aggregator"),
    (re.compile(r"(?:^|\.)monster\.(?:ch|de|com)$", re.I), "monster", "aggregator"),
    (re.compile(r"(?:^|\.)xing\.com$", re.I), "xing", "aggregator"),
    # Specialized niche boards (industry-specific, not employer ATSes)
    (re.compile(r"(?:^|\.)sozialinfo\.ch$", re.I), "sozialinfo", "aggregator"),
    (re.compile(r"(?:^|\.)sozjobs\.ch$", re.I), "sozjobs", "aggregator"),
    (re.compile(r"(?:^|\.)krippenstellen\.ch$", re.I), "krippenstellen", "aggregator"),
    (re.compile(r"(?:^|\.)swiss-architects\.com$", re.I), "swiss_architects", "aggregator"),
    (re.compile(r"(?:^|\.)jobeo\.ch$", re.I), "jobeo", "aggregator"),

    # Recruitment agencies / staffing — URL-based detection.
    # Only definitive recruiter hosts go here; name-based detection
    # (classify_by_name) catches many more that post via jobs.ch.
    (re.compile(r"(?:^|\.)randstad\.ch$", re.I), "randstad", "recruiter"),
    (re.compile(r"(?:^|\.)adecco\.ch$", re.I), "adecco", "recruiter"),
    (re.compile(r"(?:^|\.)hays\.ch$", re.I), "hays", "recruiter"),
    (re.compile(r"(?:^|\.)manpower\.ch$", re.I), "manpower", "recruiter"),
    (re.compile(r"(?:^|\.)kellyservices\.ch$", re.I), "kelly", "recruiter"),
    (re.compile(r"(?:^|\.)robertwalters\.ch$", re.I), "robert_walters", "recruiter"),
    (re.compile(r"(?:^|\.)michaelpage\.ch$", re.I), "michael_page", "recruiter"),
    (re.compile(r"(?:^|\.)experis\.ch$", re.I), "experis", "recruiter"),
    # Swiss staffing agencies discovered from the all-cantons sweep
    (re.compile(r"(?:^|\.)yellowshark\.com$", re.I), "yellowshark", "recruiter"),
    (re.compile(r"(?:^|\.)progresspersonal\.ch$", re.I), "progresspersonal", "recruiter"),
    (re.compile(r"(?:^|\.)alegro\.ch$", re.I), "alegro", "recruiter"),
    (re.compile(r"(?:^|\.)team\.jobs$", re.I), "team_jobs", "recruiter"),
    (re.compile(r"(?:^|\.)workmanagement\.ch$", re.I), "workmanagement", "recruiter"),
    (re.compile(r"(?:^|\.)work24\.com$", re.I), "work24", "recruiter"),
    (re.compile(r"(?:^|\.)careerplus\.ch$", re.I), "careerplus", "recruiter"),
    (re.compile(r"(?:^|\.)itjob\.ch$", re.I), "itjob", "recruiter"),
    (re.compile(r"(?:^|\.)myitjob\.ch$", re.I), "myitjob", "recruiter"),
    (re.compile(r"(?:^|\.)bauagro\.ch$", re.I), "bauagro", "recruiter"),
    (re.compile(r"(?:^|\.)okjob\.ch$", re.I), "okjob", "recruiter"),
    (re.compile(r"(?:^|\.)workselection\.com$", re.I), "workselection", "recruiter"),
    (re.compile(r"(?:^|\.)valjob\.digital$", re.I), "valjob", "recruiter"),
    (re.compile(r"(?:^|\.)newwork-hr\.ch$", re.I), "newwork_hr", "recruiter"),
    (re.compile(r"(?:^|\.)gefrapersonal\.ch$", re.I), "gefrapersonal", "recruiter"),
    (re.compile(r"(?:^|\.)carepeople\.ch$", re.I), "carepeople", "recruiter"),
    (re.compile(r"(?:^|\.)gl-partner\.ch$", re.I), "gl_partner", "recruiter"),
    (re.compile(r"(?:^|\.)wigumar\.ch$", re.I), "wigumar", "recruiter"),
    (re.compile(r"(?:^|\.)rentaperson\.ch$", re.I), "rentaperson", "recruiter"),
    (re.compile(r"(?:^|\.)permserv\.ch$", re.I), "permserv", "recruiter"),
    (re.compile(r"(?:^|\.)jobalino\.ch$", re.I), "jobalino", "recruiter"),
    (re.compile(r"(?:^|\.)cvmanager\.ch$", re.I), "cvmanager", "recruiter"),
    (re.compile(r"(?:^|\.)bm-emploi\.ch$", re.I), "bm_emploi", "recruiter"),
    (re.compile(r"(?:^|\.)hansleutenegger\.ch$", re.I), "hans_leutenegger", "recruiter"),
    (re.compile(r"(?:^|\.)jobteam\.ch$", re.I), "jobteam", "recruiter"),
    (re.compile(r"(?:^|\.)persigo\.ch$", re.I), "persigo", "recruiter"),
    (re.compile(r"(?:^|\.)mt-jobs\.ch$", re.I), "mt_jobs", "recruiter"),
    (re.compile(r"(?:^|\.)spitexjobs\.ch$", re.I), "spitexjobs", "recruiter"),

    # Federal admin
    (re.compile(r"(?:^|\.)admin\.ch$", re.I), "swiss_federal", "federal"),
    (re.compile(r"(?:^|\.)stadt-zuerich\.ch$", re.I), "swiss_federal", "federal"),
    (re.compile(r"\.apps\.(?:be|bs|ge|zh|vd|ag|sg|lu)\.ch$", re.I), "cantonal", "federal"),

    # ATS — turnkey rich monitors that jobseek already supports
    (re.compile(r"(?:^|\.)greenhouse\.io$", re.I), "greenhouse", "ats"),
    (re.compile(r"\.myworkdayjobs\.com$", re.I), "workday", "ats"),
    (re.compile(r"(?:^|\.)lever\.co$", re.I), "lever", "ats"),
    (re.compile(r"(?:^|\.)jobs\.personio\.(?:de|com|ch)$", re.I), "personio", "ats"),
    (re.compile(r"(?:^|\.)personio\.(?:de|com|ch)$", re.I), "personio", "ats"),
    (re.compile(r"(?:^|\.)ashbyhq\.com$", re.I), "ashby", "ats"),
    (re.compile(r"\.umantis\.com$", re.I), "umantis", "ats"),
    (re.compile(r"(?:^|\.)softgarden\.(?:io|de)$", re.I), "softgarden", "ats"),
    (re.compile(r"(?:^|\.)recruitee\.com$", re.I), "recruitee", "ats"),
    (re.compile(r"(?:^|\.)smartrecruiters\.com$", re.I), "smartrecruiters", "ats"),
    (re.compile(r"(?:^|\.)workable\.com$", re.I), "workable", "ats"),
    (re.compile(r"(?:^|\.)breezy\.hr$", re.I), "breezy", "ats"),
    (re.compile(r"(?:^|\.)teamtailor\.com$", re.I), "teamtailor", "ats"),
    (re.compile(r"(?:^|\.)successfactors\.(?:eu|com)$", re.I), "successfactors", "ats"),
    (re.compile(r"(?:^|\.)join\.com$", re.I), "join", "ats"),

    # ATS — Swiss-specific, NOT YET supported by jobseek (Phase 3 candidates)
    (re.compile(r"(?:^|\.)refline\.ch$", re.I), "refline", "ats"),
    (re.compile(r"(?:^|\.)link\.ostendis\.com$", re.I), "ostendis", "ats"),
    (re.compile(r"(?:^|\.)ostendis\.com$", re.I), "ostendis", "ats"),
    (re.compile(r"(?:^|\.)dualoo\.com$", re.I), "dualoo", "ats"),
    (re.compile(r"(?:^|\.)jacando\.com$", re.I), "jacando", "ats"),
    (re.compile(r"(?:^|\.)abacus\.ch$", re.I), "abacus", "ats"),
    # prospective.ch / OHWS — public-sector ATS used by Stadler Rail,
    # Inselspital, Klinik Hirslanden, multiple cantonal administrations,
    # federal offices (BAFU, swisstopo), universities. Largest Phase 3
    # target — ~1900 postings in a full --all-cantons sweep (Apr 2026).
    (re.compile(r"(?:^|\.)ohws\.prospective\.ch$", re.I), "prospective", "ats"),
    (re.compile(r"(?:^|\.)prospective\.ch$", re.I), "prospective", "ats"),
    # Solique — Swiss multi-posting platform with ATS partnerships;
    # used by ISS Schweiz and others. ~430 postings in full sweep.
    (re.compile(r"(?:^|\.)solique\.ch$", re.I), "solique", "ats"),
]

# ATS names that jobseek does NOT yet have a monitor for (flagged in stats).
UNSUPPORTED_ATS = frozenset({
    "refline", "ostendis", "dualoo", "jacando", "abacus", "prospective",
    "solique",
})

# Self-branded aggregator company names to skip entirely. These appear
# when an aggregator cross-posts its own listings INTO job-room with
# company.name set to the aggregator brand (e.g. "Jobup" with 1689 postings
# linking back to jobup.ch). Not real employers.
_SELF_AGGREGATOR_NAMES: frozenset[str] = frozenset({
    "jobup", "jobs.ch", "jobs ch", "jobscout24", "jobscout", "indeed",
    "linkedin", "stepstone", "monster", "xing",
})


# ---------------------------------------------------------------------------
# Name-based source-kind detection
# ---------------------------------------------------------------------------
#
# Some employers are definitively classifiable from their name alone,
# independent of the apply URL. Staffing agencies and government bodies
# can hide behind any URL (including jobs.ch syndication), so URL-based
# classification alone misses them.
#
# Recruiters are SKIPPED from the discovery output entirely — we do not
# want them polluting companies.csv. A future agent classifier can
# revisit the raw cache if a more nuanced policy is needed.

# Staffing / recruitment-agency name patterns.
_RECRUITER_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    # Endings: "... Personal AG", "... Personal SA", "... Personnel Sàrl"
    re.compile(r"\bpersonal(?:\s+(?:ag|gmbh|sa|sàrl|sagl))?\s*$", re.I),
    # Matches "Personaldienst", "Personaldienste",
    # "Personaldienstleistung", "Personaldienstleistungen"
    re.compile(r"\bpersonaldienst", re.I),
    re.compile(r"\bpersonnel\b", re.I),
    # Common English staffing brand words
    re.compile(r"\bhuman\s+resources?\b", re.I),
    re.compile(r"\bhr\s+services?\b", re.I),
    re.compile(r"\b(?:temporary|temp|temp-work|zeitarbeit)\b", re.I),
    re.compile(r"\bstaffing\b", re.I),
    re.compile(r"\bworkforce\b", re.I),
    re.compile(r"\brecruitment\b|\brecruiting\b", re.I),
    re.compile(r"\btalent\s+(?:solutions?|acquisition|partners?)\b", re.I),
    re.compile(r"\bmanpower\b", re.I),
    # Known Swiss / global brands that are pure recruiters
    re.compile(r"\bgi\s+group\b", re.I),
    re.compile(r"\badecco\b", re.I),
    re.compile(r"\brandstad\b", re.I),
    re.compile(r"\bhays\b", re.I),
    re.compile(r"\bkelly\s+services\b", re.I),
    re.compile(r"\brobert\s+walters\b", re.I),
    re.compile(r"\bmichael\s+page\b", re.I),
    re.compile(r"\bexperis\b", re.I),
    # "Jobs AG" / "Job ... AG" / "Stellen..." — often brokers, not employers
    re.compile(r"^jobsign\b", re.I),
    re.compile(r"\bkmu\s+jobs\b", re.I),
    re.compile(r"\bstellenprofis?\b", re.I),
    re.compile(r"\bstellenvermittlung\b", re.I),
    re.compile(r"\bstellenmarkt\b", re.I),
    re.compile(r"\bwork\s*4\s*you\b", re.I),
    re.compile(r"\bjobs?\s*4\s*you\b", re.I),
    re.compile(r"\bjobpartner\b", re.I),
    re.compile(r"\bpeople\s+(?:ag|sa|gmbh|sàrl)\s*$", re.I),
    # Patterns discovered from the all-cantons full sweep
    re.compile(r"\bflexsis\b", re.I),
    re.compile(r"\bjob\s+impuls\b", re.I),
    re.compile(r"\bjob\s+team\b", re.I),
    re.compile(r"\b(?:job|jobs)\s+(?:ag|gmbh|sa|sàrl|sagl)\s*$", re.I),
    re.compile(r"\bhuman\s+capital\b", re.I),
    re.compile(r"\bswiss\s+work\b", re.I),
    re.compile(r"\bwork\s+management\b", re.I),
    # "Personal Kolin AG", "Personal Sigma", etc. — "Personal" at start
    re.compile(r"^personal\s+\w+", re.I),
    # Hans Leutenegger is the name of a very large Swiss temp agency
    re.compile(r"\bhans\s+leutenegger\b", re.I),
    re.compile(r"\brent\s*a\s*person\b", re.I),
    re.compile(r"\bpermserv\b", re.I),
    re.compile(r"\bpersigo\b", re.I),
    re.compile(r"\balegro\b.*\bpersonal\b", re.I),
    re.compile(r"\bpersonalberat(?:ung|ungs)\b", re.I),
    re.compile(r"\bunternehmensberatung\b.*\bpersonal\b", re.I),
)

# Government / public-sector name patterns. These employers ARE real and
# worth adding, but they're "federal" rather than "ats" since they usually
# use a custom cantonal CMS. Not skipped — just classified.
_FEDERAL_NAME_PATTERNS: tuple[re.Pattern, ...] = (
    # German — federal + cantonal
    re.compile(r"^bundes(?:amt|rat|kanzlei|verwaltung|gericht)\b", re.I),
    re.compile(r"^(?:schweizerische|schweizer)\s+eidgenossenschaft", re.I),
    re.compile(r"^kanton\b", re.I),
    re.compile(r"^kantonale\b", re.I),
    re.compile(r"\bkantonalbank\b", re.I),
    re.compile(r"^stadt\b", re.I),
    re.compile(r"^gemeinde\b", re.I),
    re.compile(r"\bkantonsspital\b", re.I),
    re.compile(r"\binselspital\b", re.I),
    re.compile(r"\buniversitätsspital\b", re.I),
    re.compile(r"\bpädagogische\s+hochschule\b", re.I),
    # French
    re.compile(r"^office\s+fédéral\b", re.I),
    re.compile(r"^(?:confédération\s+suisse|département\s+fédéral)", re.I),
    re.compile(r"^canton\s+(?:de|du|des)\b", re.I),
    re.compile(r"^etat\s+(?:de|du|des)\b", re.I),
    re.compile(r"^ville\s+(?:de|du|des)\b", re.I),
    re.compile(r"^commune\s+(?:de|du|des)\b", re.I),
    re.compile(r"\bbanque\s+cantonale\b", re.I),
    re.compile(r"\bhôpital\s+(?:universitaire|cantonal|du|de)\b", re.I),
    re.compile(r"\bhaute\s+école\b", re.I),
    # Italian
    re.compile(r"^ufficio\s+federale\b", re.I),
    re.compile(r"^(?:confederazione\s+svizzera|dipartimento\s+federale)", re.I),
    re.compile(r"^cantone\b", re.I),
    re.compile(r"^città\s+di\b", re.I),
    re.compile(r"^comune\s+di\b", re.I),
    re.compile(r"\bbanca\s+dello\s+stato\b", re.I),
    re.compile(r"\bospedale\b", re.I),
    # Multi-lingual directorates
    re.compile(r"^direzione\s+(?:dell[ae']|delle|dei|degli)\b", re.I),
    re.compile(r"^direction\s+(?:des|de|du|de\s+l['a-z])\b", re.I),
    re.compile(r"^direktion\s+(?:für|des|der|von)\b", re.I),
    # Named federal institutions (shown in real job-room data)
    re.compile(r"\b(?:swisstopo|swissmedic|meteoschweiz|meteo-suisse|seco|suva|ruag)\b", re.I),
)


def classify_by_name(name: str) -> str | None:
    """Name-based source_kind classification.

    Returns 'recruiter' if the name matches a staffing-agency pattern,
    'federal' for public-sector entities, or None otherwise.

    Takes precedence over URL-based classification: a 'Kanton Aargau'
    posting via jobs.ch is still federal; an 'Express Personal AG'
    posting via jobs.ch is still a recruiter.
    """
    if not name:
        return None
    for pat in _RECRUITER_NAME_PATTERNS:
        if pat.search(name):
            return "recruiter"
    for pat in _FEDERAL_NAME_PATTERNS:
        if pat.search(name):
            return "federal"
    return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RawPosting:
    posting_id: str
    employer_name: str
    website: str  # may be empty
    company_city: str  # may be empty
    canton_code: str  # may be empty
    languages: list[str] = field(default_factory=list)
    external_url: str = ""  # may be empty
    surrogate: bool = False
    avam_occupation_codes: list[str] = field(default_factory=list)
    publication_date: str = ""  # ISO date or empty


@dataclass
class DiscoveredEmployer:
    employer_name: str
    website: str
    website_host: str
    company_city: str
    canton_codes: set[str] = field(default_factory=set)
    languages: set[str] = field(default_factory=set)
    detected_ats: str = "unknown"
    source_kind: str = "unknown"
    external_url_sample: str = ""
    posting_count: int = 0
    first_seen_date: str = ""
    last_seen_date: str = ""
    avam_occupation_codes: set[str] = field(default_factory=set)
    matched_company_slug: str = ""
    matched_by: str = ""


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def _strip_legal_suffix(name: str) -> str:
    """Strip trailing legal-entity suffixes like AG, SA, GmbH, Sàrl."""
    if not name:
        return ""
    # Run twice to catch chained suffixes like "Holding AG" → "Holding" → ""
    out = name.strip()
    for _ in range(2):
        new = _LEGAL_SUFFIX_PATTERN.sub("", out).strip()
        if new == out:
            break
        out = new
    return out


def _normalize_name(name: str) -> str:
    """Normalize an employer name for dedup / lookup."""
    return slugify(_strip_legal_suffix(name))


def _normalize_host(url_or_host: str) -> str:
    """Extract a comparable host from a URL or bare hostname.

    Swiss-aware: takes the last 2 dot-separated labels (jobs.stripe.ch
    -> stripe.ch). Not a full eTLD parser; adequate for Switzerland-only
    discovery work.
    """
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    if "://" in s:
        try:
            host = urlparse(s).hostname or ""
        except Exception:
            return ""
    else:
        host = s.split("/")[0]
    host = host.removeprefix("www.")
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def classify_ats(external_url: str) -> tuple[str, str]:
    """Return (ats_name, source_kind) for an apply URL.

    source_kind: "ats" | "aggregator" | "recruiter" | "federal" | "direct" | "unknown".
    Pure regex, no HTTP probing.

    - "direct" means the posting has no externalUrl at all — i.e. the
      employer posts through job-room.ch's own apply flow (typically
      sourceSystem=JOBROOM or RAV-counsellor-created for small employers
      without an ATS). These are legitimate small-employer discoveries.
    - "unknown" means an externalUrl exists but doesn't match any pattern.
    """
    if not external_url:
        return ("direct", "direct")
    try:
        host = (urlparse(external_url).hostname or "").lower()
    except Exception:
        return ("unknown", "unknown")
    if not host:
        return ("unknown", "unknown")
    host = host.removeprefix("www.")
    for pattern, name, kind in _PATTERNS:
        if pattern.search(host):
            return (name, kind)
    return ("unknown", "unknown")


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


def _build_search_url(page: int, size: int) -> str:
    return f"{API_URL}?page={page}&size={size}&sort=date_desc&_ng={LANG_NG}"


async def _fetch_page(client, page: int, size: int, canton: str | None) -> tuple[list[dict], int]:
    """POST one page of search results. Returns (postings, total_count)."""
    body = dict(DEFAULT_FILTER)
    if canton:
        body["cantonCodes"] = [canton]
    url = _build_search_url(page, size)
    resp = await client.post(url, json=body, headers={"Accept": "application/json"})
    if resp.status_code == 412:
        # Server-side offset cap reached.
        raise StopAsyncIteration
    resp.raise_for_status()
    data = resp.json()
    total = int(resp.headers.get("x-total-count", "0"))
    return data, total


async def fetch_postings(
    cache_path: Path,
    cantons: list[str | None],
    max_postings: int,
    page_size: int,
) -> int:
    """Fetch postings into the JSONL cache. Returns count fetched."""
    fetched = 0
    server_total: int | None = None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w") as cache_f:
        async with create_http_client() as client:
            for canton in cantons:
                page = 0
                seen_in_canton = 0
                while fetched < max_postings:
                    try:
                        items, total = await _fetch_page(client, page, page_size, canton)
                    except StopAsyncIteration:
                        log.info(
                            "jobroom_discover.offset_cap",
                            canton=canton,
                            page=page,
                            note="HTTP 412 — pagination offset cap reached",
                        )
                        break
                    if server_total is None:
                        server_total = total
                        log.info("jobroom_discover.server_total", x_total_count=server_total)
                    if not items:
                        log.info("jobroom_discover.empty_page", canton=canton, page=page)
                        break
                    for raw in items:
                        cache_f.write(json.dumps(raw, ensure_ascii=False) + "\n")
                        fetched += 1
                        seen_in_canton += 1
                        if fetched >= max_postings:
                            break
                    log.info(
                        "jobroom_discover.page_fetched",
                        canton=canton,
                        page=page,
                        page_size=len(items),
                        fetched_total=fetched,
                        seen_in_canton=seen_in_canton,
                    )
                    if len(items) < page_size:
                        break  # short page = end of results
                    page += 1
                    # Hard cap from server side.
                    if (page * page_size) >= HARD_OFFSET_CAP:
                        log.info(
                            "jobroom_discover.offset_cap_preempt",
                            canton=canton,
                            note=f"reached {HARD_OFFSET_CAP} offset cap",
                        )
                        break
                    await asyncio.sleep(PAGE_DELAY_SECONDS)
                if fetched >= max_postings:
                    break
    return fetched


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _safe_get(d: dict | None, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def parse_posting(raw: dict) -> RawPosting | None:
    """Extract a RawPosting from a raw API envelope. Returns None to skip."""
    ja = raw.get("jobAdvertisement") or raw  # tolerate flat shape too
    jc = ja.get("jobContent") or {}
    company = jc.get("company") or {}
    location = jc.get("location") or {}

    if company.get("surrogate") is True:
        return None  # skip anonymized employers

    name = (company.get("name") or "").strip()
    if not name:
        return None

    descriptions = jc.get("jobDescriptions") or []
    languages = [d.get("languageIsoCode") for d in descriptions if d.get("languageIsoCode")]

    occupations = jc.get("occupations") or []
    avam_codes = [
        str(o.get("avamOccupationCode"))
        for o in occupations
        if o.get("avamOccupationCode") is not None
    ]

    publication = jc.get("publication") or {}
    pub_date = (
        publication.get("startDate")
        or ja.get("createdTime")
        or ""
    )
    if pub_date:
        pub_date = pub_date.split("T", 1)[0]  # YYYY-MM-DD only

    return RawPosting(
        posting_id=str(ja.get("id") or ""),
        employer_name=name,
        website=(company.get("website") or "").strip(),
        company_city=(company.get("city") or "").strip(),
        canton_code=(location.get("cantonCode") or "").strip(),
        languages=languages,
        external_url=(jc.get("externalUrl") or "").strip(),
        surrogate=False,
        avam_occupation_codes=avam_codes,
        publication_date=pub_date,
    )


def load_cached_postings(cache_path: Path) -> Iterable[RawPosting]:
    """Stream parsed postings from the JSONL cache."""
    skipped_surrogate = 0
    parsed = 0
    with cache_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            posting = parse_posting(raw)
            if posting is None:
                skipped_surrogate += 1
                continue
            parsed += 1
            yield posting
    log.info("jobroom_discover.parse_summary", parsed=parsed, skipped_surrogate=skipped_surrogate)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


# Source-kind preference: prefer ats > federal > direct > aggregator > unknown.
# Recruiters are SKIPPED at ingest, so they never reach the ranker.
_SOURCE_KIND_RANK = {
    "ats": 0,
    "federal": 1,
    "direct": 2,  # no externalUrl, posted directly through job-room
    "aggregator": 3,
    "recruiter": 4,  # not reachable via normal flow; present for completeness
    "unknown": 5,
}


def _better_source_kind(a: str, b: str) -> str:
    """Return whichever source_kind ranks higher (lower number)."""
    return a if _SOURCE_KIND_RANK.get(a, 99) <= _SOURCE_KIND_RANK.get(b, 99) else b


@dataclass
class AggregationStats:
    """Counters that the aggregation stage emits alongside the employer dict."""
    parsed: int = 0
    skipped_recruiter_by_name: int = 0
    skipped_recruiter_by_url: int = 0
    skipped_self_aggregator: int = 0
    federal_by_name: int = 0


def aggregate_employers(
    postings: Iterable[RawPosting],
) -> tuple[dict[tuple[str, str], DiscoveredEmployer], AggregationStats]:
    """Group postings into per-employer DiscoveredEmployer records.

    Dedupes by posting_id first, then groups by employer key. Two classes
    of postings are **skipped entirely** (not included in the output):

    1. Postings whose employer name matches a recruiter/staffing-agency
       pattern (e.g. 'Express Personal AG', 'Evergreen Human Resources').
    2. Postings whose externalUrl points to a recruiter host
       (randstad.ch, adecco.ch, etc.).

    Recruiters are filtered at the discovery stage because they are
    proxies, not real employers — adding them to companies.csv would
    pollute the registry with staffing firms instead of the actual Swiss
    employers that are hiring. A future agent classifier may revisit
    the raw cache for nuanced handling.

    Federal / public-sector employers ARE kept but have source_kind
    overridden to 'federal' based on their name.
    """
    stats = AggregationStats()
    seen_ids: set[str] = set()
    employers: dict[tuple[str, str], DiscoveredEmployer] = {}
    # Per-employer ATS-name votes, bucketed by source_kind so that the
    # resolved detected_ats matches the employer's final source_kind.
    # Structure: {key: {kind: Counter(name)}}.
    ats_votes: dict[tuple[str, str], dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    # Track per-employer best (ats-link) URL sample.
    best_url: dict[tuple[str, str], tuple[int, str]] = {}
    # Name-based override cache: employer_key -> 'federal' (recruiters
    # are dropped before aggregation, so never reach this map).
    name_override: dict[tuple[str, str], str] = {}

    for posting in postings:
        if posting.posting_id and posting.posting_id in seen_ids:
            continue
        if posting.posting_id:
            seen_ids.add(posting.posting_id)

        # Skip self-branded aggregator postings — not real employers.
        # E.g. jobup.ch sometimes syndicates INTO job-room with
        # company.name="Jobup", so strip those to avoid polluting the output.
        if posting.employer_name.strip().lower() in _SELF_AGGREGATOR_NAMES:
            stats.skipped_self_aggregator += 1
            continue

        # Skip recruiters — they are proxies, not employers.
        name_kind = classify_by_name(posting.employer_name)
        if name_kind == "recruiter":
            stats.skipped_recruiter_by_name += 1
            continue

        ats, url_kind = classify_ats(posting.external_url)
        if url_kind == "recruiter":
            stats.skipped_recruiter_by_url += 1
            continue

        stats.parsed += 1

        norm_name = _normalize_name(posting.employer_name)
        if not norm_name:
            continue
        norm_host = _normalize_host(posting.website)
        key = (norm_name, norm_host)

        ats_votes[key][url_kind][ats] += 1

        # Record federal name override (applied at the end).
        if name_kind == "federal":
            name_override[key] = "federal"
            stats.federal_by_name += 1

        emp = employers.get(key)
        if emp is None:
            emp = DiscoveredEmployer(
                employer_name=posting.employer_name,
                website=posting.website,
                website_host=norm_host,
                company_city=posting.company_city,
                source_kind=url_kind,
            )
            employers[key] = emp
        else:
            # Prefer the first non-empty website / city.
            if not emp.website and posting.website:
                emp.website = posting.website
                emp.website_host = norm_host
            if not emp.company_city and posting.company_city:
                emp.company_city = posting.company_city
            # Best (preferred) source_kind across postings.
            emp.source_kind = _better_source_kind(emp.source_kind, url_kind)

        emp.posting_count += 1
        if posting.canton_code:
            emp.canton_codes.add(posting.canton_code)
        for lang in posting.languages:
            emp.languages.add(lang)
        for code in posting.avam_occupation_codes:
            emp.avam_occupation_codes.add(code)

        if posting.publication_date:
            if not emp.first_seen_date or posting.publication_date < emp.first_seen_date:
                emp.first_seen_date = posting.publication_date
            if not emp.last_seen_date or posting.publication_date > emp.last_seen_date:
                emp.last_seen_date = posting.publication_date

        # Track best URL sample: prefer URLs whose source_kind is "ats".
        kind_rank = _SOURCE_KIND_RANK.get(url_kind, 99)
        cur = best_url.get(key)
        if posting.external_url and (cur is None or kind_rank < cur[0]):
            best_url[key] = (kind_rank, posting.external_url)

    # Resolve detected_ats from votes, scoped to the employer's final
    # source_kind, and apply the name-based federal override where present.
    for key, emp in employers.items():
        if name_override.get(key) == "federal":
            emp.source_kind = "federal"
        kind_votes = ats_votes[key].get(emp.source_kind, Counter())
        ranked = [(name, n) for name, n in kind_votes.most_common() if name != "unknown"]
        if ranked:
            emp.detected_ats = ranked[0][0]
        else:
            # Fall back to any non-unknown vote across all kinds (e.g. a
            # federal-by-name employer that posted via prospective.ch —
            # keep prospective as detected_ats even though source_kind is
            # federal after the override).
            any_votes: Counter = Counter()
            for kind_counter in ats_votes[key].values():
                for name, n in kind_counter.items():
                    if name != "unknown":
                        any_votes[name] += n
            emp.detected_ats = any_votes.most_common(1)[0][0] if any_votes else "unknown"
        if key in best_url:
            emp.external_url_sample = best_url[key][1]

    return employers, stats


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def build_company_lookups(companies_csv: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Build (host_to_slug, name_to_slug) lookup dicts from companies.csv."""
    headers, rows = read_csv(companies_csv)
    host_to_slug: dict[str, str] = {}
    name_to_slug: dict[str, str] = {}
    for row in rows:
        slug = row.get("slug") or ""
        if not slug:
            continue
        host = _normalize_host(row.get("website") or "")
        if host and host not in host_to_slug:
            host_to_slug[host] = slug
        norm_name = _normalize_name(row.get("name") or "")
        if norm_name and norm_name not in name_to_slug:
            name_to_slug[norm_name] = slug
    log.info(
        "jobroom_discover.companies_loaded",
        rows=len(rows),
        hosts_indexed=len(host_to_slug),
        names_indexed=len(name_to_slug),
    )
    return host_to_slug, name_to_slug


def reconcile(
    employers: dict[tuple[str, str], DiscoveredEmployer],
    host_to_slug: dict[str, str],
    name_to_slug: dict[str, str],
) -> None:
    """Annotate each DiscoveredEmployer with matched_company_slug + matched_by."""
    for (norm_name, norm_host), emp in employers.items():
        if norm_host:
            slug = host_to_slug.get(norm_host)
            if slug:
                emp.matched_company_slug = slug
                emp.matched_by = "host"
                continue
        slug = name_to_slug.get(norm_name)
        if slug:
            emp.matched_company_slug = slug
            emp.matched_by = "name"


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _employer_to_row(emp: DiscoveredEmployer) -> dict[str, str]:
    return {
        "employer_name": emp.employer_name,
        "website": emp.website,
        "website_host": emp.website_host,
        "company_city": emp.company_city,
        "canton_codes": "|".join(sorted(emp.canton_codes)),
        "languages": "|".join(sorted(emp.languages)),
        "detected_ats": emp.detected_ats,
        "source_kind": emp.source_kind,
        "external_url_sample": emp.external_url_sample,
        "posting_count": str(emp.posting_count),
        "first_seen_date": emp.first_seen_date,
        "last_seen_date": emp.last_seen_date,
        "avam_occupation_codes": "|".join(sorted(emp.avam_occupation_codes)),
        "matched_company_slug": emp.matched_company_slug,
        "matched_by": emp.matched_by,
    }


def write_employers_csv(
    employers: dict[tuple[str, str], DiscoveredEmployer],
    output_path: Path,
) -> None:
    rows = [_employer_to_row(e) for e in employers.values()]
    rows.sort(key=lambda r: (-int(r["posting_count"]), r["employer_name"].lower()))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    write_csv(tmp, CSV_COLUMNS, rows)
    os.replace(tmp, output_path)
    log.info("jobroom_discover.csv_written", path=str(output_path), rows=len(rows))


# ---------------------------------------------------------------------------
# Stats summary
# ---------------------------------------------------------------------------


def print_summary(
    employers: dict[tuple[str, str], DiscoveredEmployer],
    fetched: int,
    parsed: int,
    skipped_surrogate: int,
    agg_stats: AggregationStats,
    canton_filter: str,
) -> None:
    print("\n=== Job-room.ch discovery summary ===", file=sys.stderr)
    print(f"Sample fetched:          {fetched}  (canton filter: {canton_filter})", file=sys.stderr)
    print(f"  Parsed:                {parsed}", file=sys.stderr)
    print(f"  Skipped (surrogate):   {skipped_surrogate}", file=sys.stderr)
    skipped_recruiter_total = (
        agg_stats.skipped_recruiter_by_name + agg_stats.skipped_recruiter_by_url
    )
    print(
        f"  Skipped (recruiter):   {skipped_recruiter_total}"
        f"  (by_name={agg_stats.skipped_recruiter_by_name}, "
        f"by_url={agg_stats.skipped_recruiter_by_url})",
        file=sys.stderr,
    )
    if agg_stats.skipped_self_aggregator:
        print(
            f"  Skipped (self-branded aggregator): {agg_stats.skipped_self_aggregator}",
            file=sys.stderr,
        )

    total_employers = len(employers)
    matched = sum(1 for e in employers.values() if e.matched_company_slug)
    new = total_employers - matched
    matched_host = sum(1 for e in employers.values() if e.matched_by == "host")
    matched_name = sum(1 for e in employers.values() if e.matched_by == "name")
    pct = (lambda n: f"{(100 * n / total_employers):.0f}%" if total_employers else "n/a")

    print(f"Unique employers:        {total_employers}", file=sys.stderr)
    print(f"  Matched (existing):    {matched} ({pct(matched)})  by host={matched_host} name={matched_name}", file=sys.stderr)
    print(f"  New:                   {new} ({pct(new)})", file=sys.stderr)

    canton_counts: Counter = Counter()
    for e in employers.values():
        for c in e.canton_codes:
            canton_counts[c] += 1
    canton_str = ", ".join(f"{c}={n}" for c, n in canton_counts.most_common(8))
    print(f"Cantons covered:         {len(canton_counts)} ({canton_str})", file=sys.stderr)

    kind_counts: Counter = Counter()
    aggregator_breakdown: Counter = Counter()
    for e in employers.values():
        kind_counts[e.source_kind] += 1
        if e.source_kind == "aggregator":
            aggregator_breakdown[e.detected_ats] += 1

    print("Source kind distribution:", file=sys.stderr)
    for kind in ("ats", "federal", "direct", "aggregator", "unknown"):
        n = kind_counts.get(kind, 0)
        suffix = ""
        if kind == "aggregator" and aggregator_breakdown:
            parts = ", ".join(f"{k}={v}" for k, v in aggregator_breakdown.most_common(5))
            suffix = f"    ({parts})"
        print(f"  {kind:12s} {n}{suffix}", file=sys.stderr)

    ats_counts: Counter = Counter()
    for e in employers.values():
        if e.source_kind == "ats":
            ats_counts[e.detected_ats] += 1
    print("ATS distribution (source_kind=ats only):", file=sys.stderr)
    for ats, n in ats_counts.most_common():
        marker = "  WARN unsupported" if ats in UNSUPPORTED_ATS else ""
        print(f"  {ats:16s} {n}{marker}", file=sys.stderr)

    print("Top 20 new employers (by posting count):", file=sys.stderr)
    new_employers = [e for e in employers.values() if not e.matched_company_slug]
    new_employers.sort(key=lambda e: (-e.posting_count, e.employer_name.lower()))
    for i, e in enumerate(new_employers[:20], start=1):
        host = e.website_host or "no-website"
        canton = "/".join(sorted(e.canton_codes)) or "?"
        print(
            f"  {i:2d}. {e.employer_name} ({host}) -- {canton} -- "
            f"{e.posting_count} postings -- {e.source_kind}/{e.detected_ats}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-postings", type=int, default=200,
                   help="Maximum postings to fetch (default: 200)")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    p.add_argument("--cache-jsonl", type=Path, default=DEFAULT_CACHE,
                   help=f"Raw posting cache path (default: {DEFAULT_CACHE})")
    p.add_argument("--all-cantons", action="store_true",
                   help="Iterate over all 26 Swiss cantons (full sweep)")
    p.add_argument("--canton", type=str, default=None,
                   help="Filter to a single canton (e.g. ZH); ignored with --all-cantons")
    p.add_argument("--no-cache", action="store_true",
                   help="Force re-fetch even if the cache file exists")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Verbose (DEBUG) logging")
    return p.parse_args()


async def amain() -> int:
    args = parse_args()
    setup_logging(level="DEBUG" if args.verbose else "INFO")

    if args.all_cantons:
        cantons: list[str | None] = list(SWISS_CANTONS)
        canton_filter = "all"
        page_size = 1000
        max_postings = args.max_postings if args.max_postings != 200 else 100_000
    elif args.canton:
        cantons = [args.canton.upper()]
        canton_filter = args.canton.upper()
        page_size = min(200, args.max_postings) or 200
        max_postings = args.max_postings
    else:
        cantons = [None]  # unfiltered single sweep
        canton_filter = "none"
        page_size = min(200, args.max_postings) or 200
        max_postings = args.max_postings

    log.info(
        "jobroom_discover.start",
        max_postings=max_postings,
        canton_filter=canton_filter,
        page_size=page_size,
        output=str(args.output),
        cache=str(args.cache_jsonl),
    )

    # Fetch (unless cache hit)
    if args.cache_jsonl.exists() and not args.no_cache:
        log.info("jobroom_discover.cache_hit", path=str(args.cache_jsonl))
        fetched = sum(1 for _ in args.cache_jsonl.open())
    else:
        t0 = time.monotonic()
        fetched = await fetch_postings(args.cache_jsonl, cantons, max_postings, page_size)
        log.info("jobroom_discover.fetch_done", fetched=fetched, elapsed_s=round(time.monotonic() - t0, 1))

    # Parse
    skipped_surrogate = 0
    parsed_postings: list[RawPosting] = []
    for line in args.cache_jsonl.open():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        posting = parse_posting(raw)
        if posting is None:
            skipped_surrogate += 1
            continue
        parsed_postings.append(posting)

    log.info("jobroom_discover.parsed", parsed=len(parsed_postings), skipped_surrogate=skipped_surrogate)

    # Aggregate per employer (recruiters skipped here, not in output)
    employers, agg_stats = aggregate_employers(parsed_postings)
    log.info(
        "jobroom_discover.aggregated",
        unique_employers=len(employers),
        skipped_recruiter_by_name=agg_stats.skipped_recruiter_by_name,
        skipped_recruiter_by_url=agg_stats.skipped_recruiter_by_url,
        federal_by_name=agg_stats.federal_by_name,
    )

    # Reconcile against companies.csv
    host_to_slug, name_to_slug = build_company_lookups(COMPANIES_CSV)
    reconcile(employers, host_to_slug, name_to_slug)

    # Write CSV
    write_employers_csv(employers, args.output)

    # Print summary
    print_summary(
        employers, fetched, len(parsed_postings), skipped_surrogate, agg_stats, canton_filter
    )
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
