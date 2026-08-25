"""Inline single-page job extraction monitor.

Extracts multiple jobs from a single career page where all postings are
listed inline (no individual job URLs).  Uses step-based extraction
(same as the DOM scraper) in a loop — the cursor advances through the
page, extracting one job per iteration.

Each job gets a synthetic URL with a ``_jid`` query parameter for
pipeline compatibility::

    https://example.com/open-positions?_jid=senior-engineer-a1b2c3

Registered as a **rich** monitor — the scraper step is skipped.

Requires playwright when ``render`` is true:
``uv run playwright install chromium``
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from src.core.monitors import DiscoveredJob, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, run_actions, safe_content
from src.shared.extract import flatten, walk_steps
from src.shared.slug import slugify
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    import httpx

    from src.core.monitor import MonitorResult

log = structlog.get_logger()

_MAX_JOBS = 500  # safety cap
_MAX_EXCLUDE_TITLE_REGEX_LENGTH = 2_048
_MAX_EXCLUDE_DESCRIPTION_REGEX_LENGTH = 2_048
_MAX_VALID_THROUGH_REGEX_LENGTH = 2_048
_MAX_VALID_THROUGH_FORMAT_LENGTH = 128
_MAX_VALID_THROUGH_PATTERNS = 8
_MAX_EMPTY_TEXT_LENGTH = 512
_MAX_EMPTY_SELECTOR_LENGTH = 256
_MAX_DETAIL_SELECTOR_LENGTH = 512
_MAX_DETAIL_IDENTITY_ATTRIBUTE_LENGTH = 128
_MAX_DETAIL_IDENTITY_REGEX_LENGTH = 2_048
_MAX_DETAIL_IDENTITY_LENGTH = 512
_MAX_ITEM_BOUNDARY_TAG_LENGTH = 32
_MAX_SECTION_REGEX_LENGTH = 2_048
_MAX_POSITIONS_PER_LISTING = 20
_DETAIL_BOUNDARY_TAG = "jobseek-inline-detail"
_DETAIL_RESERVED_ATTRIBUTE_PREFIX = "data-inline-detail-"
_HTML_TAG_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_ORDINAL_DAY_SUFFIX_RE = re.compile(r"(?<=\d)(?:st|nd|rd|th)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _DetailIdentity:
    """Raw authenticated identity plus the stable provider capture."""

    raw: str
    stable: str


@dataclass(frozen=True, slots=True)
class _FetchedInlineHtml:
    """Fetched markup and trusted click identities kept outside provider HTML."""

    html: str
    detail_identities: tuple[_DetailIdentity, ...] = ()


def _compile_exclude_title_regex(value: object) -> re.Pattern[str] | None:
    """Validate and compile the optional title-exclusion regex."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_EXCLUDE_TITLE_REGEX_LENGTH:
        raise ValueError("inline exclude_title_regex must be a non-empty bounded string")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError(f"inline exclude_title_regex is invalid: {exc}") from exc


def _compile_exclude_description_regex(value: object) -> re.Pattern[str] | None:
    """Validate and compile the optional plain-text description exclusion."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_EXCLUDE_DESCRIPTION_REGEX_LENGTH
    ):
        raise ValueError("inline exclude_description_regex must be a non-empty bounded string")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValueError(f"inline exclude_description_regex is invalid: {exc}") from exc


def _plain_description(value: str) -> str:
    """Return normalized visible text for a plain-text exclusion contract."""
    body = LexborHTMLParser(value).body
    text = body.text(separator=" ", strip=True) if body is not None else value
    return " ".join(unescape(text).split())


def _validated_empty_text(value: object) -> str | None:
    """Validate an optional authoritative empty-state marker."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_EMPTY_TEXT_LENGTH
        or "\x00" in value
    ):
        raise ValueError(
            f"inline empty_text must be non-empty text up to {_MAX_EMPTY_TEXT_LENGTH} characters"
        )
    return " ".join(value.split())


def _validated_empty_selector(value: object) -> str | None:
    """Validate the CSS selector that scopes the visible empty-state marker."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_EMPTY_SELECTOR_LENGTH
        or "\x00" in value
    ):
        raise ValueError(
            f"inline empty_selector must be a CSS selector up to {_MAX_EMPTY_SELECTOR_LENGTH} "
            "characters"
        )
    selector = value.strip()
    try:
        LexborHTMLParser("<div></div>").css(selector)
    except SelectolaxError as exc:
        raise ValueError(f"inline empty_selector is invalid: {selector!r}") from exc
    return selector


def _validated_detail_selector(value: object, *, name: str) -> str | None:
    """Validate a bounded Playwright selector used by detail-card expansion."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_DETAIL_SELECTOR_LENGTH
        or "\x00" in value
    ):
        raise ValueError(
            f"inline {name} must be a non-empty selector up to "
            f"{_MAX_DETAIL_SELECTOR_LENGTH} characters"
        )
    return value.strip()


