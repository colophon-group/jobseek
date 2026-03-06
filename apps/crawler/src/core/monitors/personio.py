"""Personio monitor — XML feed with HTML/RSC fallback.

Primary: GET https://{slug}.jobs.personio.de/xml?language=en
Fallback: parse RSC-embedded JSON from the HTML careers page.

Some Personio tenants are on ``.personio.com`` instead of ``.personio.de``,
and some have no XML feed at all (newer Next.js-based pages).  The monitor
tries XML on both domains, then falls back to parsing the listing page HTML.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register, slugs_from_url

log = structlog.get_logger()

MAX_JOBS = 10_000

_DOMAIN_RE = re.compile(r"^([\w-]+)\.jobs\.personio\.(\w+)$")

_PAGE_PATTERNS = [
    re.compile(r"([\w-]+)\.jobs\.personio\.(\w+)"),
    re.compile(r"personio\.\w+/job/"),
]

# Known Personio TLDs, ordered by prevalence for probing
_KNOWN_TLDS = ("de", "com")

_IGNORE_SLUGS = frozenset({"www", "api", "app", "docs", "help", "support", "status"})

_EMPLOYMENT_TYPE_MAP: dict[str, str | None] = {
    "permanent": None,  # combined with schedule
    "intern": "Intern",
    "trainee": "Intern",
    "freelance": "Contract",
}

_SCHEDULE_MAP: dict[str, str] = {
    "full-time": "Full-time",
    "part-time": "Part-time",
}


def _slug_from_url(board_url: str) -> str | None:
    """Extract the Personio company slug from a *.jobs.personio.* URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        slug = match.group(1)
        if slug not in _IGNORE_SLUGS:
            return slug
    return None


def _tld_from_url(board_url: str) -> str:
    """Return the Personio TLD (e.g. 'de', 'com') from the board URL."""
    parsed = urlparse(board_url)
    host = (parsed.hostname or "").lower()
    match = _DOMAIN_RE.match(host)
    if match:
        return match.group(2)
    return "de"


def _api_url(slug: str, domain: str = "de", lang: str = "en") -> str:
    return f"https://{slug}.jobs.personio.{domain}/xml?language={lang}"


def _board_base(slug: str, domain: str = "de") -> str:
    return f"https://{slug}.jobs.personio.{domain}"


def _text(el: ET.Element, tag: str) -> str | None:
    """Get text content of a child element, or None."""
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_employment_type(position: ET.Element) -> str | None:
    """Combine employmentType + schedule into a standard employment type."""
    emp_type = (_text(position, "employmentType") or "").lower()
    schedule = (_text(position, "schedule") or "").lower()

    # If employmentType maps to a specific type (intern/freelance), use that
    mapped = _EMPLOYMENT_TYPE_MAP.get(emp_type)
    if mapped is not None:
        return mapped

    # Otherwise, use schedule to determine Full-time/Part-time
    return _SCHEDULE_MAP.get(schedule)


def _parse_description(position: ET.Element) -> str | None:
    """Combine jobDescriptions into a single HTML description."""
    descs_el = position.find("jobDescriptions")
    if descs_el is None:
        return None

    parts: list[str] = []
    for desc in descs_el.findall("jobDescription"):
        name = _text(desc, "name")
        value = _text(desc, "value")
        if value:
            if name:
                parts.append(f"<h3>{name}</h3>")
            parts.append(value)

    return "\n".join(parts) if parts else None


def _parse_job(position: ET.Element, slug: str, domain: str = "de") -> DiscoveredJob | None:
    """Parse a <position> XML element into a DiscoveredJob."""
    pos_id = _text(position, "id")
    title = _text(position, "name")
    if not pos_id:
        return None

    url = f"{_board_base(slug, domain)}/job/{pos_id}"

    # Location
    office = _text(position, "office")
    locations = [office] if office else None

    # Metadata
    metadata: dict = {}
    if pos_id:
        metadata["id"] = pos_id
    for field in (
        "department",
        "subcompany",
        "recruitingCategory",
        "seniority",
        "yearsOfExperience",
        "occupation",
        "occupationCategory",
        "keywords",
    ):
        val = _text(position, field)
        if val:
            metadata[field] = val

    return DiscoveredJob(
        url=url,
        title=title,
        description=_parse_description(position),
        locations=locations,
        employment_type=_parse_employment_type(position),
        date_posted=_text(position, "createdAt"),
        metadata=metadata or None,
    )


