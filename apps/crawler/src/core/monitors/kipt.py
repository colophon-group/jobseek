"""NSC KIPT vacancy bulletin monitor.

The Kharkiv Institute of Physics and Technology publishes vacancies as
dated PDF bulletins.  Each bulletin can contain multiple positions, so the
normal DOM-monitor + PDF-scraper pipeline cannot model the source: URL-only
scrapers return exactly one posting per document.

This monitor reads only unexpired bulletins, splits their published vacancy
lines into individual rich jobs, and gives each position a stable synthetic
URL derived from the source PDF and the full vacancy line.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
import pypdf
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register
from src.shared.tdm import check_response as check_tdm_response

log = structlog.get_logger()

_DEFAULT_MAX_AGE_DAYS = 30
_DEFAULT_LOCATION = "Kharkiv, Ukraine"
_BULLETIN_PATH_RE = re.compile(
    r"/news/\d{4}/vacancy_(\d{1,2})_(\d{1,2})_(\d{4})\.pdf$",
    re.IGNORECASE,
)
_VACANCY_BLOCK_RE = re.compile(
    r"на\s+заміщення\s+вакантн(?:ої|их)\s+посад[и]?:\s*(?P<body>.+?)(?=^\s*Вимоги\s+до\s+кандидатів\s*:)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_VACANCY_RE = re.compile(
    r"^\s*[-–]\s*(?P<title>.+?)\s+[-–]\s+(?P<count>\d+)\s+ваканс(?:ія|ії|ій)\b(?P<details>.*?)[;.]\s*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_COMMON_DETAILS_RE = re.compile(
    r"^\s*Вимоги\s+до\s+кандидатів\s*:",
    re.IGNORECASE | re.MULTILINE,
)


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, value in attrs:
            if key == "href" and value:
                self.hrefs.append(value)


def _bulletin_date(url: str) -> date | None:
    match = _BULLETIN_PATH_RE.search(urlparse(url).path)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _active_bulletins(
    board_url: str,
    page_html: str,
    *,
    today: date,
    max_age_days: int,
) -> list[tuple[str, date]]:
    bulletins = _bulletin_links(board_url, page_html)

    active: dict[str, date] = {}
    for url, posted in bulletins:
        age_days = (today - posted).days
        if 0 <= age_days <= max_age_days:
            active[url] = posted
    return sorted(active.items())


def _bulletin_links(board_url: str, page_html: str) -> list[tuple[str, date]]:
    parser = _LinkExtractor()
    parser.feed(page_html)

    bulletins: dict[str, date] = {}
    for href in parser.hrefs:
        url = urljoin(board_url, href)
        posted = _bulletin_date(url)
        if posted is None:
            continue
        bulletins[url] = posted
    return sorted(bulletins.items())


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_to_html(value: str) -> str:
    paragraphs = [
        _normalize_text(paragraph) for paragraph in re.split(r"\n\s*\n", value) if paragraph.strip()
    ]
    return "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


def _synthetic_url(pdf_url: str, vacancy_text: str) -> str:
    identifier = hashlib.sha256(_normalize_text(vacancy_text).casefold().encode()).hexdigest()[:12]
    parsed = urlparse(pdf_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["_jid"] = [identifier]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _parse_bulletin(
    pdf_url: str,
    text: str,
    posted: date,
    location: str,
) -> list[DiscoveredJob]:
    block_match = _VACANCY_BLOCK_RE.search(text)
    if not block_match:
        log.warning("kipt.vacancy_block_missing", url=pdf_url)
        return []

    common_match = _COMMON_DETAILS_RE.search(text, block_match.end())
    common_details = text[common_match.start() :] if common_match else ""

    jobs: list[DiscoveredJob] = []
    for match in _VACANCY_RE.finditer(block_match.group("body")):
        title = _normalize_text(match.group("title"))
        vacancy_text = _normalize_text(match.group(0))
        if not title:
            continue
        description = _text_to_html(
            f"{vacancy_text}\n\n{common_details}" if common_details else vacancy_text
        )
        jobs.append(
            DiscoveredJob(
                url=_synthetic_url(pdf_url, vacancy_text),
                title=title,
                description=description,
                locations=[location],
                date_posted=posted.isoformat(),
                language="uk",
                metadata={
                    "source_pdf": pdf_url,
                    "vacancy_count": int(match.group("count")),
                },
            )
        )
    return jobs


async def _pdf_text(url: str, client: httpx.AsyncClient) -> str:
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()
    check_tdm_response(response)
    reader = pypdf.PdfReader(io.BytesIO(response.content))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()


async def can_handle(
    url: str,
    client: httpx.AsyncClient,
    pw=None,
) -> dict | None:
    parsed = urlparse(url)
    if (parsed.hostname or "").lower().removeprefix("www.") != "kipt.kharkov.ua":
        return None
    if parsed.path.rstrip("/").lower() not in {"/ua/vacancy.html", "/vacancy.html"}:
        return None

    page_html = await fetch_page_text(url, client)
    if not page_html or not _bulletin_links(url, page_html):
        return None
    return {
        "max_age_days": _DEFAULT_MAX_AGE_DAYS,
        "default_location": _DEFAULT_LOCATION,
    }


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    max_age_days = int(metadata.get("max_age_days", _DEFAULT_MAX_AGE_DAYS))
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    location = str(metadata.get("default_location") or _DEFAULT_LOCATION)

    page_html = await fetch_page_text(board_url, client)
    if page_html is None:
        raise RuntimeError(f"KIPT vacancy page unavailable: {board_url}")

    bulletins = _active_bulletins(
        board_url,
        page_html,
        today=date.today(),
        max_age_days=max_age_days,
    )
    jobs: list[DiscoveredJob] = []
    for pdf_url, posted in bulletins:
        text = await _pdf_text(pdf_url, client)
        parsed = _parse_bulletin(pdf_url, text, posted, location)
        if not parsed:
            raise ValueError(f"No vacancies parsed from active KIPT bulletin: {pdf_url}")
        jobs.extend(parsed)

    log.info("kipt.discovered", bulletins=len(bulletins), jobs=len(jobs))
    return jobs


register("kipt", discover, cost=60, can_handle=can_handle, rich=True)
