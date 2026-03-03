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

import json
from pathlib import Path

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions
from src.shared.extract import flatten, walk_steps

log = structlog.get_logger()


def _map_to_job_content(raw: dict[str, str | list[str] | None]) -> JobContent:
    """Map extraction result dict to a ``JobContent`` dataclass."""
    kwargs: dict[str, object] = {}
    metadata: dict[str, object] = {}

    for key, value in raw.items():
        if value is None:
            continue
        if key.startswith("metadata."):
            metadata[key.removeprefix("metadata.")] = value
        elif key in ("title", "description", "employment_type", "job_location_type", "date_posted", "valid_through"):
            kwargs[key] = value
        elif key == "location":
            kwargs["locations"] = [value] if isinstance(value, str) else value
        elif key == "locations":
            kwargs["locations"] = [value] if isinstance(value, str) else value
        elif key in ("qualifications", "responsibilities", "skills"):
            kwargs[key] = [value] if isinstance(value, str) else value
        else:
            metadata[key] = value

    if metadata:
        kwargs["metadata"] = metadata

    return JobContent(**kwargs)


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, artifact_dir: Path | None = None) -> JobContent:
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
        try:
            (artifact_dir / "flat.json").write_text(
                json.dumps(elements, indent=2, ensure_ascii=False),
            )
        except Exception:
            pass  # Best-effort

    raw = walk_steps(elements, steps)
    content = _map_to_job_content(raw)

    log.debug("dom.extracted", url=url, fields=[k for k, v in raw.items() if v is not None])
    return content


register("dom", scrape)
