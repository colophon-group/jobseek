"""Onlyfy/Prescreen detail-page scraper.

Onlyfy's current public job URLs are Next.js client shells.  The initial HTML
contains metadata, but the actual job advert is loaded separately and generic
DOM/JSON-LD scrapers therefore see no useful body content.  The same posting
is available through Onlyfy's server-rendered candidate endpoint:

``/job/show/{handle}/full?lang={locale}&mode=candidate``.

This scraper translates the public URL to that endpoint and parses its stable
``text-element`` markup without Playwright.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.scrapers import JobContent, register
from src.shared.nextdata import extract_rsc_data

log = structlog.get_logger()

_ONLYFY_MARKER_RE = re.compile(
    r"(?:onlyfy\.jobs|content\.prescreen\.io|/candidate/job/print/)",
    re.IGNORECASE,
)
_LOCATION_RE = re.compile(
    r"(?:Standort|Location)\s*:\s*(.+?)(?=\s*(?:Zeitpunkt|Start(?:\s+date)?|$))",
    re.IGNORECASE,
)


def _job_handle_and_locale(url: str, configured_language: str | None = None) -> tuple[str, str]:
    """Return the Onlyfy job handle and preferred locale from *url*."""
    parts = [part for part in urlsplit(url).path.split("/") if part]
    try:
        job_index = parts.index("job")
    except ValueError as exc:
        raise ValueError(f"Unsupported Onlyfy job URL: {url!r}") from exc

    if job_index + 1 >= len(parts) or parts[job_index + 1] in {"show", "print"}:
        raise ValueError(f"Onlyfy job handle missing from URL: {url!r}")

    handle = parts[job_index + 1]
    locale = configured_language
    if not locale and job_index > 0 and re.fullmatch(r"[a-zA-Z]{2}", parts[job_index - 1]):
        locale = parts[job_index - 1].lower()
    return handle, locale or "en"


def _candidate_url(url: str, configured_language: str | None = None) -> str:
    """Translate a public Onlyfy job URL to its server-rendered candidate URL."""
    handle, locale = _job_handle_and_locale(url, configured_language)
    parsed = urlsplit(url)
    path = f"/job/show/{quote(handle, safe='')}/full"
    query = f"lang={quote(locale, safe='')}&mode=candidate"
    return urlunsplit((parsed.scheme, parsed.netloc, path, query, ""))


def _listing_url(url: str, configured_language: str | None = None) -> str:
    """Return the localized Onlyfy listing URL for a public job URL."""
    _handle, locale = _job_handle_and_locale(url, configured_language)
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{quote(locale, safe='')}", "", ""))


def _location_from_listing(html: str, handle: str) -> list[str] | None:
    """Find *handle* in an Onlyfy listing RSC payload and return its city."""
    data = extract_rsc_data(html)
    jobs_data = data.get("jobsData") if isinstance(data, dict) else None
    jobs = jobs_data.get("data") if isinstance(jobs_data, dict) else None
    if not isinstance(jobs, list):
        return None

    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_url = str(job.get("jobAdUrl") or "")
        if job_url.rstrip("/").rsplit("/", 1)[-1] != handle:
            continue
        location = str(job.get("cityName") or "").strip()
        return [location] if location else None
    return None


def _node_fragment(node: LexborNode) -> str:
    """Return a small semantic HTML fragment for a description node."""
    inner = (node.inner_html or "").strip()
    if not inner:
        return ""
    tag = node.tag if node.tag in {"p", "li", "ul", "ol", "h1", "h2", "h3", "h4"} else "p"
    return f"<{tag}>{inner}</{tag}>"


def _parse_location(tree: LexborHTMLParser) -> list[str] | None:
    for node in tree.css(".text-element-body_text"):
        text = node.text(separator=" ", strip=True)
        match = _LOCATION_RE.search(text)
        if match:
            location = re.sub(r"\s+", " ", match.group(1)).strip(" -|,")
            if location:
                return [location]
    return None


def _parse_description(tree: LexborHTMLParser) -> str | None:
    fragments: list[str] = []
    seen: set[str] = set()

    for node in tree.css(".text-element-body_text"):
        text = re.sub(r"\s+", " ", node.text(separator=" ", strip=True)).strip()
        if not text or _LOCATION_RE.search(text) or text in seen:
            continue
        seen.add(text)
        fragment = _node_fragment(node)
        if fragment:
            fragments.append(fragment)

    return "\n".join(fragments) or None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse a server-rendered Onlyfy candidate page.

    The lightweight title/location fallback also lets scraper probing
    recognize Onlyfy's Next.js shell before the real scrape follows the
    candidate endpoint.
    """
    config = config or {}
    tree = LexborHTMLParser(html)

    title_node = tree.css_first("h1.text-element-header_text") or tree.css_first("title")
    title = title_node.text(strip=True) if title_node is not None else None
    locations = _parse_location(tree)

    if locations is None:
        meta = tree.css_first('meta[name="description"]')
        meta_text = meta.attributes.get("content", "") if meta is not None else ""
        match = _LOCATION_RE.search(meta_text)
        if match:
            location = re.sub(r"\s+", " ", match.group(1)).strip(" -|,")
            locations = [location] if location else None

    return JobContent(
        title=title or None,
        description=_parse_description(tree),
        locations=locations,
        language=config.get("language"),
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect Onlyfy/Prescreen detail pages during scraper probing."""
    if htmls and sum(bool(_ONLYFY_MARKER_RE.search(html)) for html in htmls) >= len(htmls) / 2:
        return {}
    return None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    artifact_dir: Path | None = None,
    **kwargs,
) -> JobContent:
    """Fetch and parse one Onlyfy posting through its candidate endpoint."""
    _ = pw, kwargs
    candidate_url = _candidate_url(url, config.get("language"))
    response = await http.get(candidate_url, follow_redirects=True)
    response.raise_for_status()

    if artifact_dir is not None:
        (artifact_dir / "onlyfy-candidate.html").write_text(response.text)

    content = parse_html(response.text, config)
    if not content.locations:
        handle, _locale = _job_handle_and_locale(url, config.get("language"))
        listing_url = _listing_url(url, config.get("language"))
        listing_response = await http.get(listing_url, follow_redirects=True)
        if listing_response.status_code == 200:
            content.locations = _location_from_listing(listing_response.text, handle)

    log.debug(
        "onlyfy.extracted",
        url=url,
        candidate_url=candidate_url,
        title=content.title,
        locations=content.locations,
        has_description=bool(content.description),
    )
    return content


register("onlyfy", scrape, can_handle=can_handle, parse_html=parse_html)
