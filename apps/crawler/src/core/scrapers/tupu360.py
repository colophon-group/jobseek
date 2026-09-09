"""Tupu360 (图谱天下) server-rendered job-detail scraper."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser

from src.core.scrapers import JobContent, register
from src.shared.http_retry import fetch_text_page_with_retry

_PROVIDER_HOST = "careersite.tupu360.com"
_DETAIL_PATH_RE = re.compile(r"^/[a-z0-9_-]+/position/detail$", re.IGNORECASE)
_POSITION_ID_RE = re.compile(r"^[a-f0-9]{24}$", re.IGNORECASE)
_MAX_DETAIL_BYTES = 2 * 1024 * 1024
_MARKERS = (
    "cdn.careersite.tupu360.com",
    'id="positionName"',
    'id="sourcePid"',
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _position_id(url: str) -> str | None:
    """Return the posting identity from an exact public Tupu360 detail URL."""

    try:
        parsed = urlparse(url)
        trusted_origin = (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() == _PROVIDER_HOST
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
        )
    except ValueError:
        return None
    if not trusted_origin or _DETAIL_PATH_RE.fullmatch(parsed.path) is None or parsed.fragment:
        return None
    values = parse_qs(parsed.query).get("positionId", [])
    if len(values) != 1 or _POSITION_ID_RE.fullmatch(values[0]) is None:
        return None
    return values[0].lower()


def _field(tree: LexborHTMLParser, label: str) -> str | None:
    for group in tree.css("dl.position-extend"):
        key = group.css_first("dt")
        value = group.css_first("dd")
        if key is None or value is None:
            continue
        normalized = _clean(key.text(separator=" ", strip=True)).rstrip(":：")
        if normalized == label:
            text = _clean(value.text(separator=" ", strip=True))
            return text or None
    return None


def _document_position_id(tree: LexborHTMLParser) -> str | None:
    marker = tree.css_first("#positionInfoInp")
    value = marker.attributes.get("data-id") if marker is not None else None
    if isinstance(value, str) and _POSITION_ID_RE.fullmatch(value):
        return value.lower()
    return None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one complete Tupu360 detail document."""

    _ = config
    tree = LexborHTMLParser(html)
    title_input = tree.css_first("#positionName")
    title_value = title_input.attributes.get("value") if title_input is not None else None
    title = _clean(title_value) if isinstance(title_value, str) else None
    if not title:
        title_node = tree.css_first("#positionTitleNode h5 .txt")
        title = _clean(title_node.text(strip=True)) if title_node is not None else None

    description_node = tree.css_first(".position-description")
    description = description_node.inner_html.strip() if description_node is not None else None
    if description is not None and not _clean(description):
        description = None

    location = _field(tree, "工作地点")
    date_posted = _field(tree, "发布时间")
    source = tree.css_first("#sourcePid")
    source_id = source.attributes.get("value") if source is not None else None
    recruitment = tree.css_first("#recruitmentType")
    recruitment_type = recruitment.attributes.get("value") if recruitment is not None else None
    metadata = {
        key: value
        for key, value in (
            ("position_id", _document_position_id(tree)),
            ("source_id", source_id),
            ("recruitment_type", recruitment_type),
        )
        if value
    }

    return JobContent(
        title=title,
        description=description,
        locations=[location] if location else None,
        date_posted=date_posted,
        language="zh",
        metadata=metadata or None,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect Tupu360 detail pages without matching unrelated Chinese portals."""

    matches = 0
    for html in htmls:
        if not all(marker in html for marker in _MARKERS):
            continue
        content = parse_html(html)
        if content.title and content.description and content.locations:
            matches += 1
    if matches and matches >= len(htmls) / 2:
        return {}
    return None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch a trusted Tupu360 detail URL and validate its returned identity."""

    _ = kwargs
    expected_id = _position_id(url)
    if expected_id is None:
        raise ValueError("Tupu360 scraper requires a trusted public detail URL")
    html = await fetch_text_page_with_retry(
        http,
        url,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=_MAX_DETAIL_BYTES,
    )
    if html is None:  # pragma: no cover - strict status handling above raises
        raise ValueError("Tupu360 detail response is empty")
    content = parse_html(html, config)
    actual_id = (content.metadata or {}).get("position_id")
    if actual_id != expected_id:
        raise ValueError("Tupu360 detail response identity does not match its URL")
    if not content.title or not content.description or not content.locations:
        raise ValueError("Tupu360 detail omitted a required job field")
    return content


register("tupu360", scrape, can_handle=can_handle, parse_html=parse_html)
