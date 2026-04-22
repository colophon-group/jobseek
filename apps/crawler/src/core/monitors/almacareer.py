"""AlmaCareer (Capybara) monitor.

Alma Career runs the Capybara-framework career sites used by employers in
Czechia (``*.jobs.cz``) and Slovakia (``*.topjobs.sk``).  Both countries share
an identical GraphQL backend at ``api.capybara.lmc.cz`` — only the host,
``widgetId``, and per-tenant ``apiKey`` differ.

Detection pipeline
------------------
1. URL-host match (``*.jobs.cz`` or ``*.topjobs.sk``) — extracts slug.
2. Fetch ``https://{host}/assets/js/script.min.js`` — extracts widgetId + apiKey
   from the embedded ``widgets.main`` object.
3. Page-HTML scan for ``cdn.capybara.lmc.cz`` markers (fallback for
   custom-domain portals).

Discovery pipeline
------------------
* POST the ``LISTING_QUERY`` GraphQL query against
  ``https://api.capybara.lmc.cz/api/graphql/widget`` one page at a time.
  The backend caps pagination at 10 items per page regardless of the ``rps``
  argument sent by the upstream React bundle, so we iterate pages until
  ``paginator.lastPage`` is reached.
* For each discovered ad, fire a per-job ``jobAd(id: $jobId)`` query in
  parallel (bounded by a semaphore) to pull full ``content.htmlContent``.

Scraper step is skipped — the API returns HTML descriptions natively.
"""

from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import DiscoveredJob, fetch_page_text, register

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_JOBS = 50_000
GRAPHQL_URL = "https://api.capybara.lmc.cz/api/graphql/widget"

# Concurrency cap for per-job detail fetches.  Higher values risk 429s;
# 8 is a safe empirical middle ground.
_DETAIL_CONCURRENCY = 8

# Supported country TLDs and the domain suffix Alma Career uses there.
_COUNTRY_BY_SUFFIX = {
    ".jobs.cz": "cz",
    ".topjobs.sk": "sk",
}

_IGNORE_SLUGS = frozenset(
    {
        "www",
        "api",
        "cdn",
        "assets",
        "static",
        "app",
        "help",
    }
)

# Markers in page HTML that indicate the Capybara/AlmaCareer framework.
_PAGE_MARKERS = (
    "cdn.capybara.lmc.cz",
    "api.capybara.lmc.cz",
    'data-host="',  # The Capybara template sets data-host on <html>.
)

_HOST_RE = re.compile(r'data-host="([^"]+)"')

# widgetId + apiKey are embedded inside script.min.js as a JSON subtree:
#   "widgets":{"main":{"id":"...","apiKey":"...","detailPath":"...",...}}
_WIDGET_MAIN_RE = re.compile(
    r'"widgets"\s*:\s*\{\s*"main"\s*:\s*\{(?P<body>[^{}]*)\}',
)
_WIDGET_ID_RE = re.compile(r'"id"\s*:\s*"([0-9a-fA-F-]{36})"')
_API_KEY_RE = re.compile(r'"apiKey"\s*:\s*"([a-fA-F0-9]{32,})"')
_DETAIL_PATH_RE = re.compile(r'"detailPath"\s*:\s*"([^"]+)"')

# Employment-type mapping by stable numeric id (``employmentTypesObjects.id``).
# Labels are locale-specific (CZ/SK vary), so keying on the id is robust.
_EMPLOYMENT_TYPE_BY_ID: dict[str, str] = {
    "201300001": "full-time",
    "201300002": "part-time",
    "201300003": "contract",  # freelance / trade licence
    "201300004": "contract",  # agreement / DPP / DPČ
    "201300005": "internship",
    "201300006": "contract",  # civic-service / assignment
    "201300007": "part-time",  # brigáda
}

