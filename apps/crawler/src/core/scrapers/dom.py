"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``src.shared.extract`` to pull
structured fields from the HTML.

By default (``render: false``), fetches the page via static HTTP.  Set
``render: true`` to render with Playwright for JS-heavy sites.

Config uses ``steps`` (same format as ``walk_steps``) plus optional browser
lifecycle keys (``wait``, ``timeout``, ``user_agent``, ``headless``, ``actions``)
which are only used when rendering.

Optional ``gone_url_pattern`` is a regex matched against the FINAL URL after
all redirects. When the upstream site redirects archived/removed postings to
a generic error page (e.g. L'Oréal redirects to ``/jobs/Error``), matching
that pattern raises ``httpx.HTTPStatusError(410)`` so the scrape pipeline
classifies the posting as ``permanent_gone`` and tombstones it on the first
failure instead of cycling through three "empty extraction" transient
backoffs that strand the row at ``next_scrape_at IS NULL``. See issue #2963.

Requires playwright when ``render`` is true:
``uv sync --group dev && uv run playwright install chromium``
"""

from __future__ import annotations

import asyncio
import contextlib
import html as html_lib
import io
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup
from PIL import Image

from src.core.scrapers import JobContent, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content
from src.shared.extract import flatten, walk_steps

log = structlog.get_logger()


def _check_gone_redirect(final_url: str, pattern: str | None, source_url: str) -> None:
    """Raise ``httpx.HTTPStatusError(410)`` if the final URL after redirects
    matches the configured ``gone_url_pattern`` regex.

    Called from both the render and static-HTTP code paths so any final URL
    landing on the upstream "this posting is gone" page is classified as
    permanent_gone by ``_is_permanent_gone`` in ``processing/scrape.py``.

    Generic by design: the pattern lives in the per-board scraper config so
    no host-specific code is added. Boards opt in by setting
    ``gone_url_pattern`` in their dom scraper config (see boards.csv).
    """
    if not pattern or not final_url:
        return
    try:
        if not re.search(pattern, final_url):
            return
    except re.error:
        log.warning(
            "dom.gone_url_pattern.invalid_regex",
            url=source_url,
            pattern=pattern,
        )
        return
    log.info(
        "dom.gone_redirect",
        url=source_url,
        final_url=final_url,
        pattern=pattern,
    )
    # Synthesise a 410 response so _is_permanent_gone() returns True. The
    # request URL is the original posting URL; the response URL is the
    # error page we landed on after redirects.
    request = httpx.Request("GET", source_url)
    response = httpx.Response(410, request=request, text="gone")
    raise httpx.HTTPStatusError(
        f"redirected to gone URL {final_url!r}",
        request=request,
        response=response,
    )


# ── Heuristic stop markers ────────────────────────────────────────────

_STOP_MARKERS = [
    "Apply",
    "Requirements",
    "Qualifications",
    "Back",
    "Submit",
    "Similar",
    "Share",
    "Related",
]


def _heuristic_steps(elements: list[dict]) -> list[dict] | None:
    """Generate heuristic extraction steps from flattened elements."""
    if not elements:
        return None

    # Find first h1 — title
    h1_idx = None
    for i, el in enumerate(elements):
        if el["tag"] == "h1":
            h1_idx = i
            break

    if h1_idx is None:
        return None

    steps: list[dict] = [{"tag": "h1", "field": "title"}]

    # Description: content after h1, stop at known marker
    desc_step: dict = {
        "tag": "h1",
        "offset": 1,
        "field": "description",
        "html": True,
        "optional": True,
    }

    # Look for a stop marker in elements after h1
    for i in range(h1_idx + 1, len(elements)):
        text = elements[i]["text"]
        for marker in _STOP_MARKERS:
            if marker.lower() in text.lower() and len(text) < 60:
                desc_step["stop"] = marker
                break
        if "stop" in desc_step:
            break

    # If no stop marker found, use stop_count based on remaining content
    if "stop" not in desc_step:
        remaining = len(elements) - h1_idx - 1
        desc_step["stop_count"] = min(remaining, 50)

    steps.append(desc_step)

    # Location: look for an element with "location" in its text
    for el in elements:
        text_lower = el["text"].lower()
        if "location" in text_lower and len(el["text"]) < 40:
            steps.append(
                {
                    "text": "Location",
                    "offset": 1,
                    "field": "location",
                    "optional": True,
                    "from": 0,
                }
            )
            break

    return steps


def can_handle(htmls: list[str]) -> dict | None:
    """Generate heuristic extraction steps from multiple page HTMLs.

    Analyzes all pages and returns steps that work across the collection.
    Uses the first page's structure to generate steps, then validates
    that the title step (h1) matches on other pages too.
    """
    # Try each page until we get usable steps
    best_steps = None

    for html in htmls:
        elements = flatten(html)
        if not elements:
            continue
        steps = _heuristic_steps(elements)
        if steps:
            best_steps = steps
            break

    if not best_steps:
        return None

    # Validate h1 exists on other pages too (title step consistency)
    h1_found = 0
    for html in htmls:
        elements = flatten(html)
        if any(el["tag"] == "h1" for el in elements):
            h1_found += 1

    # Require h1 on at least half the pages
    if h1_found < len(htmls) / 2:
        return None

    return {"steps": best_steps}


def parse_html(html: str, config: dict) -> JobContent:
    """Extract job data from pre-fetched HTML using step-based extraction."""
    steps = config.get("steps")
    if not steps:
        return JobContent()
    elements = flatten(html)
    raw, _ = walk_steps(elements, steps)
    return _map_to_job_content(raw)


def _fragment_start(url: str, elements: list[dict]) -> int:
    """Return the element index matching the URL fragment, or 0."""
    fragment = urlparse(url).fragment
    if not fragment:
        return 0
    for i, el in enumerate(elements):
        if el.get("attrs", {}).get("id") == fragment:
            return i
    return 0


# ── Core extraction ───────────────────────────────────────────────────


def _map_to_job_content(raw: dict[str, str | list[str] | None]) -> JobContent:
    """Map extraction result dict to a ``JobContent`` dataclass."""
    kwargs: dict[str, object] = {}
    metadata: dict[str, object] = {}
    extras: dict[str, object] = {}

    for key, value in raw.items():
        if value is None:
            continue
        if key.startswith("metadata."):
            metadata[key.removeprefix("metadata.")] = value
        elif key in (
            "title",
            "description",
            "employment_type",
            "job_location_type",
            "date_posted",
        ):
            kwargs[key] = value
        elif key == "location" or key == "locations":
            kwargs["locations"] = [value] if isinstance(value, str) else value
        elif key in ("qualifications", "responsibilities", "skills"):
            extras[key] = [value] if isinstance(value, str) else value
        elif key == "valid_through":
            extras["valid_through"] = value
        else:
            metadata[key] = value

    if metadata:
        kwargs["metadata"] = metadata
    if extras:
        kwargs["extras"] = extras

    return JobContent(**kwargs)


def _image_urls(html: str, page_url: str, config: dict) -> list[str]:
    """Return unique image URLs selected for OCR.

    OCR is deliberately opt-in and selector-driven.  Generic pages contain
    logos, tracking pixels, and decorative photography; scanning all images
    would waste CPU and pollute descriptions with unrelated text.
    """
    selector = config.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return []

    try:
        nodes = BeautifulSoup(html, "html.parser").select(selector)
    except Exception as exc:
        log.warning("dom.image_ocr.invalid_selector", selector=selector, error=str(exc))
        return []

    max_images = config.get("max_images", 5)
    if not isinstance(max_images, int) or isinstance(max_images, bool):
        max_images = 5
    max_images = max(1, min(max_images, 20))

    urls: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        src = node.get("src") or node.get("data-src")
        if not isinstance(src, str) or not src.strip():
            continue
        absolute = urljoin(page_url, src.strip())
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
        if len(urls) >= max_images:
            break
    return urls


async def _ocr_image(data: bytes, *, language: str, psm: int, timeout: float) -> str:
    """Extract text from one image with the system Tesseract binary."""
    try:
        process = await asyncio.create_subprocess_exec(
            "tesseract",
            "stdin",
            "stdout",
            "-l",
            language,
            "--psm",
            str(psm),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "image_ocr requires the tesseract binary; install the tesseract-ocr package"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(data), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"tesseract failed with exit code {process.returncode}: {detail}")
    return stdout.decode("utf-8", errors="replace").strip()


def _scale_ocr_image(data: bytes, scale: int) -> bytes:
    """Upscale small flyer text before OCR while preserving aspect ratio."""
    if scale == 1:
        return data
    with Image.open(io.BytesIO(data)) as image:
        resized = image.resize(
            (image.width * scale, image.height * scale),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        resized.save(output, format="PNG")
        return output.getvalue()


def _ocr_text_to_html(text: str, *, join_lines: bool = False) -> str:
    """Convert OCR output to safe, readable HTML paragraphs."""
    if join_lines:
        collapsed = " ".join(text.split())
        return f"<p>{html_lib.escape(collapsed)}</p>" if collapsed else ""

    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return "\n".join(f"<p>{html_lib.escape(paragraph)}</p>" for paragraph in paragraphs)


async def _ocr_selected_images(
    html: str,
    page_url: str,
    config: dict,
    http: httpx.AsyncClient,
    artifact_dir: Path | None,
    *,
    artifact_prefix: str,
) -> str:
    """Fetch and OCR one selector-defined group of images."""
    urls = _image_urls(html, page_url, config)
    if not urls:
        log.warning("dom.image_ocr.no_images", url=page_url, selector=config.get("selector"))
        return ""

    language = config.get("language", "eng")
    if not isinstance(language, str) or not language.strip():
        language = "eng"
    psm = config.get("psm", 6)
    if not isinstance(psm, int) or isinstance(psm, bool) or not 0 <= psm <= 13:
        psm = 6
    scale = config.get("scale", 1)
    if not isinstance(scale, int) or isinstance(scale, bool) or not 1 <= scale <= 4:
        scale = 1
    timeout = config.get("timeout", 30)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = 30
    max_bytes = config.get("max_bytes", 15_000_000)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        max_bytes = 15_000_000

    texts: list[str] = []
    for index, image_url in enumerate(urls, start=1):
        try:
            response = await http.get(image_url, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning(
                "dom.image_ocr.fetch_failed",
                url=page_url,
                image_url=image_url,
                error=str(exc),
            )
            continue
        content_type = response.headers.get("content-type", "").lower()
        if content_type and "image" not in content_type:
            log.warning(
                "dom.image_ocr.not_image",
                url=page_url,
                image_url=image_url,
                content_type=content_type,
            )
            continue
        if len(response.content) > max_bytes:
            log.warning(
                "dom.image_ocr.too_large",
                url=page_url,
                image_url=image_url,
                bytes=len(response.content),
                max_bytes=max_bytes,
            )
            continue
        if artifact_dir is not None:
            with contextlib.suppress(Exception):
                (artifact_dir / f"{artifact_prefix}-image-{index}.bin").write_bytes(
                    response.content
                )
        try:
            ocr_data = await asyncio.to_thread(_scale_ocr_image, response.content, scale)
            text = await _ocr_image(
                ocr_data,
                language=language,
                psm=psm,
                timeout=float(timeout),
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            log.warning(
                "dom.image_ocr.failed",
                url=page_url,
                image_url=image_url,
                error=str(exc),
            )
            continue
        if text:
            texts.append(text)

    full_text = "\n\n".join(texts).strip()
    if artifact_dir is not None and full_text:
        with contextlib.suppress(Exception):
            (artifact_dir / f"{artifact_prefix}.txt").write_text(full_text, encoding="utf-8")
    return full_text


async def _extract_image_ocr(
    html: str,
    page_url: str,
    config: dict,
    http: httpx.AsyncClient,
    artifact_dir: Path | None,
) -> tuple[str, list[str] | None]:
    """OCR configured images and return description HTML plus locations."""
    full_text = await _ocr_selected_images(
        html,
        page_url,
        config,
        http,
        artifact_dir,
        artifact_prefix="ocr",
    )

    locations = None
    location_pattern = config.get("location_pattern")
    location_text = full_text
    location_selector = config.get("location_selector")
    if isinstance(location_selector, str) and location_selector:
        location_config = {
            **config,
            "selector": location_selector,
            "psm": config.get("location_psm", config.get("psm", 6)),
            "scale": config.get("location_scale", config.get("scale", 1)),
            "max_images": config.get("location_max_images", 1),
        }
        location_text = await _ocr_selected_images(
            html,
            page_url,
            location_config,
            http,
            artifact_dir,
            artifact_prefix="ocr-location",
        )

    if location_text and isinstance(location_pattern, str) and location_pattern:
        try:
            matches = re.findall(location_pattern, location_text)
        except re.error as exc:
            log.warning(
                "dom.image_ocr.invalid_location_pattern",
                url=page_url,
                pattern=location_pattern,
                error=str(exc),
            )
        else:
            extracted = [match if isinstance(match, str) else match[0] for match in matches]
            locations = list(dict.fromkeys(value.strip() for value in extracted if value.strip()))
            if not locations:
                locations = None

    description_text = full_text
    min_line_length = config.get("description_min_line_length", 1)
    if (
        isinstance(min_line_length, int)
        and not isinstance(min_line_length, bool)
        and min_line_length > 1
    ):
        description_text = "\n".join(
            line
            for line in description_text.splitlines()
            if len(re.sub(r"\W", "", line, flags=re.UNICODE)) >= min_line_length
        )
    description_pattern = config.get("description_pattern")
    if description_text and isinstance(description_pattern, str) and description_pattern:
        try:
            match = re.search(description_pattern, description_text)
        except re.error as exc:
            log.warning(
                "dom.image_ocr.invalid_description_pattern",
                url=page_url,
                pattern=description_pattern,
                error=str(exc),
            )
        else:
            if match:
                description_text = match.group(1) if match.lastindex else match.group(0)

    return _ocr_text_to_html(
        description_text,
        join_lines=config.get("description_join_lines") is True,
    ), locations


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    artifact_dir: Path | None = None,
) -> JobContent:
    """Extract job data using step-based extraction.

    When ``render`` is false (default), fetches via static HTTP.
    When ``render`` is true, renders the page with Playwright.
    """
    steps = config.get("steps")
    if not steps:
        log.warning("dom.no_steps", url=url)
        return JobContent()

    render = config.get("render", False)

    if not render and config.get("actions"):
        log.warning(
            "dom.misconfiguration",
            url=url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    gone_pattern = config.get("gone_url_pattern")

    if render:
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
        use_proxy = bool(config.get("proxy"))

        async def _render_page(p):
            async with open_page(p, browser_config, use_proxy=use_proxy) as page:
                await navigate(page, url, browser_config)
                # Read final URL BEFORE running actions/extraction so a
                # redirect-to-gone page doesn't burn the (potentially
                # paid-proxy) action pipeline against a known dead page.
                final_url = ""
                with contextlib.suppress(Exception):
                    final_url = page.url or ""
                _check_gone_redirect(final_url, gone_pattern, url)
                await run_actions(page, browser_config.get("actions", []))
                return await safe_content(page)

        if pw is not None:
            html = await _render_page(pw)
        else:
            try:
                from playwright.async_api import async_playwright
            except ImportError as err:
                raise RuntimeError(
                    "playwright is required for the dom scraper. "
                    "Install with: uv sync --group dev && uv run playwright install chromium"
                ) from err

            async with async_playwright() as p:
                html = await _render_page(p)
    else:
        resp = await http.get(url, follow_redirects=True)
        # Detect redirect-to-gone BEFORE raise_for_status so the error page's
        # 200 doesn't shadow the actual archived signal. The redirect chain
        # may end on a 200 (rendered "this posting was removed" page), so
        # status alone never reveals gone-ness on these hosts.
        _check_gone_redirect(str(resp.url), gone_pattern, url)
        resp.raise_for_status()
        html = resp.text

    elements = flatten(html)

    if artifact_dir is not None:
        with contextlib.suppress(Exception):
            (artifact_dir / "flat.json").write_text(
                json.dumps(elements, indent=2, ensure_ascii=False),
            )

    start = _fragment_start(url, elements)
    raw, _ = walk_steps(elements, steps, start=start)
    content = _map_to_job_content(raw)

    image_ocr = config.get("image_ocr")
    if isinstance(image_ocr, dict):
        ocr_description, ocr_locations = await _extract_image_ocr(
            html,
            url,
            image_ocr,
            http,
            artifact_dir,
        )
        if ocr_description:
            mode = image_ocr.get("description_mode", "replace")
            if mode == "append" and content.description:
                content.description = f"{content.description}\n{ocr_description}"
            else:
                content.description = ocr_description
        if ocr_locations:
            content.locations = ocr_locations

    log.debug("dom.extracted", url=url, fields=[k for k, v in raw.items() if v is not None])
    return content


register("dom", scrape, can_handle=can_handle, parse_html=parse_html)
