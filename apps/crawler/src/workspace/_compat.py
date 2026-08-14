"""Static monitor/scraper type classification for standalone use.

Mirrors the runtime registries in ``src.core.monitors`` and
``src.core.scrapers`` so that workspace commands and CI scripts can
classify types without importing the full crawler core (which pulls
in asyncpg, playwright, etc.).

A sync test in ``tests/test_compat.py`` asserts these sets stay in sync
with the actual registries.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl

from src.shared.adp import adp_board_from_url
from src.shared.avature import is_avature_vendor_url
from src.shared.beisen import beisen_board_from_url
from src.shared.cornerstone import cornerstone_board_from_url
from src.shared.darwinbox import darwinbox_board_from_url
from src.shared.dayforce import dayforce_board_from_url
from src.shared.gupy import gupy_tenant_from_url
from src.shared.jobvite import jobvite_board_from_url
from src.shared.keka import keka_board_from_url
from src.shared.pageup import pageup_board_from_url
from src.shared.recruiterbox import recruiterbox_board_from_url
from src.shared.successfactors import (
    is_successfactors_host,
    successfactors_legacy_board_from_url,
)
from src.shared.taleo import taleo_board_from_url
from src.shared.ukg import is_ukg_url

_ICIMS_STATIC_QUERY_VALUES = {
    "in_iframe": "1",
    "o": "",
    "schemaId": "",
    "searchRelation": "keyword_all",
    "ss": "1",
}


def _icims_query_is_unscoped(query: str) -> bool:
    """Reject filters that would be lost by the host-wide native monitor."""
    params = parse_qsl(query, keep_blank_values=True)
    keys = [key for key, _value in params]
    if len(keys) != len(set(keys)):
        return False
    return all(
        (key == "pr" and value.isdigit()) or _ICIMS_STATIC_QUERY_VALUES.get(key) == value
        for key, value in params
    )


_RICH_MONITORS: frozenset[str] = frozenset(
    {
        "accenture",
        "adp",
        "almacareer",
        "amazon",
        "ashby",
        "bamboohr",
        "beisen",
        "brassring",
        "comeet",
        "cornerstone",
        "darwinbox",
        "dayforce",
        "deel",
        "dvinci",
        "gem",
        "greenhouse",
        "hibob",
        "hirehive",
        "hireology",
        "turbohire",
        "inline",
        "inploi",
        "jarvi",
        "jobylon",
        "keka",
        "kipt",
        "lever",
        "linkedin",
        "mokahr",
        "oracle_hcm",
        "pageup",
        "paycom",
        "paylocity",
        "pinpoint",
        "recruitee",
        "recruiter_co_kr",
        "rss",
        "traffit",
        "typify",
        "ukg",
        "welcometothejungle",
    }
)

# Personio is conditionally rich: XML feed provides descriptions,
# but the HTML fallback does not.  Richness is determined at runtime
# by ws run monitor based on actual description coverage.

# Crawler types whose ``auto_scraper_type()`` resolves to ("skip", None) —
# i.e. rich monitors with no enrichment. This is ``_RICH_MONITORS`` minus
# ``oracle_hcm``, ``adp``, ``bamboohr``, ``beisen``, ``inploi``, ``linkedin``,
# ``mokahr``, ``paycom``, ``pageup``, ``paylocity``, legacy ``rss``, ``typify``,
# and ``ukg``, which auto-resolve to enrichment scrapers. BambooHR uses a
# generic API preset; LinkedIn and Paycom use dedicated detail scrapers.
# Used by SQL filters and the ``_is_skip_no_scrape`` classifier so implicit
# rich boards (``scraper_type`` unset in metadata) are treated the same as
# explicit ``scraper_type = "skip"`` boards. See issue 01-rich-monitor-scheduling.
_AUTO_SKIP_CRAWLER_TYPES: frozenset[str] = _RICH_MONITORS - {
    "adp",
    "bamboohr",
    "beisen",
    "inploi",
    "linkedin",
    "mokahr",
    "oracle_hcm",
    "pageup",
    "paycom",
    "paylocity",
    "rss",
    "typify",
    "ukg",
}


def auto_skip_crawler_types() -> frozenset[str]:
    """Return crawler types that auto-resolve to skip-no-scrape."""
    return _AUTO_SKIP_CRAWLER_TYPES


_ALL_MONITOR_TYPES: frozenset[str] = _RICH_MONITORS | {
    "avature",
    "bite",
    "breezy",
    "eightfold",
    "gupy",
    "herp",
    "hrmos",
    "icims",
    "intervieweb",
    "jazzhr",
    "jobvite",
    "jobs_ch",
    "join",
    "personio",
    "recruiterbox",
    "practicematch",
    "taleo",
    "rippling",
    "smartrecruiters",
    "softgarden",
    "umantis",
    "workable",
    "workday",
    "ycombinator",
    "sitemap",
    "talentbrew",
    "phenom",
    "nextdata",
    "njoyn",
    "notion",
    "dom",
    "api_sniffer",
}


def api_monitor_types() -> frozenset[str]:
    """Return the set of monitor type names that return rich (full) job data."""
    return _RICH_MONITORS


def all_monitor_types() -> frozenset[str]:
    """Return the set of all known monitor type names."""
    return _ALL_MONITOR_TYPES


_ALL_SCRAPER_TYPES: frozenset[str] = frozenset(
    {
        "adp",
        "api_sniffer",
        "bite",
        "dom",
        "eightfold",
        "embedded",
        "jazzhr",
        "json-ld",
        "linkedin",
        "mokahr",
        "nextdata",
        "notion",
        "onlyfy",
        "oracle_hcm",
        "paycom",
        "paylocity",
        "pdf",
        "rippling",
        "skip",
        "smartrecruiters",
        "workable",
        "workday",
    }
)


def all_scraper_types() -> frozenset[str]:
    """Return the set of all known scraper type names."""
    return _ALL_SCRAPER_TYPES


def detect_ats_from_url(url: str) -> str | None:
    """Detect known ATS monitor type from a board URL, or None if unknown."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        _ = parsed.port
    except ValueError:
        return None

    # Exact host prefixes
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") or (
        host.startswith("job-boards.") and host.endswith(".greenhouse.io")
    ):
        return "greenhouse"
    if host == "jobs.lever.co":
        return "lever"
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and (
        (
            parsed.path.lower().startswith("/company/")
            and parsed.path.lower().rstrip("/").endswith("/jobs")
        )
        or (parsed.path.lower().startswith("/jobs/search") and "f_C=" in parsed.query)
    ):
        return "linkedin"
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "jobs.gem.com":
        return "gem"
    if host == "www.welcometothejungle.com" and re.fullmatch(
        r"/(?:[a-z]{2}/)?companies/[^/]+/jobs/?", parsed.path
    ):
        return "welcometothejungle"
    if adp_board_from_url(url) is not None:
        return "adp"
    if is_avature_vendor_url(url):
        return "avature"
    if beisen_board_from_url(url) is not None:
        return "beisen"
    if gupy_tenant_from_url(url) is not None:
        return "gupy"
    if cornerstone_board_from_url(url) is not None:
        return "cornerstone"
    if darwinbox_board_from_url(url) is not None:
        return "darwinbox"
    if dayforce_board_from_url(url) is not None:
        return "dayforce"
    if recruiterbox_board_from_url(url) is not None:
        return "recruiterbox"
    if keka_board_from_url(url) is not None:
        return "keka"
    if taleo_board_from_url(url) is not None:
        return "taleo"
    if is_ukg_url(url):
        return "ukg"
    if jobvite_board_from_url(url) is not None:
        return "jobvite"
    if pageup_board_from_url(url) is not None:
        return "pageup"
    if (
        host == "herp.careers"
        and not parsed.query
        and re.fullmatch(
            r"/v1/[a-z0-9][a-z0-9_-]{0,62}(?:/[A-Za-z0-9_~-]{6,64})?/?",
            parsed.path,
            re.IGNORECASE,
        )
    ):
        return "herp"
    if (
        host == "hrmos.co"
        and not parsed.query
        and re.fullmatch(
            r"/pages/[a-z0-9][a-z0-9_-]{0,62}/jobs"
            r"(?:/[A-Za-z0-9_-]{1,64})?/?",
            parsed.path,
            re.IGNORECASE,
        )
    ):
        return "hrmos"
    if host in ("comeet.com", "www.comeet.com", "comeet.co", "www.comeet.co"):
        return "comeet"
    if host == "jobs.deel.com":
        return "deel"
    if host == "apply.workable.com":
        return "workable"
    if host == "careers.smartrecruiters.com":
        return "smartrecruiters"
    if host.endswith(".breezy.hr"):
        return "breezy"
    if host.endswith(".bamboohr.com"):
        tenant = host.split(".", 1)[0]
        path = parsed.path.rstrip("/").lower()
        if tenant not in {"api", "app", "help", "static", "www"} and path in {
            "/careers",
            "/jobs/embed2.php",
        }:
            return "bamboohr"
    if host in {"paycomonline.net", "www.paycomonline.net"} and re.search(
        r"^/v4/ats/web\.php/portal/[0-9a-f]{32}/(?:career-page|jobs(?:/|$))",
        parsed.path,
        re.IGNORECASE,
    ):
        return "paycom"
    if (
        host.endswith(".applytojob.com")
        and host.count(".") == 2
        and re.fullmatch(
            r"(?:/apply(?:/jobs(?:/details/[A-Za-z0-9_-]+)?)?)?/?",
            parsed.path,
            re.IGNORECASE,
        )
    ):
        return "jazzhr"
    if (
        host.endswith(".icims.com")
        and host.count(".") == 2
        and host
        not in {
            "api.icims.com",
            "app.icims.com",
            "help.icims.com",
            "support.icims.com",
            "www.icims.com",
        }
        and re.fullmatch(
            r"(?:/jobs(?:/search|/\d+(?:/[^/?#]+)?/job)?)?/?",
            parsed.path,
            re.IGNORECASE,
        )
        and _icims_query_is_unscoped(parsed.query)
    ):
        return "icims"
    if host.endswith(".careers.hibob.com"):
        return "hibob"
    if host.endswith(".eightfold.ai"):
        return "eightfold"

    # Suffix-based patterns
    if host.endswith(".recruitee.com"):
        return "recruitee"
    if ".jobs.personio." in host:
        return "personio"
    if host.endswith(".pinpointhq.com"):
        return "pinpoint"
    if host.endswith(".mysmartrecruiters.com"):
        return "smartrecruiters"
    if host.endswith(".myworkdayjobs.com"):
        return "workday"
    if host.endswith(".rippling.com"):
        return "rippling"
    if host.endswith(".hireology.com"):
        return "hireology"
    if host.endswith(".hirehive.com"):
        return "hirehive"
    if host.endswith(".turbohire.co"):
        return "turbohire"
    if host.endswith(".dvinci-hr.com"):
        return "dvinci"
    if host == "intervieweb.it" or host.endswith(".intervieweb.it"):
        return "intervieweb"
    if host.endswith(".softgarden.io"):
        return "softgarden"
    if host.endswith(".traffit.com"):
        return "traffit"
    if host.endswith("recruiting.paylocity.com") and "/recruiting/jobs/" in parsed.path.lower():
        return "paylocity"

    # AlmaCareer (Capybara) — *.jobs.cz (CZ) and *.topjobs.sk (SK)
    if host.endswith(".jobs.cz") or host.endswith(".topjobs.sk"):
        return "almacareer"

    # JOIN — join.com/companies/{slug}
    if host in ("join.com", "www.join.com"):
        return "join"

    # jobs.ch — employer profiles backed by the public JobCloud search API
    if host in ("jobs.ch", "www.jobs.ch") and any(
        segment in parsed.path.lower() for segment in ("/firmen/", "/entreprises/", "/companies/")
    ):
        return "jobs_ch"

    # Jobylon — cdn.jobylon.com embed or emp.jobylon.com detail URLs
    if host == "cdn.jobylon.com" or host == "emp.jobylon.com" or host.endswith(".jobylon.com"):
        return "jobylon"

    # Umantis — recruitingapp-{ID}[.de|.ch].umantis.com
    if host.endswith(".umantis.com"):
        return "umantis"

    # Teamtailor — career sites on *.teamtailor.com
    if host.endswith(".teamtailor.com"):
        return "rss"

    # SAP SuccessFactors — modern CSB hosts and strict legacy company URLs.
    if successfactors_legacy_board_from_url(url) is not None or is_successfactors_host(host):
        return "rss"

    if (
        host in ("ycombinator.com", "www.ycombinator.com")
        and "/companies/" in parsed.path
        and "/jobs" in parsed.path
    ):
        return "ycombinator"

    return None


