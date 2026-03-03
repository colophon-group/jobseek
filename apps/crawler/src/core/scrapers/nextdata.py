"""Next.js ``__NEXT_DATA__`` scraper.

Extracts job details from the ``<script id="__NEXT_DATA__">`` JSON blob
on individual job pages built with Next.js.  Reuses the shared helpers
from ``src.shared.nextdata``.

Config example::

    {
        "path": "props.pageProps.jobData",
        "render": false,
        "fields": {
            "title": "title",
            "description": "descriptionHtml",
            "locations": "locations[].name",
            "metadata.team": "department.name"
        }
    }
"""

from __future__ import annotations

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.shared.nextdata import extract_field, extract_next_data, resolve_path

log = structlog.get_logger()


def _map_to_job_content(raw: dict[str, object]) -> JobContent:
    """Map extracted fields dict to a ``JobContent`` dataclass."""
    kwargs: dict[str, object] = {}
    metadata: dict[str, object] = {}

    for key, value in raw.items():
        if value is None:
            continue
        if key.startswith("metadata."):
            metadata[key.removeprefix("metadata.")] = value
        elif key in ("title", "description", "employment_type", "job_location_type", "date_posted", "valid_through"):
            kwargs[key] = value
        elif key == "locations":
            kwargs["locations"] = value if isinstance(value, list) else [value]
        elif key == "location":
            kwargs["locations"] = [value] if isinstance(value, str) else value
        elif key in ("qualifications", "responsibilities", "skills"):
            kwargs[key] = [value] if isinstance(value, str) else value
        else:
            metadata[key] = value

    if metadata:
        kwargs["metadata"] = metadata

    return JobContent(**kwargs)


async def scrape(url: str, config: dict, http: httpx.AsyncClient, pw=None, **kwargs) -> JobContent:
    """Extract job data from a Next.js ``__NEXT_DATA__`` blob."""
    path = config.get("path")
    fields_map: dict[str, str] = config.get("fields") or {}
    render = config.get("render", False)
    actions = config.get("actions")

    if not render and actions:
        log.warning(
            "nextdata_scraper.misconfiguration",
            url=url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    if not fields_map:
        log.warning("nextdata_scraper.no_fields", url=url)
        return JobContent()

    # Fetch page HTML
    if render:
        try:
            from src.shared.browser import render as browser_render

            browser_config = {}
            if actions:
                browser_config["actions"] = actions
            html = await browser_render(url, config=browser_config, pw=pw)
        except Exception:
            log.warning("nextdata_scraper.render_failed", url=url, exc_info=True)
            return JobContent()
    else:
        resp = await http.get(url)
        if resp.status_code != 200:
            log.warning("nextdata_scraper.fetch_failed", url=url, status=resp.status_code)
            return JobContent()
        html = resp.text

    # Extract __NEXT_DATA__
    data = extract_next_data(html)
    if not data:
        log.warning("nextdata_scraper.no_next_data", url=url)
        return JobContent()

    # Walk to the job object
    if path:
        item = resolve_path(data, path)
    else:
        item = data

    if not isinstance(item, dict):
        log.warning("nextdata_scraper.path_not_dict", url=url, path=path)
        return JobContent()

    # Extract fields
    raw: dict[str, object] = {}
    for target, spec in fields_map.items():
        raw[target] = extract_field(item, spec)

    content = _map_to_job_content(raw)

    log.debug("nextdata_scraper.extracted", url=url, fields=[k for k, v in raw.items() if v is not None])
    return content


register("nextdata", scrape)
