"""Bounded extraction for legacy binary Microsoft Word documents."""

from __future__ import annotations

import asyncio
import html
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from src.core.scrapers import JobContent
from src.core.scrapers.pdf import _apply_defaults, _extract_pattern, _title_from_text

_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
_MAX_TEXT_BYTES = 20 * 1024 * 1024
_ANTIWORD_TIMEOUT_SECONDS = 30


def _title_from_url(url: str) -> str | None:
    filename = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    title = re.sub(r"\.doc$", "", filename, flags=re.IGNORECASE)
    title = re.sub(r"[_-]+", " ", title)
    return re.sub(r"\s+", " ", title).strip() or None


def _text_to_html(text: str) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(" ".join(stripped.split()))
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)


async def _extract_text(content: bytes) -> str:
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise ValueError(f"legacy Word document exceeds {_MAX_DOCUMENT_BYTES} bytes")
    if not content.startswith(_OLE_MAGIC):
        raise ValueError("legacy Word fallback received an invalid OLE document")

    document_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as document:
            document.write(content)
            document_path = document.name
        process = await asyncio.create_subprocess_exec(
            "antiword",
            "-m",
            "UTF-8.txt",
            "-w",
            "0",
            document_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_ANTIWORD_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise ValueError("antiword timed out while parsing legacy Word document") from None
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise ValueError(f"antiword failed to parse legacy Word document: {detail}")
        if len(stdout) > _MAX_TEXT_BYTES:
            raise ValueError(f"legacy Word text exceeds {_MAX_TEXT_BYTES} bytes")
    except FileNotFoundError as exc:
        raise RuntimeError("antiword is required for legacy Word document fallback") from exc
    finally:
        if document_path is not None:
            Path(document_path).unlink(missing_ok=True)

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("legacy Word document contained no extractable text")
    return text


async def parse_bytes(content: bytes, url: str, config: dict) -> JobContent:
    """Extract a job from trusted, already-downloaded legacy Word bytes."""

    allowed_keys = {"title_source", "title_pattern", "location_pattern", "defaults"}
    if not isinstance(config, dict) or set(config) - allowed_keys:
        raise ValueError("legacy Word config contains unsupported fields")
    title_source = config.get("title_source", "url")
    if title_source not in {"url", "text"}:
        raise ValueError('legacy Word title_source must be "url" or "text"')
    for key in ("title_pattern", "location_pattern"):
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError(f"legacy Word {key} must be a non-empty regex")
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"legacy Word {key} is invalid: {exc}") from exc

    text = await _extract_text(content)
    title = _extract_pattern(text, config.get("title_pattern"))
    if title is None:
        title = _title_from_text(text) if title_source == "text" else _title_from_url(url)
    location = _extract_pattern(text, config.get("location_pattern"))
    return _apply_defaults(
        JobContent(
            title=title,
            description=_text_to_html(text),
            locations=[location] if location else None,
        ),
        config,
    )
