"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``src.shared.extract`` to pull
structured fields from the HTML.

By default (``render: false``), fetches the page via static HTTP.  Set
``render: true`` to render with Playwright for JS-heavy sites.

Config uses ``steps`` (same format as ``walk_steps``) plus optional browser
lifecycle keys (``wait``, ``timeout``, ``user_agent``, ``headless``, ``actions``)
which are only used when rendering.

Requires playwright when ``render`` is true:
``uv sync --group dev && uv run playwright install chromium``
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions
from src.shared.extract import flatten, walk_steps

log = structlog.get_logger()

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
    raw = walk_steps(elements, steps)
    return _map_to_job_content(raw)


# ── Core extraction ───────────────────────────────────────────────────


def _map_to_job_content(raw: dict[str, str | list[str] | None]) -> JobContent:
    """Map extraction result dict to a ``JobContent`` dataclass."""
    kwargs: dict[str, object] = {}
    metadata: dict[str, object] = {}

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
            "valid_through",
        ):
            kwargs[key] = value
        elif key == "location" or key == "locations":
            kwargs["locations"] = [value] if isinstance(value, str) else value
        elif key in ("qualifications", "responsibilities", "skills"):
            kwargs[key] = [value] if isinstance(value, str) else value
        else:
            metadata[key] = value

    if metadata:
        kwargs["metadata"] = metadata

    return JobContent(**kwargs)


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

    if render:
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}

        async def _render_page(p):
            async with open_page(p, browser_config) as page:
                await navigate(page, url, browser_config)
                await run_actions(page, browser_config.get("actions", []))
                return await page.content()

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
        resp.raise_for_status()
        html = resp.text

    elements = flatten(html)

    if artifact_dir is not None:
        with contextlib.suppress(Exception):
            (artifact_dir / "flat.json").write_text(
                json.dumps(elements, indent=2, ensure_ascii=False),
            )

    raw = walk_steps(elements, steps)
    content = _map_to_job_content(raw)

    log.debug("dom.extracted", url=url, fields=[k for k, v in raw.items() if v is not None])
    return content


register("dom", scrape, can_handle=can_handle, parse_html=parse_html)
