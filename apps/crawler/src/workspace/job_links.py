"""Job-link hub analysis and pattern inference for board URLs."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import httpx

_BLOCK_STATUSES = {401, 403, 429, 503}
_BLOCK_MARKERS = (
    "captcha",
    "cloudflare",
    "access denied",
    "verify you are human",
    "bot detection",
    "just a moment",
    "enable javascript",
)

_JOB_QUERY_KEYS = {
    "gh_jid",
    "jobid",
    "job_id",
    "postingid",
    "jid",
    "requisitionid",
    "vacancyid",
}

_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)

MIN_LINKS_FOR_PATTERN = 3
MIN_HUB_LINKS = 2


@dataclass
class JobLinkFetchResult:
    """Raw page fetch result used for link-pattern analysis."""

    final_url: str
    html: str
    fetch_mode: str  # "http" | "render"
    warnings: list[str] = field(default_factory=list)


@dataclass
class JobLinkPatternAnalysis:
    """Summary of outgoing links, inferred/provided pattern, and match counts."""

    board_url: str
    final_url: str
    fetch_mode: str = "http"
    provided_pattern: str | None = None
    pattern: str | None = None
    pattern_source: str | None = None  # "provided" | "inferred"
    outgoing_links_total: int = 0
    job_links_total: int = 0
    matched_outgoing_links: int = 0
    matched_job_links: int = 0
    sample_job_links: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _HrefExtractor(HTMLParser):
    """Extract hrefs from anchor tags."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                absolute = _normalize_link(urljoin(self.base_url, value))
                if absolute:
                    self.links.append(absolute)


def _normalize_link(url: str) -> str | None:
    """Normalize a candidate link for dedup/matching."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None
    # Drop fragment for stable matching.
    cleaned = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            "",
        )
    )
    return cleaned


def extract_outgoing_links(html: str, base_url: str) -> list[str]:
    """Extract and deduplicate outgoing absolute links from HTML."""
    parser = _HrefExtractor(base_url)
    parser.feed(html or "")

    seen: set[str] = set()
    out: list[str] = []
    for link in parser.links:
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
    return out


def _host_with_root(host: str) -> str:
    return host.lower().removeprefix("www.")


def _is_locale_segment(segment: str) -> bool:
    return bool(_LOCALE_SEGMENT_RE.match(segment.lower()))


def _canonical_segments(path: str) -> list[str]:
    segs = [seg for seg in path.split("/") if seg]
    while segs and _is_locale_segment(segs[0]):
        segs = segs[1:]
    return segs


def _looks_like_role_slug(segment: str) -> bool:
    lower = segment.lower()
    if not lower:
        return False
    if _is_locale_segment(lower):
        return False
    if _segment_has_identifier(lower):
        return True
    if len(lower) < 8:
        return False
    if "-" in lower or "_" in lower:
        parts = [part for part in re.split(r"[-_]+", lower) if part]
        return len(parts) >= 2 and sum(len(part) >= 3 for part in parts) >= 2
    return bool(re.fullmatch(r"[a-z0-9]+", lower)) and len(lower) >= 14


def _segment_has_identifier(segment: str) -> bool:
    lower = segment.lower()
    return bool(
        _UUID_RE.search(lower) or re.search(r"\d{3,}", lower) or re.fullmatch(r"\d+", lower)
    )


def _query_has_identifier(query: str) -> bool:
    ignored_keys = {"ref", "source", "sid", "lang", "locale"}
    for key, values in parse_qs(query).items():
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in ignored_keys:
            continue
        if lower_key in _JOB_QUERY_KEYS:
            return True
        if "id" in lower_key:
            for value in values:
                if _value_has_identifier(value):
                    return True
    return False


def _value_has_identifier(value: str) -> bool:
    lower = value.lower()
    return bool(_UUID_RE.search(lower) or re.search(r"\d{4,}", lower))


def _looks_detail_like_path(segs: list[str]) -> bool:
    if not segs:
        return False
    if len(segs) == 1:
        # Single-segment links are usually hubs/homepages unless clearly ID-like.
        return _looks_like_role_slug(segs[0]) and _segment_has_identifier(segs[0])
    tail = segs[-1]
    if _segment_has_identifier(tail):
        return True
    if len(segs) >= 3 and _looks_like_role_slug(tail):
        return True
    return len(segs) == 2 and _looks_like_role_slug(tail)


def _family_key(segs: list[str]) -> str:
    if not segs:
        return ""
    if len(segs) == 1:
        return segs[0].lower()
    second = segs[1]
    if _segment_has_identifier(second) or _looks_like_role_slug(second):
        return segs[0].lower()
    return f"{segs[0].lower()}/{second.lower()}"


def _looks_like_job_link(
    url: str,
    board_url: str,
    *,
    host_counts: Counter[str],
    family_counts: Counter[tuple[str, str]],
    board_host: str,
    board_segs: list[str],
) -> bool:
    """Structural heuristic for likely job-detail links."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    segs = _canonical_segments(parsed.path)
    host = _host_with_root(parsed.netloc)
    query_id = _query_has_identifier(parsed.query)
    tail = segs[-1] if segs else ""
    tail_id = _segment_has_identifier(tail) if tail else False
    tail_slug = _looks_like_role_slug(tail) if tail else False
    detail_path = _looks_detail_like_path(segs)

    if not (query_id or detail_path):
        return False
    if segs == board_segs and not query_id:
        return False

    family = _family_key(segs)
    family_n = family_counts[(host, family)] if family else 0
    host_n = host_counts[host]
    same_host = host == board_host
    strong_id = query_id or tail_id

    score = 0
    if query_id:
        score += 3
    if tail_id:
        score += 2
    if detail_path:
        score += 1
    if tail_slug:
        score += 1
    if len(segs) >= 3:
        score += 1
    if family_n >= 2:
        score += 1
    if family_n >= 4:
        score += 1
    if host_n >= 3:
        score += 1
    if same_host:
        score += 1

    # Two-segment paths without strong IDs are noisy on content hubs.
    if len(segs) == 2 and not (query_id or tail_id) and family_n < 3:
        score -= 2
    # Cross-host non-ID links are noisy unless they repeat strongly.
    if not same_host and not strong_id and family_n < 5:
        return False
    if len(segs) <= 1 and not query_id:
        score -= 3
    # On very large hubs, weak non-ID families are usually content links.
    if not strong_id and host_n >= 20 and (family_n / float(host_n)) < 0.15:
        return False

    return score >= 3 or (query_id and score >= 2)


