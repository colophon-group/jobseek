"""Next.js ``__NEXT_DATA__`` / RSC scraper — pre-configured embedded scraper.

Syntactic sugar for the embedded scraper with ``script_id: "__NEXT_DATA__"``
pre-injected.  When ``source: "rsc"`` is in the config (for App Router sites),
the ``script_id`` injection is skipped and RSC flight extraction is used instead.

All extraction logic lives in ``embedded.py``.

Config example (__NEXT_DATA__)::

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

Config example (RSC)::

    {
        "source": "rsc",
        "path": "job",
        "fields": {
            "title": "title",
            "description": "aboutRole",
            "locations": "location",
            "employment_type": "type"
        }
    }
"""

from __future__ import annotations

from src.core.scrapers import JobContent, register
from src.core.scrapers.embedded import _auto_map_fields, _find_job_object
from src.core.scrapers.embedded import parse_html as embedded_parse_html
from src.core.scrapers.embedded import scrape as embedded_scrape
from src.shared.nextdata import extract_next_data, extract_rsc_data, resolve_path


def _inject_script_id(config: dict) -> dict:
    """Return a copy of *config* with ``script_id`` set to ``__NEXT_DATA__``.

    Skipped when ``source`` is already set (e.g. ``"rsc"``).
    """
    if config.get("source"):
        return config
    merged = dict(config)
    merged["script_id"] = "__NEXT_DATA__"
    return merged


def _try_rsc_detection(htmls: list[str]) -> dict | None:
    """Detect RSC flight payloads with job objects across *htmls*."""
    job_objects: list[tuple[str | None, dict]] = []
    for html in htmls:
        data = extract_rsc_data(html)
        if not isinstance(data, dict):
            continue
        suffix, job_obj = _find_job_object(data, "")
        if job_obj:
            job_objects.append((suffix, job_obj))

    if not job_objects:
        return None

    from collections import Counter

    suffix_counts = Counter(suffix for suffix, _ in job_objects)
    best_suffix = suffix_counts.most_common(1)[0][0]
    matching_objs = [obj for suffix, obj in job_objects if suffix == best_suffix]

    all_keys: set[str] = set()
    for obj in matching_objs:
        all_keys.update(obj.keys())
    merged: dict = {}
    for key in all_keys:
        for obj in matching_objs:
            if key in obj:
                merged[key] = obj[key]
                break

    fields = _auto_map_fields(merged)
    if not fields:
        return None

    config: dict = {"source": "rsc", "fields": fields}
    if best_suffix:
        config["path"] = best_suffix
    return config


def can_handle(htmls: list[str]) -> dict | None:
    """Detect ``__NEXT_DATA__`` with job objects across multiple pages.

    Analyzes all pages collectively: finds the most consistent path and
    builds a field mapping from the union of keys seen across pages.
    """
    job_objects: list[tuple[str | None, dict]] = []

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
        # Fallback: try RSC flight payload (Next.js App Router)
        rsc_result = _try_rsc_detection(htmls)
        if rsc_result:
            return rsc_result
        return None

    from collections import Counter

    suffix_counts = Counter(suffix for suffix, _ in job_objects)
    best_suffix = suffix_counts.most_common(1)[0][0]

    matching_objs = [obj for suffix, obj in job_objects if suffix == best_suffix]

    all_keys: set[str] = set()
    for obj in matching_objs:
        all_keys.update(obj.keys())

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


def parse_html(html: str, config: dict) -> JobContent:
    """Extract job data from pre-fetched HTML using nextdata config."""
    return embedded_parse_html(html, _inject_script_id(config))


async def scrape(url: str, config: dict, http, pw=None, **kwargs) -> JobContent:
    """Extract job data from a Next.js ``__NEXT_DATA__`` blob."""
    return await embedded_scrape(url, _inject_script_id(config), http, pw=pw, **kwargs)


register("nextdata", scrape, can_handle=can_handle, parse_html=parse_html)
