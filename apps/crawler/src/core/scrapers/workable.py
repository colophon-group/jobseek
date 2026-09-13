"""Workable detail API scraper.

Fetches structured job data from the Workable detail endpoint:
  GET https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}
Falls back on rate limiting to Workable's public Markdown representation:
  GET https://apply.workable.com/{slug}/jobs/view/{shortcode}.md

The monitor (``src/core/monitors/workable``) discovers URLs; this scraper
fetches details on the daily scrape schedule.
"""

from __future__ import annotations

import html
import re

import httpx
import structlog

from src.core.enum_normalize import normalize_job_location_type
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

# Matches Workable job URLs — extracts slug and shortcode
# e.g. https://apply.workable.com/acme-corp/j/ABC123/
_JOB_URL_RE = re.compile(r"apply\.workable\.com/([\w-]+)/j/([\w]+)")

# Workable type codes (``full``/``part``/``contract``/``temporary``/
# ``internship``/``volunteer``/``other``) pass through — the central
# :func:`src.core.enum_normalize.normalize_employment_type` handles
# them.  The ``workplace`` field
# (``remote``/``hybrid``/``onsite``/``on_site``) is funnelled through
# :func:`src.core.enum_normalize.normalize_job_location_type`.


def _parse_job_url(url: str) -> tuple[str, str] | None:
    """Extract (slug, shortcode) from a Workable job URL.

    Example: https://apply.workable.com/acme-corp/j/ABC123/
      -> ("acme-corp", "ABC123")
    """
    match = _JOB_URL_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


def _detail_url(slug: str, shortcode: str) -> str:
    """Build the Workable detail API URL."""
    return f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}"


def _markdown_detail_url(slug: str, shortcode: str) -> str:
    """Build the public Workable Markdown detail URL."""
    return f"https://apply.workable.com/{slug}/jobs/view/{shortcode}.md"


def _markdown_inline(text: str) -> str:
    """Escape Markdown text and retain Workable's simple bold markup."""
    escaped = html.escape(text.strip())
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def _markdown_fragment_to_html(markdown: str) -> str | None:
    """Convert the small Markdown subset emitted by Workable to safe HTML."""
    output: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            continue

        heading = re.match(r"^(#{2,6})\s+(.+)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_markdown_inline(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^-\s+(.+)$", line)
        if bullet:
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{_markdown_inline(bullet.group(1))}</li>")
            continue

        close_list()
        output.append(f"<p>{_markdown_inline(line)}</p>")

    close_list()
    return "\n".join(output) or None


def _parse_markdown_detail(markdown: str) -> JobContent:
    """Parse Workable's public Markdown representation of a job."""
    title_match = re.search(r"^#\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else None

    locations: list[str] | None = None
    employment_type: str | None = None
    date_posted: str | None = None
    summary_match = re.search(r"^>\s*(.+?)\s*$", markdown, flags=re.MULTILINE)
    if summary_match:
        parts = [part.strip() for part in summary_match.group(1).split(" · ")]
        if len(parts) >= 4:
            location = " · ".join(parts[1:-2]).strip()
            location = re.sub(r"\s+\((?:remote|hybrid|on-?site)\)$", "", location, flags=re.I)
            locations = [location] if location else None
            employment_type = parts[-2] or None
            posted_match = re.fullmatch(r"Posted\s+(\d{4}-\d{2}-\d{2})", parts[-1])
            if posted_match:
                date_posted = posted_match.group(1)

    workplace_match = re.search(
        r"^\*\*Workplace:\*\*\s*(.+?)\s*$", markdown, flags=re.MULTILINE | re.I
    )
    job_location_type = None
    if workplace_match:
        job_location_type = normalize_job_location_type(workplace_match.group(1), default=None)

    department_match = re.search(
        r"^\*\*Department:\*\*\s*(.+?)\s*$", markdown, flags=re.MULTILINE | re.I
    )
    metadata = None
    if department_match:
        metadata = {"department": department_match.group(1).strip()}

    description_match = re.search(
        r"^## Description\s*$\n(.*?)(?=^## Apply\s*$|\Z)",
        markdown,
        flags=re.MULTILINE | re.DOTALL | re.I,
    )
    description = (
        _markdown_fragment_to_html(description_match.group(1)) if description_match else None
    )

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=date_posted,
        metadata=metadata,
    )


def _build_description(detail: dict) -> str | None:
    """Combine description + requirements + benefits into a single HTML body."""
    parts: list[str] = []
    for key in ("description", "requirements", "benefits"):
        text = detail.get(key)
        if text and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts) if parts else None


def _build_locations(detail: dict) -> list[str] | None:
    """Build location strings from the locations array."""
    raw_locations = detail.get("locations")
    if not raw_locations or not isinstance(raw_locations, list):
        # Fallback to single location object
        loc = detail.get("location")
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
            return [name] if name else None
        if isinstance(loc, str) and loc:
            return [loc]
        return None

    locations: list[str] = []
    seen: set[str] = set()
    for loc in raw_locations:
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("region"), loc.get("country")]
            name = ", ".join(p for p in parts if p)
        elif isinstance(loc, str):
            name = loc
        else:
            continue
        if name and name not in seen:
            locations.append(name)
            seen.add(name)

    return locations or None


def _parse_job_location_type(detail: dict) -> str | None:
    """Derive job_location_type from workplace or remote fields."""
    workplace = detail.get("workplace")
    if isinstance(workplace, str):
        mapped = normalize_job_location_type(workplace, default=None)
        if mapped:
            return mapped
    if detail.get("remote"):
        return "remote"
    return None


def _parse_detail(detail: dict) -> JobContent:
    """Parse the Workable detail API response into JobContent."""
    title = detail.get("title")
    description = _build_description(detail)

    # Employment type — pass through raw upstream label.
    raw_type = detail.get("type")
    employment_type = raw_type if isinstance(raw_type, str) and raw_type else None

    # Metadata
    metadata: dict | None = None
    dept = detail.get("department")
    if isinstance(dept, str) and dept:
        metadata = {"department": dept}
    elif isinstance(dept, list) and dept:
        metadata = {"department": ", ".join(dept)}

    return JobContent(
        title=title,
        description=description,
        locations=_build_locations(detail),
        employment_type=employment_type,
        job_location_type=_parse_job_location_type(detail),
        date_posted=detail.get("published"),
        metadata=metadata,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch job details from the Workable detail API."""
    parsed = _parse_job_url(url)
    if not parsed:
        log.warning("workable_scraper.unparseable_url", url=url)
        return JobContent()

    slug, shortcode = parsed
    # Allow config override for slug
    slug = config.get("token") or slug
    api_url = _detail_url(slug, shortcode)

    resp = await http.get(api_url)
    if resp.status_code == 429:
        markdown_url = _markdown_detail_url(slug, shortcode)
        markdown_resp = await http.get(markdown_url, follow_redirects=True)
        if markdown_resp.status_code == 200:
            log.warning(
                "workable_scraper.rate_limited_markdown_fallback",
                url=url,
            )
            return _parse_markdown_detail(markdown_resp.text)
        log.warning(
            "workable_scraper.markdown_detail_failed",
            url=url,
            status=markdown_resp.status_code,
        )

    if resp.status_code != 200:
        log.warning(
            "workable_scraper.detail_failed",
            url=url,
            status=resp.status_code,
        )
        return JobContent()

    return _parse_detail(resp.json())


register("workable", scrape)
