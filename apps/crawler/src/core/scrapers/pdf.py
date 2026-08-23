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
    location_pattern
                   Optional regex with a capture group for the location,
                   applied to the raw PDF text.
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
"""

from __future__ import annotations

import asyncio
import io
import math
import re
from pathlib import Path
from urllib.parse import unquote

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_MAX_OCR_PAGES = 20
_MAX_OCR_SCALE = 4
_MAX_OCR_PIXELS = 30_000_000
_OCR_LANGUAGES_RE = re.compile(r"[A-Za-z0-9_+-]{1,64}")


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
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()

    if artifact_dir:
        (artifact_dir / "source.pdf").write_bytes(response.content)

    return await parse_bytes(response.content, url, config)


async def parse_bytes(content: bytes, url: str, config: dict) -> JobContent:
    """Extract job data from already-downloaded PDF bytes.

    Keeping binary parsing separate from transport lets other static scrapers
    safely delegate PDF responses without downloading the same document twice.
    """
    import pypdf

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
        log.warning("pdf.empty", url=url)
        return JobContent(title=_title_from_url(url, config.get("title_pattern")))

    repair_split_initial = bool(config.get("repair_split_initial"))
    named_fields = _extract_named_fields(
        full_text,
        config.get("fields_pattern"),
        repair_split_initial=repair_split_initial,
    )

    # Title extraction — configurable via title_source
    title_source = config.get("title_source", "url")
    title_pattern = config.get("title_pattern")

    title = named_fields.get("title")
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
    description = _text_to_html(full_text)

    log.debug("pdf.extracted", url=url, title=title, text_length=len(full_text))
    return JobContent(
        title=title,
        description=description,
        locations=[location] if location else None,
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
