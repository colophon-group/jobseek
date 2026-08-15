"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``src.shared.extract`` to pull
structured fields from the HTML.

By default (``render: false``), fetches the page via static HTTP.  Set
``render: true`` to render with Playwright for JS-heavy sites.

Config uses ``steps`` (same format as ``walk_steps``), an optional ``scope``
CSS selector that limits extraction to one content container, plus browser
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

import codecs
import contextlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.scrapers import JobContent, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content
from src.shared.extract import flatten, walk_steps
from src.shared.http import is_avature_job_detail_url
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()


def _scope_html(html: str, config: dict) -> str:
    """Limit extraction to one configured container before flattening.

    Branded career pages often wrap the ATS fragment in malformed or enormous
    navigation markup. Scoping is generic DOM-scraper behavior: it makes
    selectors deterministic and prevents surrounding site chrome from
    shadowing job fields without introducing provider-specific parsing.
    """

    scope = config.get("scope")
    if scope is None:
        return html
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 256 or "\x00" in scope:
        raise ValueError("DOM scraper scope must be a non-empty CSS selector up to 256 chars")
    try:
        node = LexborHTMLParser(html).css_first(scope)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DOM scraper scope is not a valid CSS selector: {scope!r}") from exc
    if node is None:
        raise ValueError(f"DOM scraper scope did not match the page: {scope!r}")
    return node.html


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


def _status_retry_limits(config: dict, url: str) -> dict[int, int]:
    """Return validated static-fetch status retries from scraper config."""

    limits = {406: 2} if is_avature_job_detail_url(url) else {}
    configured = config.get("retry_statuses")
    if configured is None:
        return limits
    if not isinstance(configured, dict):
        raise ValueError("DOM scraper retry_statuses must be an object")
    for raw_status, raw_limit in configured.items():
        try:
            status = int(raw_status)
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("DOM scraper retry_statuses entries must be integers") from exc
        if not 400 <= status <= 599 or not 0 <= limit <= 5:
            raise ValueError("DOM scraper retry_statuses requires HTTP 400-599 and 0-5 retries")
        limits[status] = max(limits.get(status, 0), limit)
    return limits


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

_KONTACT_MARKER = "kontactintelligence.com"
_WORKLOAD_RE = re.compile(r"^\s*\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s*%\s*$")


def _title_heading(elements: list[dict]) -> tuple[int, str] | None:
    """Find the page's job-title heading without guessing from site chrome.

    ``h1`` remains the preferred semantic signal. Some CMS job templates use a
    lower-level heading for the visible title, however. In that case, accept an
    ``h2``-``h4`` only when its normalized text exactly matches the document
    title. This keeps the fallback useful without treating an arbitrary section
    heading as the job title.
    """

    for i, element in enumerate(elements):
        if element["tag"] == "h1":
            return i, "h1"

    document_title = next(
        (element["text"] for element in elements if element["tag"] == "title"),
        None,
    )
    if not document_title:
        return None
    normalized_title = " ".join(document_title.split()).casefold()
    for i, element in enumerate(elements):
        if element["tag"] in {"h2", "h3", "h4"} and (
            " ".join(element["text"].split()).casefold() == normalized_title
        ):
            return i, element["tag"]
    return None


def _kontact_config(htmls: list[str]) -> dict | None:
    """Build the stable extraction config used by KontactIntelligence pages."""

    matches = sum(_KONTACT_MARKER in html.casefold() for html in htmls)
    if not matches or matches < len(htmls) / 2:
        return None

    return {
        "scope": "#content",
        "steps": [
            {
                "text": "Location:",
                "offset": 1,
                "field": "location",
                "from": 0,
            },
            {
                "tag": "h1",
                "field": "title",
                "optional": True,
                "from": 0,
            },
            {
                "tag": "h2",
                "attr": "class=opportunityTitle",
                "field": "title",
                "optional": True,
                "from": 0,
            },
            {
                "text": "Overview",
                "offset": 1,
                "field": "description",
                "stop": "Print Opportunity",
                "html": True,
                "from": 0,
            },
        ],
    }