def _parse_html_listings(html: str, slug: str, domain: str) -> list[DiscoveredJob]:
    """Parse RSC-embedded JSON from a Personio Next.js listing page.

    Newer Personio tenants don't serve the XML feed; instead the listing
    page embeds a ``jobs`` array inside a React Server Component payload.

    The RSC payload may use escaped quotes (``\\"jobs\\"``) or plain
    quotes (``"jobs"``) depending on the nesting level.
    """
    escaped_slug = re.escape(slug)

    # Try both escaped-quote and plain-quote patterns
    patterns = [
        # Escaped quotes (inside a JS string literal)
        re.compile(
            r'\\"jobs\\":\s*(\[\{.*?\}])\s*,\s*\\"subdomain\\":\s*\\"' + escaped_slug + r'\\"'
        ),
        # Plain quotes (directly in script content)
        re.compile(r'"jobs":\s*(\[\{.*?\}])\s*,\s*"subdomain"\s*:\s*"' + escaped_slug + r'"'),
    ]

    raw_json = None
    for pattern in patterns:
        match = pattern.search(html)
        if match:
            raw_json = match.group(1)
            break

    if not raw_json:
        return []

    # Unescape backslash-escaped quotes from RSC string literals
    raw_json = raw_json.replace('\\"', '"')

    try:
        jobs_data: list[dict] = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []

    base = _board_base(slug, domain)
    jobs: list[DiscoveredJob] = []
    for item in jobs_data:
        pos_id = item.get("id")
        if not pos_id:
            continue

        # Employment type mapping
        emp_raw = (item.get("employment_type") or "").lower()
        schedule_raw = (item.get("schedule") or "").lower()
        emp_type = _EMPLOYMENT_TYPE_MAP.get(emp_raw)
        if emp_type is None:
            emp_type = _SCHEDULE_MAP.get(schedule_raw)

        # Location
        office = item.get("main_office")
        locations = [office] if office else None

        # Metadata
        metadata: dict = {}
        metadata["id"] = str(pos_id)
        for src_key, meta_key in (
            ("department", "department"),
            ("subcompany", "subcompany"),
            ("category", "recruitingCategory"),
            ("seniority", "seniority"),
            ("years_of_experience", "yearsOfExperience"),
            ("occupation", "occupation"),
            ("occupation_category", "occupationCategory"),
            ("keywords", "keywords"),
        ):
            val = item.get(src_key)
            if val:
                metadata[meta_key] = val

        jobs.append(
            DiscoveredJob(
                url=f"{base}/job/{pos_id}",
                title=item.get("name"),
                description=None,  # Not available in listing page
                locations=locations,
                employment_type=emp_type,
                date_posted=item.get("created_at"),
                metadata=metadata or None,
            )
        )

    return jobs


