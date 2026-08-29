#!/usr/bin/env python3
"""Validate, project, generate, and byte-check the synthetic semantics corpus."""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Never
from urllib.parse import SplitResult, urlsplit, urlunsplit

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "fixtures" / "semantics" / "manifest.json"

CONTENT_DOMAIN = b"jobseek.runtime.v1.content-sha256\0"
METADATA_DOMAIN = b"jobseek.runtime.v1.metadata-sha256\0"
SEMANTIC_DOMAIN = b"jobseek.runtime.v1.semantic-sha256\0"
UINT64_MAX = (1 << 64) - 1
MAX_HTML_BYTES = 1_048_576
MAX_HTML_NESTING = 128

CASE_IDS = (
    "safe_scrape_projected",
    "invalid_visible_content",
    "safe_monitor_url_only",
    "rich_monitor",
    "suppressed_precondition",
    "invalid_url",
    "locale_alias",
    "locale_rejection",
    "canonical_collision",
    "ordered_metadata",
    "absent_vs_empty",
    "digest_sensitivity",
    "localized_only_visible",
    "invalid_html_unclosed_comment",
    "invalid_html_unclosed_quote",
    "invalid_html_unclosed_suppressed",
    "invalid_html_nesting_limit",
    "invalid_url_fragment_escape",
    "invalid_url_port_zero",
    "invalid_url_legacy_ip",
    "invalid_url_above_root",
    "safe_url_query_distinctions",
    "safe_url_default_repeated_slash",
    "invalid_url_non_ascii_host",
    "browser_suppressed",
    "unknown_subject_rejected",
    "language_alias",
    "language_rejection",
    "locale_collision",
    "invalid_shape_unknown",
    "invalid_shape_missing",
    "invalid_projection_alignment",
    "monitor_batches_ordered",
    "monitor_batches_sitemap_conflict",
    "monitor_batches_counter_overflow",
    "monitor_incomplete",
    "privacy_rejected",
    "rich_url_union_lockstep",
    "divergent_rich_collision",
    "set_permutation_dedupe",
    "safe_url_leading_zero_default_port",
    "invalid_projection_malformed_url",
    "invalid_localized_description",
    "visible_unterminated_space_entities",
    "invalid_unicode_surrogate",
    "invalid_metadata_null",
    "invalid_localized_description_null",
    "invalid_url_legacy_mixed_components",
    "safe_url_numeric_overrange_dns",
    "invalid_precondition_type",
    "source_identity_explicit_null_legacy_equivalence",
    "source_identity_valid_present_propagation_and_semantic_sensitivity",
    "source_identity_invalid_present_rejected",
    "source_identity_collision_rejected",
)
REASONS = frozenset(
    {
        "canonical_collision",
        "counter_overflow",
        "ineligible_history",
        "invalid_locale",
        "invalid_projection",
        "invalid_url",
        "invalid_visible_content",
        "privacy_rejected",
        "unsupported_result",
    }
)
LOCALES = {
    value.lower(): value
    for value in (
        "de",
        "de-CH",
        "de-DE",
        "en",
        "en-CH",
        "en-GB",
        "en-US",
        "fr",
        "fr-CH",
        "fr-FR",
        "it",
        "it-CH",
        "it-IT",
    )
}
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
PATH_SAFE = UNRESERVED | frozenset("!$&'()*+,;=:@/")
QUERY_SAFE = UNRESERVED | frozenset("!$'()*+,-./:;=?@")
IGNORED_TAGS = frozenset({"noscript", "script", "style", "template"})
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
NON_VISIBLE = frozenset(
    list(range(0x09, 0x0E))
    + [0x20, 0x85, 0xA0, 0x1680]
    + list(range(0x2000, 0x200C))
    + [0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF]
)
JOB_FIELDS = frozenset(
    {
        "base_salary",
        "date_posted",
        "description_html",
        "employment_type",
        "extensions",
        "job_location_type",
        "language",
        "localizations",
        "locations",
        "skills",
        "title",
    }
)
LOCALIZATION_FIELDS = frozenset({"description_html", "locale", "title"})
SOURCE_IDENTITY_PATTERN = re.compile(
    r"^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:"
    r"[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}$"
)


class SemanticFailure(Exception):
    def __init__(self, reason: str, *, rejected: bool = False) -> None:
        if reason not in REASONS:
            raise AssertionError(f"unregistered semantics reason: {reason}")
        super().__init__(reason)
        self.reason = reason
        self.status = "rejected" if rejected else "suppressed"


def _fail(reason: str, *, rejected: bool = False) -> Never:
    raise SemanticFailure(reason, rejected=rejected)


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _fail("invalid_projection", rejected=True)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_projection", rejected=True)
            _validate_json(key)
            _validate_json(item)
        return
    _fail("invalid_projection", rejected=True)


def canonical_json(value: Any) -> bytes:
    """Encode the closed, safe canonical JSON subset used by projections."""
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def length_prefixed(value: bytes) -> bytes:
    if len(value) > UINT64_MAX:
        _fail("counter_overflow")
    return len(value).to_bytes(8, "big") + value


def content_sha256(canonical_url: str, canonical_job: dict[str, Any]) -> str:
    url_bytes = canonical_url.encode("utf-8")
    job_bytes = canonical_json(canonical_job)
    return hashlib.sha256(
        CONTENT_DOMAIN + length_prefixed(url_bytes) + length_prefixed(job_bytes)
    ).hexdigest()


def metadata_sha256(target_url: str, metadata: dict[str, Any]) -> str:
    target_bytes = target_url.encode("utf-8")
    metadata_bytes = canonical_json(metadata)
    return hashlib.sha256(
        METADATA_DOMAIN + length_prefixed(target_bytes) + length_prefixed(metadata_bytes)
    ).hexdigest()


def semantic_sha256(result_without_digest: dict[str, Any]) -> str:
    result_bytes = canonical_json(result_without_digest)
    return hashlib.sha256(SEMANTIC_DOMAIN + length_prefixed(result_bytes)).hexdigest()


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value
    )


def _forbidden_url_scalar(character: str) -> bool:
    return character == "\\" or character.isspace() or unicodedata.category(character) == "Cc"


def _canonical_component(value: str, safe: frozenset[str]) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%":
            if index + 2 >= len(value):
                _fail("invalid_url")
            pair = value[index + 1 : index + 3]
            try:
                decoded = int(pair, 16)
            except ValueError:
                _fail("invalid_url")
            decoded_character = chr(decoded)
            output.append(
                decoded_character if decoded_character in UNRESERVED else f"%{decoded:02X}"
            )
            index += 3
            continue
        if ord(character) < 128:
            if character in safe:
                output.append(character)
            else:
                output.append(f"%{ord(character):02X}")
        else:
            output.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
        index += 1
    return "".join(output)


