"""Phenom People careers-site monitor.

Phenom is a SaaS careers platform (phenompeople.com) used by large
enterprises (Marriott, Nike, Nordstrom, Elevance, McDonald's, etc.).
Every tenant exposes a sitemap at ``/sitemap.xml`` that is a sitemap-index
pointing to child sitemaps. Child naming is either:

- **Per-language** — one child per supported locale, each emitting a
  distinct URL path per job (Marriott ``/ar/<slug>/job/<hex>__ar``,
  Nike ``/de/<slug>/job/R-xxxxx``). Union-of-all-children explodes the
  URL count by N languages; we keep only ``-en`` / ``-en-us`` children
  to match the pre-existing ``site_available_languages: [en, en-us]``
  behaviour of the old api_sniffer config.
- **Sharded** — many children, all the same language suffix, each
  carrying a subset of URLs (mcdonalds-au 210 shards, mcdonalds-canada
  471 shards, mcdonalds-us 1000 shards). No per-language filtering is
  applied; every shard contributes to the union.

The sitemap is the authoritative URL set for gone detection; rich data
for each job is extracted from the detail page's JSON-LD ``JobPosting``
by the pipeline's ``json-ld`` scraper.

Why not the ``/api/get-jobs`` endpoint? Phenom's API returns rich rows
but has (a) no per-job timestamp, (b) no sort-by-recency, (c) Akamai-
gated TLS/JS fingerprint that requires a real Chrome profile. That
combination means the only way to get new-since-last-cycle from the
API is a full paginated crawl of every tenant every cycle — 20 min
for Marriott, 90 min for mchire. Sitemap plus per-URL json-ld is
linear in *new URLs*, not total URLs, so steady-state is cheap.

Incremental semantics come for free from the shared pipeline:
``_DIFF_BATCH`` classifies each sitemap URL as new/relisted/touched
against ``job_posting.last_seen_at``; ``_MARK_GONE_BY_TIMESTAMP``
retires any active row whose URL wasn't re-seen this cycle. No
watermark state, no hybrid flag, no API-sort assumption.

Tenants migrated (2026-04-23): marriott, nike, nordstrom,
elevance-health, nationwide, mondelez, mcdonalds-au, mcdonalds-canada,
mcdonalds-us.

Per-board config (in ``monitor_config``):

- ``sitemap_url`` — cached sitemap location. Auto-derived from the
  board URL on first run and persisted, so subsequent runs skip the
  implicit ``/sitemap.xml`` lookup. Mirrors the sitemap monitor.
- ``keep_languages`` — list of locale codes to keep when the sitemap-
  index carries multiple locales. Defaults to ``["en", "en-us"]``.
  mchire uses ``["en", "en-us", "es-es", "es-mx"]`` to pick up its
  Spanish franchisee shards (0.4% overlap with English → ~16k extra
  jobs, see issue #2548).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors.sitemap import (
    MAX_URLS,
    _extract_child_sitemaps,
    _extract_urls,
    _fetch_child_xml,
    _is_sitemap_index,
    _try_fetch_xml,
)

log = structlog.get_logger()

# Phenom detail URLs contain either ``/job/<id>`` (canonical Phenom pattern,
# e.g. ``careers.marriott.com/<slug>/job/<hex>``) or ``?job_id=<id>`` /
# ``&job_id=<id>`` (mchire variant used by mcdonalds-us franchisees).
# Case-insensitive because mchire renders ``/Job?job_id=...`` with a capital J.
_JOB_URL_RE = re.compile(r"/job/|[?&]job_id=", re.IGNORECASE)

# Phenom child sitemap filenames follow ``sitemap-<hex>-<lang>.xml`` (e.g.
# ``sitemap-0a80f330-en.xml``). Used as the fingerprint in ``can_handle``.
_PHENOM_CHILD_RE = re.compile(r"sitemap-[a-f0-9]+-[a-z-]+\.xml", re.IGNORECASE)

# Default languages we keep when the sitemap-index carries multiple locales.
# Matches the old ``site_available_languages: [en, en-us]`` in the pre-
# migration api_sniffer configs so the URL set stays equivalent for tenants
# that don't opt into a wider set.
#
# Override per board via ``monitor_config.keep_languages`` — e.g. mchire
# ships Spanish franchise listings in ``-es-es`` / ``-es-mx`` shards that
# carry distinct job URLs (0.4% overlap with English), so its CSV row sets
# ``keep_languages = ["en", "en-us", "es-es", "es-mx"]`` to opt in.
_DEFAULT_KEEP_LANGS = frozenset({"en", "en-us"})


def _is_phenom_job_url(url: str) -> bool:
    return bool(_JOB_URL_RE.search(url))


def _default_sitemap_url(board_url: str) -> str:
    parsed = urlparse(board_url)
    return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"


def _child_language(child_url: str) -> str | None:
    """Return the language suffix of a Phenom child sitemap filename, lowercased.

    Phenom filenames follow ``sitemap-<hex>-<lang>[-<region>].xml``; the
    language segment is everything after the second dash. Files with no
    third segment (e.g. ``sitemap-content.xml`` at nationwide, which
    holds only site-root URLs) return None — they get filtered out by
    the job-URL regex downstream anyway.
    """
    name = child_url.rsplit("/", 1)[-1]
    if not name.endswith(".xml"):
        return None
    parts = name[: -len(".xml")].split("-")
    if len(parts) < 3:
        return None
    return "-".join(parts[2:]).lower()


def _select_children(
    children: list[str],
    keep_langs: frozenset[str] = _DEFAULT_KEEP_LANGS,
) -> list[str]:
    """Filter child sitemap URLs to the configured language subset.

    Per-language indexes (marriott = 22 locales, nike = 16 locales) get
    reduced to the intersection with *keep_langs*. Sharded indexes where
    every child carries the same language suffix (mcdonalds-*) pass
    through unchanged because there is only one real language in the set.
    Children without a language suffix are always kept — they're
    typically low-cardinality content maps whose non-job URLs drop out
    via ``_is_phenom_job_url``.
    """
    if not children:
        return children
    langs_with_suffix = {_child_language(c) for c in children} - {None}
    # Only one real language → sharded layout, keep everything.
    if len(langs_with_suffix) <= 1:
        return children
    return [c for c in children if _child_language(c) is None or _child_language(c) in keep_langs]


async def _collect_urls(
    sitemap_url: str,
    client: httpx.AsyncClient,
    keep_langs: frozenset[str] = _DEFAULT_KEEP_LANGS,
) -> tuple[set[str], bool]:
    """Fetch sitemap (index or flat), traverse selectively, return URL set.

    Returns ``(urls, truncated)`` where *truncated* is True when we hit
    ``sitemap.MAX_URLS`` during accumulation; the caller may log this.

    Child shards use the strict :func:`_fetch_child_xml` rather than the
    lenient :func:`_try_fetch_xml`. This mirrors the sitemap monitor's
    #2722 hardening: a transient 5xx / 429 / timeout on a child shard
    raises :exc:`PaginationFetchError` after the retry budget, which
    propagates up through ``discover()`` to
    ``_process_one_board_streaming`` and gets recorded as a failed run
    rather than a partial-success that triggers
    ``_MARK_GONE_BY_TIMESTAMP`` on the missing URLs. mchire's 906 ``-en``
    shards make even a 1% per-shard transient failure rate produce
    multi-thousand URL drops that tombstone real postings (#2974).

    The index root itself stays on the lenient path: discovery of a new
    sitemap location is allowed to fall through to the next candidate
    on transient errors, same split as ``sitemap._discover_sitemap``.
    """
    root = await _try_fetch_xml(sitemap_url, client)
    if root is None:
        return set(), False

    if not _is_sitemap_index(root):
        return set(_extract_urls(root)), False

    children = _extract_child_sitemaps(root)
    selected = _select_children(children, keep_langs)
    skipped = len(children) - len(selected)
    if skipped:
        log.debug(
            "phenom.children_filtered",
            sitemap=sitemap_url,
            total=len(children),
            kept=len(selected),
            skipped=skipped,
        )

    urls: set[str] = set()
    truncated = False
    for child_url in selected:
        if len(urls) >= MAX_URLS:
            truncated = True
            break
        # Strict fetch — transient errors raise PaginationFetchError
        # rather than silently dropping the shard's URLs (#2974).
        child_root = await _fetch_child_xml(child_url, client)
        if child_root is None:
            # 404 / 410 / non-XML body — genuinely missing shard, skip.
            continue
        # Nested sitemap-index (rare; defensive): single-level recurse.
        if _is_sitemap_index(child_root):
            for grandchild in _select_children(_extract_child_sitemaps(child_root), keep_langs):
                if len(urls) >= MAX_URLS:
                    truncated = True
                    break
                gc_root = await _fetch_child_xml(grandchild, client)
                if gc_root is not None:
                    urls.update(_extract_urls(gc_root))
        else:
            urls.update(_extract_urls(child_root))
    return urls, truncated


def _keep_langs_from_metadata(metadata: dict) -> frozenset[str]:
    """Read ``monitor_config.keep_languages`` from board metadata, default.

    Empty list or missing key → default (``_DEFAULT_KEEP_LANGS``). Values
    are lowercased so the CSV can spell locales in any case.
    """
    raw = metadata.get("keep_languages")
    if not raw or not isinstance(raw, list):
        return _DEFAULT_KEEP_LANGS
    return frozenset(str(v).lower() for v in raw if v)


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> tuple[set[str], str | None]:
    """Fetch the Phenom sitemap, return (job_urls, new_sitemap_url).

    Derives ``/sitemap.xml`` from the board host when not cached in
    metadata, traverses the index (preferring English children for
    per-language layouts), and filters the result to URLs that look
    like job detail pages (``/job/`` or ``?job_id=``).

    ``new_sitemap_url`` is non-None only when the monitor had to derive
    the URL (mirrors the contract of ``sitemap.discover``). For boards
    that already cache it in metadata, None is returned so the pipeline
    skips the metadata-update write.
    """
    metadata = board.get("metadata") or {}
    cached = metadata.get("sitemap_url")
    sitemap_url = cached or _default_sitemap_url(board["board_url"])
    new_sitemap_url = None if cached else sitemap_url
    keep_langs = _keep_langs_from_metadata(metadata)

    urls, truncated = await _collect_urls(sitemap_url, client, keep_langs)
    job_urls = {u for u in urls if _is_phenom_job_url(u)}

    log_fn = log.warning if truncated else log.info
    log_fn(
        "phenom.discover",
        board_id=board.get("id"),
        sitemap_urls=len(urls),
        job_urls=len(job_urls),
        truncated=truncated,
    )
    return job_urls, new_sitemap_url


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect Phenom by fingerprinting the sitemap-index child naming.

    Every Phenom tenant's ``/sitemap.xml`` is a sitemap-index whose
    children follow ``sitemap-<hex>-<lang>.xml``. Non-Phenom sitemaps
    (static generators, WordPress, etc.) use different conventions, so
    matching a single child filename is sufficient signal.

    We avoid probing ``/api/get-jobs`` because Akamai returns 403 to
    datacenter IPs (the probe can_handle runs from) even for valid
    Phenom tenants — a 403 would be indistinguishable from a non-
    Phenom host blocking the endpoint. The sitemap fingerprint is
    cheaper and does not require egress proxying.
    """
    from src.shared.http_retry import PaginationFetchError

    if client is None:
        return None
    parsed = urlparse(url)
    sitemap = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    root = await _try_fetch_xml(sitemap, client)
    if root is None:
        return None
    children = _extract_child_sitemaps(root)
    if not any(_PHENOM_CHILD_RE.search(c) for c in children):
        return None
    # Probe path: a transient PaginationFetchError on a single shard
    # shouldn't fail the whole probe — return a partial count instead so
    # the operator still gets a positive Phenom-detected signal. The
    # discover() path keeps the strict semantics for production cycles.
    try:
        urls, _truncated = await _collect_urls(sitemap, client)
    except PaginationFetchError as exc:
        log.warning(
            "phenom.can_handle.partial",
            sitemap=sitemap,
            url=exc.url,
            last_status=exc.last_status,
        )
        return {"sitemap_url": sitemap, "urls": 0, "jobs": 0}
    job_urls = sum(1 for u in urls if _is_phenom_job_url(u))
    return {"sitemap_url": sitemap, "urls": len(urls), "jobs": job_urls}


# Cost between eightfold (8) and sitemap (50) — same band as other
# dedicated ATS monitors — so ``detect_monitor_type`` tries the Phenom
# fingerprint before the generic sitemap path for new Phenom tenants.
register("phenom", discover, cost=9, can_handle=can_handle)