def filter_job_links(links: list[str], board_url: str) -> list[str]:
    """Filter outgoing links to likely job-detail URLs."""
    board = urlparse(board_url)
    board_host = _host_with_root(board.netloc)
    board_segs = _canonical_segments(board.path)

    host_counts: Counter[str] = Counter()
    family_counts: Counter[tuple[str, str]] = Counter()
    for link in links:
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        host = _host_with_root(parsed.netloc)
        segs = _canonical_segments(parsed.path)
        family = _family_key(segs)
        host_counts[host] += 1
        if family:
            family_counts[(host, family)] += 1

    out: list[str] = []
    for link in links:
        if _looks_like_job_link(
            link,
            board_url,
            host_counts=host_counts,
            family_counts=family_counts,
            board_host=board_host,
            board_segs=board_segs,
        ):
            out.append(link)
    return out


def _common_path_segments(urls: list[str]) -> list[str]:
    """Longest common path prefix by segment."""
    split = [[seg for seg in urlparse(u).path.split("/") if seg] for u in urls]
    if not split:
        return []
    common: list[str] = []
    for group in zip(*split, strict=False):
        first = group[0]
        if all(seg == first for seg in group):
            common.append(first)
        else:
            break
    return common


def _most_common_host(urls: list[str]) -> tuple[str | None, int]:
    if not urls:
        return None, 0
    counts = Counter(_host_with_root(urlparse(u).netloc) for u in urls if urlparse(u).netloc)
    if not counts:
        return None, 0
    host, n = counts.most_common(1)[0]
    return host, n