def _remove_dot_segments(path: str) -> str:
    segments = path.split("/")
    output: list[str] = []
    for index, segment in enumerate(segments):
        if segment == ".":
            continue
        if segment == "..":
            if not output or (len(output) == 1 and output[0] == ""):
                _fail("invalid_url")
            output.pop()
            continue
        output.append(segment)
        if index == 0 and segment != "":
            _fail("invalid_url")
    canonical = "/".join(output)
    return canonical or "/"


def _legacy_ip_component(value: str) -> int | None:
    if value.startswith("0x"):
        digits = value[2:]
        base = 16
        allowed = "0123456789abcdef"
    elif len(value) > 1 and value.startswith("0"):
        digits = value
        base = 8
        allowed = "01234567"
    else:
        digits = value
        base = 10
        allowed = "0123456789"
    if not digits or any(character not in allowed for character in digits):
        return None
    try:
        return int(digits, base)
    except ValueError:
        return None


def _legacy_ip_literal(host: str) -> bool:
    components = host.split(".")
    if not 1 <= len(components) <= 4:
        return False
    parsed = [_legacy_ip_component(component) for component in components]
    if any(component is None for component in parsed):
        return False
    values = [int(component) for component in parsed if component is not None]
    limits = {
        1: (0xFFFFFFFF,),
        2: (0xFF, 0xFFFFFF),
        3: (0xFF, 0xFF, 0xFFFF),
        4: (0xFF, 0xFF, 0xFF, 0xFF),
    }[len(values)]
    return all(value <= limit for value, limit in zip(values, limits, strict=True))


def canonical_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid_url")
    if any(_forbidden_url_scalar(character) for character in value):
        _fail("invalid_url")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("invalid_url")
    scheme = _ascii_lower(parsed.scheme)
    if scheme not in {"http", "https"} or not parsed.netloc:
        _fail("invalid_url")
    if "@" in parsed.netloc or "[" in parsed.netloc or "]" in parsed.netloc:
        _fail("invalid_url")
    if ":" in parsed.netloc:
        _, separator, port_text = parsed.netloc.rpartition(":")
        if not separator or not port_text or not port_text.isascii() or not port_text.isdigit():
            _fail("invalid_url")
    if port == 0:
        _fail("invalid_url")
    host = parsed.hostname
    if host is None or not host.isascii() or host.endswith(".") or "%" in host:
        _fail("invalid_url")
    host = _ascii_lower(host)
    if _legacy_ip_literal(host):
        _fail("invalid_url")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _fail("invalid_url")
    if host != "localhost":
        labels = host.split(".")
        if len(host.encode("ascii")) > 253:
            _fail("invalid_url")
        for label in labels:
            if (
                not label
                or len(label) > 63
                or label[0] == "-"
                or label[-1] == "-"
                or any(
                    not (character.isascii() and (character.isalnum() or character == "-"))
                    for character in label
                )
            ):
                _fail("invalid_url")
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        authority = host
    else:
        authority = f"{host}:{port}"
    raw_path = parsed.path or "/"
    path = _remove_dot_segments(_canonical_component(raw_path, PATH_SAFE))
    _canonical_component(parsed.fragment, QUERY_SAFE)
    fields: list[tuple[bytes, bytes, bool, int, str]] = []
    if parsed.query:
        for ordinal, field in enumerate(parsed.query.split("&")):
            has_equals = "=" in field
            raw_key, _, raw_value = field.partition("=")
            key = _canonical_component(raw_key, QUERY_SAFE)
            canonical_value = _canonical_component(raw_value, QUERY_SAFE)
            rendered = key + ("=" + canonical_value if has_equals else "")
            fields.append(
                (
                    key.encode("utf-8"),
                    canonical_value.encode("utf-8"),
                    has_equals,
                    ordinal,
                    rendered,
                )
            )
    fields.sort(key=lambda item: item[:4])
    query = "&".join(item[4] for item in fields)
    return urlunsplit(SplitResult(scheme, authority, path, query, ""))


def canonical_locale(value: object) -> str:
    if not isinstance(value, str) or not value.isascii():
        _fail("invalid_locale")
    key = _ascii_lower(value.replace("_", "-"))
    if key not in LOCALES:
        _fail("invalid_locale")
    return LOCALES[key]


def _hidden_attributes(attributes: list[tuple[str, str | None]]) -> bool:
    for name, value in attributes:
        name = _ascii_lower(name)
        if name == "hidden":
            return True
        if name == "aria-hidden" and isinstance(value, str) and _ascii_lower(value) == "true":
            return True
        if name != "style" or not isinstance(value, str):
            continue
        for declaration in value.split(";"):
            property_name, separator, property_value = declaration.partition(":")
            if not separator:
                continue
            pair = (
                _ascii_lower(property_name.strip(" \t\n\r\f")),
                _ascii_lower(property_value.strip(" \t\n\r\f")),
            )
            if pair in {
                ("display", "none"),
                ("visibility", "collapse"),
                ("visibility", "hidden"),
            }:
                return True
    return False


class _VisibilityTokenizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, bool]] = []
        self.suppressed_depth = 0
        self.visible = False
        self.ambiguous = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = _ascii_lower(tag)
        if tag in VOID_TAGS:
            return
        if len(self.stack) >= MAX_HTML_NESTING:
            raise ValueError("HTML nesting limit exceeded")
        suppresses = tag in IGNORED_TAGS or _hidden_attributes(attrs)
        self.stack.append((tag, suppresses))
        if suppresses:
            self.suppressed_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = _ascii_lower(tag)
        if not self.stack:
            return
        if self.stack[-1][0] != tag:
            if self.suppressed_depth:
                self.ambiguous = True
            return
        _, suppresses = self.stack.pop()
        if suppresses:
            self.suppressed_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.suppressed_depth == 0 and any(
            ord(character) not in NON_VISIBLE for character in data
        ):
            self.visible = True

    def handle_entityref(self, name: str) -> None:
        if self.suppressed_depth == 0 and _ascii_lower(name) != "nbsp":
            self.visible = True

    def handle_charref(self, name: str) -> None:
        if self.suppressed_depth:
            return
        try:
            scalar = int(name[1:], 16) if name[:1] in {"x", "X"} else int(name, 10)
        except ValueError:
            self.visible = True
            return
        if scalar != 0xA0:
            self.visible = True


def _preserve_unterminated_space_references(value: str) -> str:
    output: list[str] = []
    index = 0
    references = ("&nbsp", "&#160", "&#xa0")
    while index < len(value):
        matched = next(
            (
                reference
                for reference in references
                if _ascii_lower(value[index : index + len(reference)]) == reference
                and value[index + len(reference) : index + len(reference) + 1] != ";"
            ),
            None,
        )
        if matched is None:
            output.append(value[index])
            index += 1
            continue
        output.append("&amp;")
        output.append(value[index + 1 : index + len(matched)])
        index += len(matched)
    return "".join(output)