def _validated_detail_identity_config(
    metadata: dict,
    *,
    enabled: bool,
) -> tuple[str | None, str | None, re.Pattern[str] | None]:
    """Validate the provider identity contract for click-only detail cards."""
    keys = (
        "detail_identity_selector",
        "detail_identity_attribute",
        "detail_identity_regex",
    )
    configured = [metadata.get(key) is not None for key in keys]
    if not enabled:
        if any(configured):
            raise ValueError("inline detail identity configuration requires detail-card expansion")
        return None, None, None
    if not all(configured):
        raise ValueError(
            "inline detail-card expansion requires detail_identity_selector, "
            "detail_identity_attribute, and detail_identity_regex"
        )

    selector = _validated_detail_selector(
        metadata.get("detail_identity_selector"), name="detail_identity_selector"
    )
    attribute = metadata.get("detail_identity_attribute")
    if (
        not isinstance(attribute, str)
        or not attribute
        or len(attribute) > _MAX_DETAIL_IDENTITY_ATTRIBUTE_LENGTH
        or re.fullmatch(r"[A-Za-z_:][A-Za-z0-9:._-]*", attribute) is None
    ):
        raise ValueError("inline detail_identity_attribute must be a valid bounded attribute name")
    raw_regex = metadata.get("detail_identity_regex")
    if (
        not isinstance(raw_regex, str)
        or not raw_regex
        or len(raw_regex) > _MAX_DETAIL_IDENTITY_REGEX_LENGTH
    ):
        raise ValueError("inline detail_identity_regex must be a non-empty bounded string")
    try:
        pattern = re.compile(raw_regex)
    except re.error as exc:
        raise ValueError(f"inline detail_identity_regex is invalid: {exc}") from exc
    if pattern.groups != 1:
        raise ValueError("inline detail_identity_regex must contain exactly one capture group")
    return selector, attribute, pattern


def _validated_source_identity_config(
    metadata: dict,
    *,
    uses_detail_expansion: bool,
) -> tuple[str | None, str | None, re.Pattern[str] | None]:
    """Validate stable identities read directly from an ordinary listing."""
    keys = (
        "source_identity_selector",
        "source_identity_attribute",
        "source_identity_regex",
    )
    configured = [metadata.get(key) is not None for key in keys]
    if not any(configured):
        return None, None, None
    if uses_detail_expansion:
        raise ValueError("inline source identity configuration cannot use detail-card expansion")
    if not all(configured):
        raise ValueError(
            "inline source identity configuration requires source_identity_selector, "
            "source_identity_attribute, and source_identity_regex"
        )
    selector = _validated_empty_selector(metadata.get("source_identity_selector"))
    assert selector is not None
    attribute = metadata.get("source_identity_attribute")
    if (
        not isinstance(attribute, str)
        or not attribute
        or len(attribute) > _MAX_DETAIL_IDENTITY_ATTRIBUTE_LENGTH
        or re.fullmatch(r"[A-Za-z_:][A-Za-z0-9:._-]*", attribute) is None
    ):
        raise ValueError("inline source_identity_attribute must be a valid bounded attribute name")
    raw_regex = metadata.get("source_identity_regex")
    if (
        not isinstance(raw_regex, str)
        or not raw_regex
        or len(raw_regex) > _MAX_DETAIL_IDENTITY_REGEX_LENGTH
    ):
        raise ValueError("inline source_identity_regex must be a non-empty bounded string")
    try:
        pattern = re.compile(raw_regex)
    except re.error as exc:
        raise ValueError(f"inline source_identity_regex is invalid: {exc}") from exc
    if pattern.groups != 1:
        raise ValueError("inline source_identity_regex must contain exactly one capture group")
    return selector, attribute, pattern


def _read_source_identities(
    html: str,
    *,
    selector: str,
    attribute: str,
    pattern: re.Pattern[str],
) -> tuple[_DetailIdentity, ...]:
    """Extract an ordered, unique provider identity sequence from static HTML."""
    nodes = LexborHTMLParser(html).css(selector)
    if not nodes:
        raise ValueError("inline source identity selector did not match any elements")
    if len(nodes) > _MAX_JOBS:
        raise ValueError("inline source identity selector exceeded the job safety cap")
    identities: list[_DetailIdentity] = []
    for index, node in enumerate(nodes):
        raw_identity = node.attributes.get(attribute)
        if raw_identity is None:
            raise ValueError(
                f"inline source identity {index + 1} is missing attribute {attribute!r}"
            )
        match = pattern.fullmatch(raw_identity)
        if match is None:
            raise ValueError(
                f"inline source identity {index + 1} did not match source_identity_regex"
            )
        identity = match.group(1)
        if (
            not identity
            or len(identity) > _MAX_DETAIL_IDENTITY_LENGTH
            or any(ord(character) < 0x20 for character in identity)
        ):
            raise ValueError(f"inline source identity {index + 1} is invalid")
        identities.append(_DetailIdentity(raw=raw_identity, stable=identity))
    if len({identity.stable for identity in identities}) != len(identities):
        raise ValueError("inline source identities must be unique")
    return tuple(identities)