def _heuristic_steps(elements: list[dict]) -> list[dict] | None:
    """Generate heuristic extraction steps from flattened elements."""
    if not elements:
        return None

    title_heading = _title_heading(elements)
    if title_heading is None:
        return None
    title_idx, title_tag = title_heading

    steps: list[dict] = [{"tag": title_tag, "field": "title"}]

    # A common compact job header is ``title, location, workload`` where the
    # latter two values are adjacent list items (for example ``Sion`` and
    # ``80-100%``). Capture the location and advance past the workload before
    # collecting the description. The percentage check prevents an ordinary
    # content list from being mistaken for job metadata.
    workload_location = False
    for i in range(title_idx + 1, min(title_idx + 6, len(elements) - 1)):
        if (
            elements[i]["tag"] == "li"
            and elements[i + 1]["tag"] == "li"
            and _WORKLOAD_RE.fullmatch(elements[i + 1]["text"])
            and not any(element["tag"] == "li" for element in elements[title_idx + 1 : i])
        ):
            steps.extend(
                [
                    {"tag": "li", "field": "location", "optional": True},
                    {"tag": "li"},
                ]
            )
            workload_location = True
            break

    # Description: continue from the cursor immediately after the title
    # heading (and compact metadata, when detected).
    # Leaving the selector empty intentionally matches the current element;
    # re-seeking the h1 would either miss it or escape a URL-fragment anchor.
    desc_step: dict = {
        "field": "description",
        "html": True,
        "optional": True,
    }

    # Look for a stop marker in elements after the title.
    for i in range(title_idx + 1, len(elements)):
        text = elements[i]["text"]
        for marker in _STOP_MARKERS:
            if marker.lower() in text.lower() and len(text) < 60:
                desc_step["stop"] = marker
                break
        if "stop" in desc_step:
            break

    # If no stop marker found, use stop_count based on remaining content
    if "stop" not in desc_step:
        remaining = len(elements) - title_idx - 1
        desc_step["stop_count"] = min(remaining, 50)

    steps.append(desc_step)

    # Location: look for an element with "location" in its text
    if not workload_location:
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
    kontact = _kontact_config(htmls)
    if kontact is not None:
        return kontact

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

    # Validate a trustworthy title heading exists on other pages too.
    expected_title_tag = best_steps[0].get("tag")
    title_found = 0
    for html in htmls:
        elements = flatten(html)
        heading = _title_heading(elements)
        if heading is not None and heading[1] == expected_title_tag:
            title_found += 1

    # Require a title heading on at least half the pages.
    if title_found < len(htmls) / 2:
        return None

    return {"steps": best_steps}


def parse_html(html: str, config: dict) -> JobContent:
    """Extract job data from pre-fetched HTML using step-based extraction."""
    steps = config.get("steps")
    if not steps:
        return JobContent()
    elements = flatten(_scope_html(html, config))
    raw, _ = walk_steps(elements, steps)
    raw = _apply_defaults(raw, config)
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


def _apply_defaults(raw: dict, config: dict) -> dict:
    """Fill fields that extraction did not produce from board-scoped defaults."""
    defaults = config.get("defaults")
    if defaults is None:
        return raw
    if not isinstance(defaults, dict):
        raise ValueError("DOM scraper defaults must be an object")

    merged = dict(raw)
    for field, value in defaults.items():
        if merged.get(field) in (None, "", []):
            merged[field] = value
    return merged


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
                html = await safe_content(page)
                _raise_if_bot_challenge(final_url or url, html)
                return html

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
        retry_limits = _status_retry_limits(config, url)
        resp = await fetch_response_with_status_retries(
            http,
            url,
            retry_limits=retry_limits,
            log_event="dom.fetch.retry_status",
        )
        # Detect redirect-to-gone BEFORE raise_for_status so the error page's
        # 200 doesn't shadow the actual archived signal. The redirect chain
        # may end on a 200 (rendered "this posting was removed" page), so
        # status alone never reveals gone-ness on these hosts.
        _check_gone_redirect(str(resp.url), gone_pattern, url)
        resp.raise_for_status()
        encoding = config.get("encoding")
        if encoding is not None:
            if not isinstance(encoding, str) or not encoding:
                raise ValueError("DOM scraper encoding must be a non-empty codec name")
            codecs.lookup(encoding)
            resp.encoding = encoding
        html = resp.text
        _raise_if_bot_challenge(str(resp.url), html)

    html = _scope_html(html, config)
    elements = flatten(html)

    if artifact_dir is not None:
        with contextlib.suppress(Exception):
            (artifact_dir / "flat.json").write_text(
                json.dumps(elements, indent=2, ensure_ascii=False),
            )

    start = _fragment_start(url, elements)
    raw, _ = walk_steps(elements, steps, start=start)
    raw = _apply_defaults(raw, config)
    content = _map_to_job_content(raw)

    log.debug("dom.extracted", url=url, fields=[k for k, v in raw.items() if v is not None])
    return content


register("dom", scrape, can_handle=can_handle, parse_html=parse_html)