# Fallback label map — used when ids are missing or unknown.  Keys are
# locale-specific; matching is exact (after lowercasing).
_EMPLOYMENT_TYPE_BY_LABEL: dict[str, str] = {
    # CZ
    "práce na plný úvazek": "full-time",
    "práce na zkrácený úvazek": "part-time",
    "brigáda": "part-time",
    "dohoda o provedení práce": "contract",
    "dohoda o pracovní činnosti": "contract",
    "externí spolupráce": "contract",
    "stáž": "internship",
    # SK
    "práca na plný úväzok": "full-time",
    "práca na skrátený úväzok": "part-time",
    "živnosť": "contract",
    "dohoda": "contract",
    "stáž/prax": "internship",
    # EN (some tenants)
    "full-time work": "full-time",
    "part-time work": "part-time",
    "internship": "internship",
    "contract": "contract",
}

# Salary period -> unit mapping (localised → normalised).  CZ and SK share
# most tokens ("hodina", "rok") — SK-only differences ("mesiac") are added.
_PERIOD_UNIT_MAP: dict[str, str] = {
    # CZ
    "měsíc": "month",
    "hodina": "hour",
    "rok": "year",
    # SK-only
    "mesiac": "month",
    # EN (tenants using English labels)
    "month": "month",
    "hour": "hour",
    "year": "year",
}


# ---------------------------------------------------------------------------
# GraphQL query — trimmed from the version shipped by the React bundle to only
# the fields we actually need.  ``$rps`` and ``$useExampleData`` are required
# by the server even though the former is effectively ignored.
# ---------------------------------------------------------------------------

_LISTING_QUERY = """
query LISTING_QUERY(
  $widgetId: ID!
  $host: String
  $page: Int
  $filters: [JobAdFilter!]!
  $useExampleData: Boolean!
  $rps: Int
) {
  widget(
    id: $widgetId
    host: $host
    useExampleData: $useExampleData
  ) {
    config { languageIso }
    jobAdList(page: $page, filters: $filters, rps: $rps) {
      paginator {
        currentPage
        lastPage
        totalNumberOfItems
        numberOfItemsPerPage
      }
      groupedJobAds {
        ...jobAdGroup
        groups {
          ...jobAdGroup
          groups {
            ...jobAdGroup
            groups {
              ...jobAdGroup
            }
          }
        }
      }
    }
  }
}

fragment jobAdGroup on JobAdGroup {
  jobAds {
    id
    title
    validFrom
    languageIso
    teaser
    locations {
      country
      region
      district
      city
      cityPart
    }
    salary {
      min
      max
      period
      currency
    }
    employer { companyName }
    parameters {
      employmentTypesObjects { id label }
    }
    fieldsObjects { id label }
    professionsObjects { id label }
  }
}
""".strip()


_DETAIL_QUERY = """
query JOB_DETAIL(
  $widgetId: ID!
  $host: String
  $useExampleData: Boolean!
  $jobId: ID!
) {
  widget(id: $widgetId, host: $host, useExampleData: $useExampleData) {
    jobAd(id: $jobId) {
      id
      languageIso
      content { htmlContent }
    }
  }
}
""".strip()


# ---------------------------------------------------------------------------
# URL / host helpers
# ---------------------------------------------------------------------------


def _match_country(host: str) -> tuple[str, str] | None:
    """Return (country, slug) if ``host`` matches an AlmaCareer suffix."""
    host_l = host.lower().removeprefix("www.")
    for suffix, country in _COUNTRY_BY_SUFFIX.items():
        if host_l.endswith(suffix):
            slug = host_l.removesuffix(suffix)
            if slug and slug not in _IGNORE_SLUGS:
                return country, slug
    return None


def _host_from_board(board_url: str, metadata: dict | None) -> str | None:
    """Return the AlmaCareer host for a board record.

    The monitor accepts either a direct URL (``mcdonalds.jobs.cz/...``) or
    an explicit ``host`` / ``slug`` + ``country`` override in metadata.
    """
    metadata = metadata or {}
    host = metadata.get("host")
    if isinstance(host, str) and host:
        return host.lower()

    slug = metadata.get("slug")
    country = metadata.get("country")
    if slug and country:
        suffix = next((s for s, c in _COUNTRY_BY_SUFFIX.items() if c == country), None)
        if suffix:
            return f"{slug}{suffix}"

    parsed = urlparse(board_url)
    host_from_url = (parsed.hostname or "").lower().removeprefix("www.")
    if host_from_url and _match_country(host_from_url):
        return host_from_url

    return None