async def _read_detail_identities(
    page,
    *,
    selector: str,
    attribute: str,
    pattern: re.Pattern[str],
    expected_count: int,
) -> tuple[_DetailIdentity, ...]:
    """Read and validate one stable provider identity per click control."""
    identity_nodes = page.locator(selector)
    identity_count = await identity_nodes.count()
    if identity_count != expected_count:
        raise ValueError(
            "inline detail identity/control count mismatch "
            f"({identity_count} identities for {expected_count} controls)"
        )

    identities: list[_DetailIdentity] = []
    for index in range(identity_count):
        raw_identity = await identity_nodes.nth(index).get_attribute(attribute)
        if raw_identity is None:
            raise ValueError(
                f"inline detail identity {index + 1} is missing attribute {attribute!r}"
            )
        match = pattern.fullmatch(raw_identity)
        if match is None:
            raise ValueError(
                f"inline detail identity {index + 1} did not match detail_identity_regex"
            )
        identity = match.group(1)
        if (
            not identity
            or len(identity) > _MAX_DETAIL_IDENTITY_LENGTH
            or any(ord(character) < 0x20 for character in identity)
        ):
            raise ValueError(f"inline detail identity {index + 1} is invalid")
        identities.append(_DetailIdentity(raw=raw_identity, stable=identity))

    if len({identity.stable for identity in identities}) != len(identities):
        raise ValueError("inline detail identities must be unique")
    return tuple(identities)


def _validated_expanded_detail_html(value: str) -> str:
    """Reject provider markup that could counterfeit a synthetic item boundary."""
    tree = LexborHTMLParser(value)
    for node in tree.css("*"):
        if node.tag == _DETAIL_BOUNDARY_TAG or node.tag == "article":
            raise ValueError(
                "inline expanded detail may not contain nested article or reserved boundary tags"
            )
        if any(
            name.casefold().startswith(_DETAIL_RESERVED_ATTRIBUTE_PREFIX)
            for name in node.attributes
        ):
            raise ValueError("inline expanded detail may not contain reserved boundary attributes")
    return value


def _consume_detail_identity(
    identity: _DetailIdentity,
    pattern: re.Pattern[str],
) -> str:
    """Re-authenticate an out-of-band identity at its point of use."""
    match = pattern.fullmatch(identity.raw)
    if match is None or match.group(1) != identity.stable:
        raise ValueError("inline trusted detail identity failed consumption validation")
    return identity.stable


def _matches_explicit_empty(html: str, selector: str, marker: str) -> bool:
    """Return whether *marker* occurs inside an explicitly selected visible state."""
    tree = LexborHTMLParser(html)
    for node in tree.css(selector):
        text = " ".join(node.text(separator=" ", strip=True).split())
        if marker.casefold() in text.casefold():
            return True
    return False