async def _probe_xml(slug: str, domain: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe the Personio XML feed on a specific domain. Returns (found, count)."""
    jobs = await _fetch_xml_jobs(slug, domain, _DEFAULT_LANGUAGE, client)
    if jobs is None:
        return False, None
    return True, len(jobs)


async def _probe_api(
    slug: str, client: httpx.AsyncClient, prefer_domain: str = "de"
) -> tuple[bool, int | None, str]:
    """Probe both .de and .com XML feeds. Returns (found, count, domain)."""
    domains = [prefer_domain] + [t for t in _KNOWN_TLDS if t != prefer_domain]
    for domain in domains:
        found, count = await _probe_xml(slug, domain, client)
        if found:
            return True, count, domain
    return False, None, prefer_domain


_DEFAULT_LANGUAGE = "en"
_DEFAULT_BACKFILL = ["de"]


async def _fetch_xml_jobs(
    slug: str, domain: str, lang: str, client: httpx.AsyncClient
) -> list[DiscoveredJob] | None:
    """Fetch and parse jobs from the XML feed for a single language.

    Returns None if the feed is unavailable (4xx/5xx/parse error),
    or a list of DiscoveredJob (possibly empty for valid but empty feeds).
    """
    url = _api_url(slug, domain, lang=lang)
    try:
        response = await client.get(url, follow_redirects=True)
        if response.status_code != 200:
            return None
        root = ET.fromstring(response.text)
    except Exception:
        return None

    positions = root.findall(".//position")
    return [j for pos in positions if (j := _parse_job(pos, slug, domain))]


async def _discover_xml(
    slug: str,
    domain: str,
    client: httpx.AsyncClient,
    language: str = _DEFAULT_LANGUAGE,
    backfill_languages: list[str] | None = None,
) -> list[DiscoveredJob] | None:
    """Try to fetch jobs from the XML feed. Returns None if unavailable.

    Fetches multiple languages and builds ``localizations`` dicts.
    The display version (top-level fields) uses English when available,
    falling back to the best-coverage language.
    """
    jobs = await _fetch_xml_jobs(slug, domain, language, client)
    if jobs is None:
        return None

    if not jobs:
        return jobs

    # Set language on all jobs from primary feed
    for j in jobs:
        j.language = language

    # Fetch additional languages to build localizations
    all_languages = [language]
    backfill = backfill_languages if backfill_languages is not None else _DEFAULT_BACKFILL
    backfill = [lang for lang in backfill if lang != language]

    if backfill:
        jobs = await _build_localizations(jobs, slug, domain, client, language, backfill)
        all_languages.extend(backfill)

    # If primary wasn't English but English is available in localizations,
    # swap English to top-level
    if language != "en":
        for j in jobs:
            if j.localizations and "en" in j.localizations:
                en = j.localizations["en"]
                # Move current top-level into localizations
                j.localizations[language] = {
                    "title": j.title,
                    "description": j.description,
                    "locations": j.locations,
                }
                # Promote English to top-level
                j.title = en.get("title") or j.title
                j.description = en.get("description") or j.description
                j.locations = en.get("locations") or j.locations
                j.language = "en"

    return jobs


async def _build_localizations(
    jobs: list[DiscoveredJob],
    slug: str,
    domain: str,
    client: httpx.AsyncClient,
    primary_lang: str,
    other_languages: list[str],
) -> list[DiscoveredJob]:
    """Fetch alternative language feeds and build localizations dicts."""
    for lang in other_languages:
        alt_jobs = await _fetch_xml_jobs(slug, domain, lang, client)
        if not alt_jobs:
            continue

        # Build id → (title, description, locations) map
        alt_map: dict[str, dict] = {}
        for aj in alt_jobs:
            job_id = (aj.metadata or {}).get("id")
            if job_id:
                entry: dict = {}
                if aj.title:
                    entry["title"] = aj.title
                if aj.description:
                    entry["description"] = aj.description
                if aj.locations:
                    entry["locations"] = aj.locations
                if entry:
                    alt_map[job_id] = entry

        if not alt_map:
            continue

        added = 0
        for job in jobs:
            job_id = (job.metadata or {}).get("id")
            if not job_id or job_id not in alt_map:
                continue

            if job.localizations is None:
                # Also store primary language content in localizations
                job.localizations = {
                    primary_lang: {
                        k: v
                        for k, v in [
                            ("title", job.title),
                            ("description", job.description),
                            ("locations", job.locations),
                        ]
                        if v
                    }
                }
            job.localizations[lang] = alt_map[job_id]
            added += 1

            # Backfill missing primary description from alt language
            if not job.description:
                alt_desc = alt_map[job_id].get("description")
                if alt_desc:
                    job.description = alt_desc

        if added:
            log.info("personio.localizations", slug=slug, lang=lang, jobs=added)

    return jobs


async def _discover_html(
    slug: str, domain: str, client: httpx.AsyncClient
) -> list[DiscoveredJob] | None:
    """Fall back to parsing the HTML listing page for RSC-embedded jobs."""
    base = _board_base(slug, domain)
    try:
        resp = await client.get(f"{base}/", follow_redirects=True)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    jobs = _parse_html_listings(resp.text, slug, domain)
    if jobs:
        log.info("personio.html_fallback", slug=slug, domain=domain, count=len(jobs))
    return jobs or None


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch job listings from Personio — XML feed first, then HTML fallback.

    Config keys (all optional, auto-detected by ``can_handle``):

    ``slug``
        Personio subdomain (e.g. ``"acme"`` for ``acme.jobs.personio.de``).
    ``language``
        Primary XML feed language (default ``"en"``).
    ``backfill_languages``
        List of fallback languages to fill in missing descriptions
        (default ``["de"]``).  Set to ``[]`` to disable backfill.
    """
    metadata = board.get("metadata") or {}
    board_url = board["board_url"]

    slug = metadata.get("slug") or _slug_from_url(board_url)
    if not slug:
        raise ValueError(
            f"Cannot derive Personio slug from board URL {board_url!r} and no slug in metadata"
        )

    language = metadata.get("language", _DEFAULT_LANGUAGE)
    backfill = metadata.get("backfill_languages")
    if backfill is None:
        backfill = _DEFAULT_BACKFILL

    prefer = _tld_from_url(board_url)
    domains = [prefer] + [t for t in _KNOWN_TLDS if t != prefer]

    # Try XML on both domains
    for domain in domains:
        jobs = await _discover_xml(slug, domain, client, language, backfill)
        if jobs is not None:
            if len(jobs) > MAX_JOBS:
                log.warning("personio.truncated", slug=slug, total=len(jobs), cap=MAX_JOBS)
                jobs = sorted(jobs, key=lambda j: j.url)[:MAX_JOBS]
            return jobs

    # XML unavailable — fall back to HTML page parsing
    for domain in domains:
        jobs = await _discover_html(slug, domain, client)
        if jobs is not None:
            return jobs

    raise ValueError(
        f"Personio feed unavailable for slug {slug!r} — "
        f"XML 404 on both .de and .com, and HTML parsing found no jobs"
    )


async def _probe_language_coverage(slug: str, domain: str, client: httpx.AsyncClient) -> dict:
    """Probe EN and DE XML feeds and return language coverage info.

    Returns a dict with ``language``, ``backfill_languages``, and
    ``coverage`` suitable for inclusion in the ``can_handle`` result
    (and therefore the auto-filled config).
    """
    result: dict = {}
    coverage: dict[str, dict] = {}

    for lang in ("en", "de"):
        jobs = await _fetch_xml_jobs(slug, domain, lang, client)
        if jobs is None:
            continue
        total = len(jobs)
        with_desc = sum(1 for j in jobs if j.description)
        coverage[lang] = {"jobs": total, "descriptions": with_desc}

    if not coverage:
        return result

    # Pick the language with the most descriptions as primary
    best_lang = max(coverage, key=lambda lang: coverage[lang]["descriptions"])
    best_desc = coverage[best_lang]["descriptions"]
    best_total = coverage[best_lang]["jobs"]

    result["language"] = best_lang
    if best_total > 0 and best_desc < best_total:
        # Need backfill — include all other languages that add coverage
        backfill = [
            lang for lang in coverage if lang != best_lang and coverage[lang]["descriptions"] > 0
        ]
        if backfill:
            result["backfill_languages"] = backfill

    result["coverage"] = coverage
    return result


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Personio: domain check -> page HTML scan -> slug-based probe.

    When an XML feed is found, also probes EN and DE to determine the best
    ``language`` and ``backfill_languages`` config — included in the
    returned dict so ``ws select monitor`` auto-fills them.
    """
    prefer = _tld_from_url(url)

    # 1. Direct *.jobs.personio.{de,com} URL
    slug = _slug_from_url(url)
    if slug:
        if client is not None:
            found, count, domain = await _probe_api(slug, client, prefer)
            if found:
                result: dict = {"slug": slug, "domain": domain}
                if count is not None:
                    result["jobs"] = count
                lang_info = await _probe_language_coverage(slug, domain, client)
                result.update(lang_info)
                return result
            # XML feed down but URL pattern matches — still report as Personio
            # (discover() will try HTML fallback)
            return {"slug": slug, "domain": prefer}
        return {"slug": slug, "domain": prefer}

    if client is None:
        return None

    # 2. HTML scan for Personio markers
    html = await fetch_page_text(url, client)
    if html:
        slug_match = re.search(r"([\w-]+)\.jobs\.personio\.(de|com)", html)
        if slug_match:
            found_slug = slug_match.group(1)
            found_domain = slug_match.group(2)
            if found_slug not in _IGNORE_SLUGS:
                log.info("personio.detected_in_page", url=url, slug=found_slug)
                found, count, domain = await _probe_api(found_slug, client, found_domain)
                if found:
                    result = {"slug": found_slug, "domain": domain}
                    if count is not None:
                        result["jobs"] = count
                    lang_info = await _probe_language_coverage(found_slug, domain, client)
                    result.update(lang_info)
                    return result

    # 3. Slug-based probe as fallback
    for slug in slugs_from_url(url):
        found, count, domain = await _probe_api(slug, client, prefer)
        if found:
            log.info("personio.detected_by_probe", url=url, slug=slug)
            result = {"slug": slug, "domain": domain}
            if count is not None:
                result["jobs"] = count
            lang_info = await _probe_language_coverage(slug, domain, client)
            result.update(lang_info)
            return result

    return None


register("personio", discover, cost=10, can_handle=can_handle, rich=False)
