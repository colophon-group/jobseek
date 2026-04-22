"""Jobylon monitor — Nordic ATS with iframe-embed widget.

Jobylon customers embed an iframe that points at one of:

    https://cdn.jobylon.com/jobs/companies/<company_id>/embed/v2/
    https://cdn.jobylon.com/jobs/company-groups/<company_group_id>/embed/v2/

The embed endpoint returns a self-contained HTML page that serves a
server-rendered AngularJS widget.  Inline ``<script>`` bodies assign
all job data to ``JBL.embed_v2['jobs']`` as a JavaScript object literal
with unquoted keys.  There is no separate JSON API — the widget ships
with the full dataset on every request.

Detail pages live at::

    https://emp.jobylon.com/jobs/<job_id>-<slug>/

and expose ``application/ld+json`` JobPosting payloads.  The monitor
itself is rich enough (title, locations, employment_type, date_posted,
language) to return ``DiscoveredJob``; descriptions are left to an
enrichment scraper if the board opts in.

Unknown company IDs return a 404 marketing page — useful as a
freshness signal.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

MAX_JOBS = 50_000

_EMBED_HOST = "cdn.jobylon.com"
_DETAIL_BASE = "https://emp.jobylon.com"

# Explicit allowlist used by ``can_handle`` to decide whether a URL is a
# first-party Jobylon host.  A broad ``endswith('.jobylon.com')`` check
# is too loose — any attacker-controlled subdomain could false-positive
# monitor selection.  The set below matches the known surface: the
# widget CDN, the detail-page origin, and the employee-facing portal
# plus the CSS/JS static host.
_JOBYLON_HOSTS: frozenset[str] = frozenset(
    {
        "jobylon.com",
        "cdn.jobylon.com",
        "emp.jobylon.com",
        "static-eu.jobylon.com",
    }
)

# Language code mapping — the embed stores human-readable names; the
# underlying ``klass`` map always has the 2-letter ISO code as
# ``job-lang-XX``.  We prefer the latter.
_LANG_CODE_RE = re.compile(r"job-lang-([a-z]{2})")

# Top-level embed-jobs block we need to capture.  The delimiter uses
# single quotes inside a bracketed key to avoid confusion with the
# widget's ng-init attributes.
_JOBS_BLOCK_RE = re.compile(
    r"JBL\.embed_v2\['jobs'\]\s*=\s*(\[.*?\]);",
    re.DOTALL,
)

# Regex to locate an ``iframe src=""`` pointing at cdn.jobylon.com, used
# during page-level detection of Jobylon.
_IFRAME_SRC_RE = re.compile(
    r"""cdn\.jobylon\.com/jobs/(?P<kind>companies|company-groups)/(?P<id>\d+)/embed""",
    re.IGNORECASE,
)

# Unquoted-key JS-object parser state machine.  Jobylon hands back an
# object literal (``{id: '1', title: 'X'}``) rather than real JSON, so
# we cannot use ``json.loads`` directly.  The parser converts the
# relevant subset into a Python structure by:
#   * quoting bare identifier keys,
#   * switching single-quoted strings into double-quoted JSON strings,
#   * turning ``true|false`` into JSON booleans (which they already are).
#
# The object contents are shallow (no nested unquoted keys beyond the
# ``klass`` sub-map, which uses quoted keys).  The function is linear
# and deterministic — we never descend into arbitrary nested JS.
_UNQUOTED_KEY_RE = re.compile(r"(?P<prefix>[\{,]\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:")


def _decode_js_string(value: str) -> str:
    r"""Decode a single-quoted JS string body (\\uXXXX, \\xXX, \\n, ...).

    Called on the raw bytes *between* the surrounding single quotes.
    """
    # Use Python's string_escape-style decoding via json after escaping
    # any inner double quotes.
    if not value:
        return ""
    # Shortcut: no escapes -> pass through as-is.
    if "\\" not in value:
        return value
    # Escape any bare double quotes so json.loads round-trips safely.
    escaped = value.replace('"', '\\"')
    try:
        return json.loads(f'"{escaped}"')
    except json.JSONDecodeError:
        return value


def _single_to_double_quoted(text: str) -> str:
    """Convert single-quoted JS strings to JSON double-quoted strings.

    Preserves escape sequences by running them through
    :func:`_decode_js_string` first, then re-emitting via ``json.dumps``.
    Ignores single quotes that appear inside already-quoted JSON strings
    (tracked by a running ``in_double`` flag).
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_double = False
    while i < n:
        ch = text[i]
        if ch == "\\" and in_double and i + 1 < n:
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if ch == "'" and not in_double:
            # Find the closing single quote, respecting \' escapes
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            body = text[i + 1 : j]
            decoded = _decode_js_string(body)
            out.append(json.dumps(decoded))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Trailing-comma stripper: removes ``, ]`` or ``, }`` (with any inner
# whitespace) that JavaScript allows but JSON does not.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _js_object_literal_to_json(text: str) -> str:
    """Translate the relevant JS-object-literal subset to valid JSON.

    Steps: quote unquoted keys, rewrite single-quoted strings into
    double-quoted JSON strings, then strip JS-style trailing commas.
    """
    quoted_keys = _UNQUOTED_KEY_RE.sub(
        lambda m: f'{m.group("prefix")}"{m.group("key")}":',
        text,
    )
    double_quoted = _single_to_double_quoted(quoted_keys)
    return _TRAILING_COMMA_RE.sub(r"\1", double_quoted)


def _parse_jobs_block(html: str) -> list[dict[str, Any]]:
    """Extract and parse the ``JBL.embed_v2['jobs']`` array from a page."""
    match = _JOBS_BLOCK_RE.search(html)
    if not match:
        return []
    raw = match.group(1)
    try:
        as_json = _js_object_literal_to_json(raw)
        data = json.loads(as_json)
    except json.JSONDecodeError:
        log.warning("jobylon.parse_failed", length=len(raw))
        return []
    if not isinstance(data, list):
        return []
    return data


def _extract_language(job: dict[str, Any]) -> str | None:
    """Prefer the ISO code from ``klass`` over the free-form ``language`` field."""
    klass = job.get("klass") or {}
    if isinstance(klass, dict):
        for key in klass:
            m = _LANG_CODE_RE.match(str(key))
            if m:
                return m.group(1)
    return None


_MONTHS_SV = {
    "januari": 1,
    "februari": 2,
    "mars": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "augusti": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
_MONTHS_NO = {
    "januar": 1,
    "februar": 2,
    "mars": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}
_MONTHS_DA = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
_MONTHS_FI = {
    "tammikuuta": 1,
    "helmikuuta": 2,
    "maaliskuuta": 3,
    "huhtikuuta": 4,
    "toukokuuta": 5,
    "kes\u00e4kuuta": 6,
    "hein\u00e4kuuta": 7,
    "elokuuta": 8,
    "syyskuuta": 9,
    "lokakuuta": 10,
    "marraskuuta": 11,
    "joulukuuta": 12,
}
_MONTHS_EN = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}

_MONTH_TABLES: tuple[dict[str, int], ...] = (
    _MONTHS_EN,
    _MONTHS_SV,
    _MONTHS_NO,
    _MONTHS_DA,
    _MONTHS_FI,
)

_DATE_PARTS_DMY_RE = re.compile(r"(\d{1,2})[\.\s]+([A-Za-z\u00c0-\u017e]+)[\.,\s]+(\d{4})")
_DATE_PARTS_MDY_RE = re.compile(r"([A-Za-z\u00c0-\u017e]+)\s+(\d{1,2})[\.,\s]+(\d{4})")


def _parse_localized_date(value: str | None) -> str | None:
    """Translate Jobylon's localized ``published_date`` to ISO-8601 (date only).

    Jobylon prints strings like ``21 april 2026`` (sv), ``21. april 2026`` (da),
    or ``April 21, 2026`` (en).  Returns ``None`` for unparseable or empty
    inputs.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    dmy = _DATE_PARTS_DMY_RE.search(value)
    if dmy:
        day_s, month_s, year_s = dmy.groups()
    else:
        mdy = _DATE_PARTS_MDY_RE.search(value)
        if not mdy:
            return None
        month_s, day_s, year_s = mdy.groups()
    month_key = month_s.lower()
    month: int | None = None
    for table in _MONTH_TABLES:
        if month_key in table:
            month = table[month_key]
            break
    if month is None:
        return None
    try:
        day = int(day_s)
        year = int(year_s)
        return datetime(year, month, day, tzinfo=UTC).date().isoformat()
    except ValueError:
        return None


def _detail_url(job_path: str) -> str:
    """Build an absolute detail URL from the embed's relative ``url`` field."""
    if job_path.startswith("http://") or job_path.startswith("https://"):
        return job_path
    if not job_path.startswith("/"):
        job_path = "/" + job_path
    return f"{_DETAIL_BASE}{job_path}"


def _parse_locations(job: dict[str, Any]) -> list[str] | None:
    locs = job.get("locations")
    if isinstance(locs, list):
        cleaned = [str(x).strip() for x in locs if isinstance(x, str) and x.strip()]
        if cleaned:
            return cleaned
    text = job.get("locations_text")
    if isinstance(text, str) and text.strip():
        return [text.strip()]
    return None


def _parse_job(job: dict[str, Any]) -> DiscoveredJob | None:
    """Map one inline-embed job record to :class:`DiscoveredJob`."""
    job_path = job.get("url")
    job_id = job.get("id")
    if not job_path or not job_id:
        return None

    url = _detail_url(str(job_path))
    title = job.get("title") if isinstance(job.get("title"), str) else None

    metadata: dict[str, Any] = {"id": str(job_id)}
    for src, key in (
        ("company_id", "company_id"),
        ("company", "company"),
        ("function", "function"),
        ("experience", "experience"),
        ("employment_type", "employment_type_label"),
        ("workspace", "workspace"),
        ("to_date", "to_date"),
        ("published_date", "published_date_raw"),
    ):
        val = job.get(src)
        if isinstance(val, str) and val and val != "None":
            metadata[key] = val

    departments = job.get("departments")
    if isinstance(departments, list):
        cleaned = [str(d).strip() for d in departments if isinstance(d, str) and d.strip()]
        if cleaned:
            metadata["departments"] = cleaned

    job_location_type: str | None = None
    workspace = job.get("workspace")
    if isinstance(workspace, str):
        ws = workspace.strip().lower()
        # Substring match — Jobylon embeds occasionally wrap the label
        # with a city ("Remote (Stockholm)"), prefix it ("Arbete distans"),
        # or append descriptors ("Hybrid - Copenhagen").  Exact-match
        # missed these; checks are ordered so a "remote" appearing
        # alongside "hybrid" still classifies as remote.
        is_remote = any(
            tok in ws for tok in ("remote", "distans", "etätyö", "hjemmefra", "fjernarbejde")
        )
        is_hybrid = "hybrid" in ws
        if is_remote:
            job_location_type = "TELECOMMUTE"
        elif is_hybrid:
            # Generic hybrid/partial-remote — leave job_location_type alone
            # (schema.org has no hybrid code) but retain the raw label in
            # metadata for downstream normalization.
            pass

    return DiscoveredJob(
        url=url,
        title=title,
        description=None,
        locations=_parse_locations(job),
        employment_type=None,  # Left to the salary/locale normalization pass.
        job_location_type=job_location_type,
        date_posted=_parse_localized_date(job.get("published_date")),
        language=_extract_language(job),
        metadata=metadata or None,
    )


# ── Embed URL helpers ────────────────────────────────────────────────────


def _embed_url(company_id: str | int | None, company_group_id: str | int | None) -> str:
    if company_group_id:
        return f"https://{_EMBED_HOST}/jobs/company-groups/{company_group_id}/embed/v2/"
    if company_id:
        return f"https://{_EMBED_HOST}/jobs/companies/{company_id}/embed/v2/"
    raise ValueError("Jobylon monitor requires company_id or company_group_id in metadata")


def _ids_from_url(url: str) -> tuple[str | None, str | None]:
    """Extract (company_id, company_group_id) from a cdn.jobylon.com URL."""
    match = _IFRAME_SRC_RE.search(url)
    if not match:
        return None, None
    if match.group("kind") == "company-groups":
        return None, match.group("id")
    return match.group("id"), None


# ── Discovery ────────────────────────────────────────────────────────────


async def _fetch_embed(url: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.warning("jobylon.fetch_error", url=url, error=str(exc))
        return None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        resp.raise_for_status()
    return resp.text


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch the Jobylon embed page and materialize :class:`DiscoveredJob`.

    Board metadata:

    ``company_id``
        Numeric Jobylon company id (single-brand customers).
    ``company_group_id``
        Numeric company-group id (multi-brand customers, e.g. McDonald's DK).

    Either key is required; when both are set, the group id wins because
    Jobylon still proxies the per-company data set through the group
    endpoint.
    """
    metadata = board.get("metadata") or {}
    company_id = metadata.get("company_id")
    company_group_id = metadata.get("company_group_id")
    if not company_id and not company_group_id:
        # Allow deriving from board_url when the URL points straight at
        # cdn.jobylon.com (useful for manual debugging).
        company_id, company_group_id = _ids_from_url(board.get("board_url") or "")

    url = _embed_url(company_id, company_group_id)
    html = await _fetch_embed(url, client)
    if html is None:
        raise BoardGoneError(
            f"Jobylon embed returned 404 for {url!r}",
            url=url,
        )

    raw_jobs = _parse_jobs_block(html)
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for item in raw_jobs:
        parsed = _parse_job(item)
        if parsed is None:
            continue
        if parsed.url in seen:
            continue
        seen.add(parsed.url)
        jobs.append(parsed)
        if len(jobs) >= MAX_JOBS:
            log.warning("jobylon.truncated", total=len(raw_jobs), cap=MAX_JOBS)
            break

    log.info(
        "jobylon.listed",
        company_id=company_id,
        company_group_id=company_group_id,
        jobs=len(jobs),
    )
    return jobs


# ── Probe / can_handle ───────────────────────────────────────────────────


_PAGE_MARKERS = (
    re.compile(r"cdn\.jobylon\.com/jobs/", re.IGNORECASE),
    re.compile(r"static-eu\.jobylon\.com", re.IGNORECASE),
    re.compile(r"emp\.jobylon\.com", re.IGNORECASE),
)


async def _probe_embed(
    company_id: str | None,
    company_group_id: str | None,
    client: httpx.AsyncClient,
) -> int | None:
    """Verify a Jobylon embed exists and count its jobs."""
    url = _embed_url(company_id, company_group_id)
    html = await _fetch_embed(url, client)
    if html is None:
        return None
    return len(_parse_jobs_block(html))


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Jobylon via either a direct cdn.jobylon.com URL or page markers."""
    host = (urlparse(url).hostname or "").lower()

    # Direct embed URL shortcut.  Narrowed to an explicit allowlist so
    # ``attacker.jobylon.com.example.org``-style hosts never match.
    if host in _JOBYLON_HOSTS:
        company_id, company_group_id = _ids_from_url(url)
        if company_id or company_group_id:
            result: dict[str, Any] = {}
            if company_id:
                result["company_id"] = company_id
            if company_group_id:
                result["company_group_id"] = company_group_id
            if client is not None:
                count = await _probe_embed(company_id, company_group_id, client)
                if count is not None:
                    result["jobs"] = count
            return result

    if client is None:
        return None

    # Page-scan fallback — look for an iframe/script pointing at
    # cdn.jobylon.com and pick the first company/company-group id.
    html = await fetch_page_text(url, client)
    if not html:
        return None
    if not any(marker.search(html) for marker in _PAGE_MARKERS):
        return None

    iframe_match = _IFRAME_SRC_RE.search(html)
    if not iframe_match:
        return None

    kind = iframe_match.group("kind")
    ident = iframe_match.group("id")
    if kind == "company-groups":
        company_id, company_group_id = None, ident
    else:
        company_id, company_group_id = ident, None

    count = await _probe_embed(company_id, company_group_id, client)
    result = {}
    if company_id:
        result["company_id"] = company_id
    if company_group_id:
        result["company_group_id"] = company_group_id
    if count is not None:
        result["jobs"] = count
    return result or None


register("jobylon", discover, cost=10, can_handle=can_handle, rich=True)