def _validated_item_boundary_tag(value: object) -> str | None:
    """Validate the optional HTML tag that starts each inline posting."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_ITEM_BOUNDARY_TAG_LENGTH
        or _HTML_TAG_RE.fullmatch(value.casefold()) is None
    ):
        raise ValueError("inline item_boundary_tag must be a valid HTML tag name")
    return value.casefold()


def _validated_section_boundary(value: object, *, name: str) -> dict | None:
    """Validate one fail-closed boundary for a section of an inline page."""
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not value
        or set(value)
        - {
            "tag",
            "text",
            "attr",
            "match_regex",
        }
    ):
        raise ValueError(f"inline {name} must contain only tag, text, attr, or match_regex")
    if not any(value.get(key) is not None for key in ("tag", "text", "attr", "match_regex")):
        raise ValueError(f"inline {name} must configure at least one matcher")
    tag = value.get("tag")
    if tag is not None and (
        not isinstance(tag, str)
        or len(tag) > _MAX_ITEM_BOUNDARY_TAG_LENGTH
        or _HTML_TAG_RE.fullmatch(tag.casefold()) is None
    ):
        raise ValueError(f"inline {name}.tag must be a valid HTML tag name")
    for key in ("text", "attr"):
        configured = value.get(key)
        if configured is not None and (
            not isinstance(configured, str)
            or not configured.strip()
            or len(configured) > 512
            or "\x00" in configured
        ):
            raise ValueError(f"inline {name}.{key} must be non-empty bounded text")
    pattern = value.get("match_regex")
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern or len(pattern) > _MAX_SECTION_REGEX_LENGTH:
            raise ValueError(f"inline {name}.match_regex must be a non-empty bounded string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"inline {name}.match_regex is invalid: {exc}") from exc
    return dict(value)


def _boundary_matches(element: dict, boundary: dict) -> bool:
    """Return whether a flattened element matches a configured section boundary."""
    tag = boundary.get("tag")
    if tag is not None and element["tag"] != tag.casefold():
        return False
    text = boundary.get("text")
    if text is not None and text.casefold() not in element["text"].casefold():
        return False
    pattern = boundary.get("match_regex")
    if pattern is not None and re.search(pattern, element["text"], re.DOTALL) is None:
        return False
    attr = boundary.get("attr")
    if attr is not None:
        if "=" in attr:
            key, expected = attr.split("=", 1)
            if key not in element["attrs"] or expected not in element["attrs"][key]:
                return False
        elif attr not in element["attrs"]:
            return False
    return True


def _scope_to_section(
    elements: list[dict],
    start_boundary: dict | None,
    end_boundary: dict | None,
) -> list[dict]:
    """Return elements strictly between two authoritative page markers."""
    if (start_boundary is None) != (end_boundary is None):
        raise ValueError("inline section_start and section_end must be configured together")
    if start_boundary is None:
        return elements
    assert end_boundary is not None

    starts = [
        index
        for index, element in enumerate(elements)
        if _boundary_matches(element, start_boundary)
    ]
    if not starts:
        raise ValueError("inline section_start did not match the page")
    if len(starts) != 1:
        raise ValueError("inline section_start matched multiple page elements")
    start = starts[0]
    ends = [
        index
        for index, element in enumerate(elements[start + 1 :], start + 1)
        if _boundary_matches(element, end_boundary)
    ]
    if not ends:
        raise ValueError("inline section_end did not match after section_start")
    if len(ends) != 1:
        raise ValueError("inline section_end matched multiple elements after section_start")
    end = ends[0]
    return elements[start + 1 : end]


def _walk_bounded_item(
    elements: list[dict],
    steps: list[dict],
    cursor: int,
    boundary_tag: str,
) -> tuple[dict[str, str | list[str] | None], int]:
    """Run extraction inside one tag-delimited posting block."""
    item_start = next(
        (index for index in range(cursor, len(elements)) if elements[index]["tag"] == boundary_tag),
        len(elements),
    )
    if item_start >= len(elements):
        return {}, len(elements)
    item_end = next(
        (
            index
            for index in range(item_start + 1, len(elements))
            if elements[index]["tag"] == boundary_tag
        ),
        len(elements),
    )
    result, _block_cursor = walk_steps(elements[item_start:item_end], steps)
    return result, item_end


def _generate_url(board_url: str, title: str, seen: dict[str, int]) -> str:
    """Generate a stable synthetic URL for an inline job.

    Format: ``{board_url}?_jid={slug}-{hash[:6]}``
    Appends a counter suffix on collision (identical titles).
    """
    slug = slugify(title)[:50]
    title_hash = hashlib.sha256(title.strip().lower().encode()).hexdigest()[:6]
    jid = f"{slug}-{title_hash}" if slug else title_hash

    # Handle collisions (identical titles on same page)
    count = seen.get(jid, 0)
    seen[jid] = count + 1
    if count > 0:
        jid = f"{jid}-{count + 1}"

    return _generate_identity_url(board_url, jid)


def _generate_identity_url(board_url: str, identity: str) -> str:
    """Append a provider-stable inline identity to the canonical board URL."""
    parsed = urlparse(board_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["_jid"] = [identity]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _validated_valid_through_config(
    metadata: dict,
) -> tuple[tuple[tuple[re.Pattern[str], str | None], ...], str | None, bool]:
    """Validate and compile deadline filtering configuration once per cycle."""
    pattern_value = metadata.get("valid_through_regex")
    patterns: list[tuple[re.Pattern[str], str | None]] = []
    legacy_pattern: re.Pattern[str] | None = None
    if pattern_value is not None:
        if (
            not isinstance(pattern_value, str)
            or not pattern_value
            or len(pattern_value) > _MAX_VALID_THROUGH_REGEX_LENGTH
        ):
            raise ValueError("inline valid_through_regex must be a non-empty bounded string")
        try:
            legacy_pattern = re.compile(pattern_value, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f"inline valid_through_regex is invalid: {exc}") from exc
        if legacy_pattern.groups < 1:
            raise ValueError("inline valid_through_regex must contain a capture group")

    date_format = metadata.get("valid_through_format")
    if date_format is not None and (
        not isinstance(date_format, str)
        or not date_format
        or len(date_format) > _MAX_VALID_THROUGH_FORMAT_LENGTH
    ):
        raise ValueError(
            "inline valid_through_format must be a non-empty string up to "
            f"{_MAX_VALID_THROUGH_FORMAT_LENGTH} characters"
        )

    pattern_values = metadata.get("valid_through_patterns")
    if pattern_values is not None:
        if pattern_value is not None:
            raise ValueError(
                "inline valid_through_patterns cannot be combined with valid_through_regex"
            )
        if (
            not isinstance(pattern_values, list)
            or not pattern_values
            or len(pattern_values) > _MAX_VALID_THROUGH_PATTERNS
        ):
            raise ValueError("inline valid_through_patterns must be a non-empty bounded list")
        for index, item in enumerate(pattern_values):
            if not isinstance(item, dict) or set(item) - {"regex", "format"}:
                raise ValueError(
                    f"inline valid_through_patterns[{index}] must contain only regex and format"
                )
            configured_regex = item.get("regex")
            configured_format = item.get("format")
            if (
                not isinstance(configured_regex, str)
                or not configured_regex
                or len(configured_regex) > _MAX_VALID_THROUGH_REGEX_LENGTH
            ):
                raise ValueError(
                    f"inline valid_through_patterns[{index}].regex must be a non-empty "
                    "bounded string"
                )
            if configured_format is not None and (
                not isinstance(configured_format, str)
                or not configured_format
                or len(configured_format) > _MAX_VALID_THROUGH_FORMAT_LENGTH
            ):
                raise ValueError(
                    f"inline valid_through_patterns[{index}].format must be a non-empty "
                    "bounded string"
                )
            try:
                compiled = re.compile(configured_regex, re.IGNORECASE | re.DOTALL)
            except re.error as exc:
                raise ValueError(
                    f"inline valid_through_patterns[{index}].regex is invalid: {exc}"
                ) from exc
            if compiled.groups < 1:
                raise ValueError(
                    f"inline valid_through_patterns[{index}].regex must contain a capture group"
                )
            patterns.append((compiled, configured_format))
    elif pattern_value is not None:
        assert legacy_pattern is not None
        patterns.append((legacy_pattern, date_format))

    exclude_expired = metadata.get("exclude_expired", False)
    if not isinstance(exclude_expired, bool):
        raise ValueError("inline exclude_expired must be a boolean")
    return tuple(patterns), date_format, exclude_expired


def _parse_valid_through(value: object, date_format: str | None) -> date:
    """Parse an inline opportunity deadline into a calendar date.

    ISO dates work without configuration. Human-readable deadlines require a
    ``valid_through_format`` so parsing remains deterministic across hosts and
    locales. English ordinal suffixes are ignored (``29th June 2026``).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("inline valid_through must be a non-empty string")
    cleaned = _ORDINAL_DAY_SUFFIX_RE.sub("", value.strip())
    try:
        if date_format:
            return datetime.strptime(cleaned, date_format).date()
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    except ValueError as exc:
        hint = f" with format {date_format!r}" if date_format else " as ISO 8601"
        raise ValueError(f"inline valid_through {value!r} could not be parsed{hint}") from exc


