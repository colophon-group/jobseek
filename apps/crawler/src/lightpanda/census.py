"""Build the deterministic, sanitized browser-capability census.

The census reads ``boards.csv`` and the in-process monitor/scraper registries.
It never allocates a browser or performs network I/O. Arbitrary configuration
values (URLs, selectors, scripts, headers, bodies, and credentials) are not
written to the artifact; only validated structural features and digests are.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.core.monitors import all_monitor_types, monitor_needs_browser
from src.core.monitors.dom import (
    _validated_inactive_detail_states,
    _validated_rich_rows,
)
from src.core.scrapers import (
    _RENDER_AWARE_SCRAPERS,
    all_scraper_types,
    get_scraper_type,
    scraper_needs_browser,
)
from src.shared.browser import VALID_WAIT_STRATEGIES, _resolve_resource_blocking

FORMAT = "jobseek.lightpanda.capability-census/v1"
CRAWLER_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOARDS_PATH = CRAWLER_ROOT / "data" / "boards.csv"
DEFAULT_MANIFEST_PATH = CRAWLER_ROOT / "tests" / "lightpanda" / "fixtures" / "census.json"

_RECORD_PROVENANCE_FIELDS = frozenset({"digest_sha256", "source_count", "source_refs_sha256"})
_SUMMARY_FIELDS = frozenset(
    {
        "browser_board_count",
        "browser_required_step_count",
        "configured_profile_occurrence_count",
        "configured_record_count",
        "registry_record_count",
        "total_record_count",
        "zero_browser_config_registry_count",
    }
)

_CSV_COLUMNS = (
    "company_slug",
    "board_slug",
    "board_url",
    "monitor_type",
    "monitor_config",
    "scraper_type",
    "scraper_config",
)
_MAX_FALLBACK_DEPTH = 16
_MAX_ACTIONS = 100
_MAX_SELECTOR_LENGTH = 4096
_MAX_SCRIPT_LENGTH = 100_000

# This is an intentional current-main freeze. A new config key on a
# browser-capable type must be classified here before the deterministic check
# accepts it; silently treating a new behavior as compatible is forbidden.
_MONITOR_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "accenture": frozenset({"country", "endpoint", "language", "site"}),
    "api_sniffer": frozenset(
        {
            "api_url",
            "api_url_match",
            "browser",
            "channel",
            "defaults",
            "delist_threshold",
            "disable_http2",
            "empty_response",
            "enrich",
            "fields",
            "headers",
            "headless",
            "item_filter",
            "items",
            "json_path",
            "json_path_values",
            "max_items",
            "max_pages",
            "method",
            "pagination",
            "pagination_convergence",
            "params",
            "persistent_context",
            "post_data",
            "post_data_refresh",
            "proxy",
            "render",
            "request_headers",
            "require_pdf_pattern",
            "require_unexpired_pdf",
            "rescrape_policy",
            "response_decrypt",
            "route_params",
            "score",
            "settle",
            "skip_ssl",
            "slug_fields",
            "stealth",
            "timeout",
            "total",
            "total_path",
            "url_allowlist",
            "url_field",
            "url_field_match",
            "url_filter",
            "url_regex",
            "url_template",
            "url_template_fields",
            "url_transform",
            "wait",
            "warmup_url",
        }
    ),
    "brassring": frozenset({"partner_id", "site_id"}),
    "bytedance": frozenset(),
    "candidatus": frozenset(),
    "darwinbox": frozenset(),
    "dayforce": frozenset({"offset_overlap", "portal", "tenant"}),
    "dom": frozenset(
        {
            "actions",
            "advertised_total",
            "block_hosts",
            "block_resource_types",
            "bot_protection",
            "channel",
            "defaults",
            "dualoo_portal",
            "empty_selector",
            "empty_states",
            "empty_text",
            "encoding",
            "exclude_detail_selector",
            "fetch_url_transform",
            "fingerprint_response",
            "headless",
            "include_board_url",
            "inactive_detail_states",
            "job_filter",
            "job_link_pattern",
            "link_selector",
            "lucca_board",
            "oracle_adf_job_ids",
            "pagination",
            "persistent_context",
            "prospective_board",
            "prospective_canonical_path",
            "proxy",
            "render",
            "request_headers",
            "require_jsonld_jobposting",
            "require_pdf_text",
            "require_unexpired_pdf",
            "resource_policy",
            "rescrape_policy",
            "retry_statuses",
            "rich_rows",
            "script_json_links",
            "skip_ssl",
            "stealth",
            "timeout",
            "url",
            "url_allowlist",
            "url_filter",
            "url_transform",
            "vagas_tenant",
            "wait",
            "wait_fallback",
            "yousty_organization",
        }
    ),
    "inline": frozenset(
        {
            "actions",
            "defaults",
            "defaults_by_title",
            "description_from_title",
            "detail_click_selector",
            "detail_content_selector",
            "detail_identity_attribute",
            "detail_identity_regex",
            "detail_identity_selector",
            "empty_requires_no_jobs",
            "empty_selector",
            "empty_text",
            "exclude_description_regex",
            "exclude_expired",
            "exclude_title_regex",
            "exclude_titles",
            "fetch_contains",
            "fetch_json_path",
            "fetch_urls",
            "include_hidden",
            "item_boundary",
            "item_boundary_tag",
            "nonempty_selector",
            "positions_per_listing",
            "preserve_single_location",
            "proxy",
            "render",
            "require_zero_proof",
            "section_end",
            "section_start",
            "source_identity_attribute",
            "source_identity_regex",
            "source_identity_selector",
            "stealth",
            "steps",
            "synthetic_identity_field",
            "timeout",
            "valid_through_format",
            "valid_through_patterns",
            "valid_through_regex",
            "wait",
        }
    ),
    "nextdata": frozenset(
        {
            "browser_expression",
            "enrich",
            "expected_hiring_organization",
            "expected_page_title",
            "fields",
            "include_item_values",
            "pagination",
            "path",
            "render",
            "require_item_values",
            "rescrape_policy",
            "slug_fields",
            "source",
            "source_identity",
            "stealth",
            "strict_path",
            "timeout",
            "total",
            "url_allowlist",
            "url_template",
            "url_transform",
            "wait",
        }
    ),
    "njoyn": frozenset(
        {
            "channel",
            "headless",
            "max_pages",
            "page_wait_ms",
            "persistent_context",
            "proxy",
            "stealth",
            "timeout",
            "wait",
        }
    ),
}

_SCRAPER_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "api_sniffer": frozenset(
        {
            "api_url",
            "auth_request",
            "browser",
            "channel",
            "enrich",
            "fields",
            "headless",
            "json_path",
            "method",
            "persistent_context",
            "post_body",
            "proxy",
            "request_headers",
            "settle",
            "timeout",
            "url_pattern",
            "wait",
            "warmup_url",
        }
    ),
    "dom": frozenset(
        {
            "_replace",
            "actions",
            "channel",
            "defaults",
            "defaults_by_url",
            "document_fallback",
            "encoding",
            "enrich",
            "fallback",
            "fetch_url_transform",
            "gone_url_pattern",
            "headless",
            "include_document_description",
            "include_document_title",
            "map",
            "persistent_context",
            "proxy",
            "render",
            "request_headers",
            "resource_policy",
            "retry_statuses",
            "same_origin_redirects",
            "scope",
            "skip_ssl",
            "stealth",
            "steps",
            "timeout",
            "user_agent",
            "wait",
            "wait_fallback",
            "warmup_url",
        }
    ),
    "embedded": frozenset(
        {"enrich", "fields", "path", "pattern", "script_id", "source", "variable"}
    ),
    "json-ld": frozenset(
        {
            "actions",
            "channel",
            "defaults",
            "defaults_by_url",
            "enrich",
            "fallback",
            "headless",
            "ignore_address_region",
            "ignore_date_posted",
            "ignore_locations",
            "ignore_valid_through",
            "persistent_context",
            "proxy",
            "render",
            "request_headers",
            "skip_ssl",
            "stealth",
            "timeout",
            "wait",
            "wait_fallback",
        }
    ),
    "nextdata": frozenset({"defaults", "enrich", "fields", "path", "render", "source", "wait"}),
}

_SELECTOR_KEYS = frozenset(
    {
        "detail_click_selector",
        "detail_content_selector",
        "detail_identity_selector",
        "empty_selector",
        "exclude_detail_selector",
        "forbidden_link_selector",
        "frame",
        "link_selector",
        "next_selector",
        "nonempty_selector",
        "page_size_selector",
        "partition_fallback_selector",
        "partition_selector",
        "required_link_selector",
        "row_required_selector",
        "row_selector",
        "scope",
        "selector",
        "source_identity_selector",
        "title_selector",
        "total_selector",
    }
)
_ACTION_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "click": (frozenset({"action", "selector"}), frozenset({"required", "timeout"})),
    "dismiss_overlays": (frozenset({"action"}), frozenset({"required", "timeout"})),
    "evaluate": (
        frozenset({"action", "script"}),
        frozenset({"required", "timeout"}),
    ),
    "paginate_collect": (
        frozenset({"action"}),
        frozenset(
            {
                "force",
                "max_pages",
                "next_selector",
                "page_size",
                "page_size_selector",
                "required",
                "stop_when_hidden",
                "timeout",
                "wait_ms",
            }
        ),
    ),
    "remove": (frozenset({"action", "selector"}), frozenset({"required", "timeout"})),
    "repeat": (
        frozenset({"action", "selector"}),
        frozenset({"force", "frame", "max", "required", "timeout", "wait_ms"}),
    ),
    "wait": (frozenset({"action"}), frozenset({"ms", "required", "timeout"})),
    "wait_for": (
        frozenset({"action", "selector"}),
        frozenset({"required", "state", "timeout"}),
    ),
}
_WAIT_FOR_STATES = frozenset({"attached", "detached", "hidden", "visible"})
_BOOL_BROWSER_KEYS = frozenset(
    {
        "browser",
        "disable_http2",
        "headless",
        "persistent_context",
        "proxy",
        "render",
        "skip_ssl",
        "stealth",
    }
)
_NUMBER_BROWSER_KEYS = frozenset({"settle", "timeout"})
_STRING_BROWSER_KEYS = frozenset(
    {
        "browser_expression",
        "channel",
        "locale",
        "resource_policy",
        "source",
        "user_agent",
        "warmup_url",
    }
)
_RESOURCE_POLICY_KEYS = frozenset(
    {"block_hosts", "block_resource_types", "bot_protection", "resource_policy"}
)
_FALLBACK_FIELDS = frozenset(
    {
        "base_salary",
        "date_posted",
        "description",
        "employment_type",
        "extras",
        "job_location_type",
        "language",
        "locations",
        "metadata",
        "title",
    }
)
_INHERENT_BROWSER_MONITORS = frozenset(
    {"accenture", "brassring", "bytedance", "candidatus", "darwinbox", "dayforce", "njoyn"}
)


class CensusError(ValueError):
    """A configuration cannot be classified safely."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "ascii"
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _load_config(raw: str, *, row_number: int, field: str) -> dict[str, Any]:
    if not raw:
        return {}

    def reject_constant(constant: str) -> object:
        raise CensusError(
            f"row {row_number} {field} contains non-standard JSON constant {constant}"
        )

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise CensusError(f"row {row_number} has invalid {field} JSON") from exc
    _validate_finite_json(value, path=f"row {row_number} {field}")
    if not isinstance(value, dict):
        raise CensusError(f"row {row_number} {field} must be an object")
    return value