def _preflight_html(value: str) -> None:
    if len(value.encode("utf-8")) > MAX_HTML_BYTES:
        _fail("invalid_visible_content")
    offset = 0
    while True:
        start = value.find("<", offset)
        if start < 0:
            return
        if value.startswith("<!--", start):
            end = value.find("-->", start + 4)
            if end < 0:
                _fail("invalid_visible_content")
            offset = end + 3
            continue
        quote: str | None = None
        end = start + 1
        while end < len(value):
            character = value[end]
            if quote is not None:
                if character == quote:
                    quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character == ">":
                break
            end += 1
        if end >= len(value) or quote is not None:
            _fail("invalid_visible_content")
        offset = end + 1


def has_visible_content(value: object) -> bool:
    if not isinstance(value, str):
        _fail("invalid_projection", rejected=True)
    _validate_json(value)
    _preflight_html(value)
    for character in value:
        scalar = ord(character)
        if (
            scalar == 0
            or (scalar < 0x20 and scalar not in range(0x09, 0x0E))
            or (0x7F <= scalar <= 0x9F and scalar != 0x85)
        ):
            _fail("invalid_visible_content")
    tokenizer = _VisibilityTokenizer()
    try:
        tokenizer.feed(_preserve_unterminated_space_references(value))
        tokenizer.close()
    except Exception:
        _fail("invalid_visible_content")
    if tokenizer.ambiguous or any(suppresses for _, suppresses in tokenizer.stack):
        _fail("invalid_visible_content")
    return tokenizer.visible


def _canonical_set(values: object) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        _fail("invalid_projection", rejected=True)
    return sorted(set(values), key=lambda item: item.encode("utf-8"))


