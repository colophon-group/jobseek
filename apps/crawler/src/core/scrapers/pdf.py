"""PDF scraper — extracts job data from PDF documents.

Downloads PDF files and extracts text content. Used for companies that host
job descriptions as PDF files (e.g. on Webflow CDN) rather than HTML pages.

Config:
    title_source   "url" (default) | "text"
                   "url"  — derive title from the PDF filename
                   "text" — use first short line from PDF text, fall back to URL
    title_pattern  Optional regex applied to the PDF filename (after URL-decoding
                   and hash stripping). First capture group becomes the title.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import unquote

import httpx
import structlog

from src.core.scrapers import JobContent, register

log = structlog.get_logger()


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
        m = re.search(pattern, name)
        if m and m.lastindex:
            return m.group(1).replace("_", " ").strip() or None
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
    import pypdf

    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()

    if artifact_dir:
        (artifact_dir / "source.pdf").write_bytes(response.content)

    reader = pypdf.PdfReader(io.BytesIO(response.content))
    pages_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    full_text = "\n\n".join(pages_text).strip()

    if not full_text:
        log.warning("pdf.empty", url=url)
        return JobContent(title=_title_from_url(url, config.get("title_pattern")))

    # Title extraction — configurable via title_source
    title_source = config.get("title_source", "url")
    title_pattern = config.get("title_pattern")

    if title_source == "text":
        title = _title_from_text(full_text) or _title_from_url(url, title_pattern)
    else:
        title = _title_from_url(url, title_pattern)

    description = _text_to_html(full_text)

    log.debug("pdf.extracted", url=url, title=title, text_length=len(full_text))
    return JobContent(title=title, description=description)


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