def _detail_url(host: str, detail_path: str, job_id: str) -> str:
    return f"https://{host}/{detail_path}?r=detail&id={job_id}"


# ---------------------------------------------------------------------------
# Widget-config extraction from script.min.js
# ---------------------------------------------------------------------------


def _extract_widget_config(script_text: str) -> dict | None:
    """Return ``{"id", "apiKey", "detail_path"}`` from a Capybara script bundle.

    The tenant's ``script.min.js`` embeds a JSON subtree with the widget
    configuration.  We slice the ``widgets.main`` object and pull out the
    three fields we need via independent regexes — tolerant to reordering.
    """
    block = _WIDGET_MAIN_RE.search(script_text)
    if block is None:
        return None
    body = block.group("body")
    id_match = _WIDGET_ID_RE.search(body)
    key_match = _API_KEY_RE.search(body)
    if id_match is None or key_match is None:
        return None
    detail_match = _DETAIL_PATH_RE.search(body)
    return {
        "id": id_match.group(1),
        "apiKey": key_match.group(1),
        "detail_path": detail_match.group(1) if detail_match else "detail-pozice",
    }


async def _fetch_widget_config(host: str, client: httpx.AsyncClient) -> dict | None:
    url = f"https://{host}/assets/js/script.min.js"
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None
        return _extract_widget_config(resp.text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _flatten_groups(group: dict | None) -> list[dict]:
    """Walk the recursive ``groupedJobAds`` tree and collect all ``jobAds``."""
    if not group:
        return []
    out: list[dict] = []
    ads = group.get("jobAds") or []
    out.extend(ads)
    for child in group.get("groups") or []:
        out.extend(_flatten_groups(child))
    return out


def _parse_location(loc: dict | None) -> str | None:
    """Build ``"City, Region, Country"`` — most specific parts first."""
    if not loc:
        return None
    parts = []
    for key in ("cityPart", "city", "district", "region"):
        val = loc.get(key)
        if val and val not in parts:
            parts.append(val)
    country = loc.get("country")
    if country and country not in parts:
        parts.append(country)
    # Avoid emitting ``cityPart, city`` pairs that duplicate each other at
    # different granularity; keep at most two leading parts.
    if len(parts) > 3:
        parts = parts[:2] + parts[-1:]
    return ", ".join(parts) if parts else None


def _parse_locations(locs: list[dict] | None) -> list[str] | None:
    if not locs:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        label = _parse_location(loc)
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out or None


def _parse_salary(salary: dict | None) -> dict | None:
    if not salary:
        return None
    currency = salary.get("currency")
    smin = salary.get("min")
    smax = salary.get("max")
    if not currency or (smin is None and smax is None):
        return None
    period_raw = (salary.get("period") or "").lower()
    unit = _PERIOD_UNIT_MAP.get(period_raw, "month")

    def _to_num(v):
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "currency": currency,
        "min": _to_num(smin),
        "max": _to_num(smax),
        "unit": unit,
    }


def _parse_employment_type(params: dict | None) -> str | None:
    if not params:
        return None
    types = params.get("employmentTypesObjects") or []
    for t in types:
        type_id = t.get("id")
        if type_id and type_id in _EMPLOYMENT_TYPE_BY_ID:
            return _EMPLOYMENT_TYPE_BY_ID[type_id]
        label = (t.get("label") or "").strip().lower()
        if label in _EMPLOYMENT_TYPE_BY_LABEL:
            return _EMPLOYMENT_TYPE_BY_LABEL[label]
    return None


