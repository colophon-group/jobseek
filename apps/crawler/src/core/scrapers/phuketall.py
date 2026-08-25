"""PhuketAll employer-board detail scraper.

PhuketAll serves stable job-detail HTML, but the field labels on its canonical
Thai URLs differ from the English text exposed by search indexes.  Parsing the
provider's structural classes keeps title and description extraction independent
of those translated labels.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.scrapers import JobContent, register
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry

_PROVIDER_HOST = "www.phuketall.com"
_DETAIL_PATH_RE = re.compile(
    r"^/(?:en/)?jobs/(?P<employer>[0-9]{6})-(?P<job>[0-9]+)-phuket/[^/?#]+[.]html$",
    re.IGNORECASE,
)
_MAX_DETAIL_BYTES = 2 * 1024 * 1024
_DETAIL_TIMEOUT_S = 30.0
_MAX_REDIRECTS = 2
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_THAI_MONTHS = {
    "ม.ค.": 1,
    "ก.พ.": 2,
    "มี.ค.": 3,
    "เม.ย.": 4,
    "พ.ค.": 5,
    "มิ.ย.": 6,
    "ก.ค.": 7,
    "ส.ค.": 8,
    "ก.ย.": 9,
    "ต.ค.": 10,
    "พ.ย.": 11,
    "ธ.ค.": 12,
}
_EMPLOYMENT_TYPES = {
    "งานประจำ": "full_time",
    "full time": "full_time",
    "full-time": "full_time",
    "งานพาร์ทไทม์": "part_time",
    "part time": "part_time",
    "part-time": "part_time",
}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _details(tree: LexborHTMLParser) -> dict[str, str]:
    nodes = tree.css(".detailsbox > div")
    details: dict[str, str] = {}
    for index in range(0, len(nodes) - 1, 2):
        label = _clean(nodes[index].text(separator=" ", strip=True)).rstrip(":：")
        value = _clean(nodes[index + 1].text(separator=" ", strip=True))
        if label and value:
            details[label] = value
    return details


def _detail(details: dict[str, str], *labels: str) -> str | None:
    return next((details[label] for label in labels if details.get(label)), None)


def _parse_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d{1,2})\s+(\S+)\s+(\d{2,4})", value.strip())
    if match and match.group(2) in _THAI_MONTHS:
        year = int(match.group(3))
        year = year + 1957 if year < 100 else year - 543
        try:
            return date(year, _THAI_MONTHS[match.group(2)], int(match.group(1))).isoformat()
        except ValueError:
            return None
    for pattern in ("%d %b %y", "%d %B %Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _description_node(tree: LexborHTMLParser) -> LexborNode | None:
    candidates = tree.css(".jobs-derails-content > .col-md-12")
    substantive = [
        node for node in candidates if len(_clean(node.text(separator=" ", strip=True))) >= 20
    ]
    return max(
        substantive,
        key=lambda node: len(node.text(separator=" ", strip=True)),
        default=None,
    )


def _trusted_detail_identity(url: str) -> tuple[str, str] | None:
    """Return the exact provider identity for a trusted PhuketAll detail URL."""
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
    if not trusted_origin or parsed.query or parsed.fragment:
        return None
    match = _DETAIL_PATH_RE.fullmatch(parsed.path)
    if match is None:
        return None
    return match.group("employer"), match.group("job")


def _document_detail_identity(html: str) -> tuple[str, str] | None:
    """Read the exact provider-owned ``og:url`` identity from a detail page."""
    tree = LexborHTMLParser(html)
    markers = [node.attributes.get("content") for node in tree.css('meta[property="og:url"]')]
    if not markers or any(not isinstance(marker, str) for marker in markers):
        return None
    identities = {_trusted_detail_identity(marker) for marker in markers if marker is not None}
    if None in identities or len(identities) != 1:
        return None
    return next(iter(identities))


async def _fetch_detail(url: str, http: httpx.AsyncClient) -> str:
    expected_identity = _trusted_detail_identity(url)
    if expected_identity is None:
        raise ValueError("PhuketAll detail URL has an untrusted origin or identity")

    current_url = url
    for redirect_count in range(_MAX_REDIRECTS + 1):
        try:
            html = await fetch_text_page_with_retry(
                http,
                current_url,
                follow_redirects=False,
                end_of_pagination_statuses=(),
                require_nonempty=True,
                max_bytes=_MAX_DETAIL_BYTES,
                timeout=_DETAIL_TIMEOUT_S,
            )
        except PaginationFetchError as exc:
            if exc.last_status not in _REDIRECT_STATUSES:
                raise
            if not exc.last_location:
                raise ValueError("PhuketAll detail redirect omitted Location") from exc
            redirected = urljoin(current_url, exc.last_location)
            if _trusted_detail_identity(redirected) != expected_identity:
                raise ValueError("PhuketAll detail returned an untrusted redirect") from exc
            if redirect_count == _MAX_REDIRECTS:
                raise ValueError("PhuketAll detail exceeded the trusted redirect cap") from exc
            current_url = redirected
            continue

        if html is None:  # pragma: no cover - strict status handling above raises
            raise ValueError("PhuketAll detail response is empty")
        if _document_detail_identity(html) != expected_identity:
            raise ValueError("PhuketAll detail response has an untrusted provider identity")
        return html
    raise RuntimeError("unreachable PhuketAll redirect loop")


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one PhuketAll job detail page in either Thai or English."""
    _ = config
    tree = LexborHTMLParser(html)

    title_node = tree.css_first(".title-feedbox h2 span")
    title = _clean(title_node.text(strip=True)) if title_node is not None else None

    description_node = _description_node(tree)
    description_inner = description_node.inner_html if description_node is not None else None
    description = f"<p>{description_inner.strip()}</p>" if description_inner else None

    location = None
    for node in tree.css(".jobs-derails-content p"):
        text = _clean(node.text(separator=" ", strip=True))
        if re.search(r"(?:ภูเก็ต|Phuket).*\b\d{5}\b", text, re.IGNORECASE):
            location = text
            break

    details = _details(tree)
    employment_raw = _detail(details, "เวลาทำงาน", "Working Time")
    employment_type = _EMPLOYMENT_TYPES.get((employment_raw or "").casefold())
    date_posted = _parse_date(_detail(details, "ลงประกาศเมื่อ", "Post Date"))
    metadata = {
        key: value
        for key, value in (
            ("department", _detail(details, "แผนก", "Department")),
            ("quantity", _detail(details, "จำนวน", "Quantity")),
            ("degree", _detail(details, "ระดับการศึกษา", "Degree")),
        )
        if value
    }

    return JobContent(
        title=title,
        description=description,
        locations=[location] if location else None,
        employment_type=employment_type,
        date_posted=date_posted,
        language="en",
        metadata=metadata or None,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect PhuketAll's stable employer job-detail markup."""
    if htmls and all(
        _document_detail_identity(html) is not None
        and LexborHTMLParser(html).css_first(".jobs-derails-content") is not None
        and LexborHTMLParser(html).css_first(".title-feedbox") is not None
        for html in htmls
    ):
        return {}
    return None


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch a complete trusted detail document under a finite response cap."""
    _ = kwargs
    return parse_html(await _fetch_detail(url, http), config)


register("phuketall", scrape, can_handle=can_handle, parse_html=parse_html)
