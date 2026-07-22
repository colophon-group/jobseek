"""ADP Workforce Now detail scraper.

The public career-center listing endpoint returns useful job metadata but
omits ``requisitionDescription``.  The corresponding detail endpoint exposes
that field and, for some employers, points at a DOCX attachment instead of
embedding the job description.  This scraper handles both shapes without a
browser.

Pair it with an ``api_sniffer`` listing monitor and configure
``{"enrich": ["description"]}`` so rich listings are queued for detail
enrichment.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from urllib.parse import parse_qs, urlparse
from xml.etree.ElementTree import Element, ParseError

import httpx
import structlog
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from src.core.enum_normalize import normalize_employment_type, normalize_salary_unit
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_DETAIL_PATH = "/careercenter/public/events/staffing/v1/job-requisitions/{job_id}"
_DOCUMENT_PATH = "/careercenter/public/events/staffing/v1/work-fulfillment/documents/123"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ATTACHMENT_PLACEHOLDER_RE = re.compile(
    r"\b(?:see|refer to)\s+(?:the\s+)?attached\s+(?:job\s+)?description\b",
    re.IGNORECASE,
)
_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9._:-]+")
_MAX_JOB_URL_LENGTH = 4096
_MAX_DETAIL_BYTES = 2 * 1024 * 1024
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_DOCUMENT_XML_BYTES = 5 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 1024
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_WORD_NS}}}"


class _ResponseTooLarge(Exception):
    def __init__(self, *, limit: int, size: int) -> None:
        self.limit = limit
        self.size = size
        super().__init__(f"response exceeds {limit} bytes (at least {size} bytes)")


def _safe_token(value: str, *, max_length: int = 128) -> bool:
    return bool(value) and len(value) <= max_length and bool(_SAFE_TOKEN_RE.fullmatch(value))


def _parse_job_url(url: str) -> tuple[str, str, str, str, str] | None:
    """Return ``(base, job_id, cid, cc_id, locale)`` for an ADP job URL."""
    if len(url) > _MAX_JOB_URL_LENGTH:
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or hostname != "workforcenow.adp.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    try:
        params = parse_qs(parsed.query, max_num_fields=32)
    except ValueError:
        return None

    def _first(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values and values[0] else None

    job_id = _first("jobId") or _first("itemId")
    cid = _first("cid")
    cc_id = _first("ccId")
    locale = _first("lang") or _first("locale") or "en_US"
    if not all(
        isinstance(value, str) and _safe_token(value) for value in (job_id, cid, cc_id, locale)
    ):
        return None

    marker = "/mdf/recruitment/"
    if marker not in parsed.path:
        return None
    prefix = parsed.path.split(marker, 1)[0]
    if (
        len(prefix) > 256
        or (prefix and not prefix.startswith("/"))
        or "\\" in prefix
        or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
        or any(segment in {".", ".."} for segment in prefix.split("/"))
    ):
        return None
    port_suffix = ":443" if port == 443 else ""
    base = f"https://workforcenow.adp.com{port_suffix}{prefix}"
    return base, job_id, cid, cc_id, locale


async def _bounded_get(
    http: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    params: dict[str, str],
    headers: dict[str, str],
) -> bytes:
    """Stream a response into memory without crossing the configured limit."""
    async with http.stream("GET", url, params=params, headers=headers) as response:
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = -1
            if declared_size > max_bytes:
                raise _ResponseTooLarge(limit=max_bytes, size=declared_size)

        content = bytearray()
        async for chunk in response.aiter_bytes():
            next_size = len(content) + len(chunk)
            if next_size > max_bytes:
                raise _ResponseTooLarge(limit=max_bytes, size=next_size)
            content.extend(chunk)
        return bytes(content)


def _plain_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(_HTML_TAG_RE.sub(" ", value)).strip()


def _meaningful_inline_description(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    plain = " ".join(_plain_text(value).split())
    if not plain:
        return None
    if len(plain) <= 200 and _ATTACHMENT_PLACEHOLDER_RE.search(plain):
        return None
    return value


def _attachment_path(detail: dict) -> str | None:
    """Return ADP's document-store path for the first DOCX attachment."""
    links = detail.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        if str(link.get("targetSchema") or "").lower() != "docx":
            continue
        schema = link.get("schema")
        arguments = link.get("payLoadArguments")
        if not isinstance(schema, str) or not isinstance(arguments, list):
            continue
        for argument in arguments:
            if not isinstance(argument, dict):
                continue
            path = argument.get("argumentPath")
            if isinstance(path, str) and path:
                file_path = f"{path.rstrip('/')}/{schema}"
                if len(file_path) <= 2048 and all(
                    ord(character) >= 32 and ord(character) != 127 for character in file_path
                ):
                    return file_path
    return None


