"""Jina Reader scraper for pages that cannot be fetched from crawler egress.

The scraper is intentionally opt-in. It asks Jina Reader for the rendered
page's visible text, then extracts a title, location, and bounded description
using explicit board configuration. This is useful for origins protected by
interactive bot challenges where neither static HTTP nor Playwright is
reliable.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.ssrf import validate_request_url

log = structlog.get_logger()

_READER_BASE_URL = "https://r.jina.ai/"
_READER_HEADERS = {
    "Accept": "application/json",
    "X-Respond-With": "text",
}


def _clean_lines(text: str) -> list[str]:
    """Return non-empty visible-text lines with whitespace normalized."""
    return [
        cleaned
        for line in text.splitlines()
        if (cleaned := re.sub(r"\s+", " ", line).strip())
    ]


def _strip_title_suffix(title: str, suffix: str | None) -> str:
    if suffix and title.endswith(suffix):
        return title[: -len(suffix)].strip()
    return title.strip()


def _match_key(value: str) -> str:
    """Normalize punctuation differences between Reader title and body text."""
    return value.translate(str.maketrans({"–": "-", "—": "-", "’": "'"})).casefold()


def _find_line(lines: list[str], value: str, *, start: int = 0) -> int | None:
    needle = _match_key(value)
    for index in range(start, len(lines)):
        if _match_key(lines[index]) == needle:
            return index
    return None


def _paragraphs_html(lines: list[str]) -> str | None:
    if not lines:
        return None
    return "\n".join(f"<p>{html.escape(line)}</p>" for line in lines)


def parse_payload(payload: dict[str, Any], config: dict) -> JobContent:
    """Parse a Jina Reader JSON response into :class:`JobContent`."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return JobContent()

    raw_title = data.get("title")
    raw_text = data.get("text")
    if not isinstance(raw_title, str) or not isinstance(raw_text, str):
        return JobContent()

    title = _strip_title_suffix(raw_title, config.get("title_suffix"))
    lines = _clean_lines(raw_text)

    locations: list[str] | None = None
    if config.get("location_after_title"):
        title_index = _find_line(lines, title)
        if title_index is not None and title_index + 1 < len(lines):
            candidate = lines[title_index + 1]
            description_start = config.get("description_start")
            if not description_start or _match_key(candidate) != _match_key(str(description_start)):
                locations = [candidate]

    description: str | None = None
    description_start = config.get("description_start")
    if isinstance(description_start, str) and description_start:
        start_index = _find_line(lines, description_start)
        if start_index is not None:
            end_index = len(lines)
            description_stop = config.get("description_stop")
            if isinstance(description_stop, str) and description_stop:
                stop_index = _find_line(lines, description_stop, start=start_index + 1)
                if stop_index is not None:
                    end_index = stop_index
            description = _paragraphs_html(lines[start_index + 1 : end_index])

    return JobContent(
        title=title or None,
        description=description,
        locations=locations,
    )


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    """Fetch rendered visible text through Jina Reader and extract configured fields."""
    # The shared HTTP transport only sees r.jina.ai after URL rewriting. Guard
    # the original target explicitly so Reader cannot be used as an SSRF relay.
    validate_request_url(url)

    response = await http.get(
        f"{_READER_BASE_URL}{url}",
        headers=_READER_HEADERS,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    content = parse_payload(payload, config)
    if not content.title or not content.description:
        raise ValueError(f"Reader response did not contain required job content for {url}")
    if config.get("require_location") and not content.locations:
        raise ValueError(f"Reader response did not contain a location for {url}")
    if content.title:
        log.debug("reader.extracted", url=url, title=content.title)
    else:
        log.warning("reader.not_found", url=url)
    return content


register("reader", scrape)
