"""Pure, inactive validation for immutable Lightpanda render assignments.

This module only binds routing identity and parser metadata.  A routing
revision is not an authorization token, and a returned assignment grants no
queue, service, egress, or production authority.  This module does not launch
a browser, read the environment, or perform network or filesystem I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from src.core.scrapers import JobContent

ASSIGNMENT_KEYS: Final = frozenset({"browser_backend", "routing_revision"})
_REQUIRED_KEYS: Final = frozenset(
    {
        "browser_backend",
        "render",
        "routing_revision",
        "timeout",
        "wait",
        "wait_fallback",
    }
)
_PARSER_BOOL_KEYS: Final = frozenset(
    {
        "ignore_address_region",
        "ignore_date_posted",
        "ignore_locations",
        "ignore_valid_through",
    }
)
_PARSER_KEYS: Final = frozenset({"defaults", "defaults_by_url", "enrich"}) | _PARSER_BOOL_KEYS
_ALLOWED_KEYS: Final = _REQUIRED_KEYS | _PARSER_KEYS
_JOB_CONTENT_FIELDS: Final = frozenset(JobContent.__dataclass_fields__)
_ROUTING_REVISION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_MAX_CONFIG_BYTES: Final = 256 * 1024


class RenderAssignmentError(ValueError):
    """A requested Lightpanda render assignment is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class _FrozenJSONMap(Mapping[str, object]):
    """Small, hashable mapping used to make the bound config deeply immutable."""

    _items: tuple[tuple[str, object], ...]

    def __getitem__(self, key: str) -> object:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True, slots=True)
class RenderAssignment:
    """Validated immutable identity snapshot, never execution authorization."""

    browser_backend: Literal["lightpanda"]
    routing_revision: str
    scraper_type: Literal["json-ld"]
    scraper_step: Literal[0]
    timeout_ms: int
    config: Mapping[str, object]
    config_digest_sha256: str


def has_render_assignment(config: object) -> bool:
    """Return whether either routing key requests assignment validation."""

    return isinstance(config, Mapping) and bool(ASSIGNMENT_KEYS & config.keys())


def resolve_render_assignment(
    scraper_type: str,
    config: Mapping[str, Any],
    *,
    scraper_step: int = 0,
) -> RenderAssignment | None:
    """Bind a complete Lightpanda identity snapshot without touching an origin.

    Configs without either routing key are outside this inactive contract and
    return ``None``.  Once one routing key is present, every field fails closed.
    Callers must separately obtain queue, service, egress, and activation
    authority before executing anything.
    """

    if not has_render_assignment(config):
        return None

    backend = config.get("browser_backend")
    if "browser_backend" in config and backend != "lightpanda":
        raise RenderAssignmentError("browser_backend must be 'lightpanda'")

    routing_revision = config.get("routing_revision")
    if "routing_revision" in config and (
        not isinstance(routing_revision, str)
        or not _ROUTING_REVISION_RE.fullmatch(routing_revision)
    ):
        raise RenderAssignmentError(
            "routing_revision must match ^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
        )

    _validate_json_tree(config)

    if scraper_type != "json-ld":
        raise RenderAssignmentError("Lightpanda assignment requires scraper step 0 type 'json-ld'")
    if type(scraper_step) is not int or scraper_step != 0:
        raise RenderAssignmentError("Lightpanda assignment is only valid at scraper step 0")

    unknown = set(config) - _ALLOWED_KEYS
    if unknown:
        raise RenderAssignmentError(
            "Lightpanda assignment has forbidden or unknown keys: " + ", ".join(sorted(unknown))
        )
    missing = _REQUIRED_KEYS - set(config)
    if missing:
        raise RenderAssignmentError(
            "Lightpanda assignment is missing required keys: " + ", ".join(sorted(missing))
        )

    assert backend == "lightpanda"
    assert isinstance(routing_revision, str)

    if config["render"] is not True:
        raise RenderAssignmentError("render must be true")
    if config["wait"] != "load":
        raise RenderAssignmentError("wait must be 'load'")
    if config["wait_fallback"] is not None:
        raise RenderAssignmentError("wait_fallback must be explicitly null")

    timeout = config["timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120_000:
        raise RenderAssignmentError("timeout must be an integer from 1 through 120000")

    for key in _PARSER_BOOL_KEYS:
        if key in config and not isinstance(config[key], bool):
            raise RenderAssignmentError(f"{key} must be boolean")
    _validate_defaults(config.get("defaults"), path="defaults")
    _validate_defaults_by_url(config.get("defaults_by_url"))
    _validate_enrich(config.get("enrich"))

    canonical = _canonical_config(config)
    plain_snapshot = json.loads(canonical)
    frozen_snapshot = _freeze_json(plain_snapshot)
    assert isinstance(frozen_snapshot, _FrozenJSONMap)
    return RenderAssignment(
        browser_backend="lightpanda",
        routing_revision=routing_revision,
        scraper_type="json-ld",
        scraper_step=0,
        timeout_ms=timeout,
        config=frozen_snapshot,
        config_digest_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _validate_defaults(value: object, *, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise RenderAssignmentError(f"{path} must be an object")
    unknown = set(value) - _JOB_CONTENT_FIELDS
    if unknown:
        raise RenderAssignmentError(f"{path} has unknown fields: {', '.join(sorted(unknown))}")


def _validate_defaults_by_url(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise RenderAssignmentError("defaults_by_url must be an object")
    for posting_url, defaults in value.items():
        if not isinstance(posting_url, str) or not isinstance(defaults, Mapping):
            raise RenderAssignmentError("defaults_by_url must map URL strings to objects")
        _validate_defaults(defaults, path=f"defaults_by_url[{posting_url!r}]")


def _validate_enrich(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise RenderAssignmentError("enrich must be a list")
    for field in value:
        if not isinstance(field, str) or field not in _JOB_CONTENT_FIELDS:
            raise RenderAssignmentError(f"enrich has unknown field: {field!r}")


def _canonical_config(config: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            config,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise RenderAssignmentError("Lightpanda assignment config must be finite JSON") from exc
    if len(encoded) > _MAX_CONFIG_BYTES:
        raise RenderAssignmentError(
            f"Lightpanda assignment config exceeds {_MAX_CONFIG_BYTES} encoded bytes"
        )
    return encoded


def _validate_json_tree(
    value: object,
    *,
    path: str = "config",
    ancestors: frozenset[int] = frozenset(),
) -> None:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise RenderAssignmentError("Lightpanda assignment config must be finite JSON")
        nested_ancestors = ancestors | {identity}
        for key, item in value.items():
            if not isinstance(key, str):
                raise RenderAssignmentError(f"{path} mapping keys must be strings")
            _validate_json_tree(
                item,
                path=f"{path}.{key}",
                ancestors=nested_ancestors,
            )
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in ancestors:
            raise RenderAssignmentError("Lightpanda assignment config must be finite JSON")
        nested_ancestors = ancestors | {identity}
        for index, item in enumerate(value):
            _validate_json_tree(
                item,
                path=f"{path}[{index}]",
                ancestors=nested_ancestors,
            )
        return
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise RenderAssignmentError(f"{path} must contain only finite JSON values")


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return _FrozenJSONMap(
            tuple((key, _freeze_json(item)) for key, item in sorted(value.items()))
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    assert value is None or isinstance(value, str | int | float | bool)
    return value