def _most_common_path_prefix(urls: list[str], depth: int) -> tuple[list[str], int]:
    if depth <= 0:
        return [], 0
    counts: Counter[tuple[str, ...]] = Counter()
    for url in urls:
        segs = [seg for seg in urlparse(url).path.split("/") if seg]
        if len(segs) < depth:
            continue
        counts[tuple(segs[:depth])] += 1
    if not counts:
        return [], 0
    prefix, n = counts.most_common(1)[0]
    return list(prefix), n


def _query_key_pattern(urls: list[str]) -> str | None:
    keys = Counter()
    for url in urls:
        for key, values in parse_qs(urlparse(url).query).items():
            lower = key.lower()
            if lower.startswith("utm_"):
                continue
            if lower in _JOB_QUERY_KEYS:
                keys[lower] += 1
                continue
            if "id" in lower and any(_value_has_identifier(value) for value in values):
                keys[lower] += 1
    if not keys:
        return None
    key, n = keys.most_common(1)[0]
    if n < 2:
        return None
    return key


def _compile_regex(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _evaluate_pattern(
    pattern: str,
    job_links: list[str],
    all_links: list[str],
) -> tuple[int, int, float]:
    compiled = _compile_regex(pattern)
    if compiled is None:
        return 0, 0, float("-inf")

    matched_job = sum(1 for url in job_links if compiled.search(url))
    matched_all = sum(1 for url in all_links if compiled.search(url))
    non_job = max(0, matched_all - matched_job)
    # Bias toward high job coverage and lower false positives.
    score = float(matched_job) - (1.5 * float(non_job))
    return matched_job, matched_all, score


def infer_job_link_pattern(
    job_links: list[str],
    all_links: list[str],
) -> tuple[str | None, int, int]:
    """Infer a regex pattern matching the majority of job links with acceptable precision."""
    if len(job_links) < MIN_LINKS_FOR_PATTERN:
        return None, 0, 0

    host, _ = _most_common_host(job_links)
    candidates: list[str] = []
    if host:
        host_re = re.escape(host)
        host_prefix = rf"^https?://(?:www\.)?{host_re}"
        host_links = [u for u in job_links if _host_with_root(urlparse(u).netloc) == host]
    else:
        host_prefix = r"^https?://[^/]+"
        host_links = list(job_links)

    common = _common_path_segments(host_links)
    if common:
        prefix = "/".join(re.escape(seg) for seg in common)
        candidates.append(rf"{host_prefix}/{prefix}(?:/|\?|$)")

    for depth in (3, 2, 1):
        prefix_segs, n = _most_common_path_prefix(host_links, depth)
        if not prefix_segs:
            continue
        if n < max(2, len(host_links) // 4):
            continue
        prefix = "/".join(re.escape(seg) for seg in prefix_segs)
        candidates.append(rf"{host_prefix}/{prefix}/[^/?#]+")

    query_key = _query_key_pattern(host_links)
    if query_key:
        candidates.append(rf"{host_prefix}/[^?#]*\?(?:[^#]*&)?{re.escape(query_key)}=")

    candidates.append(rf"{host_prefix}/[^?#]*/\d[\w-]*")

    # Preserve candidate order while removing duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    candidates = deduped

    best_pattern: str | None = None
    best_job = 0
    best_all = 0
    best_score = float("-inf")
    for pattern in candidates:
        matched_job, matched_all, score = _evaluate_pattern(pattern, job_links, all_links)
        if score > best_score:
            best_score = score
            best_pattern = pattern
            best_job = matched_job
            best_all = matched_all

    if not best_pattern:
        return None, 0, 0

    # Require reasonable quality.
    if best_job < max(2, min(3, len(job_links))):
        return None, 0, 0
    if best_all > 0 and (best_job / best_all) < 0.6:
        return None, 0, 0

    return best_pattern, best_job, best_all


def analyze_job_links(
    board_url: str,
    html: str,
    provided_pattern: str | None = None,
) -> JobLinkPatternAnalysis:
    """Analyze outgoing links from *board_url* and infer/count a job-link pattern."""
    outgoing = extract_outgoing_links(html, board_url)
    job_links = filter_job_links(outgoing, board_url)

    analysis = JobLinkPatternAnalysis(
        board_url=board_url,
        final_url=board_url,
        provided_pattern=provided_pattern,
        outgoing_links_total=len(outgoing),
        job_links_total=len(job_links),
        sample_job_links=job_links[:5],
    )

    if provided_pattern:
        compiled = _compile_regex(provided_pattern)
        analysis.pattern = provided_pattern
        analysis.pattern_source = "provided"
        if compiled is None:
            analysis.warnings.append(f"Invalid regex pattern: {provided_pattern!r}")
            return analysis
        analysis.matched_job_links = sum(1 for url in job_links if compiled.search(url))
        analysis.matched_outgoing_links = sum(1 for url in outgoing if compiled.search(url))
        return analysis

    pattern, matched_job, matched_all = infer_job_link_pattern(job_links, outgoing)
    if pattern is None:
        if not job_links:
            analysis.warnings.append("No likely job-detail links found among outgoing links.")
        else:
            analysis.warnings.append(
                f"Only {len(job_links)} likely job links found; "
                "not enough to infer a reliable pattern."
            )
        return analysis

    analysis.pattern = pattern
    analysis.pattern_source = "inferred"
    analysis.matched_job_links = matched_job
    analysis.matched_outgoing_links = matched_all
    return analysis


def _looks_bot_blocked(status_code: int | None, html: str) -> bool:
    if status_code in _BLOCK_STATUSES:
        return True
    lower = (html or "").lower()
    return any(marker in lower for marker in _BLOCK_MARKERS)


async def fetch_page_for_job_link_analysis(
    url: str,
    client: httpx.AsyncClient,
    *,
    allow_render_fallback: bool = True,
) -> JobLinkFetchResult:
    """Fetch URL for link analysis with bot/JS fallback to Playwright render."""
    warnings: list[str] = []
    html = ""
    final_url = url
    status_code: int | None = None
    mode = "http"

    try:
        resp = await client.get(url, follow_redirects=True)
        final_url = str(resp.url)
        status_code = resp.status_code
        if resp.status_code >= 400:
            warnings.append(f"HTTP {resp.status_code} while fetching board page.")
        html = resp.text or ""
    except Exception as exc:
        warnings.append(f"HTTP fetch failed: {exc}")

    blocked = _looks_bot_blocked(status_code, html)
    if blocked:
        warnings.append(
            "Page appears bot-protected or blocked; attempting browser render fallback."
        )

    html_lower = html.lower() if html else ""
    script_count = html_lower.count("<script")
    outgoing_links = extract_outgoing_links(html, final_url) if html else []
    job_links = filter_job_links(outgoing_links, final_url) if html else []
    has_js_shell_markers = any(
        marker in html_lower
        for marker in (
            "window.__",
            "hydrat",
            "__next",
            "webpack",
            "react",
            "vue",
            "svelte",
            "angular",
        )
    )
    looks_js_heavy = (
        script_count >= 5
        or has_js_shell_markers
        or (script_count >= 1 and len(outgoing_links) == 0)
    )
    sparse_js_shell = (
        bool(html)
        and len(job_links) < MIN_HUB_LINKS
        and looks_js_heavy
        and (len(outgoing_links) <= 25 or has_js_shell_markers)
    )
    if sparse_js_shell:
        warnings.append(
            "Page appears JS-rendered with too few static links; "
            "attempting browser render fallback."
        )

    http_error = status_code is not None and status_code >= 400
    if http_error:
        warnings.append("HTTP error response from board page; attempting browser render fallback.")

    if allow_render_fallback and (not html or blocked or sparse_js_shell or http_error):
        try:
            from src.shared.browser import DEFAULT_USER_AGENT, render

            html = await render(
                final_url,
                {
                    "headless": True,
                    "wait": "networkidle",
                    "timeout": 30_000,
                    "user_agent": DEFAULT_USER_AGENT,
                },
            )
            mode = "render"
            if html:
                warnings.append(
                    "Used browser rendering for link analysis (JS-heavy or bot-restricted page)."
                )
        except Exception as exc:
            warnings.append(f"Browser render fallback failed: {exc}")

    return JobLinkFetchResult(
        final_url=final_url,
        html=html or "",
        fetch_mode=mode,
        warnings=warnings,
    )
