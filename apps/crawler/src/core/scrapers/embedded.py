"""Embedded data scraper — extracts job data from structured JSON in HTML.

Handles multiple embedding patterns:
- ``<script id="...">`` blocks (e.g. Next.js ``__NEXT_DATA__``, custom app data)
- Regex patterns (e.g. ``AF_initDataCallback``)
- Variable assignments (``window.__DATA__ = {...}``)

Uses jmespath for all path resolution and field extraction.

Config example (Google Wiz)::

    {
        "pattern": "AF_initDataCallback\\\\(.*?data:",
        "path": "[0]",
        "fields": {
            "title": "[1]",
            "description": "[10]",
            "locations": "[9][*][2]"
        }
    }

Config example (variable assignment)::

    {
        "variable": "window.__DATA__",
        "path": "job",
        "fields": {
            "title": "title",
            "description": "descriptionHtml",
            "locations": "offices[].name"
        }
    }
"""

from __future__ import annotations

import jmespath
import structlog

from src.core.scrapers import JobContent, register
from src.shared.embedded import extract_script_by_id, parse_embedded

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
    keys = set(data.keys())
    has_title = bool(keys & _TITLE_KEYS)
    has_desc = bool(keys & _DESC_KEYS)
    if has_title and has_desc:
        return None, data

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

            if isinstance(val, list) and val and isinstance(val[0], dict):
                if "name" in val[0]:
                    fields[target] = f"{cand}[].name"
                else:
                    for k, v in val[0].items():
                        if isinstance(v, str):
                            fields[target] = f"{cand}[].{k}"
                            break
            else:
                fields[target] = cand
            break

    return fields


# ── Core extraction ───────────────────────────────────────────────────


def _map_to_job_content(raw: dict[str, object]) -> JobContent:
    """Map extracted fields dict to a ``JobContent`` dataclass."""
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
        elif key == "locations":
            kwargs["locations"] = value if isinstance(value, list) else [value]
        elif key == "location":
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


def can_handle(htmls: list[str]) -> dict | None:
    """Auto-detect embedded JSON with job objects across multiple pages.

    Detects:
    - ``<script id="...">`` blocks (excluding __NEXT_DATA__, which is nextdata's domain)
    - ``AF_initDataCallback`` calls
    - Common variable assignments

    Returns config dict or None.
    """
    # Pattern 1: <script id="..."> blocks with job-like JSON
    # Exclude __NEXT_DATA__ (handled by nextdata scraper)
    from src.shared.embedded import _try_parse_json

    # Try known variable patterns
    _VARIABLE_PATTERNS = [
        "window.__DATA__",
        "window.__INITIAL_STATE__",
        "window.__INITIAL_DATA__",
    ]

    # Try AF_initDataCallback
    _AF_PATTERN = r"AF_initDataCallback\s*\(\s*\{[^}]*data\s*:"

    found = 0
    best_config: dict | None = None

    for html in htmls:
        # AF_initDataCallback
        if "AF_initDataCallback" in html:
            from src.shared.embedded import extract_by_pattern

            data = extract_by_pattern(html, _AF_PATTERN)
            if data is not None:
                found += 1
                if best_config is None:
                    best_config = {"pattern": _AF_PATTERN}
                continue

        # Variable assignments
        for var_name in _VARIABLE_PATTERNS:
            if var_name in html:
                from src.shared.embedded import extract_by_variable

                data = extract_by_variable(html, var_name)
                if isinstance(data, dict):
                    suffix, job_obj = _find_job_object(data, "")
                    if job_obj:
                        found += 1
                        if best_config is None:
                            fields = _auto_map_fields(job_obj)
                            if fields:
                                config: dict = {"variable": var_name, "fields": fields}
                                if suffix:
                                    config["path"] = suffix
                                best_config = config
                        break

        # Script ID blocks (not __NEXT_DATA__)
        import re

        for match in re.finditer(r'<script\s+id="([^"]+)"', html):
            script_id = match.group(1)
            if script_id == "__NEXT_DATA__":
                continue
            content = extract_script_by_id(html, script_id)
            if content is None:
                continue
            data = _try_parse_json(content.strip())
            if not isinstance(data, dict):
                continue
            suffix, job_obj = _find_job_object(data, "")
            if job_obj:
                found += 1
                if best_config is None:
                    fields = _auto_map_fields(job_obj)
                    if fields:
                        cfg: dict = {"script_id": script_id, "fields": fields}
                        if suffix:
                            cfg["path"] = suffix
                        best_config = cfg
                break

    if found > 0 and found >= len(htmls) / 2 and best_config:
        return best_config
    return None


def parse_html(html: str, config: dict) -> JobContent:
    """Extract job data from pre-fetched HTML using embedded config."""
    fields_map: dict[str, str] = config.get("fields") or {}

    if not fields_map:
        return JobContent()

    data = parse_embedded(html, config)
    if data is None:
        return JobContent()

    # Navigate to job object via path
    path = config.get("path")
    item = jmespath.search(path, data) if path else data

    if item is None:
        return JobContent()

    # For dicts, extract fields by name. For lists/scalars, fields use positional jmespath.
    raw: dict[str, object] = {}
    for target, spec in fields_map.items():
        result = jmespath.search(spec, item)
        if result is None:
            continue
        if isinstance(result, list):
            values = [str(v) for v in result if v is not None]
            raw[target] = values or None
        else:
            raw[target] = str(result)

    return _map_to_job_content(raw)


async def scrape(
    url: str,
    config: dict,
    http,
    pw=None,
    artifact_dir=None,
    job_id=None,
    **kwargs,
) -> JobContent:
    """Extract job data from embedded structured JSON in HTML."""
    fields_map: dict[str, str] = config.get("fields") or {}
    render = config.get("render", False)
    actions = config.get("actions")

    if not render and actions:
        log.warning(
            "embedded_scraper.misconfiguration",
            url=url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    if not fields_map:
        log.warning("embedded_scraper.no_fields", url=url)
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
            log.warning("embedded_scraper.render_failed", url=url, exc_info=True)
            return JobContent()
    else:
        resp = await http.get(url)
        if resp.status_code != 200:
            log.warning("embedded_scraper.fetch_failed", url=url, status=resp.status_code)
            return JobContent()
        html = resp.text

    # Save artifact
    if artifact_dir and job_id:
        import contextlib

        with contextlib.suppress(Exception):
            (artifact_dir / f"{job_id}.html").write_text(html)

    content = parse_html(html, config)

    if content.title:
        log.debug("embedded_scraper.extracted", url=url, title=content.title)
    else:
        log.warning("embedded_scraper.no_data", url=url)
    return content


register("embedded", scrape, can_handle=can_handle, parse_html=parse_html)