def _paragraph_text(paragraph: Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _paragraph_style(paragraph: Element) -> str:
    style = paragraph.find(f"./{_W}pPr/{_W}pStyle")
    return style.get(f"{_W}val", "") if style is not None else ""


def _is_list_paragraph(paragraph: Element) -> bool:
    return paragraph.find(f"./{_W}pPr/{_W}numPr") is not None


def _table_html(table: Element) -> str | None:
    rows: list[str] = []
    for row in table.findall(f"./{_W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"./{_W}tc"):
            text = " ".join(
                part
                for paragraph in cell.findall(f".//{_W}p")
                if (part := _paragraph_text(paragraph))
            )
            cells.append(f"<td>{html.escape(text)}</td>")
        if cells:
            rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows)}</table>" if rows else None


def _docx_to_html(content: bytes) -> str | None:
    """Convert the useful text structure in a DOCX document to basic HTML."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > _MAX_ARCHIVE_MEMBERS:
                return None
            if archive.getinfo("word/document.xml").file_size > _MAX_DOCUMENT_XML_BYTES:
                return None
            document_xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(document_xml)
    except (
        KeyError,
        OSError,
        ParseError,
        RuntimeError,
        NotImplementedError,
        DefusedXmlException,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        return None

    body = root.find(f".//{_W}body")
    if body is None:
        return None

    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items.clear()

    for child in body:
        if child.tag == f"{_W}p":
            text = _paragraph_text(child)
            if not text:
                flush_list()
                continue
            escaped = html.escape(text).replace("\n", "<br>")
            if _is_list_paragraph(child):
                list_items.append(f"<li>{escaped}</li>")
                continue
            flush_list()
            style = _paragraph_style(child).lower()
            if style.startswith("heading") or style in {"title", "subtitle"}:
                blocks.append(f"<h3>{escaped}</h3>")
            else:
                blocks.append(f"<p>{escaped}</p>")
        elif child.tag == f"{_W}tbl":
            flush_list()
            table = _table_html(child)
            if table:
                blocks.append(table)

    flush_list()
    return "\n".join(blocks) or None


def _parse_locations(detail: dict) -> list[str] | None:
    values: list[str] = []
    for location in detail.get("requisitionLocations") or []:
        if not isinstance(location, dict):
            continue
        name_code = location.get("nameCode") or {}
        value = name_code.get("shortName") if isinstance(name_code, dict) else None
        if isinstance(value, str):
            value = " ".join(value.split())
            value = re.sub(r"\s+,", ",", value)
        if value and value not in values:
            values.append(value)
    return values or None


def _parse_salary(detail: dict) -> dict | None:
    pay_range = detail.get("payGradeRange")
    if not isinstance(pay_range, dict):
        return None
    minimum = pay_range.get("minimumRate") or {}
    maximum = pay_range.get("maximumRate") or {}
    sal_min = minimum.get("amountValue") if isinstance(minimum, dict) else None
    sal_max = maximum.get("amountValue") if isinstance(maximum, dict) else None
    if sal_min is None and sal_max is None:
        return None

    currency = None
    if isinstance(minimum, dict):
        currency = minimum.get("currencyCode")
    if not currency and isinstance(maximum, dict):
        currency = maximum.get("currencyCode")

    unit = None
    custom = detail.get("customFieldGroup")
    if not isinstance(custom, dict):
        custom = {}
    for field in custom.get("codeFields") or []:
        if not isinstance(field, dict):
            continue
        name_code = field.get("nameCode") or {}
        if isinstance(name_code, dict) and name_code.get("codeValue") == "SalaryType":
            unit = normalize_salary_unit(field.get("shortName") or field.get("codeValue"))
            break

    return {"currency": currency, "min": sal_min, "max": sal_max, "unit": unit}


def _parse_metadata(detail: dict) -> dict | None:
    metadata: dict = {}
    for source, target in (
        ("clientRequisitionID", "requisition_id"),
        ("itemID", "item_id"),
    ):
        value = detail.get(source)
        if value:
            metadata[target] = value

    custom = detail.get("customFieldGroup")
    if not isinstance(custom, dict):
        custom = {}
    for field in custom.get("stringFields") or []:
        if not isinstance(field, dict):
            continue
        name_code = field.get("nameCode") or {}
        code = name_code.get("codeValue") if isinstance(name_code, dict) else None
        value = field.get("stringValue")
        if code == "ExternalJobID" and value:
            metadata["external_job_id"] = value
        elif code == "JobClass" and value:
            metadata["job_class"] = value
    return metadata or None


async def _attachment_description(
    detail: dict,
    *,
    base: str,
    params: dict[str, str],
    locale: str,
    http: httpx.AsyncClient,
) -> str | None:
    file_path = _attachment_path(detail)
    if not file_path:
        return None
    headers = {
        "filePath": file_path,
        "isAbsolutePath": "true",
        "isAttachmentType": "true",
        "locale": locale,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        content = await _bounded_get(
            http,
            f"{base}{_DOCUMENT_PATH}",
            max_bytes=_MAX_ATTACHMENT_BYTES,
            params=params,
            headers=headers,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {404, 410}:
            raise
        log.warning(
            "adp_scraper.attachment_missing",
            status=exc.response.status_code,
            error=str(exc),
        )
        return None
    except _ResponseTooLarge as exc:
        log.warning(
            "adp_scraper.attachment_too_large",
            bytes=exc.size,
            limit=exc.limit,
        )
        return None
    description = _docx_to_html(content)
    if not description:
        log.warning("adp_scraper.attachment_invalid", bytes=len(content))
    return description


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    **kwargs,
) -> JobContent:
    """Fetch and parse an ADP Workforce Now requisition detail record."""
    parsed = _parse_job_url(url)
    if parsed is None:
        log.warning("adp_scraper.unparseable_url", url=url)
        return JobContent()
    base, job_id, cid, cc_id, locale = parsed
    configured_locale = config.get("locale")
    if configured_locale is not None:
        if not isinstance(configured_locale, str) or not _safe_token(configured_locale):
            log.warning("adp_scraper.invalid_locale", locale=configured_locale)
            return JobContent()
        locale = configured_locale
    params = {"cid": cid, "ccId": cc_id, "lang": locale, "locale": locale}

    try:
        detail_content = await _bounded_get(
            http,
            f"{base}{_DETAIL_PATH.format(job_id=job_id)}",
            max_bytes=_MAX_DETAIL_BYTES,
            params=params,
            headers={"Accept": "application/json"},
        )
    except _ResponseTooLarge as exc:
        log.warning("adp_scraper.detail_too_large", bytes=exc.size, limit=exc.limit, url=url)
        return JobContent()
    try:
        detail = json.loads(detail_content)
    except ValueError:
        log.warning("adp_scraper.bad_json", url=url)
        return JobContent()
    if not isinstance(detail, dict):
        return JobContent()

    description = _meaningful_inline_description(detail.get("requisitionDescription"))
    if description is None:
        description = await _attachment_description(
            detail,
            base=base,
            params={"cid": cid, "ccId": cc_id, "lang": locale},
            locale=locale,
            http=http,
        )

    work_level = detail.get("workLevelCode") or {}
    employment_type = normalize_employment_type(
        work_level.get("shortName") if isinstance(work_level, dict) else None,
    )

    title = detail.get("requisitionTitle")
    date_posted = detail.get("postDate")
    return JobContent(
        title=title if isinstance(title, str) and title else None,
        description=description,
        locations=_parse_locations(detail),
        employment_type=employment_type,
        date_posted=date_posted if isinstance(date_posted, str) and date_posted else None,
        base_salary=_parse_salary(detail),
        metadata=_parse_metadata(detail),
    )


register("adp", scrape)
