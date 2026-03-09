"""Auto-discover career page candidates from a company homepage."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import yaml


@dataclass
class CareerPageCandidate:
    """A discovered career page / job board candidate."""

    url: str  # Board URL (after redirect resolution)
    source: str  # "homepage_link" | "ats_embed" | "redirect" | "blind_probe"
    monitor_type: str  # Detected type (e.g. "greenhouse")
    monitor_config: dict = field(default_factory=dict)
    score: float = 0.0  # 0.0–1.0 confidence
    link_text: str | None = None  # Anchor text if from homepage link
    link_href: str | None = None  # Original href before redirect
    comment: str = ""  # Human-readable summary
    job_link_hub: int | None = None  # Number of likely job-detail links on the page
    job_link_pattern: str | None = None  # Inferred regex pattern for job links


# ── Constants ──────────────────────────────────────────────────────────

# Career URL path patterns (EN + major EU languages)
_CAREER_PATH_RE = re.compile(
    r"/("
    # English
    r"careers?|jobs?|openings?|positions?|join[-_]?us|join[-_]?our[-_]?team"
    r"|work[-_]?with[-_]?us|open[-_]?positions?|vacancies|opportunities|hiring"
    # German
    r"|karriere|stellenangebote|offene[-_]?stellen|jobs?[-_]?angebote"
    # French
    r"|carrieres?|emplois?|recrutement|nos[-_]?offres|offres[-_]?d[-_]?emploi"
    # Italian
    r"|carriera|lavora[-_]?con[-_]?noi|posizioni[-_]?aperte"
    # Spanish
    r"|empleo|trabaja[-_]?con[-_]?nosotros|ofertas[-_]?de[-_]?empleo"
    # Dutch
    r"|vacatures|werken[-_]?bij"
    # Portuguese
    r"|vagas|trabalhe[-_]?conosco"
    # Swedish
    r"|lediga[-_]?jobb|jobba[-_]?hos[-_]?oss"
    # Polish
    r"|kariera|oferty[-_]?pracy|praca"
    r")",
    re.IGNORECASE,
)

# Career anchor text keywords (EN + major EU languages)
_CAREER_TEXT_RE = re.compile(
    r"\b("
    # English
    r"careers?|jobs?|open\s+positions?|join\s+(us|our\s+team)"
    r"|we.re\s+hiring|work\s+with\s+us|vacancies|opportunities|hiring"
    # German
    r"|karriere|stellenangebote|offene\s+stellen|jobs?\s*angebote"
    # French
    r"|carri[eè]res?|nos\s+offres|offres\s+d.emploi|recrutement|rejoignez[- ]nous"
    # Italian
    r"|carriera|lavora\s+con\s+noi|posizioni\s+aperte|unisciti\s+a\s+noi"
    # Spanish
    r"|empleo|trabaja\s+con\s+nosotros|ofertas\s+de\s+empleo|[uú]nete"
    # Dutch
    r"|vacatures|werken\s+bij"
    # Portuguese
    r"|vagas|trabalhe\s+conosco"
    # Swedish
    r"|lediga\s+jobb|jobba\s+hos\s+oss"
    # Polish
    r"|kariera|oferty\s+pracy"
    r")\b",
    re.IGNORECASE,
)

# Known ATS URL patterns for detection in raw HTML
_ATS_URL_RE = re.compile(
    r"https?://("
    r"boards\.greenhouse\.io/[\w-]+"
    r"|job-boards(?:\.[\w-]+)?\.greenhouse\.io/[\w-]+"
    r"|jobs\.ashbyhq\.com/[\w-]+"
    r"|jobs\.lever\.co/[\w-]+"
    r"|[\w-]+\.recruitee\.com"
    r"|[\w-]+\.jobs\.personio\.(?:de|com)"
    r"|[\w-]+\.pinpointhq\.com"
    r"|(?:jobs|careers)\.smartrecruiters\.com/[\w-]+"
    r"|[\w-]+\.mysmartrecruiters\.com"
    r"|apply\.workable\.com/[\w-]+"
    r"|[\w-]+\.breezy\.hr"
    r"|ats(?:\.[\w]+)?\.rippling\.com/[\w-]+"
    r"|careers\.hireology\.com/[\w-]+"
    r"|[\w-]+\.wd\d+\.myworkdayjobs\.com(?:/[\w-]+)?"
    # d.vinci
    r"|[\w-]+\.dvinci-hr\.com"
    # Softgarden
    r"|[\w-]+\.softgarden\.io"
    # TRAFFIT
    r"|[\w-]+\.traffit\.com"
    # Umantis
    r"|recruitingapp-\d+(?:\.\w+)?\.umantis\.com"
    # Teamtailor
    r"|(?:career|jobs?)\.[\w-]+\.teamtailor\.com"
    # SAP SuccessFactors
    r"|career\d*\.successfactors\.(?:eu|com)"
    r")",
    re.IGNORECASE,
)

# ATS URL templates for blind slug probing — domain-match fast path
_BLIND_PROBE_TEMPLATES: dict[str, str] = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "recruitee": "https://{slug}.recruitee.com",
    "personio": "https://{slug}.jobs.personio.de",
    "pinpoint": "https://{slug}.pinpointhq.com",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "rippling": "https://ats.rippling.com/{slug}/jobs",
    "hireology": "https://careers.hireology.com/{slug}",
}

# Maximum career links to follow from homepage
_MAX_LINKS = 18
_MAX_FOLLOWUP_LINKS = 18
_MAX_TRAVERSAL_DEPTH = 2
_MAX_TRAVERSED_PAGES = 36

_SEED_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/job/open",
    "/open-positions",
    "/vacancies",
    "/join-us",
    "/en/careers",
    "/en/jobs",
    "/de/karriere",
    "/fr/carrieres",
    "/es/empleo",
)


# ── Intermediate data ─────────────────────────────────────────────────


@dataclass
class _ExtractedLink:
    """Intermediate link extracted from homepage HTML."""

    url: str
    source: str  # "career_link" | "ats_embed"
    context: str  # "nav" | "header" | "footer" | "body"
    text: str | None
    base_score: float


@dataclass
class _ProbeLinkResult:
    """Result of probing one extracted link."""

    candidates: list[CareerPageCandidate] = field(default_factory=list)
    followup_links: list[_ExtractedLink] = field(default_factory=list)
    page: dict | None = None


# ── HTMLParser-based extractor ─────────────────────────────────────────


class _CareerLinkExtractor(HTMLParser):
    """Single-pass HTML parser extracting career links and ATS embeds."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self._base_host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
        self.links: list[_ExtractedLink] = []
        self._ats_urls: set[str] = set()

        # Context tracking
        self._in_head = False
        self._in_header = False
        self._in_nav = False
        self._in_footer = False

        # Anchor accumulation
        self._current_a_href: str | None = None
        self._current_a_text: list[str] = []

    def _context(self) -> str:
        if self._in_nav:
            return "nav"
        if self._in_header:
            return "header"
        if self._in_footer:
            return "footer"
        return "body"

    def _resolve(self, url: str) -> str | None:
        if not url or url.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
            return None
        return urljoin(self.base_url, url)

    def _check_ats_url(self, url: str) -> None:
        """Add a URL as an ATS embed if it matches a known ATS domain."""
        if not url:
            return
        match = _ATS_URL_RE.search(url)
        if not match:
            return
        ats_url = match.group(0)
        if ats_url in self._ats_urls:
            return
        self._ats_urls.add(ats_url)
        ctx = self._context()
        score = 0.95 if ctx in ("nav", "header") else 0.90
        self.links.append(
            _ExtractedLink(
                url=ats_url, source="ats_embed", context=ctx, text=None, base_score=score
            )
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        tag_l = tag.lower()

        # Context entry
        if tag_l == "head":
            self._in_head = True
        elif tag_l == "header":
            self._in_header = True
        elif tag_l == "nav":
            self._in_nav = True
        elif tag_l == "footer":
            self._in_footer = True

        # Skip head content
        if self._in_head:
            return

        # Check all attribute values for ATS URLs
        for attr_val in a.values():
            if "://" in attr_val:
                self._check_ats_url(attr_val)

        # Track anchor start
        if tag_l == "a":
            href = a.get("href", "")
            resolved = self._resolve(href) if href else None
            self._current_a_href = resolved
            self._current_a_text = []

        # Check iframe src for ATS embeds
        if tag_l == "iframe":
            src = a.get("src", "")
            if src:
                resolved = self._resolve(src)
                if resolved:
                    self._check_ats_url(resolved)

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()

        # Context exit
        if tag_l == "head":
            self._in_head = False
        elif tag_l == "header":
            self._in_header = False
        elif tag_l == "nav":
            self._in_nav = False
        elif tag_l == "footer":
            self._in_footer = False

        # Process completed anchor
        if tag_l == "a" and self._current_a_href is not None:
            href = self._current_a_href
            text = " ".join(self._current_a_text).strip()
            self._current_a_href = None
            self._current_a_text = []

            # Check if this is a career link
            is_career = False

            # Check href path
            parsed = urlparse(href)
            if _CAREER_PATH_RE.search(parsed.path):
                is_career = True

            # Check anchor text
            if not is_career and text and _CAREER_TEXT_RE.search(text):
                is_career = True

            if is_career:
                ctx = self._context()
                if ctx in ("nav", "header"):
                    score = 0.85
                elif ctx == "footer":
                    score = 0.65
                else:
                    score = 0.55

                self.links.append(
                    _ExtractedLink(
                        url=href,
                        source="career_link",
                        context=ctx,
                        text=text or None,
                        base_score=score,
                    )
                )

    def handle_data(self, data: str) -> None:
        if self._current_a_href is not None:
            self._current_a_text.append(data)


# ── Phase 1: Link extraction ──────────────────────────────────────────


def _extract_links(html: str, base_url: str) -> list[_ExtractedLink]:
    """Extract career links and ATS embeds from homepage HTML.

    Returns links sorted by score descending, deduplicated by URL.
    """
    # Structured extraction via HTMLParser
    parser = _CareerLinkExtractor(base_url)
    parser.feed(html)

    # Also scan raw HTML for ATS URLs (catches scripts, comments, etc.)
    raw_ats = _scan_ats_urls_in_html(html)

    # Merge and dedup by URL (keep highest score)
    by_url: dict[str, _ExtractedLink] = {}
    for link in parser.links + raw_ats:
        if link.url in by_url:
            existing = by_url[link.url]
            if link.base_score > existing.base_score:
                by_url[link.url] = link
        else:
            by_url[link.url] = link

    return sorted(by_url.values(), key=lambda lnk: lnk.base_score, reverse=True)


def _merge_links(*groups: list[_ExtractedLink]) -> list[_ExtractedLink]:
    """Merge link groups by URL and keep the highest-score entry per URL."""
    by_url: dict[str, _ExtractedLink] = {}
    for group in groups:
        for link in group:
            current = by_url.get(link.url)
            if current is None or link.base_score > current.base_score:
                by_url[link.url] = link
    return sorted(by_url.values(), key=lambda lnk: lnk.base_score, reverse=True)


def _scan_ats_urls_in_html(html: str) -> list[_ExtractedLink]:
    """Find ATS URLs anywhere in raw HTML source (scripts, comments, etc.)."""
    found: list[_ExtractedLink] = []
    seen: set[str] = set()
    for match in _ATS_URL_RE.finditer(html):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            found.append(
                _ExtractedLink(
                    url=url, source="ats_embed", context="body", text=None, base_score=0.90
                )
            )
    return found


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _root_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _same_site(url_a: str, url_b: str) -> bool:
    host_a = _host(url_a)
    host_b = _host(url_b)
    if not host_a or not host_b:
        return False
    if host_a == host_b:
        return True
    return _root_domain(host_a) == _root_domain(host_b)


def _seed_links(homepage_url: str) -> list[_ExtractedLink]:
    """Generate deterministic same-site discovery seeds for deeper traversal."""
    out: list[_ExtractedLink] = []
    for path in _SEED_CAREER_PATHS:
        out.append(
            _ExtractedLink(
                url=urljoin(homepage_url, path),
                source="seed_path",
                context="body",
                text=path,
                base_score=0.45,
            )
        )
    return out


def _jobs_hint_from_metadata(metadata: dict | None) -> int:
    if not metadata:
        return 0
    for key in ("jobs", "urls", "count"):
        value = metadata.get(key)
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0


def _rerank_candidates(candidates: list[CareerPageCandidate], homepage_url: str) -> None:
    """Adjust candidate scores using traversal evidence and provenance."""
    for c in candidates:
        score = c.score
        same_site = _same_site(homepage_url, c.url)
        if same_site:
            score += 0.12
        else:
            score -= 0.08

        if c.source == "blind_probe":
            score -= 0.30
        elif c.source == "ats_embed":
            score -= 0.10
        elif c.source == "seed_path":
            score += 0.05

        if c.job_link_pattern:
            score += 0.08
        if c.job_link_hub:
            score += min(0.12, c.job_link_hub / 120.0)

        jobs_hint = _jobs_hint_from_metadata(c.monitor_config)
        if jobs_hint > 0:
            score += min(0.10, jobs_hint / 500.0)

        c.score = max(0.0, min(1.0, score))


def _save_discovery_state(
    path: Path,
    *,
    homepage_url: str,
    seed_links: list[_ExtractedLink],
    pages: list[dict],
    candidates: list[CareerPageCandidate],
) -> None:
    """Persist traversal evidence so later commands can reuse context."""
    try:
        data = {
            "version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "homepage_url": homepage_url,
            "seed_links": [
                {
                    "url": link.url,
                    "source": link.source,
                    "context": link.context,
                    "score": round(link.base_score, 4),
                    "text": link.text,
                }
                for link in seed_links
            ],
            "pages": pages,
            "candidates": [
                {
                    "url": c.url,
                    "source": c.source,
                    "monitor_type": c.monitor_type,
                    "score": round(c.score, 4),
                    "jobs_hint": _jobs_hint_from_metadata(c.monitor_config),
                    "job_link_hub": c.job_link_hub,
                    "job_link_pattern": c.job_link_pattern,
                    "same_site_as_homepage": _same_site(homepage_url, c.url),
                    "monitor_config": c.monitor_config,
                    "comment": c.comment,
                }
                for c in candidates
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    except Exception:
        # Discovery state is advisory; don't fail board discovery if writing fails.
        return


# ── Phase 2: Probe career links ───────────────────────────────────────


async def _probe_link(
    link: _ExtractedLink,
    client: httpx.AsyncClient,
    *,
    collect_followups: bool = False,
) -> _ProbeLinkResult:
    """Follow a career link, probe for monitor type, return candidates."""
    from src.core.monitors import probe_all_monitors
    from src.workspace._compat import detect_ats_from_url

    # Follow redirects to get final URL
    try:
        resp = await client.get(link.url, follow_redirects=True)
        if resp.status_code >= 400:
            return _ProbeLinkResult(
                page={
                    "requested_url": link.url,
                    "final_url": str(resp.url),
                    "source": link.source,
                    "status_code": resp.status_code,
                    "fetch_ok": False,
                    "outgoing_links": 0,
                    "likely_job_links": 0,
                    "job_link_pattern": None,
                    "detected_monitors": [],
                }
            )
        final_url = str(resp.url)
        page_html = resp.text or ""
    except Exception:
        return _ProbeLinkResult(
            page={
                "requested_url": link.url,
                "final_url": link.url,
                "source": link.source,
                "status_code": None,
                "fetch_ok": False,
                "outgoing_links": 0,
                "likely_job_links": 0,
                "job_link_pattern": None,
                "detected_monitors": [],
            }
        )

    followups: list[_ExtractedLink] = []
    if collect_followups and link.source != "ats_embed":
        followups = [
            child
            for child in _extract_links(page_html, final_url)
            if child.url not in (link.url, final_url)
        ]

    from src.workspace.job_links import analyze_job_links

    link_analysis = analyze_job_links(final_url, page_html)
    hub_links = link_analysis.job_links_total

    page_info = {
        "requested_url": link.url,
        "final_url": final_url,
        "source": link.source,
        "status_code": 200,
        "fetch_ok": True,
        "outgoing_links": link_analysis.outgoing_links_total,
        "likely_job_links": hub_links,
        "job_link_pattern": link_analysis.pattern,
        "detected_monitors": [],
    }

    # Fast path: final URL is a known ATS domain
    ats_type = detect_ats_from_url(final_url)
    if ats_type:
        ats_candidates = await _probe_specific_monitor(ats_type, final_url, link, client)
        for candidate in ats_candidates:
            candidate.job_link_hub = hub_links
            candidate.job_link_pattern = link_analysis.pattern
        if ats_candidates:
            page_info["detected_monitors"] = [ats_type]
        return _ProbeLinkResult(candidates=ats_candidates, page=page_info)

    # For ATS embeds that didn't resolve to an ATS domain after redirect,
    # try the original URL
    if link.source == "ats_embed":
        ats_type = detect_ats_from_url(link.url)
        if ats_type:
            ats_candidates = await _probe_specific_monitor(ats_type, link.url, link, client)
            for candidate in ats_candidates:
                candidate.job_link_hub = hub_links
                candidate.job_link_pattern = link_analysis.pattern
            if ats_candidates:
                page_info["detected_monitors"] = [ats_type]
            return _ProbeLinkResult(candidates=ats_candidates, page=page_info)
        return _ProbeLinkResult(page=page_info)

    # Slow path: probe all monitors on the career page
    try:
        results = await probe_all_monitors(final_url, client, timeout=15.0)
    except Exception:
        return _ProbeLinkResult(followup_links=followups, page=page_info)

    candidates = []
    page_info["detected_monitors"] = [name for name, metadata, _ in results if metadata is not None]
    for name, metadata, comment in results:
        if metadata is not None:
            source = "redirect" if link.url != final_url else "homepage_link"
            jobs_hint_n = _jobs_hint_from_metadata(metadata)

            if not _hubness_allows_candidate(
                source=source,
                hub_links=hub_links,
                inferred_pattern=link_analysis.pattern,
                jobs_hint_n=jobs_hint_n,
            ):
                continue

            candidates.append(
                CareerPageCandidate(
                    url=final_url,
                    source=source,
                    monitor_type=name,
                    monitor_config=metadata,
                    score=link.base_score * 0.9,
                    link_text=link.text,
                    link_href=link.url if link.url != final_url else None,
                    comment=comment,
                    job_link_hub=hub_links,
                    job_link_pattern=link_analysis.pattern,
                )
            )
    if candidates:
        return _ProbeLinkResult(candidates=candidates, page=page_info)
    return _ProbeLinkResult(followup_links=followups, page=page_info)


def _hubness_allows_candidate(
    *,
    source: str,
    hub_links: int,
    inferred_pattern: str | None,
    jobs_hint_n: int,
) -> bool:
    """Require homepage/redirect pages to look like job hubs unless metadata is strong."""
    from src.workspace.job_links import MIN_HUB_LINKS

    if source not in ("homepage_link", "redirect", "seed_path"):
        return True
    has_hub_signal = hub_links >= MIN_HUB_LINKS and bool(inferred_pattern)
    if has_hub_signal:
        return True
    return jobs_hint_n >= MIN_HUB_LINKS


async def _probe_specific_monitor(
    ats_type: str,
    url: str,
    link: _ExtractedLink,
    client: httpx.AsyncClient,
) -> list[CareerPageCandidate]:
    """Probe a single known ATS monitor type."""
    from src.core.monitors import _build_comment, get_can_handle

    try:
        handler = get_can_handle(ats_type)
        result = await asyncio.wait_for(handler(url, client), timeout=15.0)
        if result is not None:
            comment = _build_comment(ats_type, result)
            return [
                CareerPageCandidate(
                    url=url,
                    source=link.source,
                    monitor_type=ats_type,
                    monitor_config=result,
                    score=link.base_score,
                    link_text=link.text,
                    link_href=link.url if link.url != url else None,
                    comment=comment,
                )
            ]
    except Exception:
        pass
    return []


# ── Phase 3: Blind slug probes ─────────────────────────────────────────


async def _blind_probe_all(
    slug: str,
    client: httpx.AsyncClient,
) -> list[CareerPageCandidate]:
    """Probe all ATS APIs with a candidate slug."""
    from src.core.monitors import _build_comment, get_can_handle, slug_guess_mode

    def _jobs_count(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def _probe_one(name: str, url: str) -> CareerPageCandidate | None:
        try:
            handler = get_can_handle(name)
            result = await asyncio.wait_for(handler(url, client), timeout=15.0)
            if result is None:
                return None
            # For blind probes, require an actually populated board to avoid
            # speculative URL matches that return zero jobs.
            jobs = _jobs_count(result.get("jobs"))
            if jobs is None or jobs <= 0:
                return None
            comment = _build_comment(name, result)
            return CareerPageCandidate(
                url=url,
                source="blind_probe",
                monitor_type=name,
                monitor_config=result,
                score=0.50,
                comment=comment,
            )
        except Exception:
            return None

    with slug_guess_mode(True):
        tasks = [
            _probe_one(name, template.format(slug=slug))
            for name, template in _BLIND_PROBE_TEMPLATES.items()
        ]
        results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ── Dedup + public API ─────────────────────────────────────────────────


def _dedup_candidates(candidates: list[CareerPageCandidate]) -> list[CareerPageCandidate]:
    """Deduplicate by (monitor_type, monitor_config key), keeping highest score."""
    by_key: dict[str, CareerPageCandidate] = {}
    for c in candidates:
        # Use monitor_type + token/slug as dedup key
        config_key = c.monitor_config.get("token") or c.monitor_config.get("slug") or c.url
        key = f"{c.monitor_type}:{config_key}"
        if key in by_key:
            if c.score > by_key[key].score:
                by_key[key] = c
        else:
            by_key[key] = c
    return list(by_key.values())


async def discover_career_pages(
    homepage_url: str,
    homepage_html: str,
    client: httpx.AsyncClient,
    *,
    state_path: Path | None = None,
) -> list[CareerPageCandidate]:
    """Discover career pages from homepage HTML + blind slug probes.

    Four-phase algorithm:
    1. Extract career links from homepage HTML (zero extra HTTP requests)
    2. Follow career links + probe monitors (all concurrently)
    3. One-hop expansion: probe career links found on unresolved career pages
    4. Blind slug probes against all ATS APIs (concurrent with step 2)

    Returns only confirmed detections, ranked by score descending.
    """
    from src.core.monitors import slugs_from_url
    from src.workspace.job_links import analyze_job_links

    # Phase 1: Extract direct links + deterministic same-site seeds
    links = _merge_links(_extract_links(homepage_html, homepage_url), _seed_links(homepage_url))[
        :_MAX_LINKS
    ]

    # Start blind probes immediately so they run while traversal is in progress.
    slugs = slugs_from_url(homepage_url)
    blind_task = (
        asyncio.gather(*[_blind_probe_all(slug, client) for slug in slugs], return_exceptions=True)
        if slugs
        else None
    )

    candidates: list[CareerPageCandidate] = []
    pages: list[dict] = []

    homepage_analysis = analyze_job_links(homepage_url, homepage_html)
    pages.append(
        {
            "requested_url": homepage_url,
            "final_url": homepage_url,
            "source": "homepage",
            "depth": 0,
            "status_code": 200,
            "fetch_ok": True,
            "outgoing_links": homepage_analysis.outgoing_links_total,
            "likely_job_links": homepage_analysis.job_links_total,
            "job_link_pattern": homepage_analysis.pattern,
            "detected_monitors": [],
        }
    )

    frontier = links
    seen_urls = {homepage_url}
    depth = 0

    while frontier and depth <= _MAX_TRAVERSAL_DEPTH and len(pages) < _MAX_TRAVERSED_PAGES:
        batch: list[_ExtractedLink] = []
        for link in frontier:
            if link.url in seen_urls:
                continue
            seen_urls.add(link.url)
            batch.append(link)
            if len(pages) + len(batch) >= _MAX_TRAVERSED_PAGES:
                break

        if not batch:
            break

        collect_followups = depth < _MAX_TRAVERSAL_DEPTH
        batch_results = await asyncio.gather(
            *[_probe_link(link, client, collect_followups=collect_followups) for link in batch],
            return_exceptions=True,
        )

        followup_links: list[_ExtractedLink] = []
        for result in batch_results:
            if not isinstance(result, _ProbeLinkResult):
                continue
            candidates.extend(result.candidates)
            if result.page is not None:
                page_info = dict(result.page)
                page_info["depth"] = depth
                pages.append(page_info)
            if collect_followups:
                followup_links.extend(result.followup_links)

        if not collect_followups:
            break

        next_frontier: list[_ExtractedLink] = []
        for link in _merge_links(followup_links):
            if link.url in seen_urls:
                continue
            # Prefer internal traversal; allow ATS embeds for evidence.
            if link.source != "ats_embed" and not _same_site(homepage_url, link.url):
                continue
            next_frontier.append(link)
            if len(next_frontier) >= _MAX_FOLLOWUP_LINKS:
                break

        frontier = next_frontier
        depth += 1

    if blind_task is not None:
        blind_results = await blind_task
        for result in blind_results:
            if isinstance(result, list):
                candidates.extend(result)

    # Dedup and sort by score descending
    deduped = _dedup_candidates(candidates)
    _rerank_candidates(deduped, homepage_url)
    deduped.sort(key=lambda c: c.score, reverse=True)

    if state_path is not None:
        _save_discovery_state(
            state_path,
            homepage_url=homepage_url,
            seed_links=links,
            pages=pages,
            candidates=deduped,
        )

    return deduped
