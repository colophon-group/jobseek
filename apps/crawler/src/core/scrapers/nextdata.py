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

# ── Auto-detection helpers ────────────────────────────────────────────

_TITLE_KEYS = {"title", "name", "jobTitle", "job_title", "position"}
_DESC_KEYS = {"description", "content", "descriptionHtml", "body", "jobDescription"}

# Mapping from known raw keys to JobContent field names
_FIELD_PATTERNS: dict[str, list[str]] = {
    "title": ["title", "name", "jobTitle", "job_title", "position"],
    "description": ["description", "content", "descriptionHtml", "body", "jobDescription"],
    "locations": ["location", "locations", "office", "offices"],
    "employment_type": ["employmentType", "employment_type", "type", "jobType"],
    "job_location_type": ["locationType", "workplaceType", "remoteType"],
    "date_posted": ["datePosted", "createdAt", "publishedAt", "postedDate"],
}


def _find_job_object(data: dict, prefix: str) -> tuple[str | None, dict | None]:
    """Walk one level of nesting looking for a dict with title + description keys.

    Returns ``(path_suffix, job_dict)`` or ``(None, None)``.
    """
    # Check data itself
    keys = set(data.keys())
    has_title = bool(keys & _TITLE_KEYS)
    has_desc = bool(keys & _DESC_KEYS)
    if has_title and has_desc:
        return None, data

    # Walk one level: check each dict-valued child
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        child_keys = set(val.keys())
        if (child_keys & _TITLE_KEYS) and (child_keys & _DESC_KEYS):
            return key, val

    return None, None


def _auto_map_fields(job_obj: dict) -> dict[str, str]:
    """Map known key patterns in *job_obj* to JobContent field specs."""
    fields: dict[str, str] = {}

    for target, candidates in _FIELD_PATTERNS.items():
        for cand in candidates:
            if cand not in job_obj:
                continue
            val = job_obj[cand]

            # Array of dicts → use [].name wildcard
            if isinstance(val, list) and val and isinstance(val[0], dict):
                # Look for a "name" key in the first dict
                if "name" in val[0]:
                    fields[target] = f"{cand}[].name"
                else:
                    # Use first string-valued key
                    for k, v in val[0].items():
                        if isinstance(v, str):
                            fields[target] = f"{cand}[].{k}"
                            break
            else:
                fields[target] = cand
            break

    return fields


def can_handle(htmls: list[str]) -> dict | None:
    """Detect ``__NEXT_DATA__`` with job objects across multiple pages.

    Analyzes all pages collectively: finds the most consistent path and
    builds a field mapping from the union of keys seen across pages.
    """
    # Collect job objects from all pages
    job_objects: list[tuple[str | None, dict]] = []  # (suffix, job_obj)

    for html in htmls:
        data = extract_next_data(html)
        if data is None:
            continue
        page_props = resolve_path(data, "props.pageProps")
        if not isinstance(page_props, dict):
            continue
        suffix, job_obj = _find_job_object(page_props, "props.pageProps")
        if job_obj:
            job_objects.append((suffix, job_obj))

    if not job_objects:
        return None

    # Use the most common path suffix across pages
    from collections import Counter
    suffix_counts = Counter(suffix for suffix, _ in job_objects)
    best_suffix = suffix_counts.most_common(1)[0][0]

    # Build field mapping from all job objects with the best suffix
    matching_objs = [obj for suffix, obj in job_objects if suffix == best_suffix]

    # Collect all keys seen across all matching objects
    all_keys: set[str] = set()
    for obj in matching_objs:
        all_keys.update(obj.keys())

    # Build a merged job object for field mapping — use first object that has each key
    merged: dict = {}
    for key in all_keys:
        for obj in matching_objs:
            if key in obj:
                merged[key] = obj[key]
                break

    fields = _auto_map_fields(merged)
    if not fields:
        return None

    config: dict = {"fields": fields}
    if best_suffix:
        config["path"] = f"props.pageProps.{best_suffix}"
    else:
        config["path"] = "props.pageProps"
    return config


# ── Core extraction ───────────────────────────────────────────────────

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


def parse_html(html: str, config: dict) -> JobContent:
    """Extract job data from pre-fetched HTML using nextdata config."""
    path = config.get("path")
    fields_map: dict[str, str] = config.get("fields") or {}

    if not fields_map:
        return JobContent()

    data = extract_next_data(html)
    if not data:
        return JobContent()

    item = resolve_path(data, path) if path else data
    if not isinstance(item, dict):
        return JobContent()

    raw: dict[str, object] = {}
    for target, spec in fields_map.items():
        raw[target] = extract_field(item, spec)

    return _map_to_job_content(raw)


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

    content = parse_html(html, config)

    if content.title:
        log.debug("nextdata_scraper.extracted", url=url, title=content.title)
    else:
        log.warning("nextdata_scraper.no_data", url=url)
    return content


register("nextdata", scrape, can_handle=can_handle, parse_html=parse_html)