def canonical_job(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid_projection", rejected=True)
    if not set(value) <= JOB_FIELDS:
        _fail("invalid_projection", rejected=True)
    output = copy.deepcopy(value)
    descriptions: list[str] = []
    if "description_html" in output:
        description = output["description_html"]
        if not isinstance(description, str):
            _fail("invalid_projection", rejected=True)
        descriptions.append(description)
    if "skills" in output:
        output["skills"] = _canonical_set(output["skills"])
    if "locations" in output:
        locations = output["locations"]
        if not isinstance(locations, dict) or set(locations) != {"values"}:
            _fail("invalid_projection", rejected=True)
        locations["values"] = _canonical_set(locations["values"])
    if "language" in output:
        output["language"] = canonical_locale(output["language"])
    localizations = output.get("localizations", [])
    if not isinstance(localizations, list):
        _fail("invalid_projection", rejected=True)
    by_locale: dict[str, tuple[str, dict[str, Any]]] = {}
    for localization in localizations:
        if (
            not isinstance(localization, dict)
            or "locale" not in localization
            or not set(localization) <= LOCALIZATION_FIELDS
        ):
            _fail("invalid_projection", rejected=True)
        source_locale = localization["locale"]
        locale = canonical_locale(source_locale)
        canonical = copy.deepcopy(localization)
        canonical["locale"] = locale
        if "description_html" in canonical:
            description = canonical["description_html"]
            if not isinstance(description, str):
                _fail("invalid_projection", rejected=True)
            descriptions.append(description)
        previous = by_locale.get(locale)
        if previous is not None:
            previous_source, previous_value = previous
            if previous_source != source_locale or canonical_json(previous_value) != canonical_json(
                canonical
            ):
                _fail("canonical_collision")
            continue
        if not isinstance(source_locale, str):
            _fail("invalid_locale")
        by_locale[locale] = (source_locale, canonical)
    output["localizations"] = [
        by_locale[key][1] for key in sorted(by_locale, key=lambda item: item.encode("utf-8"))
    ]
    visible = False
    for description in descriptions:
        visible = has_visible_content(description) or visible
    if not visible:
        _fail("invalid_visible_content")
    canonical_json(output)
    return output


def _uint64(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail("invalid_projection", rejected=True)
    if value > UINT64_MAX:
        _fail("counter_overflow")
    return value


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        _fail("invalid_projection", rejected=True)
    return value


def _source_identity(value: object, *, present: bool) -> str | None:
    if not present or value is None:
        return None
    if not isinstance(value, str) or SOURCE_IDENTITY_PATTERN.fullmatch(value) is None:
        _fail("invalid_projection", rejected=True)
    return value


def _base_effects(request: dict[str, Any], execution_kind: str, target_url: str) -> dict[str, Any]:
    return {
        "canonicalization_rule": "CANONICALIZATION_RULE_RUNTIME_V1",
        "content_hash_rule": "HASH_RULE_CONTENT_SHA256_V1",
        "content_hashes": [],
        "execution_kind": execution_kind,
        "filtered_count": 0,
        "gone_detection_allowed": False,
        "hybrid": False,
        "job_effects": [],
        "origin_request_id": _required_string(request, "origin_request_id"),
        "request_id": _required_string(request, "request_id"),
        "security_filtered_count": 0,
        "target_url": target_url,
        "targets": [],
        "truncated": False,
        "urls_to_upsert": [],
    }


def _add_target(
    effects: dict[str, Any],
    url: str,
    digest: str | None,
    source_identity: str | None = None,
) -> None:
    sentinel = digest or ""
    effects["urls_to_upsert"].append(url)
    effects["content_hashes"].append(sentinel)
    job_effect = {"content_sha256": sentinel, "source_url": url}
    if source_identity is not None:
        job_effect["source_identity"] = source_identity
    effects["job_effects"].append(job_effect)
    target = {"action": "PROJECTED_ACTION_UPSERT", "url": url}
    if digest is not None:
        target["content_sha256"] = digest
    effects["targets"].append(target)


def _require_shape(
    value: object,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or not set(value) <= required | optional
    ):
        _fail("invalid_projection", rejected=True)
    return value


def _validate_existing_effects(value: object) -> None:
    if not isinstance(value, dict):
        _fail("invalid_projection", rejected=True)
    urls = value.get("urls_to_upsert")
    hashes = value.get("content_hashes")
    jobs = value.get("job_effects")
    targets = value.get("targets")
    if (
        not isinstance(urls, list)
        or not isinstance(hashes, list)
        or not isinstance(jobs, list)
        or not isinstance(targets, list)
    ):
        _fail("invalid_projection", rejected=True)
    if len({len(urls), len(hashes), len(jobs), len(targets)}) != 1:
        _fail("invalid_projection", rejected=True)
    if "source_identity" in value:
        _fail("invalid_projection", rejected=True)
    seen_urls: dict[str, tuple[str | None, str]] = {}
    seen_identities: dict[str, str] = {}
    previous_order: tuple[bytes, int, bytes] | None = None
    for index, raw_url in enumerate(urls):
        try:
            canonical = canonical_url(raw_url)
        except SemanticFailure:
            _fail("invalid_projection", rejected=True)
        job_effect = jobs[index]
        target = targets[index]
        digest = hashes[index]
        if (
            not isinstance(job_effect, dict)
            or not {"content_sha256", "source_url"} <= set(job_effect)
            or not set(job_effect) <= {"content_sha256", "source_identity", "source_url"}
        ):
            _fail("invalid_projection", rejected=True)
        source_identity = _source_identity(
            job_effect.get("source_identity"),
            present="source_identity" in job_effect,
        )
        if (
            not isinstance(digest, str)
            or not isinstance(target, dict)
            or "source_identity" in target
            or job_effect.get("source_url") != canonical
            or job_effect.get("content_sha256") != digest
            or target.get("url") != canonical
            or target.get("content_sha256", "") != digest
        ):
            _fail("invalid_projection", rejected=True)
        if digest == "" and source_identity is not None:
            _fail("invalid_projection", rejected=True)
        previous = seen_urls.get(canonical)
        if previous is not None and previous != (source_identity, digest):
            _fail("canonical_collision")
        if source_identity is not None:
            previous_url = seen_identities.get(source_identity)
            if previous_url is not None and previous_url != canonical:
                _fail("canonical_collision")
            seen_identities[source_identity] = canonical
        seen_urls[canonical] = (source_identity, digest)
        order = (
            canonical.encode("utf-8"),
            0 if source_identity is None else 1,
            b"" if source_identity is None else source_identity.encode("utf-8"),
        )
        if previous_order is not None and order < previous_order:
            _fail("invalid_projection", rejected=True)
        previous_order = order


def _project_scrape(input_value: dict[str, Any]) -> dict[str, Any]:
    request = _require_shape(
        input_value.get("request"),
        frozenset({"source_url"}),
        frozenset({"origin_request_id", "request_id"}),
    )
    result = _require_shape(input_value.get("result"), frozenset({"content"}))
    source_url = canonical_url(request.get("source_url"))
    content = canonical_job(result.get("content"))
    digest = content_sha256(source_url, content)
    effects = _base_effects(request, "EXECUTION_KIND_SCRAPE", source_url)
    _add_target(effects, source_url, digest)
    return effects


def _checked_add(left: int, right: int) -> int:
    if left > UINT64_MAX - right:
        _fail("counter_overflow")
    return left + right


def _monitor_results(input_value: dict[str, Any]) -> list[dict[str, Any]]:
    if ("result" in input_value) == ("batches" in input_value):
        _fail("invalid_projection", rejected=True)
    if "result" in input_value:
        return [_monitor_result_shape(input_value["result"])]
    batches = input_value["batches"]
    if not isinstance(batches, list) or not batches:
        _fail("invalid_projection", rejected=True)
    results: list[dict[str, Any]] = []
    checked_total = 0
    for batch in batches:
        shaped = _require_shape(
            batch,
            frozenset({"checked_count", "complete", "result"}),
        )
        checked_total = _checked_add(checked_total, _uint64(shaped["checked_count"]))
        if not isinstance(shaped["complete"], bool):
            _fail("invalid_projection", rejected=True)
        if shaped["complete"] is not True:
            _fail("ineligible_history")
        results.append(_monitor_result_shape(shaped["result"]))
    return results


def _monitor_result_shape(value: object) -> dict[str, Any]:
    return _require_shape(
        value,
        frozenset(
            {
                "filtered_count",
                "hybrid",
                "jobs",
                "security_filtered_count",
                "truncated",
                "urls",
            }
        ),
        frozenset({"metadata_updates", "new_sitemap_url"}),
    )


def _project_monitor(input_value: dict[str, Any]) -> dict[str, Any]:
    request = _require_shape(
        input_value.get("request"),
        frozenset({"target_url"}),
        frozenset({"origin_request_id", "request_id"}),
    )
    target_url = canonical_url(request.get("target_url"))
    results = _monitor_results(input_value)
    tuples: dict[str, tuple[str, dict[str, Any] | None, str | None]] = {}
    identity_urls: dict[str, str] = {}
    filtered_count = 0
    security_filtered_count = 0
    hybrid = False
    truncated = False
    sitemaps: list[str] = []
    metadata_sequence: list[dict[str, Any] | None] = []
    for result in results:
        urls = result["urls"]
        jobs = result["jobs"]
        if not isinstance(urls, list) or not isinstance(jobs, list):
            _fail("invalid_projection", rejected=True)
        for source in urls:
            canonical = canonical_url(source)
            previous = tuples.get(canonical)
            if previous is not None and previous[0] != source:
                _fail("canonical_collision")
            if previous is not None and previous[2] is not None:
                _fail("canonical_collision")
            tuples.setdefault(canonical, (source, None, None))
        for discovered in jobs:
            if (
                not isinstance(discovered, dict)
                or not {"content", "url"} <= set(discovered)
                or not set(discovered) <= {"content", "source_identity", "url"}
            ):
                _fail("invalid_projection", rejected=True)
            source = discovered["url"]
            canonical = canonical_url(source)
            content = canonical_job(discovered["content"])
            source_identity = _source_identity(
                discovered.get("source_identity"),
                present="source_identity" in discovered,
            )
            if source_identity is not None:
                identity_url = identity_urls.get(source_identity)
                if identity_url is not None and identity_url != canonical:
                    _fail("canonical_collision")
            previous = tuples.get(canonical)
            if previous is not None:
                previous_source, previous_content, previous_identity = previous
                if previous_source != source:
                    _fail("canonical_collision")
                if previous_identity != source_identity:
                    _fail("canonical_collision")
                if previous_content is not None and canonical_json(
                    previous_content
                ) != canonical_json(content):
                    _fail("canonical_collision")
            tuples[canonical] = (source, content, source_identity)
            if source_identity is not None:
                identity_urls[source_identity] = canonical
        filtered_count = _checked_add(filtered_count, _uint64(result["filtered_count"]))
        security_filtered_count = _checked_add(
            security_filtered_count, _uint64(result["security_filtered_count"])
        )
        if not isinstance(result["hybrid"], bool) or not isinstance(result["truncated"], bool):
            _fail("invalid_projection", rejected=True)
        hybrid = hybrid or result["hybrid"]
        truncated = truncated or result["truncated"]
        if "new_sitemap_url" in result:
            sitemaps.append(canonical_url(result["new_sitemap_url"]))
        if "metadata_updates" in result:
            metadata = result["metadata_updates"]
            if not isinstance(metadata, dict):
                _fail("invalid_projection", rejected=True)
            canonical_json(metadata)
        else:
            metadata = None
        metadata_sequence.append(metadata)
    if len(set(sitemaps)) > 1:
        _fail("canonical_collision")
    effects = _base_effects(request, "EXECUTION_KIND_MONITOR", target_url)
    effects.update(
        {
            "filtered_count": filtered_count,
            "gone_detection_allowed": not hybrid
            and not truncated
            and filtered_count == 0
            and security_filtered_count == 0,
            "hybrid": hybrid,
            "security_filtered_count": security_filtered_count,
            "truncated": truncated,
        }
    )
    if sitemaps:
        effects["new_sitemap_url"] = sitemaps[0]
    if any(metadata is not None for metadata in metadata_sequence):
        metadata_value = (
            metadata_sequence[0] if len(metadata_sequence) == 1 else {"batches": metadata_sequence}
        )
        assert isinstance(metadata_value, dict)
        effects["metadata_updates_sha256"] = metadata_sha256(target_url, metadata_value)
    ordered_tuples = sorted(
        tuples.items(),
        key=lambda item: (
            item[0].encode("utf-8"),
            0 if item[1][2] is None else 1,
            b"" if item[1][2] is None else item[1][2].encode("utf-8"),
        ),
    )
    for canonical, (_, content, source_identity) in ordered_tuples:
        _add_target(
            effects,
            canonical,
            content_sha256(canonical, content) if content is not None else None,
            source_identity,
        )
    return effects


def project_case(case_value: object) -> dict[str, Any]:
    case_id = "invalid-case"
    try:
        if not isinstance(case_value, dict):
            _fail("invalid_projection", rejected=True)
        if set(case_value) not in (
            {"id", "input", "subject_kind"},
            {"expected", "id", "input", "subject_kind"},
        ):
            _fail("invalid_projection", rejected=True)
        raw_id = case_value.get("id")
        if isinstance(raw_id, str) and raw_id:
            case_id = raw_id
        else:
            _fail("invalid_projection", rejected=True)
        _validate_json(case_value)
        subject_kind = case_value.get("subject_kind")
        input_value = case_value.get("input")
        if not isinstance(input_value, dict):
            _fail("invalid_projection", rejected=True)
        if not set(input_value) <= {
            "batches",
            "comparison_content",
            "comparison_content_sha256",
            "comparison_metadata_updates",
            "existing_projected_effects",
            "preconditions",
            "request",
            "result",
        }:
            _fail("invalid_projection", rejected=True)
        precondition = _require_shape(
            input_value.get("preconditions"),
            frozenset(
                {
                    "batches_complete",
                    "eligible_for_commit",
                    "privacy_status",
                    "protocol_accepted",
                    "terminal_status",
                }
            ),
        )
        privacy_status = precondition["privacy_status"]
        if (
            not isinstance(precondition["protocol_accepted"], bool)
            or not isinstance(precondition["terminal_status"], str)
            or not isinstance(precondition["eligible_for_commit"], bool)
            or not isinstance(precondition["batches_complete"], bool)
            or not isinstance(privacy_status, str)
        ):
            _fail("invalid_projection", rejected=True)
        if privacy_status == "rejected":
            _fail("privacy_rejected")
        if privacy_status not in {"unchanged", "transformed"}:
            _fail("invalid_projection", rejected=True)
        if (
            precondition.get("protocol_accepted") is not True
            or precondition.get("terminal_status") != "success"
            or precondition.get("eligible_for_commit") is not True
            or precondition.get("batches_complete") is not True
        ):
            _fail("ineligible_history")
        if "existing_projected_effects" in input_value:
            _validate_existing_effects(input_value["existing_projected_effects"])
        if subject_kind == "scrape":
            effects = _project_scrape(input_value)
        elif subject_kind == "monitor":
            effects = _project_monitor(input_value)
        elif subject_kind == "browser":
            _require_shape(input_value.get("result"), frozenset({"browser_result"}))
            _fail("unsupported_result")
        else:
            _fail("unsupported_result", rejected=True)
        result: dict[str, Any] = {
            "case_id": case_id,
            "projected_effects": effects,
            "status": "projected",
        }
        result["semantic_sha256"] = semantic_sha256(result)
        return result
    except SemanticFailure as failure:
        return {"case_id": case_id, "reason": failure.reason, "status": failure.status}


def _preconditions(*, eligible: bool = True) -> dict[str, Any]:
    return {
        "batches_complete": True,
        "eligible_for_commit": eligible,
        "privacy_status": "unchanged",
        "protocol_accepted": True,
        "terminal_status": "success",
    }


def _job(
    title: str,
    description_html: str,
    *,
    locale: str | None = None,
    language: str | None = None,
    locations: list[str] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "description_html": description_html,
        "extensions": [],
        "localizations": [],
        "skills": ["python", "sql"],
        "title": title,
    }
    if locale is not None:
        value["localizations"] = [
            {
                "description_html": "<p>Localized synthetic role.</p>",
                "locale": locale,
            }
        ]
    if language is not None:
        value["language"] = language
    if locations is not None:
        value["locations"] = {"values": locations}
    return value


def _case(case_id: str, subject_kind: str, input_value: dict[str, Any]) -> dict[str, Any]:
    value = {"id": case_id, "input": input_value, "subject_kind": subject_kind}
    return {**value, "expected": project_case(value)}


def _monitor_result(
    *,
    urls: list[str] | None = None,
    jobs: list[dict[str, Any]] | None = None,
    filtered_count: int = 0,
    security_filtered_count: int = 0,
    hybrid: bool = False,
    truncated: bool = False,
    metadata_updates: dict[str, Any] | None = None,
    new_sitemap_url: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "filtered_count": filtered_count,
        "hybrid": hybrid,
        "jobs": jobs or [],
        "security_filtered_count": security_filtered_count,
        "truncated": truncated,
        "urls": urls or [],
    }
    if metadata_updates is not None:
        value["metadata_updates"] = metadata_updates
    if new_sitemap_url is not None:
        value["new_sitemap_url"] = new_sitemap_url
    return value


def make_cases() -> list[dict[str, Any]]:
    scrape_job = _job(
        "Synthetic Platform Engineer",
        "<p>Build synthetic systems.</p>",
        locations=["Zürich"],
    )
    monitor_url = "https://jobs.example.invalid/board"
    rich_url = "https://jobs.example.invalid/openings/rich"
    rich_job = _job(
        "Synthetic Rich Result",
        "<article>Visible synthetic details.</article>",
        locations=["Zürich", "Basel", "Zürich"],
    )
    rich_job["skills"] = ["sql", "python", "sql"]
    locale_url = "https://jobs.example.invalid/openings/locale"
    metadata = {
        "cursor": "synthetic-cursor",
        "extensions": [
            {
                "encoding": "EXTENSION_ENCODING_CANONICAL_JSON",
                "payload": "eyJvcmRlciI6MX0=",
                "payload_sha256": (
                    "3d2524ed9fbec1d4c1611e689ea8150643096bb4a52472654a37908ee4be5cf6"
                ),
                "schema_id": "jobseek.synthetic.metadata",
                "schema_version": 1,
            },
            {
                "encoding": "EXTENSION_ENCODING_CANONICAL_JSON",
                "payload": "eyJvcmRlciI6Mn0=",
                "payload_sha256": (
                    "87034c610b65301f0be90073e93ed894241a8cf5340af95f0a4c6f7628a1d630"
                ),
                "schema_id": "jobseek.synthetic.metadata",
                "schema_version": 1,
            },
        ],
    }
    absent_job = _job("Synthetic Optional", "<p>Visible.</p>")
    empty_job = {**absent_job, "locations": {"values": []}}
    baseline_job = _job("Synthetic Digest A", "<p>Visible.</p>")
    changed_job = _job("Synthetic Digest B", "<p>Visible.</p>")
    digest_url = "https://jobs.example.invalid/openings/digest"

    cases = [
        _case(
            "safe_scrape_projected",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-scrape",
                    "request_id": "req-seed-scrape",
                    "source_url": "HTTPS://jobs.example.invalid:443/openings/./platform?b=2&a=1#synthetic",
                },
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_visible_content",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/hidden"},
                "result": {
                    "content": _job(
                        "A title is insufficient",
                        "<section hidden>Only hidden text</section>",
                    )
                },
            },
        ),
        _case(
            "safe_monitor_url_only",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-monitor-urls",
                    "request_id": "req-seed-monitor-urls",
                    "target_url": monitor_url,
                },
                "result": {
                    "filtered_count": 0,
                    "hybrid": False,
                    "jobs": [],
                    "security_filtered_count": 0,
                    "truncated": False,
                    "urls": [
                        "https://jobs.example.invalid/openings/b",
                        "https://jobs.example.invalid/openings/a",
                    ],
                },
            },
        ),
        _case(
            "rich_monitor",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-monitor-rich",
                    "request_id": "req-seed-monitor-rich",
                    "target_url": monitor_url,
                },
                "result": {
                    "filtered_count": 0,
                    "hybrid": False,
                    "jobs": [
                        {"content": rich_job, "url": rich_url},
                        {"content": copy.deepcopy(rich_job), "url": rich_url},
                    ],
                    "security_filtered_count": 0,
                    "truncated": False,
                    "urls": [rich_url],
                },
            },
        ),
        _case(
            "suppressed_precondition",
            "monitor",
            {
                "preconditions": _preconditions(eligible=False),
                "request": {"target_url": monitor_url},
                "result": {"jobs": [], "urls": []},
            },
        ),
        _case(
            "invalid_url",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://synthetic-user@jobs.example.invalid/opening"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "locale_alias",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-locale",
                    "request_id": "req-seed-locale",
                    "source_url": locale_url,
                },
                "result": {
                    "content": _job(
                        "Synthetic Locale Alias",
                        "<p>Base synthetic details.</p>",
                        locale="EN_us",
                    )
                },
            },
        ),
        _case(
            "locale_rejection",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": locale_url},
                "result": {
                    "content": _job(
                        "Synthetic Unsupported Locale",
                        "<p>Visible synthetic details.</p>",
                        locale="es-ES",
                    )
                },
            },
        ),
        _case(
            "canonical_collision",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
                "result": {
                    "filtered_count": 0,
                    "hybrid": False,
                    "jobs": [],
                    "security_filtered_count": 0,
                    "truncated": False,
                    "urls": [
                        "https://jobs.example.invalid/openings/%72ole",
                        "https://jobs.example.invalid/openings/role",
                    ],
                },
            },
        ),
        _case(
            "ordered_metadata",
            "monitor",
            {
                "comparison_metadata_updates": {
                    **metadata,
                    "extensions": list(reversed(metadata["extensions"])),
                },
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-metadata",
                    "request_id": "req-seed-metadata",
                    "target_url": monitor_url,
                },
                "result": {
                    "filtered_count": 0,
                    "hybrid": False,
                    "jobs": [],
                    "metadata_updates": metadata,
                    "security_filtered_count": 0,
                    "truncated": False,
                    "urls": [],
                },
            },
        ),
        _case(
            "absent_vs_empty",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-optional",
                    "request_id": "req-optional",
                    "target_url": monitor_url,
                },
                "result": {
                    "filtered_count": 0,
                    "hybrid": False,
                    "jobs": [
                        {
                            "content": absent_job,
                            "url": "https://jobs.example.invalid/openings/optional-absent",
                        },
                        {
                            "content": empty_job,
                            "url": "https://jobs.example.invalid/openings/optional-empty",
                        },
                    ],
                    "security_filtered_count": 0,
                    "truncated": False,
                    "urls": [],
                },
            },
        ),
        _case(
            "digest_sensitivity",
            "scrape",
            {
                "comparison_content": baseline_job,
                "comparison_content_sha256": content_sha256(digest_url, baseline_job),
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-seed-digest",
                    "request_id": "req-seed-digest",
                    "source_url": digest_url,
                },
                "result": {"content": changed_job},
            },
        ),
        _case(
            "localized_only_visible",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-localized-only",
                    "request_id": "req-localized-only",
                    "source_url": "https://jobs.example.invalid/openings/localized-only",
                },
                "result": {
                    "content": {
                        "extensions": [],
                        "localizations": [
                            {
                                "description_html": "<p>Localized visible role.</p>",
                                "locale": "fr-CH",
                            }
                        ],
                        "skills": [],
                        "title": "Synthetic Localized Only",
                    }
                },
            },
        ),
        _case(
            "invalid_html_unclosed_comment",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/html-comment"},
                "result": {
                    "content": _job(
                        "Synthetic Comment",
                        "<p>Visible before comment.</p><!-- unclosed",
                    )
                },
            },
        ),
        _case(
            "invalid_html_unclosed_quote",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/html-quote"},
                "result": {
                    "content": _job(
                        "Synthetic Quote",
                        "<p hidden='unterminated>Visible-looking text",
                    )
                },
            },
        ),
        _case(
            "invalid_html_unclosed_suppressed",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/html-script"},
                "result": {
                    "content": _job(
                        "Synthetic Script",
                        "<script>Visible-looking text",
                    )
                },
            },
        ),
        _case(
            "invalid_html_nesting_limit",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/html-depth"},
                "result": {
                    "content": _job(
                        "Synthetic Deep HTML",
                        "<div>" * (MAX_HTML_NESTING + 1)
                        + "Visible"
                        + "</div>" * (MAX_HTML_NESTING + 1),
                    )
                },
            },
        ),
        _case(
            "invalid_url_fragment_escape",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/role#bad%ZZ"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_url_port_zero",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid:0/role"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_url_legacy_ip",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "http://0177.0.0.1/role"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_url_above_root",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/../../role"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "safe_url_query_distinctions",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-query",
                    "request_id": "req-query",
                    "source_url": "HTTP://jobs.example.invalid:80?a=&a&b=2&b=1&b=1",
                },
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "safe_url_default_repeated_slash",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-slashes",
                    "request_id": "req-slashes",
                    "source_url": "https://JOBS.EXAMPLE.INVALID:443//openings///role",
                },
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_url_non_ascii_host",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jöbs.example.invalid/role"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "browser_suppressed",
            "browser",
            {
                "preconditions": _preconditions(),
                "result": {"browser_result": {}},
            },
        ),
        _case(
            "unknown_subject_rejected",
            "synthetic-unknown",
            {"preconditions": _preconditions()},
        ),
        _case(
            "language_alias",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-language",
                    "request_id": "req-language",
                    "source_url": "https://jobs.example.invalid/openings/language",
                },
                "result": {
                    "content": _job(
                        "Synthetic Language Alias",
                        "<p>Visible language role.</p>",
                        language="DE_ch",
                    )
                },
            },
        ),
        _case(
            "language_rejection",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/language-bad"},
                "result": {
                    "content": _job(
                        "Synthetic Bad Language",
                        "<p>Visible language role.</p>",
                        language="pt-BR",
                    )
                },
            },
        ),
        _case(
            "locale_collision",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": locale_url},
                "result": {
                    "content": {
                        "description_html": "<p>Visible base.</p>",
                        "extensions": [],
                        "localizations": [
                            {"description_html": "<p>One.</p>", "locale": "EN_us"},
                            {"description_html": "<p>One.</p>", "locale": "en-US"},
                        ],
                        "skills": [],
                        "title": "Synthetic Locale Collision",
                    }
                },
            },
        ),
        _case(
            "invalid_shape_unknown",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": digest_url},
                "result": {"content": changed_job},
                "unknown_field": "synthetic",
            },
        ),
        _case(
            "invalid_shape_missing",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": digest_url},
                "result": {},
            },
        ),
        _case(
            "invalid_projection_alignment",
            "scrape",
            {
                "existing_projected_effects": {
                    "content_hashes": [],
                    "job_effects": [],
                    "targets": [],
                    "urls_to_upsert": [digest_url],
                },
                "preconditions": _preconditions(),
            },
        ),
        _case(
            "monitor_batches_ordered",
            "monitor",
            {
                "batches": [
                    {
                        "checked_count": 2,
                        "complete": True,
                        "result": _monitor_result(
                            urls=["https://jobs.example.invalid/openings/batch-b"],
                            filtered_count=1,
                            metadata_updates={
                                "cursor": "first",
                                "extensions": [metadata["extensions"][0]],
                            },
                            new_sitemap_url="https://jobs.example.invalid/sitemap.xml",
                        ),
                    },
                    {
                        "checked_count": 3,
                        "complete": True,
                        "result": _monitor_result(
                            urls=["https://jobs.example.invalid/openings/batch-a"],
                            filtered_count=2,
                            metadata_updates={
                                "cursor": "second",
                                "extensions": [metadata["extensions"][1]],
                            },
                            new_sitemap_url="https://jobs.example.invalid/sitemap.xml",
                        ),
                    },
                ],
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-batches",
                    "request_id": "req-batches",
                    "target_url": monitor_url,
                },
            },
        ),
        _case(
            "monitor_batches_sitemap_conflict",
            "monitor",
            {
                "batches": [
                    {
                        "checked_count": 1,
                        "complete": True,
                        "result": _monitor_result(
                            new_sitemap_url="https://jobs.example.invalid/one.xml"
                        ),
                    },
                    {
                        "checked_count": 1,
                        "complete": True,
                        "result": _monitor_result(
                            new_sitemap_url="https://jobs.example.invalid/two.xml"
                        ),
                    },
                ],
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
            },
        ),
        _case(
            "monitor_batches_counter_overflow",
            "monitor",
            {
                "batches": [
                    {
                        "checked_count": UINT64_MAX,
                        "complete": True,
                        "result": _monitor_result(),
                    },
                    {
                        "checked_count": 1,
                        "complete": True,
                        "result": _monitor_result(),
                    },
                ],
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
            },
        ),
        _case(
            "monitor_incomplete",
            "monitor",
            {
                "batches": [
                    {
                        "checked_count": 1,
                        "complete": False,
                        "result": _monitor_result(urls=["https://jobs.example.invalid/incomplete"]),
                    }
                ],
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
            },
        ),
        _case(
            "privacy_rejected",
            "scrape",
            {
                "preconditions": {
                    **_preconditions(),
                    "privacy_status": "rejected",
                }
            },
        ),
        _case(
            "rich_url_union_lockstep",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-union",
                    "request_id": "req-union",
                    "target_url": monitor_url,
                },
                "result": _monitor_result(
                    urls=[
                        rich_url,
                        "https://jobs.example.invalid/openings/url-only",
                    ],
                    jobs=[{"content": rich_job, "url": rich_url}],
                ),
            },
        ),
        _case(
            "divergent_rich_collision",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
                "result": _monitor_result(
                    jobs=[
                        {"content": rich_job, "url": rich_url},
                        {
                            "content": {**rich_job, "title": "Divergent synthetic title"},
                            "url": rich_url,
                        },
                    ]
                ),
            },
        ),
        _case(
            "set_permutation_dedupe",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-sets",
                    "request_id": "req-sets",
                    "target_url": monitor_url,
                },
                "result": _monitor_result(
                    jobs=[
                        {
                            "content": {
                                **rich_job,
                                "locations": {"values": ["Basel", "Zürich", "Basel"]},
                                "skills": ["python", "sql", "python"],
                            },
                            "url": "https://jobs.example.invalid/openings/sets",
                        }
                    ]
                ),
            },
        ),
        _case(
            "safe_url_leading_zero_default_port",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-leading-port",
                    "request_id": "req-leading-port",
                    "source_url": "HTTPS://jobs.example.invalid:0443/openings/leading-port",
                },
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_projection_malformed_url",
            "scrape",
            {
                "existing_projected_effects": {
                    "content_hashes": [""],
                    "job_effects": [
                        {
                            "content_sha256": "",
                            "source_url": "https://jobs.example.invalid/%ZZ",
                        }
                    ],
                    "targets": [
                        {
                            "action": "PROJECTED_ACTION_UPSERT",
                            "url": "https://jobs.example.invalid/%ZZ",
                        }
                    ],
                    "urls_to_upsert": ["https://jobs.example.invalid/%ZZ"],
                },
                "preconditions": _preconditions(),
            },
        ),
        _case(
            "invalid_localized_description",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "source_url": "https://jobs.example.invalid/openings/localized-invalid"
                },
                "result": {
                    "content": {
                        "description_html": "<p>Visible base description.</p>",
                        "extensions": [],
                        "localizations": [
                            {
                                "description_html": "<script>unclosed",
                                "locale": "en",
                            }
                        ],
                        "skills": [],
                        "title": "Synthetic Invalid Localization",
                    }
                },
            },
        ),
        _case(
            "visible_unterminated_space_entities",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-unterminated-entities",
                    "request_id": "req-unterminated-entities",
                    "source_url": "https://jobs.example.invalid/openings/unterminated-entities",
                },
                "result": {
                    "content": _job(
                        "Synthetic Unterminated Entities",
                        "&nbsp &#160 &#xA0",
                    )
                },
            },
        ),
        _case(
            "invalid_unicode_surrogate",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/openings/surrogate"},
                "result": {"content": _job("Synthetic Invalid Unicode", "\ud800")},
            },
        ),
        _case(
            "invalid_metadata_null",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
                "result": {**_monitor_result(), "metadata_updates": None},
            },
        ),
        _case(
            "invalid_localized_description_null",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "https://jobs.example.invalid/openings/null-localized"},
                "result": {
                    "content": {
                        "description_html": "<p>Visible base description.</p>",
                        "extensions": [],
                        "localizations": [{"description_html": None, "locale": "en"}],
                        "skills": [],
                        "title": "Synthetic Null Localization",
                    }
                },
            },
        ),
        _case(
            "invalid_url_legacy_mixed_components",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {"source_url": "http://0x7f.0.0.1/openings/mixed-ip"},
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "safe_url_numeric_overrange_dns",
            "scrape",
            {
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-numeric-dns",
                    "request_id": "req-numeric-dns",
                    "source_url": "https://4294967296/openings/numeric-dns",
                },
                "result": {"content": scrape_job},
            },
        ),
        _case(
            "invalid_precondition_type",
            "scrape",
            {
                "preconditions": {
                    **_preconditions(),
                    "privacy_status": "rejected",
                    "protocol_accepted": "true",
                },
            },
        ),
        _case(
            "source_identity_explicit_null_legacy_equivalence",
            "monitor",
            {
                "existing_projected_effects": {
                    "content_hashes": [""],
                    "job_effects": [
                        {
                            "content_sha256": "",
                            "source_identity": None,
                            "source_url": "https://jobs.example.invalid/existing-legacy",
                        }
                    ],
                    "targets": [
                        {
                            "action": "PROJECTED_ACTION_UPSERT",
                            "url": "https://jobs.example.invalid/existing-legacy",
                        }
                    ],
                    "urls_to_upsert": ["https://jobs.example.invalid/existing-legacy"],
                },
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-source-identity",
                    "request_id": "req-source-identity",
                    "target_url": monitor_url,
                },
                "result": _monitor_result(
                    jobs=[
                        {
                            "content": rich_job,
                            "source_identity": None,
                            "url": "https://jobs.example.invalid/openings/source-identity",
                        }
                    ]
                ),
            },
        ),
        _case(
            "source_identity_valid_present_propagation_and_semantic_sensitivity",
            "monitor",
            {
                "existing_projected_effects": {
                    "content_hashes": [
                        content_sha256(
                            "https://jobs.example.invalid/openings/source-identity",
                            canonical_job(copy.deepcopy(rich_job)),
                        )
                    ],
                    "job_effects": [
                        {
                            "content_sha256": content_sha256(
                                "https://jobs.example.invalid/openings/source-identity",
                                canonical_job(copy.deepcopy(rich_job)),
                            ),
                            "source_identity": "smartrecruiters:example:42",
                            "source_url": "https://jobs.example.invalid/openings/source-identity",
                        }
                    ],
                    "targets": [
                        {
                            "action": "PROJECTED_ACTION_UPSERT",
                            "content_sha256": content_sha256(
                                "https://jobs.example.invalid/openings/source-identity",
                                canonical_job(copy.deepcopy(rich_job)),
                            ),
                            "url": "https://jobs.example.invalid/openings/source-identity",
                        }
                    ],
                    "urls_to_upsert": ["https://jobs.example.invalid/openings/source-identity"],
                },
                "preconditions": _preconditions(),
                "request": {
                    "origin_request_id": "origin-source-identity",
                    "request_id": "req-source-identity",
                    "target_url": monitor_url,
                },
                "result": _monitor_result(
                    jobs=[
                        {
                            "content": rich_job,
                            "source_identity": "smartrecruiters:example:42",
                            "url": "https://jobs.example.invalid/openings/source-identity",
                        }
                    ]
                ),
            },
        ),
        _case(
            "source_identity_invalid_present_rejected",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
                "result": _monitor_result(
                    jobs=[
                        {
                            "content": rich_job,
                            "source_identity": "not-namespaced",
                            "url": "https://jobs.example.invalid/openings/invalid-identity",
                        }
                    ]
                ),
            },
        ),
        _case(
            "source_identity_collision_rejected",
            "monitor",
            {
                "preconditions": _preconditions(),
                "request": {"target_url": monitor_url},
                "result": _monitor_result(
                    jobs=[
                        {
                            "content": rich_job,
                            "source_identity": "smartrecruiters:example:42",
                            "url": "https://jobs.example.invalid/openings/identity-a",
                        },
                        {
                            "content": copy.deepcopy(rich_job),
                            "source_identity": "smartrecruiters:example:42",
                            "url": "https://jobs.example.invalid/openings/identity-b",
                        },
                    ]
                ),
            },
        ),
    ]
    actual_ids = tuple(item["id"] for item in cases)
    if actual_ids != CASE_IDS:
        raise AssertionError(f"seed case IDs changed: {actual_ids!r}")
    return cases