def _resolve_valid_through(
    result: dict,
    job_defaults: dict,
    description: object,
    patterns: tuple[tuple[re.Pattern[str], str | None], ...],
    date_format: str | None,
    exclude_expired: bool,
) -> date | None:
    """Resolve a deadline from an extracted field, description, or default."""
    raw = result.get("valid_through")
    if raw is not None:
        return _parse_valid_through(raw, date_format)

    if not isinstance(description, str):
        description = ""
    for pattern, pattern_format in patterns:
        match = pattern.search(description)
        if match is not None:
            return _parse_valid_through(match.group(1).strip(), pattern_format)

    raw = job_defaults.get("valid_through")

    if raw is None:
        if exclude_expired:
            raise ValueError("inline exclude_expired requires valid_through for every opportunity")
        return None
    return _parse_valid_through(raw, date_format)


async def _fetch_html(
    board_url: str,
    metadata: dict,
    http: httpx.AsyncClient | None,
    pw=None,
) -> _FetchedInlineHtml:
    """Fetch page HTML, using Playwright when render is configured.

    ``fetch_urls`` lets a board keep its public URL as the canonical posting
    source while trying equivalent, externally reachable representations in
    order. This is useful for small evergreen boards whose public frontend is
    blocked from the crawler network but is available through a read-only
    rendering gateway. Synthetic job URLs must continue to use ``board_url``.
    """
    configured_urls = metadata.get("fetch_urls")
    if configured_urls is None:
        fetch_url = metadata.get("fetch_url") or board_url
        if not isinstance(fetch_url, str):
            raise ValueError("inline fetch_url must be a non-empty string")
        fetch_candidates = [(fetch_url, None)]
    else:
        if not isinstance(configured_urls, list) or not configured_urls:
            raise ValueError("inline fetch_urls must be a non-empty list")
        fetch_candidates = []
        for index, candidate in enumerate(configured_urls):
            if isinstance(candidate, str):
                fetch_url = candidate
                fetch_headers = None
            elif isinstance(candidate, dict):
                fetch_url = candidate.get("url")
                fetch_headers = candidate.get("headers")
                if fetch_headers is not None and (
                    not isinstance(fetch_headers, dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, str)
                        for key, value in fetch_headers.items()
                    )
                ):
                    raise ValueError(
                        f"inline fetch_urls[{index}].headers must map strings to strings"
                    )
            else:
                raise ValueError(f"inline fetch_urls[{index}] must be a URL string or object")
            if not isinstance(fetch_url, str) or not fetch_url:
                raise ValueError(f"inline fetch_urls[{index}].url must be a non-empty string")
            fetch_candidates.append((fetch_url, fetch_headers))
    required_text = metadata.get("fetch_contains")
    detail_click_selector = _validated_detail_selector(
        metadata.get("detail_click_selector"), name="detail_click_selector"
    )
    detail_content_selector = _validated_detail_selector(
        metadata.get("detail_content_selector"), name="detail_content_selector"
    )
    if (detail_click_selector is None) != (detail_content_selector is None):
        raise ValueError(
            "inline detail_click_selector and detail_content_selector must be configured together"
        )
    if detail_click_selector is not None and not metadata.get("render"):
        raise ValueError("inline detail-card expansion requires render=true")
    identity_selector, identity_attribute, identity_pattern = _validated_detail_identity_config(
        metadata,
        enabled=detail_click_selector is not None,
    )

    def validate(html: str, fetch_url: str) -> str:
        if required_text and required_text not in html:
            raise ValueError(f"inline fetch from {fetch_url} omitted required text")
        return html

    last_error: Exception | None = None
    if metadata.get("render") and pw:
        browser_cfg = {k: v for k, v in metadata.items() if k in BROWSER_KEYS}
        for fetch_url, _fetch_headers in fetch_candidates:
            try:
                async with open_page(
                    pw, browser_cfg, use_proxy=bool(metadata.get("proxy"))
                ) as page:
                    await navigate(page, fetch_url, browser_cfg)
                    await run_actions(page, browser_cfg.get("actions", []))
                    if detail_click_selector is not None:
                        detail_links = page.locator(detail_click_selector)
                        detail_timeout = browser_cfg.get("timeout", 30_000)
                        await detail_links.first.wait_for(state="visible", timeout=detail_timeout)
                        detail_count = await detail_links.count()
                        if detail_count == 0:
                            raise ValueError(
                                "inline detail_click_selector did not match any elements"
                            )
                        if detail_count > _MAX_JOBS:
                            raise ValueError(
                                "inline detail_click_selector exceeded the "
                                f"{_MAX_JOBS}-job safety cap"
                            )
                        assert identity_selector is not None
                        assert identity_attribute is not None
                        assert identity_pattern is not None
                        identities = await _read_detail_identities(
                            page,
                            selector=identity_selector,
                            attribute=identity_attribute,
                            pattern=identity_pattern,
                            expected_count=detail_count,
                        )

                        details: list[str] = []
                        for index in range(detail_count):
                            if index:
                                await navigate(page, fetch_url, browser_cfg)
                                await run_actions(page, browser_cfg.get("actions", []))
                                detail_links = page.locator(detail_click_selector)
                                await detail_links.first.wait_for(
                                    state="visible", timeout=detail_timeout
                                )
                                current_count = await detail_links.count()
                                if current_count != detail_count:
                                    raise ValueError(
                                        "inline detail_click_selector match count changed during "
                                        f"expansion ({detail_count} -> {current_count})"
                                    )
                                current_identities = await _read_detail_identities(
                                    page,
                                    selector=identity_selector,
                                    attribute=identity_attribute,
                                    pattern=identity_pattern,
                                    expected_count=detail_count,
                                )
                                if current_identities != identities:
                                    raise ValueError(
                                        "inline detail identity sequence changed during expansion"
                                    )

                            await detail_links.nth(index).click()
                            detail_content = page.locator(detail_content_selector)
                            await detail_content.wait_for(state="visible", timeout=detail_timeout)
                            content_count = await detail_content.count()
                            if content_count != 1:
                                raise ValueError(
                                    "inline detail_content_selector must match exactly one element "
                                    f"after each click (matched {content_count})"
                                )
                            detail_html = _validated_expanded_detail_html(
                                await detail_content.inner_html()
                            )
                            details.append(
                                f"<{_DETAIL_BOUNDARY_TAG}>"
                                "__inline_detail_boundary__"
                                f"{detail_html}</{_DETAIL_BOUNDARY_TAG}>"
                            )
                        return _FetchedInlineHtml(
                            html=validate("".join(details), fetch_url),
                            detail_identities=identities,
                        )
                    return _FetchedInlineHtml(html=validate(await safe_content(page), fetch_url))
            except Exception as exc:
                last_error = exc
                log.warning("inline.fetch_fallback", url=fetch_url, error=str(exc))
    else:
        if http is None:
            raise ValueError("inline static fetch requires an HTTP client")
        for fetch_url, fetch_headers in fetch_candidates:
            try:
                resp = await http.get(
                    fetch_url,
                    follow_redirects=True,
                    headers=fetch_headers,
                )
                resp.raise_for_status()
                return _FetchedInlineHtml(html=validate(resp.text, fetch_url))
            except Exception as exc:
                last_error = exc
                log.warning("inline.fetch_fallback", url=fetch_url, error=str(exc))

    if last_error is not None:
        raise last_error
    return _FetchedInlineHtml(html="")


