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

    # Recruitment agencies / staffing
    (re.compile(r"(?:^|\.)randstad\.ch$", re.I), "randstad", "recruiter"),
    (re.compile(r"(?:^|\.)adecco\.ch$", re.I), "adecco", "recruiter"),
    (re.compile(r"(?:^|\.)hays\.ch$", re.I), "hays", "recruiter"),
    (re.compile(r"(?:^|\.)manpower\.ch$", re.I), "manpower", "recruiter"),
    (re.compile(r"(?:^|\.)kellyservices\.ch$", re.I), "kelly", "recruiter"),
    (re.compile(r"(?:^|\.)robertwalters\.ch$", re.I), "robert_walters", "recruiter"),
    (re.compile(r"(?:^|\.)michaelpage\.ch$", re.I), "michael_page", "recruiter"),
    (re.compile(r"(?:^|\.)experis\.ch$", re.I), "experis", "recruiter"),

    # Federal admin
    (re.compile(r"(?:^|\.)admin\.ch$", re.I), "swiss_federal", "federal"),

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

    # ATS — Swiss-specific, NOT YET supported by jobseek (Phase 3 candidates)
    (re.compile(r"(?:^|\.)refline\.ch$", re.I), "refline", "ats"),
    (re.compile(r"(?:^|\.)ostendis\.com$", re.I), "ostendis", "ats"),
    (re.compile(r"(?:^|\.)dualoo\.com$", re.I), "dualoo", "ats"),
    (re.compile(r"(?:^|\.)jacando\.com$", re.I), "jacando", "ats"),
    (re.compile(r"(?:^|\.)abacus\.ch$", re.I), "abacus", "ats"),
]

# ATS names that jobseek does NOT yet have a monitor for (flagged in stats).
UNSUPPORTED_ATS = frozenset({"refline", "ostendis", "dualoo", "jacando", "abacus"})


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

    source_kind: "ats" | "aggregator" | "recruiter" | "federal" | "unknown".
    Pure regex, no HTTP probing.
    """
    if not external_url:
        return ("unknown", "unknown")
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


# Source-kind preference: prefer ats > federal > aggregator > recruiter > unknown.
_SOURCE_KIND_RANK = {
    "ats": 0,
    "federal": 1,
    "aggregator": 2,
    "recruiter": 3,
    "unknown": 4,
}


def _better_source_kind(a: str, b: str) -> str:
    """Return whichever source_kind ranks higher (lower number)."""
    return a if _SOURCE_KIND_RANK.get(a, 99) <= _SOURCE_KIND_RANK.get(b, 99) else b


def aggregate_employers(postings: Iterable[RawPosting]) -> dict[tuple[str, str], DiscoveredEmployer]:
    """Group postings into per-employer DiscoveredEmployer records.

    Dedupes incoming postings by posting_id first, then groups by employer key.
    """
    seen_ids: set[str] = set()
    employers: dict[tuple[str, str], DiscoveredEmployer] = {}
    # Track per-employer ATS-name votes so we can pick the most common one.
    ats_votes: dict[tuple[str, str], Counter] = defaultdict(Counter)
    # Track per-employer best (ats-link) URL sample.
    best_url: dict[tuple[str, str], tuple[int, str]] = {}

    for posting in postings:
        if posting.posting_id and posting.posting_id in seen_ids:
            continue
        if posting.posting_id:
            seen_ids.add(posting.posting_id)

        norm_name = _normalize_name(posting.employer_name)
        if not norm_name:
            continue
        norm_host = _normalize_host(posting.website)
        key = (norm_name, norm_host)

        ats, kind = classify_ats(posting.external_url)
        ats_votes[key][ats] += 1

        emp = employers.get(key)
        if emp is None:
            emp = DiscoveredEmployer(
                employer_name=posting.employer_name,
                website=posting.website,
                website_host=norm_host,
                company_city=posting.company_city,
                source_kind=kind,
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
            emp.source_kind = _better_source_kind(emp.source_kind, kind)

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
        kind_rank = _SOURCE_KIND_RANK.get(kind, 99)
        cur = best_url.get(key)
        if posting.external_url and (cur is None or kind_rank < cur[0]):
            best_url[key] = (kind_rank, posting.external_url)

    # Resolve detected_ats from votes (most common non-unknown).
    for key, emp in employers.items():
        votes = ats_votes[key]
        ranked = [(name, n) for name, n in votes.most_common() if name != "unknown"]
        emp.detected_ats = ranked[0][0] if ranked else "unknown"
        if key in best_url:
            emp.external_url_sample = best_url[key][1]

    return employers


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
    canton_filter: str,
) -> None:
    print("\n=== Job-room.ch discovery summary ===", file=sys.stderr)
    print(f"Sample fetched:          {fetched}  (canton filter: {canton_filter})", file=sys.stderr)
    print(f"  Parsed:                {parsed}", file=sys.stderr)
    print(f"  Skipped (surrogate):   {skipped_surrogate}", file=sys.stderr)

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
    for kind in ("ats", "aggregator", "recruiter", "federal", "unknown"):
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

    # Aggregate per employer
    employers = aggregate_employers(parsed_postings)
    log.info("jobroom_discover.aggregated", unique_employers=len(employers))

    # Reconcile against companies.csv
    host_to_slug, name_to_slug = build_company_lookups(COMPANIES_CSV)
    reconcile(employers, host_to_slug, name_to_slug)

    # Write CSV
    write_employers_csv(employers, args.output)

    # Print summary
    print_summary(employers, fetched, len(parsed_postings), skipped_surrogate, canton_filter)
    return 0


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
