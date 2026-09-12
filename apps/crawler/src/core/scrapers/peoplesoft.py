"""Oracle PeopleSoft Candidate Gateway detail scraper."""

from __future__ import annotations

import re
from html import escape

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.enum_normalize import normalize_employment_type, normalize_job_location_type
from src.core.monitors.peoplesoft import fetch_peoplesoft_page, peoplesoft_job_from_url
from src.core.salary_extract import parse_salary_text
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_WORKPLACE_RE = re.compile(r"\b(remote|hybrid|on[ -]?site)\b", re.IGNORECASE)


def _node_text(document: LexborHTMLParser, selector: str) -> str | None:
    node = document.css_first(selector)
    if node is None:
        return None
    value = " ".join(node.text(strip=True).split())
    return value or None


def parse_detail(page: str, *, expected_job_id: str | None = None) -> JobContent:
    document = LexborHTMLParser(page)
    job_id = _node_text(document, "#HRS_SCH_WRK2_HRS_JOB_OPENING_ID")
    title = _node_text(document, "#HRS_SCH_WRK2_POSTING_TITLE")
    if not job_id or not title or (expected_job_id is not None and job_id != expected_job_id):
        raise ValueError("PeopleSoft detail response omitted or changed the requested job identity")

    sections: list[str] = []
    qualification_parts: list[str] = []
    responsibility_parts: list[str] = []
    salary_text: str | None = None
    for row in document.css("div[id^='win0divHRS_SCH_PSTDSC_row$']"):
        heading_node = row.css_first("h2 .ps-text")
        content_node = row.css_first("span[id^='HRS_SCH_PSTDSC_DESCRLONG$']")
        if heading_node is None or content_node is None:
            continue
        heading = " ".join(heading_node.text(strip=True).split())
        content = (content_node.inner_html or "").strip()
        if not heading or not content:
            continue
        section = f"<h2>{escape(heading)}</h2>\n{content}"
        sections.append(section)
        folded = heading.casefold()
        if "qualification" in folded:
            qualification_parts.append(section)
        if "what your job will be like" in folded or "responsibilit" in folded:
            responsibility_parts.append(section)
        if "salary" in folded:
            salary_text = content_node.text(strip=True)
    if not sections:
        raise ValueError("PeopleSoft detail response omitted all description sections")

    raw_workplace = None
    workplace_match = _WORKPLACE_RE.search(title)
    if workplace_match is not None:
        raw_workplace = workplace_match.group(1)
    employment = _node_text(document, "#HRS_SCH_WRK_HRS_FULL_PART_TIME")
    regular_temporary = _node_text(document, "#HRS_SCH_WRK_HRS_REG_TEMP")
    extras = {
        key: "\n".join(parts)
        for key, parts in {
            "qualifications": qualification_parts,
            "responsibilities": responsibility_parts,
        }.items()
        if parts
    }
    metadata = {
        key: value
        for key, value in {
            "job_id": job_id,
            "regular_or_temporary": regular_temporary,
        }.items()
        if value
    }
    return JobContent(
        title=title,
        description="\n".join(sections),
        locations=(
            [location] if (location := _node_text(document, "#HRS_SCH_WRK_HRS_DESCRLONG")) else None
        ),
        employment_type=normalize_employment_type(employment),
        job_location_type=normalize_job_location_type(raw_workplace),
        # Candidate Gateway labels this provider field as an annual salary
        # range but omits the period from the numeric value itself.
        base_salary=parse_salary_text(f"{salary_text} per year") if salary_text else None,
        extras=extras or None,
        metadata=metadata,
    )


async def can_handle(url: str, client: httpx.AsyncClient | None = None) -> dict | None:
    _ = client
    return {} if peoplesoft_job_from_url(url) is not None else None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    _ = config, kwargs
    identity = peoplesoft_job_from_url(url)
    if identity is None:
        log.error("peoplesoft_scraper.invalid_job_url", url=url)
        return JobContent()
    board, job_id = identity
    page = await fetch_peoplesoft_page(board, board.detail_url(job_id), http)
    return parse_detail(page, expected_job_id=job_id)


register("peoplesoft", scrape, can_handle=can_handle)