async def discover(
    board: dict,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> list[DiscoveredJob] | MonitorResult:
    """Extract inline jobs from a single page.

    Config keys:
        steps      — extraction steps (same format as DOM scraper)
        render     — if true, use Playwright (default: false)
        detail_click_selector — click each matching card control in turn (render only)
        detail_content_selector — exactly one expanded detail container after each click
        detail_identity_selector — one provider-identity element per click control
        detail_identity_attribute — attribute containing the raw provider identity
        detail_identity_regex — full-match regex with one stable-identity capture group
        source_identity_selector — CSS selector for stable identities on ordinary listings
        source_identity_attribute — attribute containing the raw ordinary-listing identity
        source_identity_regex — full-match regex capturing the stable ordinary-listing ID
        fetch_urls — ordered alternate read URLs; canonical URLs use board_url
        include_hidden — include HTML hidden by tab/accordion state (default: false)
        empty_selector — CSS selector that scopes a visibly active empty-state element
        empty_text — authoritative text required inside empty_selector
        nonempty_selector — optional selector whose presence overrides the empty marker
        require_zero_proof — fail when extraction returns no jobs without an explicit
                             empty_selector/empty_text match (default: false)
        item_boundary_tag — optional tag that starts and bounds each posting
        section_start — first page-section boundary (exclusive; requires section_end)
        section_end — last page-section boundary (exclusive; requires section_start)
        preserve_single_location — keep an extracted location string intact (default: false)
        description_from_title — reuse the extracted title as description (default: false)
        positions_per_listing — expand one aggregate source row into this many stable jobs
        defaults   — default field values applied to all jobs
        defaults_by_title — per-title defaults applied to missing fields
        exclude_titles — exact titles to skip after extraction
        exclude_title_regex — regex matching titles to skip after extraction
        exclude_description_regex — regex matching normalized description text to skip
        valid_through_regex — capture one deadline format from the description
        valid_through_patterns — ordered regex/format pairs for multiple deadline formats
        valid_through_format — strptime format for non-ISO deadlines
        exclude_expired — omit opportunities after valid_through (UTC, inclusive deadline)
        + browser keys (wait, timeout, actions, etc.)
    """
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}

    empty_text = _validated_empty_text(metadata.get("empty_text"))
    empty_selector = _validated_empty_selector(metadata.get("empty_selector"))
    nonempty_selector = _validated_empty_selector(metadata.get("nonempty_selector"))
    if (empty_text is None) != (empty_selector is None):
        raise ValueError("inline explicit empty state requires empty_selector and empty_text")
    if nonempty_selector is not None and empty_text is None:
        raise ValueError("inline nonempty_selector requires empty_selector and empty_text")
    require_zero_proof = metadata.get("require_zero_proof", False)
    if not isinstance(require_zero_proof, bool):
        raise ValueError("inline require_zero_proof must be a boolean")

    steps = metadata.get("steps")
    if not steps:
        if empty_text is not None:
            raise ValueError("inline explicit empty state requires non-empty steps")
        if require_zero_proof:
            raise ValueError("inline require_zero_proof requires non-empty steps")
        log.warning("inline.no_steps", url=board_url)
        return []

    defaults = metadata.get("defaults") or {}
    defaults_by_title = metadata.get("defaults_by_title") or {}
    exclude_titles = set(metadata.get("exclude_titles") or [])
    exclude_title_regex = _compile_exclude_title_regex(metadata.get("exclude_title_regex"))
    exclude_description_regex = _compile_exclude_description_regex(
        metadata.get("exclude_description_regex")
    )
    item_boundary_tag = _validated_item_boundary_tag(metadata.get("item_boundary_tag"))
    uses_detail_expansion = metadata.get("detail_click_selector") is not None
    if uses_detail_expansion:
        if item_boundary_tag is not None:
            raise ValueError("inline detail-card expansion sets its item boundary automatically")
        item_boundary_tag = _DETAIL_BOUNDARY_TAG
    source_identity_selector, source_identity_attribute, source_identity_pattern = (
        _validated_source_identity_config(
            metadata,
            uses_detail_expansion=uses_detail_expansion,
        )
    )
    section_start = _validated_section_boundary(metadata.get("section_start"), name="section_start")
    section_end = _validated_section_boundary(metadata.get("section_end"), name="section_end")
    preserve_single_location = metadata.get("preserve_single_location", False)
    if not isinstance(preserve_single_location, bool):
        raise ValueError("inline preserve_single_location must be a boolean")
    description_from_title = metadata.get("description_from_title", False)
    if not isinstance(description_from_title, bool):
        raise ValueError("inline description_from_title must be a boolean")
    positions_per_listing = metadata.get("positions_per_listing", 1)
    if (
        not isinstance(positions_per_listing, int)
        or isinstance(positions_per_listing, bool)
        or not 1 <= positions_per_listing <= _MAX_POSITIONS_PER_LISTING
    ):
        raise ValueError(
            "inline positions_per_listing must be an integer from 1 to "
            f"{_MAX_POSITIONS_PER_LISTING}"
        )
    valid_through_patterns, valid_through_format, exclude_expired = _validated_valid_through_config(
        metadata
    )

    fetched = await _fetch_html(board_url, metadata, client, pw)
    html = fetched.html
    detail_identities = fetched.detail_identities
    _identity_selector, _identity_attribute, detail_identity_pattern = (
        _validated_detail_identity_config(metadata, enabled=uses_detail_expansion)
    )
    if uses_detail_expansion:
        assert detail_identity_pattern is not None
        if not detail_identities:
            raise ValueError("inline detail-card expansion returned no trusted identities")
    elif detail_identities:
        raise ValueError("inline ordinary mode received unexpected detail identities")
    source_identities: tuple[_DetailIdentity, ...] = ()
    if source_identity_selector is not None:
        assert source_identity_attribute is not None
        assert source_identity_pattern is not None
        source_identities = _read_source_identities(
            html,
            selector=source_identity_selector,
            attribute=source_identity_attribute,
            pattern=source_identity_pattern,
        )
    include_hidden = bool(metadata.get("include_hidden"))
    elements = flatten(html, include_hidden=include_hidden)

    elements = _scope_to_section(elements, section_start, section_end)
    if empty_text is not None:
        assert empty_selector is not None
        if _matches_explicit_empty(html, empty_selector, empty_text):
            has_nonempty_items = nonempty_selector is not None and bool(
                LexborHTMLParser(html).css_first(nonempty_selector)
            )
            if not has_nonempty_items:
                log.info("inline.explicit_empty", url=board_url)
                return []
    if not elements:
        if empty_text is not None:
            raise ValueError(
                "inline monitor found no accepted jobs and did not match the configured "
                "explicit empty state"
            )
        if require_zero_proof:
            raise ValueError(
                "inline monitor found no accepted jobs without authoritative empty-state proof"
            )
        log.info("inline.empty_page", url=board_url)
        return []

    # Extract jobs by running steps repeatedly
    jobs: list[DiscoveredJob] = []
    seen_jids: dict[str, int] = {}
    expired_count = 0
    processed_count = 0
    expansion_truncated = False
    today = datetime.now(UTC).date()
    cursor = 0
    detail_item_index = 0
    source_identity_index = 0

    while cursor < len(elements) and processed_count < _MAX_JOBS:
        if item_boundary_tag is None:
            result, new_cursor = walk_steps(elements, steps, start=cursor)
            provider_identity = None
        else:
            result, new_cursor = _walk_bounded_item(
                elements,
                steps,
                cursor,
                item_boundary_tag,
            )
            provider_identity = None
            if uses_detail_expansion and new_cursor > cursor:
                if detail_item_index >= len(detail_identities):
                    raise ValueError("inline expanded detail boundary count exceeded identities")
                assert detail_identity_pattern is not None
                provider_identity = _consume_detail_identity(
                    detail_identities[detail_item_index],
                    detail_identity_pattern,
                )
                detail_item_index += 1

        # Stop if no title found or cursor didn't advance
        title = cast(str | None, result.get("title"))
        if new_cursor <= cursor:
            break
        if not title:
            if uses_detail_expansion:
                raise ValueError("inline expanded detail omitted its title")
            if item_boundary_tag is None:
                break
            cursor = new_cursor
            continue

        cursor = new_cursor
        processed_count += 1

        if source_identity_selector is not None:
            if source_identity_index >= len(source_identities):
                raise ValueError("inline extracted more jobs than source identities")
            assert source_identity_pattern is not None
            provider_identity = _consume_detail_identity(
                source_identities[source_identity_index], source_identity_pattern
            )
            source_identity_index += 1

        if title in exclude_titles or (
            exclude_title_regex is not None and exclude_title_regex.search(title)
        ):
            continue

        description = cast(str | None, result.get("description")) or (
            title if description_from_title else None
        )
        if (
            description is not None
            and exclude_description_regex is not None
            and exclude_description_regex.search(_plain_description(description))
        ):
            continue

        if provider_identity is not None:
            url = _generate_identity_url(board_url, provider_identity)
        else:
            url = _generate_url(board_url, title, seen_jids)

        job_defaults = {**defaults, **(defaults_by_title.get(title) or {})}

        # Build DiscoveredJob with extracted + default fields
        valid_through = _resolve_valid_through(
            result,
            job_defaults,
            description,
            valid_through_patterns,
            valid_through_format,
            exclude_expired,
        )
        if exclude_expired and valid_through is not None and valid_through < today:
            expired_count += positions_per_listing
            continue
        location = result.get("location")
        locations = None
        if location:
            if isinstance(location, list):
                locations = location
            elif preserve_single_location:
                locations = [location.strip()]
            else:
                locations = [loc.strip() for loc in location.split(",") if loc.strip()]

        for position_index in range(positions_per_listing):
            if len(jobs) >= _MAX_JOBS:
                expansion_truncated = True
                break
            if position_index > 0:
                if provider_identity is not None:
                    url = _generate_identity_url(
                        board_url, f"{provider_identity}-{position_index + 1}"
                    )
                else:
                    url = _generate_url(board_url, title, seen_jids)
            jobs.append(
                DiscoveredJob(
                    url=url,
                    title=title,
                    description=description or job_defaults.get("description"),
                    locations=locations or job_defaults.get("locations"),
                    employment_type=cast(str | None, result.get("employment_type"))
                    or job_defaults.get("employment_type"),
                    job_location_type=cast(str | None, result.get("job_location_type"))
                    or job_defaults.get("job_location_type"),
                    date_posted=cast(str | None, result.get("date_posted"))
                    or job_defaults.get("date_posted"),
                    extras={"valid_through": valid_through.isoformat()} if valid_through else None,
                )
            )
        if expansion_truncated:
            break

    if uses_detail_expansion and detail_item_index != len(detail_identities):
        raise ValueError(
            "inline expanded detail boundary/identity count mismatch "
            f"({detail_item_index} boundaries for {len(detail_identities)} identities)"
        )
    if source_identity_selector is not None and source_identity_index != len(source_identities):
        raise ValueError(
            "inline source identity/job count mismatch "
            f"({len(source_identities)} identities for {source_identity_index} jobs)"
        )

    if empty_text is not None and not jobs:
        raise ValueError(
            "inline monitor found no accepted jobs and did not match the configured explicit "
            "empty state"
        )
    if require_zero_proof and not jobs:
        raise ValueError(
            "inline monitor found no accepted jobs without authoritative empty-state proof"
        )

    truncated = expansion_truncated or (processed_count >= _MAX_JOBS and cursor < len(elements))
    log.info("inline.discovered", url=board_url, jobs=len(jobs), expired=expired_count)
    if truncated:
        log.warning("inline.truncated", url=board_url, total=len(jobs), cap=_MAX_JOBS)
        return truncated_rich_result(jobs)
    if not jobs and expired_count and expired_count == processed_count * positions_per_listing:
        from src.core.monitor import MonitorResult

        return MonitorResult(
            urls=set(),
            jobs_by_url={},
            verified_empty_reason="all extracted jobs are past their verified deadline",
        )
    return jobs


register("inline", discover, cost=60, rich=True)
