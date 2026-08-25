"""PDF scraper — extracts job data from PDF documents.

Downloads PDF files and extracts text content. Used for companies that host
job descriptions as PDF files (e.g. on Webflow CDN) rather than HTML pages.

Config:
    title_source   "url" (default) | "text"
                   "url"  — derive title from the PDF filename
                   "text" — use first short line from PDF text, fall back to URL
    title_pattern  Optional regex with a capture group for the title.
                   When title_source is "url", applied to the PDF filename
                   (after URL-decoding and hash stripping).
                   When title_source is "text", applied to the raw PDF text
                   before falling back to the heading-line heuristic.
    require_title_pattern
                   Opt in to fail-closed title extraction. Requires
                   title_source="text" and a non-empty title_pattern; raises
                   when that pattern does not match instead of falling back to
                   a generic heading or the PDF filename.
    location_pattern
                   Optional regex with a capture group for the location,
                   applied to the raw PDF text.
    location_url_pattern
                   Optional fallback regex with a capture group for the
                   location, applied to the URL-decoded PDF filename when the
                   PDF text does not yield a location.
    fields_pattern Optional regex with named ``title`` and/or ``location``
                   capture groups. This is useful for table-like PDFs where
                   both values must be matched from the same row layout.
                   Named values take precedence; title_pattern and
                   location_pattern remain field-specific fallbacks.
    repair_split_initial
                   Opt in to joining a capital initial split from the rest of
                   its word by a PDF extraction newline (M\\nechanical).
    ocr            Opt in to OCR when the PDF has no extractable text.
    ocr_languages  Tesseract language expression (default: "eng").
    ocr_scale      Integer PDF render scale from 1 to 4 (default: 2).
                   OCR is limited to 20 pages and 30 million pixels per page.
    defaults       Missing-only defaults for JobContent fields. Useful for a
                   board whose PDFs omit a location that is authoritative at
                   board level; extracted values always win. Types, canonical
                   enums, ISO dates, and structured salary shapes are validated.
    request_headers
                   Optional request headers for the PDF download. Useful for
                   origins that reject the shared browser-like User-Agent but
                   allow an explicit crawler identity.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import math
import re
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.public_request_headers import public_get, validated_public_request_headers
from src.shared.response_fingerprint import (
    MAX_RESPONSE_FINGERPRINT_BYTES,
    extract_response_fingerprint_url,
    response_fingerprint_token,
    response_fingerprint_validators,
    same_origin_response,
)

log = structlog.get_logger()

_MAX_OCR_PAGES = 20
_MAX_OCR_SCALE = 4
_MAX_OCR_PIXELS = 30_000_000
_OCR_LANGUAGES_RE = re.compile(r"[A-Za-z0-9_+-]{1,64}")
_DEFAULT_FIELDS = frozenset(JobContent.__slots__)
_DEFAULT_STRING_FIELDS = frozenset({"title", "description", "date_posted", "language"})
_EMPLOYMENT_TYPES = frozenset(
    {"full_time", "part_time", "contract", "internship", "temporary", "volunteer", "full_or_part"}
)
_JOB_LOCATION_TYPES = frozenset({"onsite", "remote", "hybrid"})
_SALARY_UNITS = frozenset({"year", "month", "week", "day", "hour"})
_SALARY_FIELDS = frozenset({"currency", "min", "max", "unit"})


def _validate_salary_default(value: object) -> None:
    """Validate the canonical salary shape accepted by the processing pipeline."""
    if not isinstance(value, dict) or not value or set(value) - _SALARY_FIELDS:
        raise ValueError("PDF defaults.base_salary must use currency, min, max, and unit fields")
    currency = value.get("currency")
    if currency is not None and (
        not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None
    ):
        raise ValueError("PDF defaults.base_salary.currency must be an ISO 4217 code")
    unit = value.get("unit")
    if unit is not None and unit not in _SALARY_UNITS:
        raise ValueError("PDF defaults.base_salary.unit must be a canonical salary unit")
    amounts = [value.get("min"), value.get("max")]
    if all(amount is None for amount in amounts):
        raise ValueError("PDF defaults.base_salary must contain min or max")
    if any(
        amount is not None
        and (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
            or not math.isfinite(amount)
            or amount < 0
        )
        for amount in amounts
    ):
        raise ValueError("PDF defaults.base_salary min and max must be finite non-negative numbers")
    minimum, maximum = amounts
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("PDF defaults.base_salary min cannot exceed max")


def _validate_default(field: str, value: object) -> None:
    """Reject defaults that cannot inhabit the corresponding JobContent field."""
    if field in _DEFAULT_STRING_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"PDF defaults.{field} must be non-empty text")
        if field == "date_posted":
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("PDF defaults.date_posted must be an ISO date") from exc
        if field == "language" and re.fullmatch(r"[a-z]{2}", value) is None:
            raise ValueError("PDF defaults.language must be a lowercase ISO 639-1 code")
        return
    if field == "locations":
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(location, str) or not location.strip() for location in value)
        ):
            raise ValueError("PDF defaults.locations must be a non-empty list of strings")
        return
    if field == "employment_type":
        if value not in _EMPLOYMENT_TYPES:
            raise ValueError("PDF defaults.employment_type must be a canonical value")
        return
    if field == "job_location_type":
        if value not in _JOB_LOCATION_TYPES:
            raise ValueError("PDF defaults.job_location_type must be onsite, remote, or hybrid")
        return
    if field == "base_salary":
        _validate_salary_default(value)
        return
    if field in {"extras", "metadata"} and not isinstance(value, dict):
        raise ValueError(f"PDF defaults.{field} must be an object")


def _apply_defaults(content: JobContent, config: dict) -> JobContent:
    """Fill missing PDF fields from explicit board-scoped defaults."""
    defaults = config.get("defaults")
    if defaults is None:
        return content
    if not isinstance(defaults, dict) or any(field not in _DEFAULT_FIELDS for field in defaults):
        raise ValueError("PDF defaults must contain only JobContent fields")
    for field, value in defaults.items():
        _validate_default(field, value)
        if getattr(content, field) in (None, "", []):
            setattr(content, field, value)
    return content


async def _download_verified_fingerprinted_pdf(
    url: str,
    http: httpx.AsyncClient,
    *,
    request_headers: dict[str, str] | None = None,
) -> bytes | None:
    """Download a synthetic PDF URL and bind its bytes to the monitor identity.

    Regular PDF URLs return ``None`` and retain the legacy transport path. A
    URL carrying the private fingerprint parameter is accepted only when its
    GET response exposes the exact validator set used to derive that token.
    """
    extracted = extract_response_fingerprint_url(url)
    if extracted is None:
        return None
    source_url, expected_token = extracted

    response = await same_origin_response(
        http,
        "GET",
        url,
        stream=True,
        headers=request_headers,
    )
    try:
        validators = response_fingerprint_validators(
            response,
            expected_content_type="application/pdf",
            source_url=source_url,
        )
        actual_token = response_fingerprint_token(source_url, validators)
        if not hmac.compare_digest(actual_token, expected_token):
            raise ValueError("PDF response validators no longer match its discovered fingerprint")

        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > MAX_RESPONSE_FINGERPRINT_BYTES:
                raise ValueError(
                    f"PDF fingerprinted response exceeds {MAX_RESPONSE_FINGERPRINT_BYTES} bytes"
                )
            content.extend(chunk)
        if len(content) != validators.content_length:
            raise ValueError("PDF fingerprinted response body does not match Content-Length")
        if not content.lstrip().startswith(b"%PDF"):
            raise ValueError("PDF fingerprinted response is not a PDF document")
        return bytes(content)
    finally:
        await response.aclose()


def _normalize_captured_text(
    value: str,
    *,
    repair_split_initial: bool = False,
) -> str | None:
    """Collapse PDF layout whitespace in a captured scalar field.

    Some PDFs split a word after its first capital letter (for example,
    ``"M\nechanical"`` or ``"S enior"``). When explicitly requested, rejoin
    that ambiguous extraction artefact before collapsing the remaining
    whitespace.
    """
    # A hyphen immediately before a PDF layout newline is an unambiguous
    # continuation marker (``large-\nscale``), not a word boundary followed by
    # a space. Repair it for every captured scalar before whitespace collapse.
    value = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "-", value)
    if repair_split_initial:
        value = re.sub(
            r"\b([A-Z])(?:[ \t]+|[ \t]*\r?\n[ \t]*)(?=[^\W\d_])",
            r"\1",
            value,
        )
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _extract_pattern(
    text: str,
    pattern: str | None,
    *,
    repair_split_initial: bool = False,
) -> str | None:
    """Return a normalized first capture group from *pattern*."""
    if not pattern:
        return None
    match = re.search(pattern, text)
    if not match or not match.lastindex:
        return None
    return _normalize_captured_text(
        match.group(1),
        repair_split_initial=repair_split_initial,
    )


def _extract_named_fields(
    text: str,
    pattern: str | None,
    *,
    repair_split_initial: bool = False,
) -> dict[str, str]:
    """Return normalized title/location named groups from *pattern*."""
    if not pattern:
        return {}
    match = re.search(pattern, text)
    if not match:
        return {}

    fields: dict[str, str] = {}
    for field in ("title", "location"):
        if field not in match.re.groupindex:
            continue
        value = match.group(field)
        if value is None:
            continue
        normalized = _normalize_captured_text(
            value,
            repair_split_initial=repair_split_initial,
        )
        if normalized:
            fields[field] = normalized
    return fields


def _title_from_url(url: str, pattern: str | None = None) -> str | None:
    """Extract a plausible job title from the PDF filename."""
    path = unquote(url).rsplit("/", 1)[-1]
    name = re.sub(r"\.pdf$", "", path, flags=re.IGNORECASE)
    # Strip leading hex IDs (e.g. Webflow asset hashes like "69aee028...")
    name = re.sub(r"^[a-f0-9]{20,}_", "", name)
    if not name:
        return None
    # Apply pattern before character cleanup so it can match original separators
    if pattern:
        captured = _extract_pattern(name, pattern)
        if captured:
            captured = captured.replace("_", " ").replace("-", " ")
            return re.sub(r"\s+", " ", captured).strip() or None
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name if name else None


def _location_from_url(url: str, pattern: str | None = None) -> str | None:
    """Extract a location from the URL-decoded PDF filename."""
    if not pattern:
        return None
    path = unquote(url).split("?", 1)[0].split("#", 1)[0]
    filename = path.rsplit("/", 1)[-1]
    return _extract_pattern(filename, pattern)


def _title_from_text(text: str) -> str | None:
    """Extract a title from the first heading-like line of PDF text.

    Skips lines that are unlikely to be titles: bullets, very long lines
    (pypdf sometimes merges entire pages), and lines starting lowercase
    (sentence continuations).
    """
    lines_checked = 0
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) < 3:
            continue
        lines_checked += 1
        if lines_checked > 5:
            break
        if len(line) > 120:
            break
        if line[0] in "•·‣▪▸–*►" or line[0].islower():
            continue
        return line
    return None


def _text_to_html(text: str) -> str:
    """Convert plain text to simple HTML paragraphs."""
    paragraphs: list[str] = []
    current: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
        else:
            current.append(stripped)

    if current:
        paragraphs.append(" ".join(current))

    if not paragraphs:
        return ""

    return "\n".join(f"<p>{p}</p>" for p in paragraphs)


def _ocr_pdf(content: bytes, *, languages: str, scale: int) -> str:
    """Render and OCR a bounded image-only PDF."""
    import pypdfium2
    import pytesseract

    document = pypdfium2.PdfDocument(content)
    pages_text: list[str] = []
    try:
        page_count = len(document)
        if page_count > _MAX_OCR_PAGES:
            raise ValueError(f"PDF OCR is limited to {_MAX_OCR_PAGES} pages; got {page_count}")
        for page_index in range(page_count):
            page = document[page_index]
            try:
                width, height = page.get_size()
                if not (
                    math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0
                ):
                    raise ValueError(f"PDF OCR page {page_index + 1} has invalid dimensions")
                rendered_pixels = width * height * scale * scale
                if rendered_pixels > _MAX_OCR_PIXELS:
                    raise ValueError(
                        f"PDF OCR page {page_index + 1} would render "
                        f"{rendered_pixels:.0f} pixels; limit is {_MAX_OCR_PIXELS}"
                    )
                bitmap = page.render(scale=scale)
                try:
                    text = pytesseract.image_to_string(bitmap.to_pil(), lang=languages)
                finally:
                    bitmap.close()
            finally:
                page.close()
            if text.strip():
                pages_text.append(text)
    finally:
        document.close()
    return "\n\n".join(pages_text).strip()


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    artifact_dir: Path | None = None,
    **kwargs,
) -> JobContent:
    """Extract job data from a PDF document.

    Downloads the PDF, extracts text with pypdf, and maps to JobContent.
    Title source is controlled by config (default: URL filename).
    """
    request_headers = validated_public_request_headers(
        config.get("request_headers"), owner="PDF scraper"
    )
    content = await _download_verified_fingerprinted_pdf(
        url,
        http,
        request_headers=request_headers or None,
    )
    if content is None:
        if request_headers:
            response = await public_get(http, url, headers=request_headers)
        else:
            response = await http.get(url, follow_redirects=True)
        response.raise_for_status()
        content = response.content

    if artifact_dir:
        (artifact_dir / "source.pdf").write_bytes(content)

    return await parse_bytes(content, url, config)


async def parse_bytes(content: bytes, url: str, config: dict) -> JobContent:
    """Extract job data from already-downloaded PDF bytes.

    Keeping binary parsing separate from transport lets other static scrapers
    safely delegate PDF responses without downloading the same document twice.
    """
    import pypdf

    title_source = config.get("title_source", "url")
    title_pattern = config.get("title_pattern")
    require_title_pattern = config.get("require_title_pattern", False)
    if not isinstance(require_title_pattern, bool):
        raise ValueError("PDF require_title_pattern must be a boolean")
    if require_title_pattern and (
        title_source != "text" or not isinstance(title_pattern, str) or not title_pattern
    ):
        raise ValueError(
            'PDF require_title_pattern requires title_source="text" and a title_pattern'
        )

    reader = pypdf.PdfReader(io.BytesIO(content))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    full_text = "\n\n".join(pages_text).strip()

    if not full_text and config.get("ocr"):
        languages = str(config.get("ocr_languages", "eng")).strip()
        if not _OCR_LANGUAGES_RE.fullmatch(languages):
            raise ValueError("PDF ocr_languages must be a non-empty Tesseract language expression")
        try:
            scale = int(config.get("ocr_scale", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("PDF ocr_scale must be an integer from 1 to 4") from exc
        if not 1 <= scale <= _MAX_OCR_SCALE:
            raise ValueError("PDF ocr_scale must be an integer from 1 to 4")
        full_text = await asyncio.to_thread(
            _ocr_pdf,
            content,
            languages=languages,
            scale=scale,
        )
        log.info("pdf.ocr", url=url, text_length=len(full_text))

    if not full_text:
        if require_title_pattern:
            raise ValueError("PDF title_pattern did not match extracted text")
        log.warning("pdf.empty", url=url)
        return _apply_defaults(JobContent(title=_title_from_url(url, title_pattern)), config)

    repair_split_initial = bool(config.get("repair_split_initial"))
    named_fields = _extract_named_fields(
        full_text,
        config.get("fields_pattern"),
        repair_split_initial=repair_split_initial,
    )

    # Title extraction — configurable via title_source
    title = named_fields.get("title")
    if require_title_pattern:
        required_title = _extract_pattern(
            full_text,
            title_pattern,
            repair_split_initial=repair_split_initial,
        )
        if not required_title:
            raise ValueError("PDF title_pattern did not match extracted text")
        if not title:
            title = required_title
    if not title and title_source == "text":
        title = _extract_pattern(
            full_text,
            title_pattern,
            repair_split_initial=repair_split_initial,
        )
        if not title:
            title = _title_from_text(full_text) or _title_from_url(url, title_pattern)
    elif not title:
        title = _title_from_url(url, title_pattern)

    # Keep the legacy location_pattern normalization unchanged. The split-
    # initial repair was historically title-only and can corrupt legitimate
    # locations such as "A Coruna" by turning them into "ACoruna". A named
    # fields_pattern opts into the shared repair behavior explicitly.
    location = named_fields.get("location") or _extract_pattern(
        full_text,
        config.get("location_pattern"),
    )
    if not location:
        location = _location_from_url(url, config.get("location_url_pattern"))
    description = _text_to_html(full_text)

    log.debug("pdf.extracted", url=url, title=title, text_length=len(full_text))
    return _apply_defaults(
        JobContent(
            title=title,
            description=description,
            locations=[location] if location else None,
        ),
        config,
    )


def can_handle(htmls: list[str]) -> dict | None:
    """Detect PDF content — checks if fetched data starts with the PDF magic header."""
    pdf_count = sum(1 for h in htmls if h.lstrip().startswith("%PDF"))
    if pdf_count > 0 and pdf_count >= len(htmls) / 2:
        return {}
    return None


def parse_html(html: str, config: dict | None = None) -> JobContent:
    """Stub for probe compatibility — real extraction requires binary download."""
    return JobContent()


register("pdf", scrape, can_handle=can_handle, parse_html=parse_html)
