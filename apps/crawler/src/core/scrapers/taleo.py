"""Oracle Taleo Enterprise job-detail scraper.

Taleo Enterprise renders job details from a bounded ``api.fillList`` payload
embedded in the public detail page.  The payload is available over plain HTTP,
so parsing it directly avoids launching a browser for every posting.

This is distinct from Taleo Business Edition (``*.tbe.taleo.net``), whose
detail pages expose JSON-LD and continue to use the JSON-LD scraper.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

_MAX_HTML_CHARS = 2_000_000
_MAX_VALUES = 128
_MAX_VALUE_CHARS = 1_000_000
_FILL_LIST_MARKER_RE = re.compile(
    r"api\.fillList\(\s*['\"]requisitionDescriptionInterface['\"]\s*,\s*"
    r"['\"]descRequisition['\"]\s*,\s*"
)
_DETAIL_PATH_RE = re.compile(
    r"^/careersection/[a-z0-9_-]{1,64}/jobdetail\.ftl$",
    re.IGNORECASE,
)
_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$", re.IGNORECASE)
_WORKPLACE_RE = re.compile(r"#LI[-_ ]?(hybrid|remote|on[- ]?site)", re.IGNORECASE)


def _detail_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    job = parse_qs(parsed.query).get("job", [""])[0]
    return bool(
        parsed.scheme == "https"
        and host.endswith(".taleo.net")
        and not host.endswith(".tbe.taleo.net")
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and _DETAIL_PATH_RE.fullmatch(parsed.path)
        and _JOB_ID_RE.fullmatch(job)
    )


def _decode_escape(source: str, index: int) -> tuple[str, int]:
    """Decode one bounded JavaScript string escape at *index*."""
    char = source[index]
    simple = {
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "0": "\0",
        "\\": "\\",
        "'": "'",
        '"': '"',
    }
    if char in simple:
        return simple[char], index + 1
    if char in {"x", "u"}:
        digits = 2 if char == "x" else 4
        raw = source[index + 1 : index + 1 + digits]
        if len(raw) != digits or not all(value in "0123456789abcdefABCDEF" for value in raw):
            raise ValueError("Taleo fillList contains an invalid hexadecimal escape")
        return chr(int(raw, 16)), index + 1 + digits
    if char in {"\n", "\r"}:
        next_index = index + 1
        if char == "\r" and next_index < len(source) and source[next_index] == "\n":
            next_index += 1
        return "", next_index
    # JavaScript treats an unknown escaped character as the character itself.
    return char, index + 1


def _parse_fill_list(html: str) -> list[str] | None:
    """Return the Taleo ``descRequisition`` string array, if present.

    The page is untrusted input, so this uses a small bounded parser instead of
    evaluating JavaScript or a Python literal.
    """
    if len(html) > _MAX_HTML_CHARS:
        raise ValueError("Taleo detail page exceeded the HTML safety cap")
    marker = _FILL_LIST_MARKER_RE.search(html)
    if marker is None:
        return None

    index = marker.end()
    if index >= len(html) or html[index] != "[":
        return None
    index += 1
    values: list[str] = []

    while index < len(html):
        while index < len(html) and html[index].isspace():
            index += 1
        if index < len(html) and html[index] == "]":
            return values
        if len(values) >= _MAX_VALUES:
            raise ValueError("Taleo fillList exceeded the value safety cap")
        if index >= len(html) or html[index] not in {"'", '"'}:
            raise ValueError("Taleo fillList contains a non-string value")

        quote = html[index]
        index += 1
        chars: list[str] = []
        while index < len(html):
            char = html[index]
            if char == quote:
                index += 1
                break
            if char == "\\":
                index += 1
                if index >= len(html):
                    raise ValueError("Taleo fillList ends inside an escape")
                decoded, index = _decode_escape(html, index)
                chars.append(decoded)
            else:
                chars.append(char)
                index += 1
            if len(chars) > _MAX_VALUE_CHARS:
                raise ValueError("Taleo fillList value exceeded the safety cap")
        else:
            raise ValueError("Taleo fillList contains an unterminated string")

        values.append("".join(chars))
        while index < len(html) and html[index].isspace():
            index += 1
        if index < len(html) and html[index] == ",":
            index += 1
            continue
        if index < len(html) and html[index] == "]":
            return values
        raise ValueError("Taleo fillList contains an invalid separator")

    raise ValueError("Taleo fillList is unterminated")


def _decoded_html(value: str) -> str | None:
    decoded = unquote(value.removeprefix("!*!")).strip()
    return unescape(decoded) or None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Parse one public Taleo Enterprise detail page."""
    _ = config
    values = _parse_fill_list(html)
    if values is None or len(values) < 29:
        return JobContent()

    title = unescape(unquote(values[9])).strip() or None
    # Most Enterprise tenants split the description and requirements across
    # slots 11 and 13. WIPO's forms keep those slots for organisation/grade
    # or department/duration and store the complete encoded posting in slot
    # 20 (internships) or 22 (staff).
    wipo_description_index = next(
        (index for index in (20, 22) if len(values) > index and values[index].startswith("!*!")),
        None,
    )
    wipo_layout = wipo_description_index is not None
    description_indexes = (
        (wipo_description_index,) if wipo_description_index is not None else (11, 13)
    )
    description_parts = [
        part for index in description_indexes if (part := _decoded_html(values[index])) is not None
    ]
    description = "\n".join(description_parts) or None
    location_index = wipo_description_index - 5 if wipo_description_index is not None else 17
    location = unescape(values[location_index]).strip() or None
    employment_type = None if wipo_layout else unescape(values[23]).strip() or None
    date_posted = None
    if wipo_description_index is not None:
        date_posted = unescape(values[wipo_description_index - 4]).strip() or None
    valid_through_index = wipo_description_index - 2 if wipo_description_index is not None else 27
    valid_through = unescape(values[valid_through_index]).strip() or None

    job_location_type = None
    if description and (workplace := _WORKPLACE_RE.search(description)):
        value = workplace.group(1).casefold().replace("-", "").replace(" ", "")
        job_location_type = "onsite" if value == "onsite" else value

    extras: dict[str, object] = {}
    requirements = None if wipo_layout else _decoded_html(values[13])
    if requirements:
        extras["qualifications"] = requirements
    if valid_through:
        extras["valid_through"] = valid_through

    metadata = {
        "ats_job_id": values[0],
        "requisition_number": values[10],
    }
    if wipo_layout:
        organisation = unescape(values[11]).strip()
        grade = unescape(values[12]).strip() if wipo_description_index == 22 else ""
        contract_duration_index = 14 if wipo_description_index == 22 else 12
        contract_duration = unescape(values[contract_duration_index]).strip()
        if organisation:
            metadata["organisation"] = organisation
        if grade:
            metadata["grade"] = grade
        if contract_duration:
            metadata["contract_duration"] = contract_duration
    else:
        business_area = unescape(values[15]).strip()
        organisation = unescape(values[21]).strip()
        if business_area:
            metadata["business_area"] = business_area
        if organisation:
            metadata["organisation"] = organisation

    return JobContent(
        title=title,
        description=description,
        locations=[location] if location else None,
        employment_type=employment_type,
        job_location_type=job_location_type,
        date_posted=date_posted,
        extras=extras or None,
        metadata=metadata,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Recognize Taleo Enterprise detail pages with usable public payloads."""
    matched = 0
    for html in htmls:
        try:
            content = parse_html(html)
        except ValueError:
            continue
        if content.title and content.description and content.locations:
            matched += 1
    return {} if matched and matched >= len(htmls) / 2 else None


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    """Fetch and parse one Taleo Enterprise detail page without a browser."""
    _ = config, kwargs
    if not _detail_url(url):
        raise ValueError(f"invalid Taleo Enterprise job URL: {url!r}")
    response = await fetch_response_with_status_retries(
        http,
        url,
        retry_limits={429: 2, 503: 2},
        log_event="taleo_enterprise.detail_retry",
    )
    response.raise_for_status()
    content = parse_html(response.text)
    if not content.title or not content.description:
        log.warning("taleo_enterprise.detail_missing", url=url)
    return content


register("taleo", scrape, can_handle=can_handle, parse_html=parse_html)