def manifest_bytes() -> bytes:
    manifest = {
        "cases": make_cases(),
        "format": "jobseek.runtime.semantics-corpus/v1",
        "required_case_ids": list(CASE_IDS),
    }
    return (
        json.dumps(
            manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def validate_manifest(manifest: object) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or set(manifest) != {
        "cases",
        "format",
        "required_case_ids",
    }:
        _fail("invalid_projection", rejected=True)
    if manifest["format"] != "jobseek.runtime.semantics-corpus/v1":
        _fail("invalid_projection", rejected=True)
    cases = manifest["cases"]
    if not isinstance(cases, list) or manifest["required_case_ids"] != list(CASE_IDS):
        _fail("invalid_projection", rejected=True)
    if tuple(item.get("id") for item in cases if isinstance(item, dict)) != CASE_IDS:
        _fail("invalid_projection", rejected=True)
    for item in cases:
        if not isinstance(item, dict) or set(item) != {"expected", "id", "input", "subject_kind"}:
            _fail("invalid_projection", rejected=True)
        if project_case(item) != item["expected"]:
            _fail("invalid_projection", rejected=True)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = manifest_bytes()
    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_bytes(expected)
        print(f"wrote {MANIFEST_PATH.relative_to(ROOT)} ({len(CASE_IDS)} cases)")
        return 0

    if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_bytes() != expected:
        parser.error("generated semantics manifest differs; run with --write")
    validate_manifest(json.loads(MANIFEST_PATH.read_bytes()))
    print(f"checked {MANIFEST_PATH.relative_to(ROOT)} ({len(CASE_IDS)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