def _validate_finite_json(value: object, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CensusError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_finite_json(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_finite_json(item, path=f"{path}[{index}]")


def _browser_capable_monitor_types() -> frozenset[str]:
    probes = (
        {},
        {"browser": True},
        {"browser_expression": "synthetic"},
        {"render": True},
        {"source": "browser"},
    )
    return frozenset(
        name
        for name in all_monitor_types()
        if any(monitor_needs_browser(name, cfg) for cfg in probes)
    )


def _browser_capable_scraper_types() -> frozenset[str]:
    return frozenset(
        name
        for name in all_scraper_types()
        if (entry := get_scraper_type(name)) is not None
        and (entry.needs_browser or name in _RENDER_AWARE_SCRAPERS)
    )


def _validate_selector(value: object, *, path: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_SELECTOR_LENGTH
        or "\x00" in value
        or any(ord(char) < 0x20 and char not in "\t\r\n" for char in value)
    ):
        raise CensusError(f"{path} must be a bounded non-empty selector string")


def _validate_selector_fields(value: object, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in _SELECTOR_KEYS:
                _validate_selector(item, path=child_path)
            elif "selector" in key or key in {"frame", "scope"}:
                raise CensusError(f"unknown selector field {child_path}")
            _validate_selector_fields(item, path=child_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_selector_fields(item, path=f"{path}[{index}]")


def _validate_number(value: object, *, path: str, minimum: float = 0) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < minimum
        or value > 1_000_000_000
    ):
        raise CensusError(f"{path} must be a bounded finite number")


def _validate_actions(value: object, *, path: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) > _MAX_ACTIONS:
        raise CensusError(f"{path} must be a bounded action array")
    abstract: list[dict[str, Any]] = []
    for index, action in enumerate(value):
        action_path = f"{path}[{index}]"
        if not isinstance(action, dict):
            raise CensusError(f"{action_path} must be an object")
        kind = action.get("action")
        if not isinstance(kind, str) or kind not in _ACTION_KEYS:
            raise CensusError(f"{action_path} has unknown action {kind!r}")
        required, optional = _ACTION_KEYS[kind]
        if not required <= set(action) or not set(action) <= required | optional:
            raise CensusError(f"{action_path} has unknown or missing keys")
        if "required" in action and not isinstance(action["required"], bool):
            raise CensusError(f"{action_path}.required must be boolean")
        if "force" in action and not isinstance(action["force"], bool):
            raise CensusError(f"{action_path}.force must be boolean")
        if "stop_when_hidden" in action and not isinstance(action["stop_when_hidden"], bool):
            raise CensusError(f"{action_path}.stop_when_hidden must be boolean")
        for key in ("max", "max_pages", "ms", "timeout", "wait_ms"):
            if key in action:
                _validate_number(action[key], path=f"{action_path}.{key}")
        if "state" in action and action["state"] not in _WAIT_FOR_STATES:
            raise CensusError(f"{action_path}.state is unknown")
        if "page_size" in action:
            page_size = action["page_size"]
            if isinstance(page_size, bool) or not isinstance(page_size, int | str):
                raise CensusError(f"{action_path}.page_size must be a string or integer")
        if "script" in action:
            script = action["script"]
            if (
                not isinstance(script, str)
                or not script.strip()
                or len(script) > _MAX_SCRIPT_LENGTH
                or "\x00" in script
            ):
                raise CensusError(f"{action_path}.script must be a bounded string")
        abstract.append(
            {
                "action": kind,
                "keys": sorted(action),
                "required": bool(action.get("required")) or kind == "paginate_collect",
                **({"state": action["state"]} if "state" in action else {}),
            }
        )
    return tuple(abstract)


def _abstract_value(key: str, value: object) -> object:
    if key in _SELECTOR_KEYS:
        return "selector"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        if key in {"channel", "resource_policy", "source", "wait", "wait_fallback"}:
            return value
        return "string"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return {
            "kind": "array",
            "length_class": "empty" if not value else "one" if len(value) == 1 else "many",
            "item_types": sorted({type(item).__name__ for item in value}),
        }
    if isinstance(value, dict):
        return {
            "kind": "object",
            "size_class": "empty" if not value else "one" if len(value) == 1 else "many",
        }
    raise CensusError(f"config.{key} has an unsupported JSON value")


def _validate_and_abstract_config(
    surface: str,
    crawler_type: str,
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    allowed_by_type = _MONITOR_CONFIG_KEYS if surface == "monitor" else _SCRAPER_CONFIG_KEYS
    if crawler_type not in allowed_by_type:
        raise CensusError(f"browser-capable {surface} type {crawler_type!r} has no key registry")
    unknown = set(config) - allowed_by_type[crawler_type]
    if unknown:
        raise CensusError(
            f"unknown {surface} config keys for {crawler_type}: {', '.join(sorted(unknown))}"
        )
    if _RESOURCE_POLICY_KEYS & set(config):
        try:
            _resolve_resource_blocking(dict(config))
        except ValueError as exc:
            raise CensusError(
                f"{surface}.{crawler_type} browser resource config is invalid: {exc}"
            ) from None
    selector_config: Mapping[str, Any] = config
    if surface == "monitor" and crawler_type == "dom" and "rich_rows" in config:
        try:
            _validated_rich_rows(config["rich_rows"])
        except ValueError:
            raise CensusError("monitor.dom.rich_rows is invalid") from None
        selector_config = {key: value for key, value in config.items() if key != "rich_rows"}
    if surface == "monitor" and crawler_type == "dom" and "inactive_detail_states" in config:
        try:
            _validated_inactive_detail_states(config["inactive_detail_states"])
        except ValueError:
            raise CensusError("monitor.dom.inactive_detail_states is invalid") from None
    _validate_selector_fields(selector_config)
    for key in _BOOL_BROWSER_KEYS & set(config):
        if not isinstance(config[key], bool):
            raise CensusError(f"{surface}.{crawler_type}.{key} must be boolean")
    for key in _NUMBER_BROWSER_KEYS & set(config):
        _validate_number(config[key], path=f"{surface}.{crawler_type}.{key}")
    for key in _STRING_BROWSER_KEYS & set(config):
        value = config[key]
        if not isinstance(value, str) or not value or "\x00" in value:
            raise CensusError(f"{surface}.{crawler_type}.{key} must be a non-empty string")
    if "wait" in config and config["wait"] not in VALID_WAIT_STRATEGIES:
        raise CensusError(f"{surface}.{crawler_type}.wait is unknown")
    if "wait_fallback" in config and config["wait_fallback"] not in (
        None,
        *VALID_WAIT_STRATEGIES,
    ):
        raise CensusError(f"{surface}.{crawler_type}.wait_fallback is unknown")
    actions = _validate_actions(config.get("actions", []), path=f"{surface}.{crawler_type}.actions")
    abstract = {
        key: _abstract_value(key, value)
        for key, value in sorted(config.items())
        if key not in {"actions", "fallback"}
    }
    if actions:
        abstract["actions"] = list(actions)
    if "fallback" in config:
        abstract["fallback"] = "validated-chain"
    return abstract, actions


def _parse_scraper_chain(
    scraper_type: str,
    config: dict[str, Any],
) -> list[tuple[str, dict[str, Any], int]]:
    registered = all_scraper_types()
    chain: list[tuple[str, dict[str, Any], int]] = []
    current_type = scraper_type
    current_config = config
    for depth in range(_MAX_FALLBACK_DEPTH + 1):
        if current_type not in registered:
            raise CensusError(f"unknown scraper fallback type {current_type!r}")
        chain.append((current_type, current_config, depth))
        fallback = current_config.get("fallback")
        if fallback is None:
            return chain
        if not isinstance(fallback, dict):
            raise CensusError("scraper fallback must be an object")
        if "type" not in fallback or not set(fallback) <= {"config", "fields", "type"}:
            raise CensusError("scraper fallback has unknown or missing keys")
        next_type = fallback["type"]
        if not isinstance(next_type, str) or not next_type:
            raise CensusError("scraper fallback type must be a non-empty string")
        next_config = fallback.get("config", {})
        if next_config is None:
            next_config = {}
        if not isinstance(next_config, dict):
            raise CensusError("scraper fallback config must be an object")
        fields = fallback.get("fields")
        if fields is not None and (
            not isinstance(fields, list)
            or any(not isinstance(field, str) or field not in _FALLBACK_FIELDS for field in fields)
        ):
            raise CensusError("scraper fallback fields are invalid")
        current_type = next_type
        current_config = next_config
    raise CensusError(f"scraper fallback exceeds {_MAX_FALLBACK_DEPTH} steps")


def _capabilities(
    surface: str,
    crawler_type: str,
    config: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    *,
    browser_required: bool,
) -> tuple[str, ...]:
    capabilities: set[str] = set()
    if browser_required:
        capabilities.update({"cdp.navigation", "dom.snapshot"})
    else:
        capabilities.add("chain.static_context")
    if crawler_type == "api_sniffer" and browser_required:
        capabilities.update({"network.request_observe", "network.response_capture"})
    if surface == "monitor" and crawler_type in _INHERENT_BROWSER_MONITORS:
        capabilities.add("provider.browser_session")
    if crawler_type == "nextdata" and (
        config.get("source") == "browser" or config.get("browser_expression")
    ):
        capabilities.add("javascript.evaluate")
    if config.get("browser_expression"):
        capabilities.add("javascript.evaluate")
    capability_keys = {
        "channel": "browser.channel",
        "disable_http2": "network.disable_http2",
        "headless": "browser.headful",
        "persistent_context": "browser.persistent_context",
        "proxy": "network.proxy",
        "skip_ssl": "network.skip_tls_verification",
        "stealth": "browser.stealth",
        "user_agent": "browser.custom_user_agent",
        "warmup_url": "navigation.warmup",
    }
    for key, capability in capability_keys.items():
        value = config.get(key)
        if key == "headless":
            if value is False:
                capabilities.add(capability)
        elif value:
            capabilities.add(capability)
    if "wait_fallback" in config:
        capabilities.add("navigation.wait_fallback")
    if "wait" in config:
        capabilities.add(f"navigation.wait.{config['wait']}")
    for action in actions:
        kind = str(action["action"])
        capabilities.add(f"action.{kind}")
        if kind in {"evaluate", "paginate_collect", "remove"}:
            capabilities.add("javascript.evaluate")
        if kind == "repeat" and "frame" in action["keys"]:
            capabilities.add("frame.cross_origin")
    return tuple(sorted(capabilities))


def _compatibility_class(capabilities: Iterable[str]) -> str:
    values = frozenset(capabilities)
    if "chain.static_context" in values:
        return "chain_context"
    if "frame.cross_origin" in values:
        return "cross_origin_frame"
    if values & {
        "browser.channel",
        "browser.custom_user_agent",
        "browser.headful",
        "browser.persistent_context",
        "browser.stealth",
        "navigation.warmup",
        "network.proxy",
    }:
        return "browser_identity"
    if values & {
        "network.request_observe",
        "network.response_capture",
        "provider.browser_session",
    }:
        return "network_session"
    if "javascript.evaluate" in values:
        return "javascript_execution"
    if any(value.startswith("action.") for value in values):
        return "interactive_dom"
    return "navigation_dom"


def _source_ref(row: Mapping[str, str], *, surface: str, crawler_type: str, depth: int) -> str:
    raw = "\0".join(
        (
            row["company_slug"],
            row["board_slug"],
            row["board_url"],
            surface,
            crawler_type,
            str(depth),
        )
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _record_with_digest(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "digest_sha256": _sha256(record)}


def _read_rows(boards_path: Path) -> tuple[list[dict[str, str]], str]:
    content = boards_path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CensusError("boards.csv must be UTF-8") from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != _CSV_COLUMNS:
        raise CensusError("boards.csv columns changed")
    rows = [dict(row) for row in reader]
    return rows, hashlib.sha256(content).hexdigest()


def build_manifest(boards_path: Path = DEFAULT_BOARDS_PATH) -> dict[str, Any]:
    rows, boards_sha256 = _read_rows(boards_path)
    monitor_types = all_monitor_types()
    scraper_types = all_scraper_types()
    capable_monitors = _browser_capable_monitor_types()
    capable_scrapers = _browser_capable_scraper_types()
    if capable_monitors != frozenset(_MONITOR_CONFIG_KEYS):
        raise CensusError("browser-capable monitor registry changed")
    if capable_scrapers != frozenset(_SCRAPER_CONFIG_KEYS):
        raise CensusError("browser-capable scraper registry changed")

    profile_groups: dict[tuple[object, ...], list[str]] = defaultdict(list)
    registry_refs: dict[tuple[str, str], list[str]] = defaultdict(list)
    registry_browser_counts: dict[tuple[str, str], int] = defaultdict(int)
    browser_board_indexes: set[int] = set()
    configured_step_count = 0
    browser_step_count = 0

    for row_index, row in enumerate(rows, start=2):
        monitor_type = row["monitor_type"]
        scraper_type = row["scraper_type"]
        if monitor_type not in monitor_types:
            raise CensusError(f"row {row_index} has unknown monitor type {monitor_type!r}")
        if scraper_type and scraper_type not in scraper_types:
            raise CensusError(f"row {row_index} has unknown scraper type {scraper_type!r}")
        monitor_config = _load_config(
            row["monitor_config"], row_number=row_index, field="monitor_config"
        )
        scraper_config = _load_config(
            row["scraper_config"], row_number=row_index, field="scraper_config"
        )

        validated_monitor: tuple[dict[str, Any], tuple[dict[str, Any], ...]] | None = None
        if monitor_type in capable_monitors:
            validated_monitor = _validate_and_abstract_config(
                "monitor", monitor_type, monitor_config
            )

        chain = _parse_scraper_chain(scraper_type, scraper_config) if scraper_type else []
        validated_chain: dict[int, tuple[dict[str, Any], tuple[dict[str, Any], ...]]] = {}
        for name, config, depth in chain:
            if name in capable_scrapers:
                validated_chain[depth] = _validate_and_abstract_config("scraper", name, config)

        monitor_browser = monitor_needs_browser(monitor_type, monitor_config)
        chain_browser = any(scraper_needs_browser(name, config) for name, config, _ in chain)
        if monitor_browser or chain_browser:
            browser_board_indexes.add(row_index)

        if monitor_type in capable_monitors:
            ref = _source_ref(row, surface="monitor", crawler_type=monitor_type, depth=0)
            registry_refs[("monitor", monitor_type)].append(ref)
            if monitor_browser:
                registry_browser_counts[("monitor", monitor_type)] += 1
                assert validated_monitor is not None
                abstract, actions = validated_monitor
                capabilities = _capabilities(
                    "monitor",
                    monitor_type,
                    monitor_config,
                    actions,
                    browser_required=True,
                )
                config_digest = _sha256(abstract)
                key = (
                    "monitor",
                    monitor_type,
                    "primary",
                    True,
                    config_digest,
                    capabilities,
                )
                profile_groups[key].append(ref)
                configured_step_count += 1
                browser_step_count += 1

        if chain_browser:
            for name, config, depth in chain:
                if name not in capable_scrapers:
                    raise CensusError(
                        f"browser-relevant chain contains unclassified scraper type {name!r}"
                    )
                required = scraper_needs_browser(name, config)
                ref = _source_ref(row, surface="scraper", crawler_type=name, depth=depth)
                registry_refs[("scraper", name)].append(ref)
                if required:
                    registry_browser_counts[("scraper", name)] += 1
                    browser_step_count += 1
                abstract, actions = validated_chain[depth]
                capabilities = _capabilities(
                    "scraper", name, config, actions, browser_required=required
                )
                config_digest = _sha256(abstract)
                key = (
                    "scraper",
                    name,
                    "primary" if depth == 0 else "fallback",
                    required,
                    config_digest,
                    capabilities,
                )
                profile_groups[key].append(ref)
                configured_step_count += 1
        else:
            for name, _config, depth in chain:
                if name in capable_scrapers:
                    registry_refs[("scraper", name)].append(
                        _source_ref(row, surface="scraper", crawler_type=name, depth=depth)
                    )

    records: list[dict[str, Any]] = []
    for key, refs in sorted(profile_groups.items()):
        surface, crawler_type, chain_role, required, config_digest, capabilities = key
        assert isinstance(capabilities, tuple)
        compatibility_class = _compatibility_class(capabilities)
        profile_id_seed = {
            "browser_required": required,
            "chain_role": chain_role,
            "config_shape_sha256": config_digest,
            "crawler_type": crawler_type,
            "surface": surface,
        }
        profile_id = f"profile.{surface}.{crawler_type}.{_sha256(profile_id_seed)[:16]}"
        record = {
            "blocker": (
                "pinned_lightpanda_replay_required"
                if required
                else "downstream_browser_step_requires_replay"
            ),
            "browser_capable": True,
            "browser_required": required,
            "capabilities": list(capabilities),
            "chain_role": chain_role,
            "compatibility_class": compatibility_class,
            "config_shape_sha256": config_digest,
            "crawler_type": crawler_type,
            "fixture": f"synthetic.{compatibility_class}.v1",
            "id": profile_id,
            "profile_kind": "configured",
            "source_count": len(refs),
            "source_refs_sha256": _sha256(sorted(refs)),
            "status": "pending_replay" if required else "chain_context_only",
            "surface": surface,
        }
        records.append(_record_with_digest(record))

    for surface, crawler_type in sorted(
        [("monitor", name) for name in capable_monitors]
        + [("scraper", name) for name in capable_scrapers]
    ):
        refs = registry_refs[(surface, crawler_type)]
        required_without_config = (
            monitor_needs_browser(crawler_type, {})
            if surface == "monitor"
            else scraper_needs_browser(crawler_type, {})
        )
        baseline_capabilities = _capabilities(
            surface,
            crawler_type,
            {},
            (),
            browser_required=True,
        )
        compatibility_class = _compatibility_class(baseline_capabilities)
        browser_profiles = registry_browser_counts[(surface, crawler_type)]
        record = {
            "blocker": (
                "no_configured_browser_profile"
                if browser_profiles == 0
                else "configured_profiles_pending_replay"
            ),
            "browser_capable": True,
            "browser_required": required_without_config,
            "capabilities": list(baseline_capabilities),
            "chain_role": "registry",
            "compatibility_class": compatibility_class,
            "config_shape_sha256": _sha256(
                {"crawler_type": crawler_type, "surface": surface, "zero_config": True}
            ),
            "crawler_type": crawler_type,
            "fixture": f"synthetic.{compatibility_class}.v1",
            "id": f"registry.{surface}.{crawler_type}",
            "profile_kind": "registry",
            "source_count": len(refs),
            "source_refs_sha256": _sha256(sorted(refs)),
            "status": "zero_browser_config" if browser_profiles == 0 else "inventory",
            "surface": surface,
        }
        records.append(_record_with_digest(record))

    records.sort(key=lambda record: record["id"])
    configured_records = [record for record in records if record["profile_kind"] == "configured"]
    registry_records = [record for record in records if record["profile_kind"] == "registry"]
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "input": {
            "boards_row_count": len(rows),
            "boards_sha256": boards_sha256,
            "network_access": False,
            "sanitization": "structural-values-only-v1",
        },
        "records": records,
        "summary": {
            "browser_board_count": len(browser_board_indexes),
            "browser_required_step_count": browser_step_count,
            "configured_profile_occurrence_count": configured_step_count,
            "configured_record_count": len(configured_records),
            "registry_record_count": len(registry_records),
            "total_record_count": len(records),
            "zero_browser_config_registry_count": sum(
                record["status"] == "zero_browser_config" for record in registry_records
            ),
        },
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_sha256(value: object, *, path: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise CensusError(f"{path} must be a lowercase SHA-256 digest")
    return value


def _load_manifest(path: Path, *, role: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="ascii")
    except FileNotFoundError as exc:
        raise CensusError(f"missing {role} census: {path}") from exc
    except UnicodeDecodeError as exc:
        raise CensusError(f"{role} census must be ASCII: {path}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CensusError(f"{role} census is not valid JSON: {path}") from exc
    if not isinstance(manifest, dict):
        raise CensusError(f"{role} census must be a JSON object: {path}")
    return manifest


def _validate_record_integrity(record: Mapping[str, Any], *, role: str) -> None:
    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise CensusError(f"{role} census record has an invalid id")
    path = f"{role} census record {record_id}"

    digest = _require_sha256(record.get("digest_sha256"), path=f"{path}.digest_sha256")
    digest_payload = dict(record)
    digest_payload.pop("digest_sha256", None)
    if digest != _sha256(digest_payload):
        raise CensusError(f"{path} digest does not match its content")

    source_count = record.get("source_count")
    if not _is_non_negative_int(source_count):
        raise CensusError(f"{path}.source_count must be a non-negative integer")
    _require_sha256(record.get("source_refs_sha256"), path=f"{path}.source_refs_sha256")
    config_shape = _require_sha256(
        record.get("config_shape_sha256"), path=f"{path}.config_shape_sha256"
    )

    surface = record.get("surface")
    crawler_type = record.get("crawler_type")
    profile_kind = record.get("profile_kind")
    chain_role = record.get("chain_role")
    browser_required = record.get("browser_required")
    capabilities = record.get("capabilities")
    compatibility_class = record.get("compatibility_class")
    if surface not in {"monitor", "scraper"}:
        raise CensusError(f"{path}.surface is invalid")
    if not isinstance(crawler_type, str) or not crawler_type:
        raise CensusError(f"{path}.crawler_type is invalid")
    if record.get("browser_capable") is not True:
        raise CensusError(f"{path}.browser_capable must be true")
    if not isinstance(browser_required, bool):
        raise CensusError(f"{path}.browser_required must be boolean")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(capability, str) or not capability for capability in capabilities)
        or capabilities != sorted(set(capabilities))
    ):
        raise CensusError(f"{path}.capabilities must be sorted and unique")
    expected_class = _compatibility_class(capabilities)
    if compatibility_class != expected_class:
        raise CensusError(f"{path}.compatibility_class does not match its capabilities")
    if record.get("fixture") != f"synthetic.{expected_class}.v1":
        raise CensusError(f"{path}.fixture does not match its compatibility class")

    if profile_kind == "configured":
        if source_count == 0:
            raise CensusError(f"{path}.source_count must be at least one")
        if chain_role not in {"primary", "fallback"}:
            raise CensusError(f"{path}.chain_role is invalid for a configured profile")
        profile_id_seed = {
            "browser_required": browser_required,
            "chain_role": chain_role,
            "config_shape_sha256": config_shape,
            "crawler_type": crawler_type,
            "surface": surface,
        }
        expected_id = f"profile.{surface}.{crawler_type}.{_sha256(profile_id_seed)[:16]}"
        if record_id != expected_id:
            raise CensusError(f"{path}.id does not match its stable profile identity")
        expected_status = "pending_replay" if browser_required else "chain_context_only"
        expected_blocker = (
            "pinned_lightpanda_replay_required"
            if browser_required
            else "downstream_browser_step_requires_replay"
        )
        if record.get("status") != expected_status or record.get("blocker") != expected_blocker:
            raise CensusError(f"{path} is not in its required fail-safe state")
    elif profile_kind == "registry":
        if chain_role != "registry":
            raise CensusError(f"{path}.chain_role is invalid for a registry profile")
        if record_id != f"registry.{surface}.{crawler_type}":
            raise CensusError(f"{path}.id does not match its registry identity")
        status = record.get("status")
        expected_blockers = {
            "inventory": "configured_profiles_pending_replay",
            "zero_browser_config": "no_configured_browser_profile",
        }
        if status not in expected_blockers or record.get("blocker") != expected_blockers[status]:
            raise CensusError(f"{path} has an invalid registry status or blocker")
    else:
        raise CensusError(f"{path}.profile_kind is invalid")


def _validate_manifest_integrity(manifest: Mapping[str, Any], *, role: str) -> None:
    if manifest.get("format") != FORMAT:
        raise CensusError(f"{role} census format must be {FORMAT}")

    inputs = manifest.get("input")
    if not isinstance(inputs, dict):
        raise CensusError(f"{role} census input must be an object")
    if inputs.get("network_access") is not False:
        raise CensusError(f"{role} census must preserve network_access=false")
    if inputs.get("sanitization") != "structural-values-only-v1":
        raise CensusError(f"{role} census sanitization marker is invalid")
    boards_row_count = inputs.get("boards_row_count")
    if not _is_non_negative_int(boards_row_count):
        raise CensusError(f"{role} census boards_row_count must be a non-negative integer")
    _require_sha256(inputs.get("boards_sha256"), path=f"{role} census input.boards_sha256")

    records = manifest.get("records")
    if not isinstance(records, list):
        raise CensusError(f"{role} census records must be an array")
    record_ids: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise CensusError(f"{role} census records must contain only objects")
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise CensusError(f"{role} census record has an invalid id")
        record_ids.append(record_id)
    duplicates = sorted(record_id for record_id, count in Counter(record_ids).items() if count > 1)
    if duplicates:
        raise CensusError(f"{role} census contains duplicate record ids: {', '.join(duplicates)}")
    if record_ids != sorted(record_ids):
        raise CensusError(f"{role} census record ids must be sorted")
    for record in records:
        _validate_record_integrity(record, role=role)

    summary = manifest.get("summary")
    if not isinstance(summary, dict) or set(summary) != _SUMMARY_FIELDS:
        raise CensusError(f"{role} census summary fields are invalid")
    if any(not _is_non_negative_int(summary[field]) for field in _SUMMARY_FIELDS):
        raise CensusError(f"{role} census summary values must be non-negative integers")
    configured = [record for record in records if record["profile_kind"] == "configured"]
    registry = [record for record in records if record["profile_kind"] == "registry"]
    configured_pairs = {(record["surface"], record["crawler_type"]) for record in configured}
    registry_pairs = {(record["surface"], record["crawler_type"]) for record in registry}
    missing_registry_pairs = sorted(configured_pairs - registry_pairs)
    if missing_registry_pairs:
        formatted = ", ".join(
            f"{surface}.{crawler_type}" for surface, crawler_type in missing_registry_pairs
        )
        raise CensusError(
            f"{role} census configured profiles have no corresponding registry record: {formatted}"
        )
    configured_browser_pairs = {
        (record["surface"], record["crawler_type"])
        for record in configured
        if record["browser_required"]
    }
    for record in registry:
        pair = (record["surface"], record["crawler_type"])
        expected_status, expected_blocker = (
            ("inventory", "configured_profiles_pending_replay")
            if pair in configured_browser_pairs
            else ("zero_browser_config", "no_configured_browser_profile")
        )
        if record["status"] != expected_status or record["blocker"] != expected_blocker:
            raise CensusError(
                f"{role} census record {record['id']} status does not match "
                "configured browser occurrences"
            )
    derived_summary = {
        "browser_required_step_count": sum(
            record["source_count"] for record in configured if record["browser_required"]
        ),
        "configured_profile_occurrence_count": sum(record["source_count"] for record in configured),
        "configured_record_count": len(configured),
        "registry_record_count": len(registry),
        "total_record_count": len(records),
        "zero_browser_config_registry_count": sum(
            record["status"] == "zero_browser_config" for record in registry
        ),
    }
    for field, expected in derived_summary.items():
        if summary[field] != expected:
            raise CensusError(f"{role} census summary.{field} is inconsistent with its records")
    if summary["browser_board_count"] > boards_row_count:
        raise CensusError(f"{role} census browser_board_count exceeds boards_row_count")
    if summary["browser_board_count"] > summary["browser_required_step_count"]:
        raise CensusError(f"{role} census browser_board_count exceeds browser-required steps")

    digest = _require_sha256(manifest.get("manifest_sha256"), path=f"{role} census manifest_sha256")
    digest_payload = dict(manifest)
    digest_payload.pop("manifest_sha256", None)
    if digest != _sha256(digest_payload):
        raise CensusError(f"{role} census manifest digest does not match its content")


def _stable_record(record: Mapping[str, Any]) -> dict[str, Any]:
    ignored = set(_RECORD_PROVENANCE_FIELDS)
    if record.get("profile_kind") == "registry":
        ignored.update({"blocker", "status"})
    return {key: value for key, value in record.items() if key not in ignored}


def compare_semantic_manifests(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    allow_additions: bool = False,
) -> dict[str, Any]:
    """Require a candidate baseline to exactly cover the current semantics."""

    _validate_manifest_integrity(baseline, role="baseline")
    _validate_manifest_integrity(current, role="current")

    baseline_records = {record["id"]: record for record in baseline["records"]}
    current_records = {record["id"]: record for record in current["records"]}
    baseline_registry_ids = {
        record_id
        for record_id, record in baseline_records.items()
        if record["profile_kind"] == "registry"
    }
    current_registry_ids = {
        record_id
        for record_id, record in current_records.items()
        if record["profile_kind"] == "registry"
    }
    removed_registry_ids = sorted(baseline_registry_ids - current_registry_ids)
    if removed_registry_ids:
        raise CensusError(
            "current census removed or renamed pinned registry profiles: "
            + ", ".join(removed_registry_ids)
        )
    new_registry_ids = sorted(current_registry_ids - baseline_registry_ids)
    if new_registry_ids and not allow_additions:
        raise CensusError("current census registry profile ID set differs from the pinned baseline")

    baseline_configured_ids = {
        record_id
        for record_id, record in baseline_records.items()
        if record["profile_kind"] == "configured"
    }
    current_configured_ids = {
        record_id
        for record_id, record in current_records.items()
        if record["profile_kind"] == "configured"
    }
    missing_ids = sorted(baseline_configured_ids - current_configured_ids)
    if missing_ids:
        raise CensusError(
            "current census removed or renamed pinned configured profiles: "
            + ", ".join(missing_ids)
        )
    unpinned_ids = sorted(current_configured_ids - baseline_configured_ids)
    if unpinned_ids and not allow_additions:
        raise CensusError(
            "current census has configured profiles absent from the candidate baseline: "
            + ", ".join(unpinned_ids)
        )

    changed_ids = sorted(
        record_id
        for record_id in baseline_records.keys() & current_records.keys()
        if _stable_record(baseline_records[record_id]) != _stable_record(current_records[record_id])
    )
    if changed_ids:
        raise CensusError(
            "current census changed pinned stable profile semantics: " + ", ".join(changed_ids)
        )

    return {
        "baseline_configured_profile_count": len(baseline_configured_ids),
        "current_configured_profile_count": len(current_configured_ids),
        "new_configured_profile_ids": unpinned_ids,
        "new_registry_profile_ids": new_registry_ids,
        "registry_profile_count": len(current_registry_ids),
    }


def compare_baseline_evolution(
    previous_baseline: Mapping[str, Any],
    candidate_baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Require an append-only baseline update that exactly covers current output."""

    baseline_update = compare_semantic_manifests(
        previous_baseline,
        candidate_baseline,
        allow_additions=True,
    )
    comparison = compare_semantic_manifests(candidate_baseline, current)
    comparison["new_configured_profile_ids"] = baseline_update["new_configured_profile_ids"]
    comparison["new_registry_profile_ids"] = baseline_update["new_registry_profile_ids"]
    comparison["previous_baseline_configured_profile_count"] = baseline_update[
        "baseline_configured_profile_count"
    ]
    return comparison


def write_manifest(
    boards_path: Path = DEFAULT_BOARDS_PATH,
    output_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = build_manifest(boards_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(manifest_bytes(manifest))
    return manifest


def check_manifest(
    boards_path: Path = DEFAULT_BOARDS_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    expected = manifest_bytes(build_manifest(boards_path))
    try:
        actual = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise CensusError(f"missing census fixture: {manifest_path}") from exc
    if actual != expected:
        raise CensusError(
            f"census fixture is stale: run python -m src.lightpanda.census --output {manifest_path}"
        )
    return json.loads(actual)


def check_semantic_baseline(
    baseline_path: Path,
    current_path: Path,
    *,
    previous_baseline_path: Path | None = None,
) -> dict[str, Any]:
    baseline = _load_manifest(baseline_path, role="baseline")
    current = _load_manifest(current_path, role="current")
    if previous_baseline_path is not None:
        previous_baseline = _load_manifest(previous_baseline_path, role="previous baseline")
        return compare_baseline_evolution(previous_baseline, baseline, current)
    return compare_semantic_manifests(baseline, current)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boards", type=Path, default=DEFAULT_BOARDS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Pinned semantic baseline to compare after checking the current output",
    )
    parser.add_argument(
        "--previous-baseline",
        type=Path,
        help="Base-revision baseline used to enforce append-only semantic changes",
    )
    args = parser.parse_args()
    if args.baseline is not None and not args.check:
        parser.error("--baseline requires --check")
    if args.previous_baseline is not None and args.baseline is None:
        parser.error("--previous-baseline requires --baseline")

    comparison: dict[str, Any] | None = None
    if args.check:
        manifest = check_manifest(args.boards, args.output)
        action = "checked"
        if args.baseline is not None:
            comparison = check_semantic_baseline(
                args.baseline,
                args.output,
                previous_baseline_path=args.previous_baseline,
            )
    else:
        manifest = write_manifest(args.boards, args.output)
        action = "wrote"
    print(
        f"{action} {args.output} "
        f"({manifest['summary']['total_record_count']} records, "
        f"{manifest['summary']['browser_board_count']} browser boards)"
    )
    if comparison is not None:
        print(
            "semantic baseline compatible "
            f"({comparison['current_configured_profile_count']} configured profiles, "
            f"{len(comparison['new_configured_profile_ids'])} newly pinned profiles)"
        )
        if comparison["new_configured_profile_ids"]:
            print(
                "ADDITIVE PINNED FAIL-SAFE PROFILES: "
                + ", ".join(comparison["new_configured_profile_ids"])
            )
        if comparison["new_registry_profile_ids"]:
            print(
                "ADDITIVE PINNED REGISTRY PROFILES: "
                + ", ".join(comparison["new_registry_profile_ids"])
            )


if __name__ == "__main__":
    main()
