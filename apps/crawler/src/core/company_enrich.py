"""Company enrichment — fetch structured metadata from JSON-LD and Wikidata.

Given a company website URL and name, extracts schema.org Organization
fields from the homepage and queries Wikidata for structured data.
Returns a merged CompanyMeta with source attribution.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
import structlog

log = structlog.get_logger()

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# ── JSON-LD extraction ────────────────────────────────────────────────

_CTRL_REPLACEMENTS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}

_ORG_TYPES = {
    "Organization",
    "Corporation",
    "LocalBusiness",
    "NGO",
    "EducationalOrganization",
    "GovernmentOrganization",
    "MedicalOrganization",
    "SportsOrganization",
}


def _escape_control_chars(raw: str) -> str:
    out: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            out.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ord(ch) < 0x20:
            out.append(_CTRL_REPLACEMENTS.get(ch, ""))
            continue
        out.append(ch)
    return "".join(out)


class _JsonLdExtractor(HTMLParser):
    """Extract JSON-LD blocks from HTML."""

    def __init__(self):
        super().__init__()
        self._in_jsonld = False
        self._data: list[str] = []
        self.results: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attr_dict = dict(attrs)
            if attr_dict.get("type") == "application/ld+json":
                self._in_jsonld = True
                self._data = []

    def handle_data(self, data):
        if self._in_jsonld:
            self._data.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            raw = "".join(self._data).strip()
            if raw:
                try:
                    self.results.append(json.loads(raw))
                except json.JSONDecodeError:
                    cleaned = _escape_control_chars(raw)
                    with contextlib.suppress(json.JSONDecodeError):
                        self.results.append(json.loads(cleaned))


class _MetaExtractor(HTMLParser):
    """Extract <meta name="description"> and og:description from HTML."""

    def __init__(self):
        super().__init__()
        self.description: str | None = None
        self.og_description: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attr_dict = dict(attrs)
        name = (attr_dict.get("name") or "").lower()
        prop = (attr_dict.get("property") or "").lower()
        content = attr_dict.get("content", "")
        if name == "description" and content:
            self.description = content.strip()
        elif prop == "og:description" and content:
            self.og_description = content.strip()


def _find_organization(data: dict | list) -> dict | None:
    """Recursively find an Organization-type object in JSON-LD."""
    if isinstance(data, list):
        for item in data:
            result = _find_organization(item)
            if result:
                return result
        return None
    if isinstance(data, dict):
        type_val = data.get("@type", "")
        if isinstance(type_val, str) and type_val in _ORG_TYPES:
            return data
        if isinstance(type_val, list) and any(t in _ORG_TYPES for t in type_val):
            return data
        graph = data.get("@graph")
        if isinstance(graph, list):
            return _find_organization(graph)
    return None


def _extract_from_jsonld(org: dict) -> dict:
    """Extract fields from a JSON-LD Organization object."""
    result: dict = {}

    desc = org.get("description")
    if isinstance(desc, str) and desc.strip():
        result["description"] = desc.strip()

    founding = org.get("foundingDate")
    if isinstance(founding, str):
        m = re.match(r"(\d{4})", founding)
        if m:
            result["founded_year"] = int(m.group(1))

    employees = org.get("numberOfEmployees")
    if isinstance(employees, dict):
        val = employees.get("value")
        if val is not None:
            result["employee_count"] = int(val)
        else:
            lo = employees.get("minValue")
            hi = employees.get("maxValue")
            if hi is not None:
                result["employee_count"] = int(hi)
            elif lo is not None:
                result["employee_count"] = int(lo)
    elif isinstance(employees, (int, float)):
        result["employee_count"] = int(employees)

    same_as = org.get("sameAs")
    if isinstance(same_as, str):
        result["same_as"] = [same_as]
    elif isinstance(same_as, list):
        result["same_as"] = [s for s in same_as if isinstance(s, str)]

    address = org.get("address")
    if isinstance(address, dict):
        parts = []
        for key in ("addressLocality", "addressRegion", "addressCountry"):
            v = address.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
        if parts:
            result["hq_location_name"] = ", ".join(parts)
    elif isinstance(address, str) and address.strip():
        result["hq_location_name"] = address.strip()

    return result


# ── Wikidata Action API ───────────────────────────────────────────────

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"

_WIKIDATA_USER_AGENT = (
    "jobseek-crawler/0.1 "
    "(https://github.com/colophon-group/jobseek; bot-contact@colophon-group.org)"
)

# Properties we extract from Wikidata entities
_PROPS = {
    "P571": "inception",  # founding date
    "P1128": "employees",  # number of employees
    "P452": "industry",  # industry (entity ref → needs label)
    "P159": "hq",  # HQ location (entity ref → needs label)
    "P749": "parent",  # parent org (entity ref → needs label)
    "P414": "ticker",  # stock ticker (string)
    "P1448": "legal_name",  # official name (monolingual text)
    "P856": "website",  # official website (URL)
    "P4264": "linkedin",  # LinkedIn company ID
    "P2037": "github",  # GitHub username
    "P2002": "twitter",  # Twitter/X username
}


async def _wikidata_api(http: httpx.AsyncClient, params: dict) -> dict | None:
    """Call Wikidata action API."""
    try:
        resp = await http.get(
            _WIKIDATA_API,
            params={**params, "format": "json"},
            headers={"User-Agent": _WIKIDATA_USER_AGENT},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("wikidata.api_failed", error=str(e))
        return None


async def _search_wikidata_entity(http: httpx.AsyncClient, name: str, website: str) -> str | None:
    """Search for a company entity by name, verify by website URL.

    Returns the QID if found, None otherwise.
    """
    data = await _wikidata_api(
        http,
        {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": 5,
        },
    )
    if not data:
        return None

    candidates = data.get("search", [])
    if not candidates:
        return None

    # Fetch all candidates in one call to verify website
    qids = [c["id"] for c in candidates]
    entity_data = await _wikidata_api(
        http,
        {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "claims",
            "format": "json",
        },
    )
    if not entity_data:
        return None

    website_normalized = website.rstrip("/").lower()

    for qid in qids:
        entity = entity_data.get("entities", {}).get(qid, {})
        claims = entity.get("claims", {})

        # Check P856 (official website) matches
        p856 = claims.get("P856", [])
        for claim in p856:
            try:
                url = claim["mainsnak"]["datavalue"]["value"]
                if url.rstrip("/").lower() == website_normalized:
                    return qid
            except (KeyError, TypeError):
                continue

    # Fallback: accept the first result only if its label closely matches
    # our search name AND its description suggests a company/organization.
    candidate_label = candidates[0].get("label", "").lower().strip()
    search_lower = name.lower().strip()
    # Require the candidate label to contain the search name or vice versa
    name_matches = search_lower in candidate_label or candidate_label in search_lower
    if name_matches:
        desc = candidates[0].get("description", "").lower()
        company_words = {
            "company",
            "corporation",
            "firm",
            "enterprise",
            "inc",
            "ltd",
            "gmbh",
            "ag",
            "sa",
            "startup",
            "platform",
            "service",
        }
        if any(w in desc for w in company_words):
            return candidates[0]["id"]

    return None


def _get_claim_value(claims: dict, prop: str) -> str | None:
    """Extract the first value for a property from entity claims."""
    prop_claims = claims.get(prop, [])
    if not prop_claims:
        return None
    snak = prop_claims[0].get("mainsnak", {})
    dv = snak.get("datavalue", {})
    dv_type = dv.get("type")
    value = dv.get("value")
    if not value:
        return None
    if dv_type == "string":
        return value
    if dv_type == "wikibase-entityid":
        return value.get("id")
    if dv_type == "time":
        return value.get("time", "")
    if dv_type == "quantity":
        return value.get("amount", "")
    if dv_type == "monolingualtext":
        return value.get("text", "")
    return str(value)


async def _resolve_labels(http: httpx.AsyncClient, qids: set[str]) -> dict[str, str]:
    """Resolve QIDs to English labels in a single API call."""
    if not qids:
        return {}
    data = await _wikidata_api(
        http,
        {
            "action": "wbgetentities",
            "ids": "|".join(sorted(qids)),
            "props": "labels",
            "languages": "en",
        },
    )
    if not data:
        return {}
    labels: dict[str, str] = {}
    for qid, entity in data.get("entities", {}).items():
        label = entity.get("labels", {}).get("en", {}).get("value")
        if label:
            labels[qid] = label
    return labels


async def _extract_from_wikidata(http: httpx.AsyncClient, qid: str) -> dict:
    """Fetch entity claims and extract structured fields."""
    data = await _wikidata_api(
        http,
        {
            "action": "wbgetentities",
            "ids": qid,
            "props": "claims",
        },
    )
    if not data:
        return {}

    entity = data.get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})

    result: dict = {"wikidata_id": qid}

    # Founding date
    inception = _get_claim_value(claims, "P571")
    if inception:
        m = re.match(r"\+?(\d{4})", inception)
        if m:
            result["founded_year"] = int(m.group(1))

    # Employee count
    employees = _get_claim_value(claims, "P1128")
    if employees:
        with contextlib.suppress(ValueError):
            result["employee_count"] = int(float(employees.lstrip("+")))

    # Ticker
    ticker = _get_claim_value(claims, "P414")
    if ticker:
        result["ticker_symbol"] = ticker

    # Legal name
    legal_name = _get_claim_value(claims, "P1448")
    if legal_name:
        result["legal_name"] = legal_name

    # Social links
    same_as: list[str] = []
    linkedin = _get_claim_value(claims, "P4264")
    if linkedin:
        same_as.append(f"https://www.linkedin.com/company/{linkedin}")
    github = _get_claim_value(claims, "P2037")
    if github:
        same_as.append(f"https://github.com/{github}")
    twitter = _get_claim_value(claims, "P2002")
    if twitter:
        same_as.append(f"https://twitter.com/{twitter}")
    if same_as:
        result["same_as"] = same_as

    # Collect QIDs that need label resolution
    qids_to_resolve: set[str] = set()

    industry_qid = _get_claim_value(claims, "P452")
    if industry_qid and industry_qid.startswith("Q"):
        qids_to_resolve.add(industry_qid)

    hq_qid = _get_claim_value(claims, "P159")
    if hq_qid and hq_qid.startswith("Q"):
        qids_to_resolve.add(hq_qid)

    parent_qid = _get_claim_value(claims, "P749")
    if parent_qid and parent_qid.startswith("Q"):
        qids_to_resolve.add(parent_qid)

    # Resolve all labels in one call
    labels = await _resolve_labels(http, qids_to_resolve)

    if industry_qid:
        label = labels.get(industry_qid)
        if label:
            result["industry_raw"] = label

    if hq_qid:
        label = labels.get(hq_qid)
        if label:
            result["hq_location_name"] = label

    if parent_qid:
        label = labels.get(parent_qid)
        if label:
            result["parent_org_name"] = label

    return result


# ── Industry matching ─────────────────────────────────────────────────

_INDUSTRIES: list[dict] | None = None


def _load_industries() -> list[dict]:
    global _INDUSTRIES
    if _INDUSTRIES is not None:
        return _INDUSTRIES

    path = DATA_DIR / "industries.csv"
    if not path.exists():
        _INDUSTRIES = []
        return _INDUSTRIES

    import csv

    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keywords = [k.strip().lower() for k in row.get("keywords", "").split(",") if k.strip()]
            rows.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "keywords": keywords,
                }
            )
    _INDUSTRIES = rows
    return _INDUSTRIES


def match_industry(raw_label: str) -> int | None:
    """Match a raw industry label to an industry ID via keyword matching."""
    if not raw_label:
        return None

    industries = _load_industries()
    label_lower = raw_label.lower()

    # Exact name match first
    for ind in industries:
        if ind["name"].lower() == label_lower:
            return ind["id"]

    # Keyword match
    for ind in industries:
        for kw in ind["keywords"]:
            if kw in label_lower or label_lower in kw:
                return ind["id"]

    return None


def get_industry_name(industry_id: int) -> str | None:
    """Get industry name by ID."""
    for ind in _load_industries():
        if ind["id"] == industry_id:
            return ind["name"]
    return None


# ── Employee count bucketing ──────────────────────────────────────────

_EMPLOYEE_RANGES = [
    (1, 10, 1),
    (11, 50, 2),
    (51, 200, 3),
    (201, 500, 4),
    (501, 1_000, 5),
    (1_001, 5_000, 6),
    (5_001, 10_000, 7),
    (10_001, float("inf"), 8),
]


def employee_count_to_range(count: int) -> int:
    """Map raw employee count to range bucket ID."""
    for lo, hi, bucket_id in _EMPLOYEE_RANGES:
        if lo <= count <= hi:
            return bucket_id
    return 8  # 10,001+


def range_to_label(bucket_id: int) -> str:
    """Human-readable label for an employee range bucket."""
    labels = {
        1: "1-10",
        2: "11-50",
        3: "51-200",
        4: "201-500",
        5: "501-1,000",
        6: "1,001-5,000",
        7: "5,001-10,000",
        8: "10,001+",
    }
    return labels.get(bucket_id, "?")


# ── Main enrichment function ─────────────────────────────────────────


@dataclass
class CompanyMeta:
    """Merged enrichment result with source attribution."""

    description: str | None = None
    industry_id: int | None = None
    industry_raw: str | None = None
    employee_count_range: int | None = None
    founded_year: int | None = None
    hq_location_name: str | None = None
    same_as: list[str] = field(default_factory=list)
    parent_org_name: str | None = None
    legal_name: str | None = None
    ticker_symbol: str | None = None
    wikidata_id: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    tier: str = "C"

    @property
    def extras(self) -> dict:
        """Build the extras JSONB dict for DB storage."""
        d: dict = {}
        if self.same_as:
            d["sameAs"] = self.same_as
        if self.parent_org_name:
            d["parentOrganization"] = {"name": self.parent_org_name}
        if self.legal_name:
            d["legalName"] = self.legal_name
        if self.ticker_symbol:
            d["tickerSymbol"] = self.ticker_symbol
        if self.wikidata_id:
            d["wikidataId"] = self.wikidata_id
        return d


async def enrich_company(
    website: str,
    name: str,
    http: httpx.AsyncClient,
    *,
    skip_wikidata: bool = False,
) -> CompanyMeta:
    """Fetch and merge company metadata from homepage JSON-LD and Wikidata."""

    meta = CompanyMeta()
    jsonld_data: dict = {}
    meta_desc: str | None = None

    # 1. Fetch homepage and extract JSON-LD + meta tags
    html: str | None = None
    try:
        resp = await http.get(website, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        log.info("company_enrich.http_failed_trying_playwright", url=website, error=str(e))
        # Fallback to Playwright for sites that block plain HTTP
        try:
            from src.shared.browser import render

            html = await render(website, {"timeout": 15_000, "wait": "domcontentloaded"})
        except Exception as e2:
            log.warning("company_enrich.playwright_failed", url=website, error=str(e2))

    if html:
        # JSON-LD
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        for block in extractor.results:
            org = _find_organization(block)
            if org:
                jsonld_data = _extract_from_jsonld(org)
                break

        # Meta tags
        meta_ext = _MetaExtractor()
        meta_ext.feed(html)
        meta_desc = meta_ext.description or meta_ext.og_description

    # 2. Query Wikidata (unless skipped)
    wiki_data: dict = {}

    if not skip_wikidata:
        # Search by name, verify by website URL, then fetch claims
        qid = await _search_wikidata_entity(http, name, website)
        if qid:
            wiki_data = await _extract_from_wikidata(http, qid)

    # 3. Merge — Wikidata wins for structured, JSON-LD/meta for text
    # Description: JSON-LD > meta > (agent fills)
    if jsonld_data.get("description"):
        meta.description = jsonld_data["description"]
        meta.sources["description"] = "jsonld"
    elif meta_desc:
        meta.description = meta_desc
        meta.sources["description"] = "meta"

    # Industry: Wikidata P452 label → match to industries.csv
    industry_raw = wiki_data.get("industry_raw")
    if industry_raw:
        meta.industry_raw = industry_raw
        matched = match_industry(industry_raw)
        if matched:
            meta.industry_id = matched
            meta.sources["industry"] = "wikidata"

    # Founded year: Wikidata > JSON-LD
    if wiki_data.get("founded_year"):
        meta.founded_year = wiki_data["founded_year"]
        meta.sources["founded_year"] = "wikidata"
    elif jsonld_data.get("founded_year"):
        meta.founded_year = jsonld_data["founded_year"]
        meta.sources["founded_year"] = "jsonld"

    # Employee count: Wikidata > JSON-LD → bucket
    emp_count = wiki_data.get("employee_count") or jsonld_data.get("employee_count")
    if emp_count:
        meta.employee_count_range = employee_count_to_range(emp_count)
        meta.sources["employee_count_range"] = (
            "wikidata" if wiki_data.get("employee_count") else "jsonld"
        )

    # HQ location: Wikidata > JSON-LD
    if wiki_data.get("hq_location_name"):
        meta.hq_location_name = wiki_data["hq_location_name"]
        meta.sources["hq_location_name"] = "wikidata"
    elif jsonld_data.get("hq_location_name"):
        meta.hq_location_name = jsonld_data["hq_location_name"]
        meta.sources["hq_location_name"] = "jsonld"

    # sameAs: merge JSON-LD + Wikidata, deduplicate
    all_same_as: list[str] = []
    seen_domains: set[str] = set()
    for url in jsonld_data.get("same_as", []) + wiki_data.get("same_as", []):
        domain = urlparse(url).netloc
        if domain not in seen_domains:
            seen_domains.add(domain)
            all_same_as.append(url)
    meta.same_as = all_same_as

    # Extras from Wikidata
    meta.parent_org_name = wiki_data.get("parent_org_name")
    meta.legal_name = wiki_data.get("legal_name")
    meta.ticker_symbol = wiki_data.get("ticker_symbol")
    meta.wikidata_id = wiki_data.get("wikidata_id")

    # 4. Classify tier
    has_desc = meta.description is not None
    has_industry = meta.industry_id is not None
    if has_desc and has_industry:
        meta.tier = "A"
    elif has_desc or has_industry:
        meta.tier = "B"
    else:
        meta.tier = "C"

    return meta