_BREEZY_SCRAPER_CONFIG: dict = {
    "fallback": {
        "type": "dom",
        "config": {
            "render": False,
            "steps": [
                {"tag": "h1", "field": "title"},
                {
                    "tag": "li",
                    "attr": "class=location",
                    "field": "locations",
                    "regex": r"([A-Za-z .-]+,\s*[A-Z]{2})",
                },
                {
                    "tag": "p",
                    "field": "description",
                    "stop": "%BUTTON_APPLY_TO_POSITION%",
                    "html": True,
                },
            ],
        },
    },
}


def auto_scraper_type(
    monitor_type: str,
    config: dict | None = None,
) -> tuple[str, dict | None] | None:
    """Return the auto-configured scraper (type, config) for a monitor, or None.

    Some monitors automatically determine the scraper:
    - Rich monitors (greenhouse, lever, etc.) → ("skip", None)
    - Workday → ("workday", None)
    - Breezy → ("json-ld", {fallback dom config})
    - api_sniffer/nextdata with ``fields`` → ("skip", None)

    Returns None when manual scraper selection is needed.
    """
    # oracle_hcm is a rich monitor (returns DiscoveredJob with title/location/date)
    # but needs a scraper for descriptions. The ``enrich`` key in scraper_config
    # tells the batch processor to schedule scrapes for newly discovered jobs
    # even though the monitor is rich.  Without ``enrich``, rich monitors skip
    # scraping entirely (is_rich_no_scrape = is_rich and not enrich_fields).
    if monitor_type == "oracle_hcm":
        return ("oracle_hcm", {"enrich": ["description"]})
    if monitor_type == "mokahr":
        return ("mokahr", {"enrich": ["description"]})
    if monitor_type == "inploi":
        return ("json-ld", {"enrich": ["description"]})
    if monitor_type == "typify":
        return ("json-ld", {"enrich": ["description"]})
    if monitor_type == "jobs_ch":
        return ("json-ld", None)
    if monitor_type == "bamboohr":
        return (
            "api_sniffer",
            {
                "api_url": "https://{tenant}.bamboohr.com/careers/{id}/detail",
                "url_pattern": (
                    r"^https://(?P<tenant>[a-z0-9-]+)\.bamboohr\.com/"
                    r"careers/(?P<id>\d+)(?:/|$)"
                ),
                "json_path": "result.jobOpening",
                "fields": {
                    "title": "jobOpeningName",
                    "description": "description",
                    "locations": {
                        "concat": [
                            "not_null(location.city, atsLocation.city)",
                            (
                                "not_null(location.state, location.province, "
                                "atsLocation.state, atsLocation.province)"
                            ),
                            (
                                "not_null(location.addressCountry, location.country, "
                                "atsLocation.country)"
                            ),
                        ],
                        "separator": ", ",
                    },
                    "employment_type": "employmentStatusLabel",
                    "job_location_type": {
                        "path": "not_null(locationType, isRemote)",
                        "map": {
                            "0": "onsite",
                            "1": "remote",
                            "2": "hybrid",
                            "True": "remote",
                        },
                    },
                    "date_posted": "datePosted",
                    "metadata.department": "departmentLabel",
                    "metadata.department_id": "departmentId",
                    "metadata.minimum_experience": "minimumExperience",
                },
                "enrich": [
                    "description",
                    "locations",
                    "employment_type",
                    "job_location_type",
                    "date_posted",
                ],
            },
        )
    if monitor_type == "adp":
        return (
            "adp",
            {
                "enrich": [
                    "title",
                    "description",
                    "locations",
                    "employment_type",
                    "date_posted",
                    "base_salary",
                ],
            },
        )
    if monitor_type == "paycom":
        return (
            "paycom",
            {
                "enrich": [
                    "title",
                    "description",
                    "locations",
                    "employment_type",
                    "job_location_type",
                    "date_posted",
                    "base_salary",
                ],
            },
        )
    if monitor_type == "paylocity":
        return (
            "paylocity",
            {"enrich": ["description", "employment_type", "job_location_type"]},
        )
    if monitor_type == "linkedin":
        return (
            "linkedin",
            {"enrich": ["description", "employment_type", "job_location_type"]},
        )
    if monitor_type == "pageup":
        return (
            "dom",
            {
                "gone_url_pattern": r"/listing/\?jobnotfound=true(?:&|$)",
                "scope": "#job-content",
                "steps": [
                    {
                        "tag": "h3",
                        "offset": 1,
                        "field": "description",
                        "html": True,
                        "stop": "Back to search results",
                        "optional": True,
                    },
                    {
                        "tag": "dt",
                        "text": "Categories:",
                        "offset": 2,
                        "field": "description",
                        "html": True,
                        "stop": "Advertised:",
                        "optional": True,
                        "from": 0,
                    },
                    {
                        "tag": "p",
                        "text": "Categories:",
                        "offset": 1,
                        "field": "description",
                        "html": True,
                        "stop": "Advertised:",
                        "optional": True,
                        "from": 0,
                    },
                    {
                        "tag": "p",
                        "text": "Job no:",
                        "offset": 1,
                        "field": "description",
                        "html": True,
                        "stop": "Advertised:",
                        "optional": True,
                        "from": 0,
                    },
                ],
                "enrich": ["description"],
            },
        )
    if monitor_type == "rss" and (config or {}).get("variant") == "legacy":
        return (
            "dom",
            {
                "scope": ".joqReqDescription",
                "steps": [
                    {
                        "field": "description",
                        "html": True,
                        "stop_count": 10_000,
                    }
                ],
                "enrich": ["description"],
            },
        )
    if monitor_type == "ukg":
        return (
            "embedded",
            {
                "pattern": r"new\s+US\.Opportunity\.CandidateOpportunityDetail\s*\(",
                # Title is extracted for scraper observability and safe empty-field
                # backfill; the enrichment allowlist still updates description only.
                "fields": {"title": "Title", "description": "Description"},
                "enrich": ["description"],
            },
        )
    if monitor_type == "beisen":
        variant = (config or {}).get("variant")
        if variant == "modern":
            return ("skip", None)
        if variant == "legacy":
            template = (config or {}).get("legacy_template")
            if template == "standard":
                return (
                    "dom",
                    {
                        "steps": [
                            {
                                "text": "工作职责",
                                "field": "description",
                                "html": True,
                                "stop": "现在申请",
                            },
                        ],
                        "enrich": ["description"],
                    },
                )
            if template == "inline":
                return (
                    "dom",
                    {
                        "steps": [
                            {
                                "text": "岗位职责",
                                "field": "description",
                                "html": True,
                                "stop": "立即申请",
                            },
                        ],
                        "enrich": ["description"],
                    },
                )
        return None
    if monitor_type in _RICH_MONITORS:
        return ("skip", None)
    if monitor_type == "join":
        return ("nextdata", None)
    if monitor_type == "jazzhr":
        return ("jazzhr", None)
    if monitor_type == "jobvite":
        return ("json-ld", None)
    if monitor_type == "icims":
        return ("json-ld", None)
    if monitor_type == "intervieweb":
        return ("json-ld", None)
    if monitor_type == "herp":
        return ("json-ld", None)
    if monitor_type == "gupy":
        return ("json-ld", None)
    if monitor_type == "hrmos":
        return ("json-ld", None)
    if monitor_type == "recruiterbox":
        return ("json-ld", None)
    if monitor_type == "taleo":
        return ("json-ld", None)
    if monitor_type == "avature":
        return (
            "dom",
            {
                "gone_url_pattern": r"/(?:Error|SearchJobs)(?:[/?#]|$)",
                "retry_statuses": {"406": 2},
                "steps": [
                    {"tag": "h2", "field": "title"},
                    {
                        "field": "description",
                        "html": True,
                        "stop": "Apply",
                        "optional": True,
                    },
                    {
                        "text": "Location",
                        "offset": 1,
                        "field": "locations",
                        "optional": True,
                        "from": 0,
                    },
                    {
                        "text": "Working time",
                        "offset": 1,
                        "field": "employment_type",
                        "optional": True,
                        "from": 0,
                    },
                    {
                        "text": "Posted",
                        "offset": 1,
                        "field": "date_posted",
                        "optional": True,
                        "from": 0,
                    },
                ],
            },
        )
    if monitor_type == "breezy":
        return ("json-ld", _BREEZY_SCRAPER_CONFIG)
    if monitor_type == "bite":
        return ("bite", None)
    if monitor_type == "rippling":
        return ("rippling", None)
    if monitor_type == "smartrecruiters":
        return ("smartrecruiters", None)
    if monitor_type == "workable":
        return ("workable", None)
    if monitor_type == "workday":
        return ("workday", None)
    if monitor_type == "eightfold":
        return ("eightfold", None)
    if monitor_type == "phenom":
        # Every tenant has a JSON-LD JobPosting on the detail page.
        # Tenants whose pages need Playwright render (mcdonalds-*, nationwide
        # detail) override with ``{"render": true}`` in boards.csv.
        return ("json-ld", None)
    if monitor_type == "practicematch":
        return ("json-ld", {"proxy": True})
    if monitor_type == "talentbrew":
        return ("json-ld", None)
    if monitor_type == "softgarden":
        return ("json-ld", None)
    if monitor_type == "ycombinator":
        return ("json-ld", None)
    if monitor_type in ("api_sniffer", "nextdata") and bool((config or {}).get("fields")):
        return ("skip", None)
    return None


def is_rich_monitor(monitor_type: str, config: dict | None = None) -> bool:
    """Check if a monitor type returns rich data (scraper not needed).

    Statically-rich monitors (greenhouse, lever, etc.) always return True.
    api_sniffer is rich only when ``fields`` is present in config.

    Note: this is narrower than ``auto_scraper_type``. Workday has an
    auto-configured scraper but is NOT rich (monitor returns URLs only).
    """
    return monitor_type in _RICH_MONITORS or (
        monitor_type in ("api_sniffer", "nextdata") and bool((config or {}).get("fields"))
    )