def _parse_date(raw: str | None) -> str | None:
    """Return the ISO date portion of ``validFrom`` (YYYY-MM-DD)."""
    if not raw or not isinstance(raw, str):
        return None
    # Formats seen: ``2026-04-22T09:10:22+00:00``, ``2026-04-22T09:10:22+02:00``.
    return raw[:10] if len(raw) >= 10 else None


def _parse_job(raw: dict, host: str, detail_path: str, country: str) -> DiscoveredJob | None:
    job_id = raw.get("id")
    title = raw.get("title")
    if not job_id or not title:
        return None

    url = _detail_url(host, detail_path, str(job_id))
    locations = _parse_locations(raw.get("locations"))
    salary = _parse_salary(raw.get("salary"))
    employment_type = _parse_employment_type(raw.get("parameters"))
    date_posted = _parse_date(raw.get("validFrom"))
    language = raw.get("languageIso")
    teaser = raw.get("teaser")

    employer = (raw.get("employer") or {}).get("companyName")
    fields = [f.get("label") for f in raw.get("fieldsObjects") or [] if f.get("label")]
    professions = [p.get("label") for p in raw.get("professionsObjects") or [] if p.get("label")]

    metadata: dict = {"id": str(job_id), "country": country}
    if employer:
        metadata["company_name"] = employer
    if fields:
        metadata["fields"] = fields
    if professions:
        metadata["professions"] = professions

    return DiscoveredJob(
        url=url,
        title=title,
        # Description may be overwritten with full HTML after the detail fetch.
        description=teaser,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        base_salary=salary,
        language=language,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# GraphQL calls
# ---------------------------------------------------------------------------


def _build_headers(api_key: str, host: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Origin": f"https://{host}",
        "Referer": f"https://{host}/",
        "Accept": "*/*",
    }


async def _post_graphql(
    client: httpx.AsyncClient,
    *,
    query: str,
    variables: dict,
    api_key: str,
    host: str,
) -> dict:
    resp = await client.post(
        GRAPHQL_URL,
        headers=_build_headers(api_key, host),
        content=json.dumps({"query": query, "variables": variables}),
    )
    resp.raise_for_status()
    payload = resp.json()
    if errors := payload.get("errors"):
        # Raise the first error's message so board processing surfaces it.
        msg = errors[0].get("message") if isinstance(errors, list) and errors else str(errors)
        raise RuntimeError(f"AlmaCareer GraphQL error: {msg}")
    return payload.get("data") or {}


async def _fetch_list_page(
    client: httpx.AsyncClient,
    *,
    widget_id: str,
    api_key: str,
    host: str,
    page: int,
) -> dict:
    data = await _post_graphql(
        client,
        query=_LISTING_QUERY,
        variables={
            "widgetId": widget_id,
            "host": host,
            "useExampleData": False,
            "page": page,
            "filters": [],
            "rps": 100,  # server caps at 10 — requested for completeness.
        },
        api_key=api_key,
        host=host,
    )
    widget = data.get("widget") or {}
    return widget.get("jobAdList") or {}


async def _fetch_job_html(
    client: httpx.AsyncClient,
    *,
    widget_id: str,
    api_key: str,
    host: str,
    job_id: str,
) -> str | None:
    try:
        data = await _post_graphql(
            client,
            query=_DETAIL_QUERY,
            variables={
                "widgetId": widget_id,
                "host": host,
                "useExampleData": False,
                "jobId": job_id,
            },
            api_key=api_key,
            host=host,
        )
    except Exception as exc:
        log.warning("almacareer.detail_failed", job_id=job_id, host=host, error=str(exc))
        return None
    widget = data.get("widget") or {}
    job_ad = widget.get("jobAd") or {}
    content = job_ad.get("content") or {}
    html = content.get("htmlContent")
    return html if isinstance(html, str) and html.strip() else None


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch all AlmaCareer job ads for a board, paginating the GraphQL API."""

    metadata = board.get("metadata") or {}
    board_url = board.get("board_url") or ""
    host = _host_from_board(board_url, metadata)
    if not host:
        raise ValueError(
            f"Cannot derive AlmaCareer host from board_url={board_url!r}; "
            "set metadata.host or metadata.{slug,country}."
        )

    country_match = _match_country(host)
    country = country_match[0] if country_match else (metadata.get("country") or "cz")

    # widgetId + apiKey may be pre-seeded in metadata to skip the script fetch.
    widget_id = metadata.get("widget_id")
    api_key = metadata.get("api_key")
    detail_path = metadata.get("detail_path")

    if not widget_id or not api_key:
        config = await _fetch_widget_config(host, client)
        if not config:
            raise ValueError(
                f"AlmaCareer widget config not found at https://{host}/assets/js/script.min.js"
            )
        widget_id = widget_id or config["id"]
        api_key = api_key or config["apiKey"]
        detail_path = detail_path or config["detail_path"]

    detail_path = detail_path or "detail-pozice"

    # ---- 1. Walk the paginator to collect raw ads ----
    raw_ads: list[dict] = []
    page = 1
    last_page = 1
    while page <= last_page:
        listing = await _fetch_list_page(
            client,
            widget_id=widget_id,
            api_key=api_key,
            host=host,
            page=page,
        )
        if not listing:
            break
        raw_ads.extend(_flatten_groups(listing.get("groupedJobAds")))
        paginator = listing.get("paginator") or {}
        last_page = int(paginator.get("lastPage") or 1)
        page += 1
        if len(raw_ads) >= MAX_JOBS:
            log.warning("almacareer.truncated", host=host, collected=len(raw_ads), cap=MAX_JOBS)
            break

    # ---- 2. Parse into DiscoveredJob ----
    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for raw in raw_ads:
        parsed = _parse_job(raw, host=host, detail_path=detail_path, country=country)
        if parsed is None:
            continue
        if parsed.url in seen:
            continue
        seen.add(parsed.url)
        jobs.append(parsed)

    # ---- 3. Fill in full HTML descriptions in parallel ----
    sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def _hydrate(job: DiscoveredJob) -> None:
        job_id = (job.metadata or {}).get("id")
        if not job_id:
            return
        async with sem:
            html = await _fetch_job_html(
                client,
                widget_id=widget_id,
                api_key=api_key,
                host=host,
                job_id=job_id,
            )
        if html:
            job.description = html

    if jobs:
        await asyncio.gather(*[_hydrate(j) for j in jobs])

    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect AlmaCareer via URL suffix or HTML markers + widget-config probe."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    matched_country_slug: tuple[str, str] | None = None
    if host:
        matched_country_slug = _match_country(host)

    if matched_country_slug is None and client is not None:
        # HTML fallback: the tenant may run on a custom domain.
        html = await fetch_page_text(url, client)
        if html:
            has_marker = any(marker in html for marker in _PAGE_MARKERS)
            if has_marker:
                host_match = _HOST_RE.search(html)
                if host_match:
                    host = host_match.group(1).lower()
                    matched_country_slug = _match_country(host)

    if matched_country_slug is None or not host:
        return None

    country, slug = matched_country_slug

    result: dict = {"slug": slug, "host": host, "country": country}

    if client is None:
        return result

    config = await _fetch_widget_config(host, client)
    if not config:
        # Host matched but no Capybara script — not an AlmaCareer tenant.
        return None

    result["widget_id"] = config["id"]
    result["api_key"] = config["apiKey"]
    result["detail_path"] = config["detail_path"]

    # Probe the listing once to report a job count.
    try:
        listing = await _fetch_list_page(
            client,
            widget_id=config["id"],
            api_key=config["apiKey"],
            host=host,
            page=1,
        )
        paginator = listing.get("paginator") or {}
        total = paginator.get("totalNumberOfItems")
        if isinstance(total, int):
            result["jobs"] = total
    except Exception:
        # Probe failure shouldn't block detection — we already know the tenant.
        pass

    return result


register("almacareer", discover, cost=10, can_handle=can_handle, rich=True)
