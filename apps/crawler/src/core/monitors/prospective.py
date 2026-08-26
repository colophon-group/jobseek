"""Prospective CareerCenter server-rendered listing monitor.

Prospective's branded CareerCenter pages paginate through a regular HTML
``POST`` form.  Some tenants also expose a public JSON ``medium`` endpoint,
which is handled by :mod:`api_sniffer`, but that endpoint is not available for
every medium.  This monitor uses the canonical form as a fail-closed fallback.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, register
from src.shared.http_retry import fetch_text_page_with_retry

if TYPE_CHECKING:
    import httpx


_MEDIUM_RE = re.compile(r"/careercenter/(?P<medium_id>\d+)/assets/")
_PAGINATION_RE = re.compile(r"\bsendPagination\((?P<offset>\d+)\)")
_FILTER_NAME_RE = re.compile(r"filter_\d+")
_JOB_PATH_RE = re.compile(
    r"^/offene-stellen/[^/]+/(?P<job_id>[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)
_MAX_PAGES = 200
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_IDENTITY_REGEX_LENGTH = 4_096
_MAX_APPLICATION_LINK_TEXTS = 8
_MAX_LOCALE_PRIORITY = 8


@dataclass(frozen=True, slots=True)
class _ApplicationIdentityConfig:
    """Fail-closed identity contract for rich Prospective detail enrichment."""

    link_texts: frozenset[str]
    source_pattern: re.Pattern[str]
    canonical_pattern: re.Pattern[str]
    locale_priority: tuple[str, ...]
    concurrency: int


def _single_input_value(tree: LexborHTMLParser, name: str, default: str = "") -> str:
    nodes = tree.css(f'input[name="{name}"]')
    if len(nodes) > 1:
        raise ValueError(f"Prospective form contains duplicate {name!r} inputs")
    if not nodes:
        return default
    return nodes[0].attributes.get("value", default)


def _medium_id(html: str) -> str:
    match = _MEDIUM_RE.search(html)
    if match is None:
        raise ValueError("Prospective CareerCenter medium marker was not found")
    return match.group("medium_id")


def _validated_filters(value: object) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 8:
        raise ValueError("Prospective filters must be a mapping with at most 8 fields")

    result: dict[str, tuple[str, ...]] = {}
    for name, raw_values in value.items():
        if not isinstance(name, str) or _FILTER_NAME_RE.fullmatch(name) is None:
            raise ValueError("Prospective filter names must match filter_<number>")
        values: Iterable[object]
        if isinstance(raw_values, str):
            values = (raw_values,)
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            raise ValueError("Prospective filter values must be strings or lists of strings")
        normalized = tuple(values)
        if (
            not normalized
            or len(normalized) > 100
            or any(
                not isinstance(item, str) or not item or len(item) > 128 or "\x00" in item
                for item in normalized
            )
        ):
            raise ValueError("Prospective filter values must be 1-100 bounded strings")
        result[name] = normalized  # type: ignore[assignment]
    return result


def _compile_identity_pattern(value: object, *, name: str) -> re.Pattern[str]:
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENTITY_REGEX_LENGTH:
        raise ValueError(f"Prospective {name} must be a non-empty bounded regex")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError(f"Prospective {name} is invalid: {exc}") from exc


def _validated_application_identity(value: object) -> _ApplicationIdentityConfig:
    if not isinstance(value, dict) or set(value) - {
        "link_texts",
        "source_url_allowlist",
        "canonical_url_allowlist",
        "locale_priority",
        "concurrency",
    }:
        raise ValueError(
            "Prospective application_identity must contain only link texts, URL allowlists, "
            "locale priority, and concurrency"
        )

    raw_link_texts = value.get("link_texts")
    if (
        not isinstance(raw_link_texts, list)
        or not raw_link_texts
        or len(raw_link_texts) > _MAX_APPLICATION_LINK_TEXTS
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 64
            for item in raw_link_texts
        )
    ):
        raise ValueError("Prospective application identity link_texts must be bounded text")
    link_texts = frozenset(" ".join(item.split()).casefold() for item in raw_link_texts)
    if len(link_texts) != len(raw_link_texts):
        raise ValueError("Prospective application identity link_texts must be unique")

    raw_locale_priority = value.get("locale_priority")
    if (
        not isinstance(raw_locale_priority, list)
        or not raw_locale_priority
        or len(raw_locale_priority) > _MAX_LOCALE_PRIORITY
        or any(
            not isinstance(item, str) or re.fullmatch(r"[a-z]{2}", item) is None
            for item in raw_locale_priority
        )
        or len(set(raw_locale_priority)) != len(raw_locale_priority)
    ):
        raise ValueError(
            "Prospective application identity locale_priority must be unique ISO language codes"
        )

    concurrency = value.get("concurrency", 8)
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or not 1 <= concurrency <= 16
    ):
        raise ValueError("Prospective application identity concurrency must be 1-16")

    return _ApplicationIdentityConfig(
        link_texts=link_texts,
        source_pattern=_compile_identity_pattern(
            value.get("source_url_allowlist"), name="source_url_allowlist"
        ),
        canonical_pattern=_compile_identity_pattern(
            value.get("canonical_url_allowlist"), name="canonical_url_allowlist"
        ),
        locale_priority=tuple(raw_locale_priority),
        concurrency=concurrency,
    )


def _validate_filter_options(
    tree: LexborHTMLParser,
    filters: dict[str, tuple[str, ...]],
) -> None:
    """Fail closed when a configured ownership/category allowlist drifts."""
    for name, selected in filters.items():
        selects = tree.css(f'select[name="{name}"]')
        if len(selects) != 1:
            raise ValueError(f"Prospective configured filter {name!r} was not found exactly once")
        available = {
            option.attributes.get("value", "") for option in selects[0].css("option[value]")
        }
        missing = set(selected) - available
        if missing:
            raise ValueError(
                f"Prospective configured filter {name!r} contains unavailable values: "
                f"{sorted(missing)!r}"
            )


def _parse_page(
    html: str,
    board_url: str,
    *,
    expected_medium_id: str | None,
    filters: dict[str, tuple[str, ...]],
) -> tuple[set[str], int, str, str]:
    medium_id = _medium_id(html)
    if expected_medium_id is not None and medium_id != expected_medium_id:
        raise ValueError(f"Prospective medium changed from {expected_medium_id!r} to {medium_id!r}")

    tree = LexborHTMLParser(html)
    form = tree.css_first("form#careercenter-form")
    if form is None or form.attributes.get("method", "").casefold() != "post":
        raise ValueError("Prospective CareerCenter POST form was not found")
    _validate_filter_options(tree, filters)

    parsed_board = urlparse(board_url)
    board_origin = (parsed_board.scheme.casefold(), parsed_board.netloc.casefold())
    urls: set[str] = set()
    job_lists = tree.css("#jobs-list")
    if len(job_lists) != 1:
        raise ValueError("Prospective listing omitted its unique jobs-list container")
    job_list = job_lists[0]
    for link in job_list.css("a.job-title[href]"):
        candidate = urljoin(board_url, link.attributes["href"])
        parsed = urlparse(candidate)
        path_match = _JOB_PATH_RE.fullmatch(parsed.path)
        if (
            (
                parsed.scheme.casefold(),
                parsed.netloc.casefold(),
            )
            != board_origin
            or path_match is None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"Prospective listing returned an unexpected job URL: {candidate}")
        job_id = path_match.group("job_id").casefold()
        urls.add(f"{parsed_board.scheme}://{parsed_board.netloc}/offene-stellen/job/{job_id}")

    if not urls:
        empty_markers = job_list.css("#no-results")
        if len(empty_markers) != 1 or not empty_markers[0].text(strip=True):
            raise ValueError(
                "Prospective listing returned zero jobs without its authoritative empty marker"
            )

    current_offset_raw = _single_input_value(tree, "offset", "0")
    active_page = tree.css_first("#pagination .page.active[onclick]")
    if active_page is not None:
        active_match = _PAGINATION_RE.search(active_page.attributes.get("onclick", ""))
        if active_match is None:
            raise ValueError("Prospective active pagination control omitted its offset")
        current_offset_raw = active_match.group("offset")
    limit = _single_input_value(tree, "limit", "10")
    lang = _single_input_value(tree, "lang", "de")
    if not current_offset_raw.isdigit() or not limit.isdigit() or int(limit) < 1:
        raise ValueError("Prospective form returned invalid offset/limit values")
    current_offset = int(current_offset_raw)

    next_offsets = {
        int(match.group("offset"))
        for node in tree.css("#pagination [onclick]")
        if (match := _PAGINATION_RE.search(node.attributes.get("onclick", "")))
        and int(match.group("offset")) > current_offset
    }
    next_offset = min(next_offsets) if next_offsets else -1
    return urls, next_offset, limit, lang


def _form_data(
    *,
    offset: int,
    limit: str,
    lang: str,
    filters: dict[str, tuple[str, ...]],
) -> bytes:
    data: list[tuple[str, str]] = [
        ("offset", str(offset)),
        ("limit", limit),
        ("lang", lang),
        ("query", ""),
    ]
    for name, values in filters.items():
        data.extend((name, item) for item in values)
    return urlencode(data).encode()


async def _fetch_listing(
    client: httpx.AsyncClient,
    board_url: str,
    content: bytes | None = None,
) -> str:
    parsed = urlparse(board_url)
    headers = {"Accept": "text/html,application/xhtml+xml"}
    method = "GET"
    if content is not None:
        method = "POST"
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": f"{parsed.scheme}://{parsed.netloc}",
                "Referer": board_url,
            }
        )
    html = await fetch_text_page_with_retry(
        client,
        board_url,
        method=method,
        content=content,
        headers=headers,
        end_of_pagination_statuses=(),
        retryable_statuses={403},
        require_nonempty=True,
        max_bytes=_MAX_HTML_BYTES,
        log_event="prospective.page_backoff",
    )
    if html is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError("Prospective CareerCenter returned no document")
    return html


def _detail_locale(tree: LexborHTMLParser, identity: _ApplicationIdentityConfig) -> str:
    root = tree.css_first("html[lang]")
    raw_locale = root.attributes.get("lang", "") if root is not None else ""
    locale = raw_locale.split("-", 1)[0].casefold()
    if locale not in identity.locale_priority:
        raise ValueError(f"Prospective detail returned an unsupported locale: {raw_locale!r}")
    return locale


def _application_url(
    tree: LexborHTMLParser,
    source_url: str,
    identity: _ApplicationIdentityConfig,
) -> str:
    candidates = {
        urljoin(source_url, node.attributes["href"])
        for node in tree.css("a[href]")
        if " ".join(node.text(separator=" ", strip=True).split()).casefold() in identity.link_texts
    }
    if len(candidates) != 1:
        raise ValueError(
            "Prospective detail must expose exactly one configured application identity link"
        )
    candidate = next(iter(candidates))
    if identity.source_pattern.fullmatch(candidate) is None:
        raise ValueError(f"Prospective detail returned an untrusted application URL: {candidate}")
    return candidate


async def _fetch_rich_job(
    source_url: str,
    client: httpx.AsyncClient,
    identity: _ApplicationIdentityConfig,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, DiscoveredJob]:
    """Fetch one detail and bind its content to a resolving durable application URL."""
    async with semaphore:
        html = await fetch_text_page_with_retry(
            client,
            source_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            end_of_pagination_statuses=(),
            retryable_statuses={403},
            require_nonempty=True,
            max_bytes=_MAX_HTML_BYTES,
            log_event="prospective.detail_backoff",
        )
        if html is None:  # Strict status handling above makes this unreachable.
            raise RuntimeError("Prospective detail returned no document")

        tree = LexborHTMLParser(html)
        locale = _detail_locale(tree, identity)
        application_url = _application_url(tree, source_url, identity)

        async with client.stream(
            "GET",
            application_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            canonical_url = str(response.url)
        if identity.canonical_pattern.fullmatch(canonical_url) is None:
            raise ValueError(
                "Prospective application link resolved outside its canonical allowlist: "
                f"{canonical_url}"
            )

        from src.core.scrapers.jsonld import parse_html

        content = parse_html(html)
        if not content.title or not content.description:
            raise ValueError("Prospective detail omitted required JSON-LD title or description")
        job = DiscoveredJob(
            url=canonical_url,
            title=content.title,
            description=content.description,
            locations=content.locations,
            employment_type=content.employment_type,
            job_location_type=content.job_location_type,
            date_posted=content.date_posted,
            base_salary=content.base_salary,
            language=locale,
            extras=content.extras,
            metadata={
                **(content.metadata or {}),
                "prospective_source_url": source_url,
            },
        )
        return canonical_url, locale, job


def _localization(job: DiscoveredJob) -> dict:
    return {
        key: value
        for key, value in {
            "title": job.title,
            "description": job.description,
            "locations": job.locations,
        }.items()
        if value
    }


def _merge_localized_jobs(
    details: list[tuple[str, str, DiscoveredJob]],
    identity: _ApplicationIdentityConfig,
) -> list[DiscoveredJob]:
    grouped: dict[str, dict[str, DiscoveredJob]] = {}
    for canonical_url, locale, job in details:
        variants = grouped.setdefault(canonical_url, {})
        if locale in variants:
            raise ValueError(
                "Prospective application identity mapped multiple details to the same locale"
            )
        variants[locale] = job

    jobs: list[DiscoveredJob] = []
    for canonical_url in sorted(grouped):
        variants = grouped[canonical_url]
        selected_locale = min(
            variants,
            key=lambda locale: identity.locale_priority.index(locale),
        )
        selected = variants[selected_locale]
        selected.localizations = {
            locale: _localization(variants[locale]) for locale in sorted(variants)
        }
        selected.metadata = {
            **(selected.metadata or {}),
            "application_identity": canonical_url,
            "prospective_source_urls": sorted(
                str((variant.metadata or {})["prospective_source_url"])
                for variant in variants.values()
            ),
        }
        jobs.append(selected)
    return jobs


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Collect filtered details and collapse locale aliases by application identity."""
    _ = pw
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    filters = _validated_filters(metadata.get("filters"))
    identity = _validated_application_identity(metadata.get("application_identity"))
    expected_medium_id = metadata.get("medium_id")
    if expected_medium_id is not None and (
        not isinstance(expected_medium_id, str) or not expected_medium_id.isdigit()
    ):
        raise ValueError("Prospective medium_id must be a numeric string")

    shell = await _fetch_listing(client, board_url)
    _, _, limit, lang = _parse_page(
        shell,
        board_url,
        expected_medium_id=expected_medium_id,
        filters=filters,
    )

    offset = 0
    all_urls: set[str] = set()
    for _page_number in range(1, _MAX_PAGES + 1):
        html = await _fetch_listing(
            client,
            board_url,
            _form_data(offset=offset, limit=limit, lang=lang, filters=filters),
        )
        page_urls, next_offset, page_limit, page_lang = _parse_page(
            html,
            board_url,
            expected_medium_id=expected_medium_id,
            filters=filters,
        )
        if page_limit != limit or page_lang != lang:
            raise ValueError("Prospective form parameters changed during pagination")
        if next_offset >= 0 and not (page_urls - all_urls):
            raise ValueError("Prospective pagination made no progress")
        all_urls.update(page_urls)
        if next_offset < 0:
            semaphore = asyncio.Semaphore(identity.concurrency)
            details = await asyncio.gather(
                *(_fetch_rich_job(url, client, identity, semaphore) for url in sorted(all_urls))
            )
            return _merge_localized_jobs(details, identity)
        if next_offset <= offset:
            raise ValueError("Prospective pagination returned a non-increasing offset")
        offset = next_offset

    raise ValueError(f"Prospective pagination exceeded {_MAX_PAGES} pages")


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Recognize the provider's stable CareerCenter HTML contract."""
    _ = pw
    from src.core.monitors import fetch_page_text

    try:
        html = await fetch_page_text(url, client)
        if not html:
            return None
        medium_id = _medium_id(html)
        urls, _, limit, _ = _parse_page(
            html,
            url,
            expected_medium_id=medium_id,
            filters={},
        )
    except (ValueError, RuntimeError):
        return None
    return {"medium_id": medium_id, "page_size": int(limit), "urls": len(urls)}


register("prospective", discover, cost=10, can_handle=can_handle, rich=True)
