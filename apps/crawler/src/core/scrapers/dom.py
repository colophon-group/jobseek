"""DOM scraper — extracts job data using step-based extraction.

Uses the step-based extraction engine from ``src.shared.extract`` to pull
structured fields from the HTML.

By default (``render: false``), fetches the page via static HTTP.  Set
``render: true`` to render with Playwright for JS-heavy sites.

Config uses ``steps`` (same format as ``walk_steps``), an optional ``scope``
CSS selector that limits extraction to one content container, and optional
``include_document_title`` / ``include_document_description`` flags when a
scoped layout keeps useful metadata in ``<head>``.
Browser lifecycle keys (``wait``, ``timeout``, ``user_agent``, ``headless``,
``actions``) are only used when rendering.

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
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors.dom import BotChallengeError, _raise_if_bot_challenge
from src.core.scrapers import JobContent, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content
from src.shared.extract import flatten, walk_steps
from src.shared.fetch_url import transformed_fetch_url
from src.shared.http import is_avature_job_detail_url
from src.shared.http_retry import fetch_response_with_status_retries

log = structlog.get_logger()

_RENDER_CHALLENGE_RETRIES = 1

_LUCCA_SCRAPER_CONFIG = {
    "scope": ".jobOffer-article",
    "steps": [
        {
            "tag": "h1",
            "attr": "data-testid=job-offer-title",
            "field": "title",
        },
        {
            "tag": "h2",
            "text": "Job description",
            "offset": 1,
            "field": "description",
            "html": True,
            "stop_attr": "data-testid=job-offer-publication-date",
        },
    ],
}


def _scope_html(html: str, config: dict) -> str:
    """Limit extraction to one configured container before flattening.

    Branded career pages often wrap the ATS fragment in malformed or enormous
    navigation markup. Scoping is generic DOM-scraper behavior: it makes
    selectors deterministic and prevents surrounding site chrome from
    shadowing job fields without introducing provider-specific parsing.
    """

    include_document_title = config.get("include_document_title", False)
    include_document_description = config.get("include_document_description", False)
    if not isinstance(include_document_title, bool):
        raise ValueError("DOM scraper include_document_title must be a boolean")
    if not isinstance(include_document_description, bool):
        raise ValueError("DOM scraper include_document_description must be a boolean")

    scope = config.get("scope")
    if scope is None:
        if include_document_title or include_document_description:
            raise ValueError("DOM scraper document metadata options require scope")
        return html
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 256 or "\x00" in scope:
        raise ValueError("DOM scraper scope must be a non-empty CSS selector up to 256 chars")
    try:
        document = LexborHTMLParser(html)
        node = document.css_first(scope)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DOM scraper scope is not a valid CSS selector: {scope!r}") from exc
    if node is None:
        raise ValueError(f"DOM scraper scope did not match the page: {scope!r}")
    prefixes: list[str] = []
    if include_document_title:
        title = document.css_first("title")
        if title is not None:
            prefixes.append(title.html)
    if include_document_description:
        description = document.css_first('meta[name="description"]')
        content = description.attributes.get("content") if description is not None else None
        if content:
            prefixes.append(f'<p data-document-description="true">{escape(content)}</p>')
        # A visible sentinel gives range steps a stable boundary between
        # injected document metadata and scoped content. Consumers match its
        # attribute and use offset=1, so it never enters extracted output.
        prefixes.append('<p data-document-scope-start="true">scope</p>')
    # HTML parsers intentionally treat noscript descendants as raw text when
    # scripting is enabled.  Re-wrapping a scoped noscript node therefore
    # makes its semantic fallback markup inert on the second parse below.
    # Return the inner markup for that element so job pages which publish an
    # accessible no-JavaScript fallback remain extractable.
    scoped_html = node.inner_html if node.tag == "noscript" else node.html
    return "".join(prefixes) + scoped_html


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
_DOCUMENT_TITLE_SEPARATORS = (" | ", " - ", " – ", " — ", " : ", " · ")
_LOCATION_LABELS = (
    "job location",
    "location",
    "workplace",
    "lieu de travail",
    "lieu",
    "arbeitsort",
    "arbeitsplatz",
    "luogo di lavoro",
)


def _matches_document_title(heading: str, document_title: str) -> bool:
    """Return whether a heading is a delimited title segment.

    Job sites commonly prefix or suffix the role with their brand or a generic
    careers label in ``<title>``. Requiring a separator boundary avoids fuzzy
    substring matches while still distinguishing the real role heading from a
    repeated page-level ``<h1>`` such as "Jobs".
    """

    heading = " ".join(heading.split()).casefold()
    document_title = " ".join(document_title.split()).casefold()
    if not heading:
        return False
    if heading == document_title:
        return True
    return any(
        document_title.startswith(f"{heading}{separator}")
        or document_title.endswith(f"{separator}{heading}")
        for separator in _DOCUMENT_TITLE_SEPARATORS
    )


def _title_heading(elements: list[dict]) -> tuple[int, str] | None:
    """Find the page's job-title heading without guessing from site chrome.

    ``h1`` remains the preferred semantic signal when it agrees with the
    document title. Some CMS job templates keep a generic careers-page ``h1``
    above the role, however. In that case, accept an ``h2``-``h4`` only when it
    is a separator-delimited prefix or suffix of the document title. This keeps
    the fallback useful without treating an arbitrary section heading as the
    job title.
    """

    document_title = next(
        (element["text"] for element in elements if element["tag"] == "title"),
        None,
    )
    if document_title:
        for tag in ("h1", "h2", "h3", "h4"):
            for i, element in enumerate(elements):
                if element["tag"] == tag and _matches_document_title(
                    element["text"], document_title
                ):
                    return i, tag

    for i, element in enumerate(elements):
        if element["tag"] == "h1":
            return i, "h1"
    return None


def _talentsoft_config(htmls: list[str]) -> dict | None:
    """Build locale-independent extraction steps for Talentsoft details."""

    matches = sum(
        "ts-offer-page__body" in html and "fldlocation_location_geographicalareacollection" in html
        for html in htmls
    )
    if not matches or matches < len(htmls) / 2:
        return None
    return {
        "steps": [
            {
                "tag": "h1",
                "attr": "class=ts-offer-page__title",
                "field": "title",
            },
            {
                "tag": "h2",
                "attr": "class=JobDescription",
                "field": "description",
                "html": True,
                "stop_tag": "h2",
            },
            {
                "attr": "id=fldjobdescription_contract",
                "field": "employment_type",
                "from": 0,
                "optional": True,
            },
            {
                "attr": "id=fldlocation_location_geographicalareacollection",
                "field": "locations",
                "from": 0,
            },
            {
                "tag": "h2",
                "attr": "class=ApplicantCriteria",
                "field": "qualifications",
                "html": True,
                "stop_tag": "h2",
                "from": 0,
                "optional": True,
            },
        ]
    }


_ELVIUM_MARKERS = (
    "career-page job-posting-layout",
    "job-posting-widget",
    "contact-info-widget",
)
_STADT_ZUERICH_MARKERS = (
    "job-detailseite.",
    "career_job_req_id=",
    "<stzh-pagetitle",
)
_SWISS_CANTON_CODES = (
    "AG|AI|AR|BE|BL|BS|FR|GE|GL|GR|JU|LU|NE|NW|OW|SG|SH|SO|SZ|TG|TI|UR|VD|VS|ZG|ZH"
)
_CLINCH_CLASS_MARKERS = (
    "job-description-container",
    "job-title",
    "job-description",
    "job-component-location",
)
_SOLIQUE_HOST_MARKER = "solique.ch/"
_SOLIQUE_CLASS_MARKERS = ("job-title", "tasks-profile-wrapper")
_REXX_PORTAL7_MARKERS = ("rexx recruitment - portal7", "jobtplcontainer")


def _has_html_class(html: str, class_name: str) -> bool:
    """Return whether raw HTML contains *class_name* as a class token."""

    return bool(
        re.search(
            rf"\bclass\s*=\s*(['\"])[^'\"]*(?<![^\s'\"]){re.escape(class_name)}"
            rf"(?![^\s'\"])\s*[^'\"]*\1",
            html,
            re.IGNORECASE,
        )
    )


def _stadt_zuerich_config(htmls: list[str]) -> dict | None:
    """Build extraction steps for City of Zurich's AEM job template.

    The public detail pages expose stable title, employment type, department,
    and description elements, but deliberately omit a location field. City
    roles are based in Zurich by default. The shared inventory also contains a
    small number of municipal-service roles outside the city; those postings
    identify their canton or Graubunden region in the title, so preserve that
    explicit value before applying the board-wide fallback.
    """

    matches = sum(all(marker in html for marker in _STADT_ZUERICH_MARKERS) for html in htmls)
    if not matches or matches < len(htmls) / 2:
        return None

    canton_location = rf"(?i)\bStandort\s+([^,()]+?\s+(?:{_SWISS_CANTON_CODES}))\b"
    regional_location = r"(?i)\b(Mittelbünden|Graubünden)\b"
    return {
        "defaults": {"locations": ["Zurich, Switzerland"]},
        "steps": [
            {"tag": "stzh-heading", "field": "title"},
            {
                "tag": "stzh-text",
                "attr": "slot=lead",
                "field": "employment_type",
            },
            {
                "tag": "stzh-text",
                "attr": "slot=lead",
                "field": "metadata.department",
            },
            {
                "tag": "stzh-heading",
                "match_regex": canton_location,
                "field": "locations",
                "regex": canton_location,
                "from": 0,
                "optional": True,
            },
            {
                "tag": "stzh-heading",
                "match_regex": regional_location,
                "field": "locations",
                "regex": regional_location,
                "from": 0,
                "optional": True,
            },
            {
                "tag": "p",
                "field": "description",
                "html": True,
                "stop": "Arbeiten bei der Stadt",
            },
        ],
    }


def _rexx_portal7_config(htmls: list[str]) -> dict | None:
    """Build a static config for legacy Rexx Portal7 detail pages.

    Portal7 keeps the role body in ``#jobTplContainer``, the title in the
    document title, and the location at the end of the meta description.
    Listing URLs often carry an expiring ``sid``; the DOM monitor removes that
    token before these pages reach the scraper.
    """

    matches = sum(
        all(marker in html.casefold() for marker in _REXX_PORTAL7_MARKERS) for html in htmls
    )
    if not matches or matches < len(htmls) / 2:
        return None

    return {
        "scope": "#jobTplContainer",
        "include_document_title": True,
        "include_document_description": True,
        "steps": [
            {
                "tag": "title",
                "field": "title",
                "regex": r"^(?:Stellenangebot|Job offer)\s+(.+?)\s+(?:bei|at)\s+",
                "from": 0,
            },
            {
                "attr": "data-document-description=true",
                "field": "locations",
                "regex": r"(?s)^.*\bin\s+(.+?)\s*$",
                "from": 0,
                "optional": True,
            },
            {
                "attr": "data-document-scope-start=true",
                "offset": 1,
                "field": "description",
                "html": True,
                "to_end": True,
                "from": 0,
            },
        ],
    }


def _solique_config(htmls: list[str]) -> dict | None:
    """Build a static extraction config for Solique publication pages.

    Solique puts the visible job title in a ``div`` inside ``header`` rather
    than an ``h1``. The generic flattener intentionally excludes header
    chrome, so the normal h1 heuristic cannot identify these otherwise fully
    static pages. Their content classes are stable across branded tenants and
    languages, making them a safer detection signal than translated headings.
    """

    matches = sum(
        _SOLIQUE_HOST_MARKER in html.casefold()
        and all(_has_html_class(html, marker) for marker in _SOLIQUE_CLASS_MARKERS)
        for html in htmls
    )
    if not matches or matches < len(htmls) / 2:
        return None

    return {
        "steps": [
            {
                "tag": "title",
                "field": "title",
                "regex": r"^(.*?)(?:\s+-\s+\d{1,3}(?:\s*-\s*\d{1,3})?%)?$",
                "from": 0,
            },
            {
                "attr": "class=intro",
                "field": "description",
                "stop_attr": "class=contact-title",
                "html": True,
                "from": 0,
            },
            {
                "tag": "div",
                "attr": "class=location",
                "field": "location",
                "from": 0,
            },
        ]
    }


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


def _elvium_config(htmls: list[str]) -> dict | None:
    """Build a resilient DOM preset for Elvium job-detail pages.

    Elvium pages do not publish ``JobPosting`` JSON-LD and their visible title
    is commonly a styled ``<p>`` rather than a heading.  The generic DOM
    heuristic therefore cannot identify them.  Their stable job layout does,
    however, expose a dedicated content section followed by a contact-address
    widget.  Scope extraction to that layout and retry Elvium's bursty 429
    responses so a board can use the existing DOM monitor/scraper pair.
    """

    matches = sum(all(marker in html for marker in _ELVIUM_MARKERS) for html in htmls)
    if not matches or matches < len(htmls) / 2:
        return None

    return {
        "scope": "section.job-posting-layout",
        "include_document_title": True,
        "retry_statuses": {"429": 3},
        "steps": [
            {
                "tag": "title",
                "field": "title",
                "regex": r"^(.*?)\s+at\s+.+$",
            },
            {
                "tag": "p",
                "field": "description",
                "html": True,
                "stop": "Kontakt Info",
            },
            {"tag": "p", "field": "locations"},
        ],
    }


def _clinch_config(htmls: list[str]) -> dict | None:
    """Build a rendered DOM config for PageUp Clinch job pages.

    Clinch career sites use stable job-component classes but commonly put a
    marketing slogan in ``h1`` and the actual job title in ``h3.job-title``.
    The generic heading heuristic therefore extracts the wrong title.  Some
    tenants also challenge static HTTP requests while serving the complete
    page to Chromium, so the rendered probe adds the browser flags separately.
    """

    matches = sum(
        all(_has_html_class(html, marker) for marker in _CLINCH_CLASS_MARKERS) for html in htmls
    )
    if not matches or matches < len(htmls) / 2:
        return None

    return {
        "scope": ".job-description-container",
        "steps": [
            {"tag": "h3", "attr": "class=job-title", "field": "title"},
            {
                "tag": "li",
                "attr": "class=job-component-workplace-type",
                "field": "job_location_type",
                "from": 0,
                "optional": True,
            },
            {
                "tag": "li",
                "attr": "class=job-component-location",
                "field": "locations",
                "from": 0,
            },
            {
                "tag": "li",
                "attr": "class=job-component-department",
                "field": "metadata.department",
                "from": 0,
                "optional": True,
            },
            {
                "tag": "li",
                "attr": "class=job-component-employment-type",
                "field": "employment_type",
                "from": 0,
                "optional": True,
            },
            {
                "tag": "div",
                "attr": "class=job-description-controls",
                "optional": True,
                "from": 0,
            },
            {"field": "description", "html": True, "stop_count": 200},
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

    # Location: support both a standalone label followed by its value and
    # compact locale-specific ``Label: value`` elements. The latter is common
    # on otherwise static European job pages and must not advance to the next
    # metadata row (for example a start date).
    if not workload_location:
        for el in elements:
            text = el["text"]
            text_lower = text.strip().casefold()
            label = next(
                (
                    candidate
                    for candidate in _LOCATION_LABELS
                    if text_lower == candidate
                    or re.match(rf"^{re.escape(candidate)}\s*[:：]", text_lower)
                ),
                None,
            )
            if label is None or len(text) >= 120:
                continue

            inline_pattern = rf"(?i)^\s*{re.escape(label)}\s*[:：]\s*(\S.*)\s*$"
            inline_value = re.match(inline_pattern, text)
            if inline_value:
                steps.append(
                    {
                        "text": label,
                        "match_regex": inline_pattern,
                        "field": "location",
                        "regex": inline_pattern,
                        "optional": True,
                        "from": 0,
                    }
                )
            elif len(text) < 40:
                steps.append(
                    {
                        "text": label,
                        "match_regex": rf"(?i)^\s*{re.escape(label)}\s*(?:[:：]\s*)?$",
                        "offset": 1,
                        "field": "location",
                        "optional": True,
                        "from": 0,
                    }
                )
            break

    return steps


def _lucca_config(htmls: list[str]) -> dict | None:
    """Return the stable DOM detail preset for Lucca/Poplee postings."""

    matches = 0
    for html in htmls:
        tree = LexborHTMLParser(html)
        if (
            tree.css_first("article.jobOffer-article") is not None
            and tree.css_first('[data-testid="job-offer-title"]') is not None
            and tree.css_first('[data-testid="job-offer-location"]') is not None
            and tree.css_first(".jobOffer-article-content") is not None
        ):
            matches += 1
    if not matches or matches < len(htmls) / 2:
        return None
    return {
        "scope": _LUCCA_SCRAPER_CONFIG["scope"],
        "steps": [dict(step) for step in _LUCCA_SCRAPER_CONFIG["steps"]],
    }


def can_handle(htmls: list[str]) -> dict | None:
    """Generate heuristic extraction steps from multiple page HTMLs.

    Analyzes all pages and returns steps that work across the collection.
    Uses the first page's structure to generate steps, then validates
    that the title step (h1) matches on other pages too.
    """
    lucca = _lucca_config(htmls)
    if lucca is not None:
        return lucca

    stadt_zuerich = _stadt_zuerich_config(htmls)
    if stadt_zuerich is not None:
        return stadt_zuerich

    rexx_portal7 = _rexx_portal7_config(htmls)
    if rexx_portal7 is not None:
        return rexx_portal7

    solique = _solique_config(htmls)
    if solique is not None:
        return solique

    kontact = _kontact_config(htmls)
    if kontact is not None:
        return kontact

    talentsoft = _talentsoft_config(htmls)
    if talentsoft is not None:
        return talentsoft

    elvium = _elvium_config(htmls)
    if elvium is not None:
        return elvium

    clinch = _clinch_config(htmls)
    if clinch is not None:
        return clinch

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

    # Probes intentionally sample several postings. The first usable page can
    # be a legitimate location-less role even when the board normally exposes
    # a stable location label. Borrow only that optional step from a later
    # sample so the generated config reflects the board layout without making
    # the first posting fail extraction.
    if not any(step.get("field") in {"location", "locations"} for step in best_steps):
        for html in htmls[1:]:
            candidate_steps = _heuristic_steps(flatten(html)) or []
            location_step = next(
                (
                    step
                    for step in candidate_steps
                    if step.get("field") in {"location", "locations"}
                ),
                None,
            )
            if location_step is not None:
                best_steps.append(location_step)
                break

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


async def probe_pw(urls: list[str], pw) -> tuple[dict | None, str]:
    """Render samples when static scraper probing is blocked or shell-only.

    This is a bounded setup-time fallback.  Runtime scraping still happens
    only when the returned config is selected for a board.
    """

    htmls: list[str] = []
    browser_config = {
        "wait": "domcontentloaded",
        "timeout": 30_000,
        "stealth": True,
    }
    for url in urls[:3]:
        try:
            async with open_page(pw, browser_config) as page:
                await navigate(page, url, browser_config)
                # Turbo/Stimulus career sites may commit the document before
                # their server-rendered job block is attached.  Keep this
                # setup-only fallback short and bounded.
                await page.wait_for_timeout(2_000)
                html = await safe_content(page)
                _raise_if_bot_challenge(page.url or url, html)
                htmls.append(html)
        except Exception:
            log.debug("dom.probe_pw.fetch_failed", url=url, exc_info=True)

    if not htmls:
        return None, "Rendered fetch failed"

    detected = can_handle(htmls)
    if detected is None:
        return None, "Rendered DOM not detected"

    config = {**browser_config, "render": True, **detected}
    contents = [parse_html(html, config) for html in htmls]
    titles = sum(content.title is not None for content in contents)
    descriptions = sum(content.description is not None for content in contents)
    locations = sum(content.locations is not None for content in contents)
    total = len(contents)
    metadata = {
        "config": config,
        "total": total,
        "titles": titles,
        "descriptions": descriptions,
        "locations": locations,
        "fields": {
            name: count
            for name, count in {
                "title": titles,
                "description": descriptions,
                "locations": locations,
            }.items()
            if count
        },
    }
    return metadata, (
        f"Rendered: {titles}/{total} titles, {descriptions}/{total} desc, "
        f"{locations}/{total} locations"
    )


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


def _document_fallback_config(config: dict) -> dict | None:
    """Validate the optional per-format static document fallback config."""
    value = config.get("document_fallback")
    if value is None or value is False:
        return None
    if not isinstance(value, dict) or set(value) - {"pdf", "docx"}:
        raise ValueError("DOM scraper document_fallback must contain only pdf/docx configs")
    for kind, kind_config in value.items():
        if not isinstance(kind_config, dict):
            raise ValueError(f"DOM scraper document_fallback.{kind} must be an object")
    return value


async def _parse_static_document(
    content: bytes,
    url: str,
    fallback: dict,
) -> tuple[JobContent, str] | None:
    """Return parsed PDF/DOCX content, or ``None`` for an HTML response."""
    if content.lstrip().startswith(b"%PDF-"):
        from src.core.scrapers.pdf import parse_bytes

        parsed = await parse_bytes(content, url, fallback.get("pdf") or {})
        return parsed, "pdf"

    if content.startswith(b"PK\x03\x04"):
        from src.core.scrapers.adp import docx_to_html
        from src.core.scrapers.pdf import _extract_pattern, _title_from_text

        description = docx_to_html(content)
        if description is None:
            raise ValueError("DOM scraper document_fallback received an invalid DOCX archive")
        docx_config = fallback.get("docx") or {}
        text = LexborHTMLParser(description).text(separator="\n", strip=True)
        title = _extract_pattern(text, docx_config.get("title_pattern"))
        if not title and docx_config.get("title_source") == "text":
            title = _title_from_text(text)
        location = _extract_pattern(text, docx_config.get("location_pattern"))
        raw = _apply_defaults(
            {
                "title": title,
                "description": description,
                "locations": [location] if location else None,
            },
            docx_config,
        )
        return _map_to_job_content(raw), "docx"

    return None


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
    ``fetch_url_transform`` may rewrite only the URL used for that read; the
    caller's canonical posting URL remains the source identity.
    """
    steps = config.get("steps")
    if not steps:
        log.warning("dom.no_steps", url=url)
        return JobContent()

    render = config.get("render", False)
    fetch_url = transformed_fetch_url(
        url,
        config.get("fetch_url_transform"),
        owner="DOM scraper",
    )
    same_origin_redirects = config.get("same_origin_redirects", False)
    if not isinstance(same_origin_redirects, bool):
        raise ValueError("DOM scraper same_origin_redirects must be a boolean")
    document_fallback = _document_fallback_config(config)

    if render and document_fallback is not None:
        raise ValueError("DOM scraper document_fallback requires render=false")

    if not render and config.get("actions"):
        log.warning(
            "dom.misconfiguration",
            url=url,
            detail="actions require render=true; overriding render to true",
        )
        render = True

    if render and same_origin_redirects:
        raise ValueError("DOM scraper same_origin_redirects requires render=false")

    gone_pattern = config.get("gone_url_pattern")

    if render:
        browser_config = {k: v for k, v in config.items() if k in BROWSER_KEYS}
        use_proxy = bool(config.get("proxy"))

        async def _render_page(p):
            async with open_page(p, browser_config, use_proxy=use_proxy) as page:
                await navigate(page, fetch_url, browser_config)
                # Read final URL BEFORE running actions/extraction so a
                # redirect-to-gone page doesn't burn the (potentially
                # paid-proxy) action pipeline against a known dead page.
                final_url = ""
                with contextlib.suppress(Exception):
                    final_url = page.url or ""
                _check_gone_redirect(final_url, gone_pattern, url)
                await run_actions(page, browser_config.get("actions", []))
                html = await safe_content(page)
                _raise_if_bot_challenge(final_url or fetch_url, html)
                return html

        async def _render_with_challenge_retry(p):
            for attempt in range(_RENDER_CHALLENGE_RETRIES + 1):
                try:
                    return await _render_page(p)
                except BotChallengeError:
                    if attempt == _RENDER_CHALLENGE_RETRIES:
                        raise
                    log.info(
                        "dom.render.retry_bot_challenge",
                        url=url,
                        attempt=attempt + 1,
                    )
            raise AssertionError("unreachable")

        if pw is not None:
            html = await _render_with_challenge_retry(pw)
        else:
            try:
                from playwright.async_api import async_playwright
            except ImportError as err:
                raise RuntimeError(
                    "playwright is required for the dom scraper. "
                    "Install with: uv sync --group dev && uv run playwright install chromium"
                ) from err

            async with async_playwright() as p:
                html = await _render_with_challenge_retry(p)
    else:
        retry_limits = _status_retry_limits(config, url)
        resp = await fetch_response_with_status_retries(
            http,
            fetch_url,
            retry_limits=retry_limits,
            same_origin_redirects=same_origin_redirects,
            log_event="dom.fetch.retry_status",
        )
        # Detect redirect-to-gone BEFORE raise_for_status so the error page's
        # 200 doesn't shadow the actual archived signal. The redirect chain
        # may end on a 200 (rendered "this posting was removed" page), so
        # status alone never reveals gone-ness on these hosts.
        _check_gone_redirect(str(resp.url), gone_pattern, url)
        resp.raise_for_status()
        if document_fallback is not None:
            document = await _parse_static_document(resp.content, url, document_fallback)
            if document is not None:
                content, extension = document
                if artifact_dir is not None:
                    with contextlib.suppress(Exception):
                        (artifact_dir / f"source.{extension}").write_bytes(resp.content)
                log.debug("dom.document_fallback", url=url, document_type=extension)
                return content
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


register(
    "dom",
    scrape,
    can_handle=can_handle,
    parse_html=parse_html,
    probe_pw=probe_pw,
)
