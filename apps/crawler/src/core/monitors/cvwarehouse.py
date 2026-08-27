"""CVWarehouse hosted careers monitor.

CVWarehouse tenant home pages expose category tiles rather than job links.  The
largest tile is the unfiltered "all vacancies" section; requesting that opaque
section renders every localized job card and embeds the full matching detail
documents in the response.  This monitor follows that provider contract and
combines all advertised locales into one rich, job-id-deduplicated result.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.core.monitors.raw import save_text_response

log = structlog.get_logger()

MAX_JOBS = 10_000
MAX_LOCALES = 20
MAX_PAGE_CHARS = 30_000_000
_SECTION_RE = re.compile(r"^[0-9a-fA-F-]{16,64}$")
_COUNT_RE = re.compile(r"\d[\d., ]*")


def _is_cvw_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "cvw.io" or host.endswith(".cvw.io")


def _is_provider_page(page: str) -> bool:
    folded = page.casefold()
    return "cvwarehouse" in folded and ("section=" in folded or "data-jobdetail-job-id" in folded)


def _section_url(url: str, section: str) -> str:
    if not _SECTION_RE.fullmatch(section):
        raise ValueError("CVWarehouse section must be a bounded provider identifier")
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["section"] = [section]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _section_from_page(page: str) -> tuple[str, int] | None:
    """Return the largest (therefore unfiltered) section and advertised count."""
    tree = LexborHTMLParser(page)
    candidates: list[tuple[int, str]] = []
    for link in tree.css('a[href*="section="]'):
        href = link.attributes.get("href") or ""
        section = (parse_qs(urlparse(href).query).get("section") or [""])[0]
        badge = link.css_first(".badge")
        if not _SECTION_RE.fullmatch(section) or badge is None:
            continue
        match = _COUNT_RE.search(badge.text(separator=" ", strip=True))
        if match is None:
            continue
        digits = re.sub(r"\D", "", match.group(0))
        if digits:
            candidates.append((int(digits), section))
    if not candidates:
        return None
    count, section = max(candidates)
    return section, count


def _locale_urls(page: str, section_url: str) -> list[str]:
    """Return same-tenant language variants, keeping the configured locale first."""
    base = urlparse(section_url)
    urls = [section_url]
    tree = LexborHTMLParser(page)
    for link in tree.css("#language-modal a[href]"):
        candidate = urljoin(section_url, link.attributes.get("href") or "")
        parsed = urlparse(candidate)
        section = (parse_qs(parsed.query).get("section") or [""])[0]
        if parsed.hostname != base.hostname or not _SECTION_RE.fullmatch(section):
            continue
        if candidate not in urls:
            urls.append(candidate)
        if len(urls) >= MAX_LOCALES:
            break
    return urls


def _job_location_type(card) -> str | None:
    for node in card.css(".workType"):
        if node.css_first(".lni-laptop") is not None:
            return node.text(separator=" ", strip=True).strip() or None
    return None


def _schedule(card) -> list[str]:
    raw = card.attributes.get("data-filter-workschedule")
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _language(url: str) -> str | None:
    locale = (parse_qs(urlparse(url).query).get("lang") or [None])[0]
    return locale.split("-", 1)[0].lower() if isinstance(locale, str) and locale else None


def _parse_locale_page(page: str, page_url: str) -> list[DiscoveredJob]:
    tree = LexborHTMLParser(page)
    details = {
        node.attributes.get("data-jobdetail-job-id"): node
        for node in tree.css("[data-jobdetail-job-id]")
        if node.attributes.get("data-jobdetail-job-id")
    }
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()

    for card in tree.css("[data-item-collection]"):
        link = card.css_first("[data-jobid][href]")
        if link is None:
            continue
        job_id = link.attributes.get("data-jobid") or ""
        if not job_id.isdigit() or job_id in seen:
            continue
        seen.add(job_id)

        detail = details.get(job_id)
        title_node = detail.css_first("h2.job-title") if detail is not None else None
        location_node = (
            detail.css_first(".additional-data .location") if detail is not None else None
        )
        description_node = detail.css_first(".jobDescriptionText") if detail is not None else None
        title = title_node.text(separator=" ", strip=True).strip() if title_node is not None else ""
        location = (
            location_node.text(separator=" ", strip=True).strip()
            if location_node is not None
            else ""
        )
        description = description_node.inner_html.strip() if description_node is not None else ""
        if not title or not location or not description:
            raise ValueError(f"CVWarehouse job {job_id} is missing required rich fields")

        linked_job_url = urljoin(page_url, link.attributes.get("href") or "")
        parsed_job_url = urlparse(linked_job_url)
        parsed_page_url = urlparse(page_url)
        linked_id = (parse_qs(parsed_job_url.query).get("job") or [None])[0]
        if parsed_job_url.hostname != parsed_page_url.hostname or linked_id != job_id:
            raise ValueError(f"CVWarehouse job {job_id} has an invalid detail URL")
        # ``lang``, ``section``, and the title-derived ``q`` value select one
        # publication of the same provider job and change across locales or
        # title edits.  CVWarehouse resolves the job-id-only route directly,
        # so persist that stable user-facing URL instead of churning postings.
        job_url = urlunparse(
            parsed_job_url._replace(
                query=urlencode({"job": job_id}),
                fragment="",
            )
        )

        schedule = _schedule(card)
        work_type = card.attributes.get("data-filter-worktype") or None
        brand_values = card.attributes.get("data-filter-attribute")
        metadata: dict[str, object] = {"job_id": job_id}
        if work_type:
            metadata["work_type"] = work_type
        if schedule:
            metadata["work_schedule"] = schedule
        if brand_values:
            try:
                metadata["brand"] = json.loads(brand_values)
            except json.JSONDecodeError:
                metadata["brand"] = brand_values

        employment_type = (
            "internship" if work_type == "Stagiair" else (schedule[0] if schedule else None)
        )
        jobs.append(
            DiscoveredJob(
                url=job_url,
                title=title,
                description=description,
                locations=[location],
                employment_type=employment_type,
                job_location_type=_job_location_type(card),
                language=_language(linked_job_url),
                metadata=metadata,
            )
        )
    return jobs


async def _fetch_page(url: str, client: httpx.AsyncClient) -> str:
    page = await fetch_page_text(url, client, max_chars=MAX_PAGE_CHARS)
    if page is None:
        raise ValueError(f"Failed to fetch CVWarehouse board {url!r}")
    return page


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch every localized vacancy and embedded detail from a CVWarehouse board."""
    _ = pw
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    landing = await _fetch_page(board_url, client)
    if not _is_provider_page(landing):
        raise ValueError(f"CVWarehouse markers not found at {board_url!r}")

    section = metadata.get("section")
    advertised = metadata.get("jobs")
    if not isinstance(section, str):
        detected = _section_from_page(landing)
        if detected is None:
            raise ValueError(f"CVWarehouse all-vacancies section not found at {board_url!r}")
        section, advertised = detected

    primary_url = _section_url(board_url, section)
    primary_page = await _fetch_page(primary_url, client)
    pages = [(primary_url, primary_page)]
    for locale_url in _locale_urls(primary_page, primary_url)[1:]:
        pages.append((locale_url, await _fetch_page(locale_url, client)))

    by_id: dict[str, DiscoveredJob] = {}
    for page_url, page in pages:
        for job in _parse_locale_page(page, page_url):
            job_id = str((job.metadata or {})["job_id"])
            by_id.setdefault(job_id, job)

    jobs = list(by_id.values())
    if isinstance(advertised, int) and len(jobs) != advertised:
        raise ValueError(
            f"CVWarehouse advertised {advertised} jobs but exposed {len(jobs)} localized jobs"
        )
    if len(jobs) > MAX_JOBS:
        raise ValueError(f"CVWarehouse result exceeds safety cap of {MAX_JOBS}")
    log.info("cvwarehouse.discovered", board_url=board_url, jobs=len(jobs), locales=len(pages))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect hosted CVWarehouse pages and their unfiltered section."""
    _ = pw
    if client is None:
        return {"host": urlparse(url).hostname} if _is_cvw_host(url) else None
    page = await fetch_page_text(url, client, max_chars=MAX_PAGE_CHARS)
    if page is None or not _is_provider_page(page):
        return None
    detected = _section_from_page(page)
    if detected is None:
        return None
    section, jobs = detected
    return {"section": section, "jobs": jobs}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    section = metadata.get("section")
    url = _section_url(board_url, section) if isinstance(section, str) else board_url
    await save_text_response(
        artifact_dir,
        client,
        url,
        filename="cvwarehouse.html",
        follow_redirects=True,
    )


register(
    "cvwarehouse",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    save_raw=save_raw,
)
