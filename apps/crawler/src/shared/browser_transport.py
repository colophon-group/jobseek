"""Bounded Chromium transport observation and standalone capture validation.

This module is intentionally not wired into the production browser lifecycle yet.
It exposes the pure tracker, the public-CDP observer boundary, and the frozen v1
registry/capture validators needed by the post-#8401 integration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect as connect_websocket

REGISTRY_SCHEMA = "jobseek.browser-transport-registry/v1"
CAPTURE_SCHEMA = "jobseek.browser-transport-capture-fixture/v1"
REGISTRY_VERSION = "v1"

STAGES = ("monitor", "detail")
CAPABILITIES = (
    "navigation-evaluation",
    "interaction-capture",
    "identity-transport",
)
REQUEST_CLASSES = ("main", "redirect", "subresource", "warmup")
VALID_ROUTE_PROVIDER_PAIRS = (("direct", "direct"), ("proxy", "static-proxy"))
TERMINAL_OUTCOMES = (
    "complete_response",
    "partial_response",
    "transport_failure",
    "policy_rejected",
    "cancelled",
    "target_closed",
)
PRETRANSPORT_REASONS = (
    "required_proxy_unavailable",
    "resource_policy",
    "unknown_capability",
    "unknown_provider",
)
INSTRUMENTATION_REASONS = (
    "observer_attach_failed",
    "observer_protocol_error",
    "byte_lifecycle_missing",
    "classification_missing",
    "lifecycle_conflict",
    "drain_timeout",
    "registry_mismatch",
    "unattributed_task",
)
HISTOGRAM_BUCKETS: tuple[int | str, ...] = (
    0,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    16777216,
    "+Inf",
)
HISTOGRAM_BUCKET_LABELS = tuple(
    "+Inf" if bound == "+Inf" else f"{float(bound):.1f}" for bound in HISTOGRAM_BUCKETS
)
DRAIN_TIMEOUT_SECONDS = 5.0
EXACT_TIME_SERIES_BUDGET = 1_449
BASE_ROW_COUNT = 48

ATTEMPTS_METRIC = "crawler_browser_transport_attempts_total"
OUTCOMES_METRIC = "crawler_browser_transport_outcomes_total"
BYTES_METRIC = "crawler_browser_transport_transferred_bytes_total"
HISTOGRAM_METRIC = "crawler_browser_transport_response_size_bytes"
ACCEPTED_TASKS_METRIC = "crawler_browser_transport_accepted_tasks_total"
PRETRANSPORT_METRIC = "crawler_browser_transport_pretransport_total"
INSTRUMENTATION_METRIC = "crawler_browser_transport_instrumentation_failures"
REGISTRY_INFO_METRIC = "crawler_browser_transport_registry_info"

REQUEST_LABELS = (
    "stage",
    "execution_class",
    "browser_backend",
    "request_class",
    "route",
    "provider",
    "capability",
)
TASK_LABELS = (
    "stage",
    "execution_class",
    "browser_backend",
    "route",
    "provider",
    "capability",
)
SUPPORT_LABELS = ("stage", "execution_class", "browser_backend", "reason")
REGISTRY_INFO_LABELS = ("registry_version", "registry_sha256")
ALLOWED_LABEL_NAMES = frozenset(
    REQUEST_LABELS + TASK_LABELS + SUPPORT_LABELS + REGISTRY_INFO_LABELS + ("outcome", "le")
)
FORBIDDEN_LABEL_NAMES = frozenset(
    {
        "url",
        "uri",
        "origin",
        "hostname",
        "host",
        "ip",
        "company",
        "board",
        "posting",
        "proxy_endpoint",
        "exception",
        "error",
        "target_id",
        "session_id",
        "request_id",
        "release_sha",
        "image_digest",
    }
)
PUBLIC_CDP_TARGET_TYPES = frozenset({"page", "iframe", "worker", "shared_worker", "service_worker"})

ADAPTER_BLOCKERS = (
    "window_mismatch",
    "registry_mismatch",
    "missing_series",
    "extra_series",
    "illegal_label",
    "illegal_route_provider_pair",
    "bucket_mismatch",
    "counter_reset",
    "fractional_delta",
    "conservation_mismatch",
    "instrumentation_failure",
    "observer_lifecycle_gap",
    "terminal_duplication",
    "event_tape_mismatch",
    "provider_none_misclassification",
)


class RegistryError(ValueError):
    """The checked-in registry or its digest is not the frozen v1 contract."""


class CaptureValidationError(ValueError):
    """A standalone fixture is structurally unusable."""


class Stage(StrEnum):
    MONITOR = "monitor"
    DETAIL = "detail"


class Capability(StrEnum):
    NAVIGATION_EVALUATION = "navigation-evaluation"
    INTERACTION_CAPTURE = "interaction-capture"
    IDENTITY_TRANSPORT = "identity-transport"


class RequestClass(StrEnum):
    MAIN = "main"
    REDIRECT = "redirect"
    SUBRESOURCE = "subresource"
    WARMUP = "warmup"


class TerminalOutcome(StrEnum):
    COMPLETE_RESPONSE = "complete_response"
    PARTIAL_RESPONSE = "partial_response"
    TRANSPORT_FAILURE = "transport_failure"
    POLICY_REJECTED = "policy_rejected"
    CANCELLED = "cancelled"
    TARGET_CLOSED = "target_closed"


class PretransportReason(StrEnum):
    REQUIRED_PROXY_UNAVAILABLE = "required_proxy_unavailable"
    RESOURCE_POLICY = "resource_policy"
    UNKNOWN_CAPABILITY = "unknown_capability"
    UNKNOWN_PROVIDER = "unknown_provider"


class InstrumentationReason(StrEnum):
    OBSERVER_ATTACH_FAILED = "observer_attach_failed"
    OBSERVER_PROTOCOL_ERROR = "observer_protocol_error"
    BYTE_LIFECYCLE_MISSING = "byte_lifecycle_missing"
    CLASSIFICATION_MISSING = "classification_missing"
    LIFECYCLE_CONFLICT = "lifecycle_conflict"
    DRAIN_TIMEOUT = "drain_timeout"
    REGISTRY_MISMATCH = "registry_mismatch"
    UNATTRIBUTED_TASK = "unattributed_task"


@dataclass(frozen=True, slots=True)
class MetricSeries:
    """One exact Prometheus series identity, without a value."""

    name: str
    labels: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class BrowserTransportRegistry:
    version: str
    digest: str
    path: Path
    document: Mapping[str, Any]


def _ledger() -> list[dict[str, int | str]]:
    return [
        {"component": "attempts", "rows": 48, "series_per_row": 2, "series": 96},
        {
            "component": "terminal_outcomes",
            "rows": 48,
            "series_per_row": 12,
            "series": 576,
        },
        {
            "component": "transferred_bytes",
            "rows": 48,
            "series_per_row": 2,
            "series": 96,
        },
        {
            "component": "response_size_histogram",
            "rows": 48,
            "series_per_row": 13,
            "series": 624,
        },
        {"component": "accepted_tasks", "rows": 12, "series_per_row": 2, "series": 24},
        {"component": "pretransport", "rows": 8, "series_per_row": 2, "series": 16},
        {
            "component": "instrumentation_failures",
            "rows": 16,
            "series_per_row": 1,
            "series": 16,
        },
        {"component": "registry_info", "rows": 1, "series_per_row": 1, "series": 1},
    ]


def build_registry_document() -> dict[str, Any]:
    """Return the one canonical v1 registry document."""

    return {
        "schema_version": REGISTRY_SCHEMA,
        "registry_version": REGISTRY_VERSION,
        "fixed_labels": {"execution_class": "browser", "browser_backend": "chromium"},
        "enums": {
            "stage": list(STAGES),
            "capability": list(CAPABILITIES),
            "request_class": list(REQUEST_CLASSES),
            "terminal_outcome": list(TERMINAL_OUTCOMES),
            "pretransport_reason": list(PRETRANSPORT_REASONS),
            "instrumentation_reason": list(INSTRUMENTATION_REASONS),
        },
        "valid_route_provider_pairs": [
            {"route": route, "provider": provider} for route, provider in VALID_ROUTE_PROVIDER_PAIRS
        ],
        "histogram_buckets_bytes": list(HISTOGRAM_BUCKETS),
        "histogram_bucket_label_values": list(HISTOGRAM_BUCKET_LABELS),
        "drain_timeout_seconds": DRAIN_TIMEOUT_SECONDS,
        "metric_contract": {
            "attempts": {
                "name": ATTEMPTS_METRIC,
                "labels": list(REQUEST_LABELS),
                "created": True,
            },
            "terminal_outcomes": {
                "name": OUTCOMES_METRIC,
                "labels": [*REQUEST_LABELS, "outcome"],
                "created": True,
            },
            "transferred_bytes": {
                "name": BYTES_METRIC,
                "labels": list(REQUEST_LABELS),
                "created": True,
            },
            "response_size_histogram": {
                "name": HISTOGRAM_METRIC,
                "labels": list(REQUEST_LABELS),
                "created": True,
            },
            "accepted_tasks": {
                "name": ACCEPTED_TASKS_METRIC,
                "labels": list(TASK_LABELS),
                "created": True,
            },
            "pretransport": {
                "name": PRETRANSPORT_METRIC,
                "labels": list(SUPPORT_LABELS),
                "created": True,
            },
            "instrumentation_failures": {
                "name": INSTRUMENTATION_METRIC,
                "labels": list(SUPPORT_LABELS),
                "created": False,
                "monotonic": True,
            },
            "registry_info": {
                "name": REGISTRY_INFO_METRIC,
                "labels": list(REGISTRY_INFO_LABELS),
                "created": False,
            },
        },
        "privacy": {
            "allowed_label_names": sorted(ALLOWED_LABEL_NAMES),
            "forbidden_label_names": sorted(FORBIDDEN_LABEL_NAMES),
            "request_identifiers_are_internal_only": True,
        },
        "cardinality_budget": {
            "base_request_rows": BASE_ROW_COUNT,
            "ledger": _ledger(),
            "exact_time_series": EXACT_TIME_SERIES_BUDGET,
            "maximum_time_series": EXACT_TIME_SERIES_BUDGET,
        },
        "adapter_blockers": list(ADAPTER_BLOCKERS),
    }


def canonical_registry_bytes(document: Mapping[str, Any] | None = None) -> bytes:
    """Serialize deterministically; the digest covers these exact bytes."""

    payload = build_registry_document() if document is None else document
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def registry_digest(document: Mapping[str, Any] | None = None) -> str:
    return hashlib.sha256(canonical_registry_bytes(document)).hexdigest()


def default_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts/browser/transport/v1/registry.json"


def _digest_file_path(registry_path: Path) -> Path:
    return registry_path.with_name("registry.sha256")


def validate_registry_document(document: object) -> None:
    """Reject drift, broadening, privacy regressions, and budget changes."""

    if document != build_registry_document():
        raise RegistryError("registry document differs from the frozen canonical v1 contract")
    expected = build_registry_document()
    privacy = cast(dict[str, Any], expected["privacy"])
    allowed = set(cast(list[str], privacy["allowed_label_names"]))
    forbidden = set(cast(list[str], privacy["forbidden_label_names"]))
    if allowed & forbidden:
        raise RegistryError("registry label allowlist intersects forbidden labels")
    if set(REQUEST_LABELS) - allowed or set(TASK_LABELS) - allowed:
        raise RegistryError("registry omits a required label from the allowlist")
    ledger = cast(dict[str, Any], expected["cardinality_budget"])["ledger"]
    total = sum(int(item["series"]) for item in ledger)
    if total != EXACT_TIME_SERIES_BUDGET:
        raise RegistryError("registry cardinality ledger does not total 1,449")


def load_registry(path: Path | None = None) -> BrowserTransportRegistry:
    """Load the registry with strict canonical bytes and sidecar verification."""

    registry_path = path or default_registry_path()
    try:
        raw = registry_path.read_bytes()
        document = json.loads(raw)
        sidecar = _digest_file_path(registry_path).read_text().strip().split()
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError("browser transport registry is unreadable") from exc
    if raw != canonical_registry_bytes(cast(Mapping[str, Any], document)):
        raise RegistryError("registry JSON is not canonical")
    validate_registry_document(document)
    digest = hashlib.sha256(raw).hexdigest()
    if sidecar != [digest, "registry.json"]:
        raise RegistryError("registry SHA-256 sidecar mismatch")
    frozen_document = MappingProxyType(cast(dict[str, Any], document))
    return BrowserTransportRegistry(REGISTRY_VERSION, digest, registry_path, frozen_document)


def _labels(**values: str) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(values.items()))


def _request_label_sets() -> Iterable[dict[str, str]]:
    for stage in STAGES:
        for request_class in REQUEST_CLASSES:
            for route, provider in VALID_ROUTE_PROVIDER_PAIRS:
                for capability in CAPABILITIES:
                    yield {
                        "stage": stage,
                        "execution_class": "browser",
                        "browser_backend": "chromium",
                        "request_class": request_class,
                        "route": route,
                        "provider": provider,
                        "capability": capability,
                    }


def iter_metric_series(registry: BrowserTransportRegistry) -> Iterable[MetricSeries]:
    """Enumerate the exact 1,449-series v1 exposition."""

    for labels in _request_label_sets():
        base = _labels(**labels)
        yield MetricSeries(ATTEMPTS_METRIC, base)
        yield MetricSeries(ATTEMPTS_METRIC.removesuffix("_total") + "_created", base)
        yield MetricSeries(BYTES_METRIC, base)
        yield MetricSeries(BYTES_METRIC.removesuffix("_total") + "_created", base)
        for outcome in TERMINAL_OUTCOMES:
            outcome_labels = _labels(**labels, outcome=outcome)
            yield MetricSeries(OUTCOMES_METRIC, outcome_labels)
            yield MetricSeries(OUTCOMES_METRIC.removesuffix("_total") + "_created", outcome_labels)
        for bound in HISTOGRAM_BUCKET_LABELS:
            yield MetricSeries(f"{HISTOGRAM_METRIC}_bucket", _labels(**labels, le=bound))
        yield MetricSeries(f"{HISTOGRAM_METRIC}_sum", base)
        yield MetricSeries(f"{HISTOGRAM_METRIC}_count", base)
        yield MetricSeries(f"{HISTOGRAM_METRIC}_created", base)
    for stage in STAGES:
        for route, provider in VALID_ROUTE_PROVIDER_PAIRS:
            for capability in CAPABILITIES:
                labels = _labels(
                    stage=stage,
                    execution_class="browser",
                    browser_backend="chromium",
                    route=route,
                    provider=provider,
                    capability=capability,
                )
                yield MetricSeries(ACCEPTED_TASKS_METRIC, labels)
                yield MetricSeries(
                    ACCEPTED_TASKS_METRIC.removesuffix("_total") + "_created", labels
                )
    for stage in STAGES:
        for reason in PRETRANSPORT_REASONS:
            labels = _labels(
                stage=stage,
                execution_class="browser",
                browser_backend="chromium",
                reason=reason,
            )
            yield MetricSeries(PRETRANSPORT_METRIC, labels)
            yield MetricSeries(PRETRANSPORT_METRIC.removesuffix("_total") + "_created", labels)
        for reason in INSTRUMENTATION_REASONS:
            yield MetricSeries(
                INSTRUMENTATION_METRIC,
                _labels(
                    stage=stage,
                    execution_class="browser",
                    browser_backend="chromium",
                    reason=reason,
                ),
            )
    yield MetricSeries(
        REGISTRY_INFO_METRIC,
        _labels(registry_version=registry.version, registry_sha256=registry.digest),
    )


def audit_metric_series(registry: BrowserTransportRegistry) -> tuple[MetricSeries, ...]:
    """Mechanically enforce uniqueness, privacy, value closure, and exact budget."""

    series = tuple(iter_metric_series(registry))
    if len(series) != EXACT_TIME_SERIES_BUDGET or len(set(series)) != len(series):
        raise RegistryError("metric exposition is not exactly 1,449 unique series")
    for item in series:
        label_names = {name for name, _value in item.labels}
        if not label_names <= ALLOWED_LABEL_NAMES or label_names & FORBIDDEN_LABEL_NAMES:
            raise RegistryError(f"metric {item.name} has a forbidden label")
        values = dict(item.labels)
        pair = values.get("route"), values.get("provider")
        if values.get("route") is not None and pair not in VALID_ROUTE_PROVIDER_PAIRS:
            raise RegistryError(f"metric {item.name} has an illegal route/provider pair")
        if "stage" in values and values["stage"] not in STAGES:
            raise RegistryError(f"metric {item.name} has an illegal stage")
        if "capability" in values and values["capability"] not in CAPABILITIES:
            raise RegistryError(f"metric {item.name} has an illegal capability")
        if "request_class" in values and values["request_class"] not in REQUEST_CLASSES:
            raise RegistryError(f"metric {item.name} has an illegal request class")
        if "outcome" in values and values["outcome"] not in TERMINAL_OUTCOMES:
            raise RegistryError(f"metric {item.name} has an illegal outcome")
        if "le" in values and values["le"] not in HISTOGRAM_BUCKET_LABELS:
            raise RegistryError(f"metric {item.name} has an illegal histogram bucket")
    return series


@dataclass(frozen=True, slots=True)
class TaskAttribution:
    stage: Stage
    capability: Capability
    route: str
    provider: str

    def __post_init__(self) -> None:
        if (self.route, self.provider) not in VALID_ROUTE_PROVIDER_PAIRS:
            raise ValueError("illegal route/provider pair")


@dataclass(frozen=True, slots=True)
class RouteResolution:
    attribution: tuple[str, str] | None
    pretransport_reason: PretransportReason | None


def resolve_route(
    *,
    use_proxy: bool,
    configured_provider: str,
    proxy_acquired: bool,
    proxy_required: bool = False,
) -> RouteResolution:
    """Resolve actual current routing without turning optional proxy use mandatory."""

    if configured_provider == "none":
        return RouteResolution(("direct", "direct"), None)
    if configured_provider != "static-proxy":
        return RouteResolution(None, PretransportReason.UNKNOWN_PROVIDER)
    if not use_proxy:
        return RouteResolution(("direct", "direct"), None)
    if proxy_acquired:
        return RouteResolution(("proxy", "static-proxy"), None)
    if proxy_required:
        return RouteResolution(None, PretransportReason.REQUIRED_PROXY_UNAVAILABLE)
    return RouteResolution(None, PretransportReason.REQUIRED_PROXY_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    capability: Capability | None
    pretransport_reason: PretransportReason | None


def resolve_capability(value: str) -> CapabilityResolution:
    """Resolve a registry capability without creating an arbitrary label value."""

    try:
        return CapabilityResolution(Capability(value), None)
    except ValueError:
        return CapabilityResolution(None, PretransportReason.UNKNOWN_CAPABILITY)


@dataclass(frozen=True, slots=True)
class RequestDeclaration:
    task_id: str
    explicitly_warmup: bool = False
    initial_navigation: bool = False


@dataclass(frozen=True, slots=True)
class RequestKey:
    browser_generation: int
    session_id: str
    request_id: str
    redirect_hop: int


@dataclass(slots=True)
class _RequestRecord:
    key: RequestKey
    task_id: str
    target_id: str
    target_type: str
    attribution: TaskAttribution
    request_class: RequestClass
    observer_complete: bool
    encoded_bytes: int = 0
    seen_data_events: set[str] = field(default_factory=set)
    seen_terminal_events: set[str] = field(default_factory=set)
    suppressed_non_transport: bool = False
    terminal: TerminalOutcome | None = None


@dataclass(frozen=True, slots=True)
class TerminalRecord:
    key: RequestKey
    attribution: TaskAttribution
    request_class: RequestClass
    outcome: TerminalOutcome
    encoded_bytes: int
    observer_complete: bool
    target_type: str


def classify_request(*, explicitly_warmup: bool, initial_navigation: bool) -> RequestClass:
    if explicitly_warmup:
        return RequestClass.WARMUP
    if initial_navigation:
        return RequestClass.MAIN
    return RequestClass.SUBRESOURCE


class BrowserTransportTracker:
    """Pure idempotent request lifecycle FSM; identifiers never become labels."""

    def __init__(self, *, browser_generation: int = 0) -> None:
        if (
            not isinstance(browser_generation, int)
            or isinstance(browser_generation, bool)
            or browser_generation < 0
        ):
            raise ValueError("browser_generation must be a nonnegative integer")
        self._browser_generation = browser_generation
        self._tasks: dict[str, TaskAttribution] = {}
        self._accepted = Counter[TaskAttribution]()
        self._pretransport = Counter[tuple[Stage, PretransportReason]]()
        self._instrumentation = Counter[tuple[Stage, InstrumentationReason]]()
        self._records: dict[RequestKey, _RequestRecord] = {}
        self._current: dict[tuple[str, str], RequestKey] = {}
        self._terminals: list[TerminalRecord] = []
        self._request_event_keys: dict[tuple[str, str], RequestKey] = {}
        self._seen_rejected_request_events: set[tuple[str, str]] = set()
        self._rejected_current: set[tuple[str, str]] = set()
        self._session_targets: dict[str, tuple[str, str]] = {}
        self._network_enabled: set[str] = set()
        self._admission_frozen = False

    @property
    def live_count(self) -> int:
        return sum(
            record.terminal is None and not record.suppressed_non_transport
            for record in self._records.values()
        )

    @property
    def non_transport_count(self) -> int:
        return sum(record.suppressed_non_transport for record in self._records.values())

    @property
    def terminals(self) -> tuple[TerminalRecord, ...]:
        return tuple(self._terminals)

    @property
    def instrumentation_counts(self) -> Mapping[tuple[Stage, InstrumentationReason], int]:
        return MappingProxyType(dict(self._instrumentation))

    @property
    def pretransport_counts(self) -> Mapping[tuple[Stage, PretransportReason], int]:
        return MappingProxyType(dict(self._pretransport))

    @property
    def accepted_task_counts(self) -> Mapping[TaskAttribution, int]:
        return MappingProxyType(dict(self._accepted))

    def instrument(self, stage: Stage, reason: InstrumentationReason) -> None:
        self._instrumentation[(stage, reason)] += 1

    def record_pretransport(self, stage: Stage, reason: PretransportReason) -> None:
        self._pretransport[(stage, reason)] += 1

    def accept_task(self, task_id: str, attribution: TaskAttribution) -> bool:
        if self._admission_frozen:
            self.instrument(attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return False
        existing = self._tasks.get(task_id)
        if existing is None:
            self._tasks[task_id] = attribution
            self._accepted[attribution] += 1
            return True
        if existing == attribution:
            return False
        self.instrument(attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
        return False

    def observer_target_attached(self, session_id: str, target_id: str, target_type: str) -> None:
        existing = self._session_targets.get(session_id)
        current = (target_id, target_type)
        if existing is not None and existing != current:
            for stage in Stage:
                self.instrument(stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        self._session_targets[session_id] = current

    def observer_network_enabled(self, session_id: str) -> None:
        if session_id not in self._session_targets:
            for stage in Stage:
                self.instrument(stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        self._network_enabled.add(session_id)

    def request_will_be_sent(
        self,
        *,
        session_id: str,
        target_id: str,
        request_id: str,
        event_fingerprint: str,
        declaration: RequestDeclaration | None,
        redirect_response: bool,
        stage: Stage | None = None,
    ) -> RequestKey | None:
        dedupe = (request_id, event_fingerprint)
        replay_key = self._request_event_keys.get(dedupe)
        if replay_key is not None:
            if replay_key.session_id != session_id:
                self._current[(session_id, request_id)] = replay_key
            return replay_key
        if dedupe in self._seen_rejected_request_events:
            return None
        if self._admission_frozen:
            self._seen_rejected_request_events.add(dedupe)
            self._rejected_current.add((session_id, request_id))
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return None
        if declaration is None:
            self._seen_rejected_request_events.add(dedupe)
            self._rejected_current.add((session_id, request_id))
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(
                    affected_stage,
                    InstrumentationReason.CLASSIFICATION_MISSING,
                )
            return None
        attribution = self._tasks.get(declaration.task_id)
        if attribution is None:
            self._seen_rejected_request_events.add(dedupe)
            self._rejected_current.add((session_id, request_id))
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.UNATTRIBUTED_TASK)
            return None
        current_slot = (session_id, request_id)
        previous_key = self._current.get(current_slot)
        if redirect_response:
            if previous_key is None:
                self.instrument(attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
                return None
            previous = self._records[previous_key]
            if previous.terminal is not None or previous.suppressed_non_transport:
                self.instrument(attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
                return None
            previous.request_class = RequestClass.REDIRECT
            self._terminalize(previous, TerminalOutcome.COMPLETE_RESPONSE)
            hop = previous_key.redirect_hop + 1
        elif previous_key is not None:
            self.instrument(attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return previous_key
        else:
            hop = 0
        observed_target = self._session_targets.get(session_id)
        observer_complete = (
            session_id in self._network_enabled
            and observed_target is not None
            and observed_target[0] == target_id
            and observed_target[1] in PUBLIC_CDP_TARGET_TYPES
        )
        if not observer_complete:
            self.instrument(attribution.stage, InstrumentationReason.BYTE_LIFECYCLE_MISSING)
        key = RequestKey(self._browser_generation, session_id, request_id, hop)
        record = _RequestRecord(
            key=key,
            task_id=declaration.task_id,
            target_id=target_id,
            target_type=observed_target[1] if observed_target is not None else "unknown",
            attribution=attribution,
            request_class=classify_request(
                explicitly_warmup=declaration.explicitly_warmup,
                initial_navigation=declaration.initial_navigation,
            ),
            observer_complete=observer_complete,
        )
        self._records[key] = record
        self._current[current_slot] = key
        self._request_event_keys[dedupe] = key
        return key

    def associate_replayed_tail(
        self,
        session_id: str,
        request_id: str,
        *,
        stage: Stage | None = None,
    ) -> bool | None:
        """Bind a sparse reparented tail iff its canonical request is unambiguous.

        Chromium can declare an iframe or worker bootstrap request on its parent
        session, then emit the response tail on the newly attached child
        session. A tail cannot declare a new attempt. It may therefore inherit
        the one current key with the same Chromium request id; multiple matches
        remain ambiguous and must fail closed.
        """

        slot = (session_id, request_id)
        if slot in self._current or slot in self._rejected_current:
            return True
        candidates = {
            key
            for (_candidate_session, candidate_request_id), key in self._current.items()
            if candidate_request_id == request_id
        }
        if len(candidates) == 1:
            self._current[slot] = next(iter(candidates))
            return True
        if candidates:
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return False
        return None

    def data_received(
        self,
        *,
        session_id: str,
        request_id: str,
        encoded_data_length: object,
        event_fingerprint: str,
        stage: Stage | None = None,
    ) -> None:
        record = self._find_current(session_id, request_id)
        if record is None:
            if (session_id, request_id) in self._rejected_current:
                return
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        if event_fingerprint in record.seen_data_events:
            return
        record.seen_data_events.add(event_fingerprint)
        if (
            not isinstance(encoded_data_length, int)
            or isinstance(encoded_data_length, bool)
            or encoded_data_length < 0
        ):
            self.instrument(record.attribution.stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        if record.suppressed_non_transport:
            if encoded_data_length > 0:
                self.instrument(
                    record.attribution.stage,
                    InstrumentationReason.LIFECYCLE_CONFLICT,
                )
            return
        if record.terminal is not None:
            self.instrument(record.attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        record.encoded_bytes += encoded_data_length

    def loading_finished(
        self,
        *,
        session_id: str,
        request_id: str,
        event_fingerprint: str,
        stage: Stage | None = None,
    ) -> None:
        record = self._find_current(session_id, request_id)
        if record is None:
            if (session_id, request_id) in self._rejected_current:
                return
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        if event_fingerprint in record.seen_terminal_events:
            return
        record.seen_terminal_events.add(event_fingerprint)
        if record.suppressed_non_transport:
            return
        self._terminalize(record, TerminalOutcome.COMPLETE_RESPONSE)

    def loading_failed(
        self,
        *,
        session_id: str,
        request_id: str,
        event_fingerprint: str,
        cancelled: bool = False,
        policy_rejected: bool = False,
        stage: Stage | None = None,
    ) -> None:
        record = self._find_current(session_id, request_id)
        if record is None:
            if (session_id, request_id) in self._rejected_current:
                return
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        if event_fingerprint in record.seen_terminal_events:
            return
        record.seen_terminal_events.add(event_fingerprint)
        if record.suppressed_non_transport:
            return
        desired = (
            TerminalOutcome.POLICY_REJECTED
            if policy_rejected
            else TerminalOutcome.CANCELLED
            if cancelled
            else TerminalOutcome.TRANSPORT_FAILURE
        )
        self._terminalize(record, desired)

    def target_closed(self, *, session_id: str | None = None, target_id: str | None = None) -> None:
        if session_id is None and target_id is None:
            return
        for record in tuple(self._records.values()):
            if (
                (
                    (session_id is not None and record.key.session_id == session_id)
                    or (session_id is None and record.target_id == target_id)
                )
                and record.terminal is None
                and not record.suppressed_non_transport
            ):
                self._terminalize(record, TerminalOutcome.TARGET_CLOSED)

    def mark_non_transport(
        self,
        *,
        session_id: str,
        request_id: str,
        stage: Stage | None = None,
    ) -> None:
        """Suppress a cache/service-worker wrapper that made no transport attempt."""

        record = self._find_current(session_id, request_id)
        if record is None:
            if (session_id, request_id) in self._rejected_current:
                return
            for affected_stage in Stage if stage is None else (stage,):
                self.instrument(affected_stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        if record.suppressed_non_transport:
            return
        if record.terminal is not None or record.encoded_bytes > 0:
            self.instrument(record.attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
        record.suppressed_non_transport = True

    def freeze_new_admission(self) -> None:
        self._admission_frozen = True

    def terminalize_live(
        self,
        *,
        stage: Stage | None = None,
        desired: TerminalOutcome = TerminalOutcome.CANCELLED,
    ) -> None:
        for record in tuple(self._records.values()):
            if (
                record.terminal is None
                and not record.suppressed_non_transport
                and (stage is None or record.attribution.stage is stage)
            ):
                self._terminalize(record, desired)

    def observer_transport_lost(self, stage: Stage) -> None:
        self.instrument(stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        self.terminalize_live(stage=stage, desired=TerminalOutcome.TRANSPORT_FAILURE)

    def aggregate_rows(self) -> dict[tuple[str, ...], dict[str, Any]]:
        rows: dict[tuple[str, ...], dict[str, Any]] = {}
        for labels in _request_label_sets():
            key = tuple(labels[name] for name in REQUEST_LABELS)
            rows[key] = {
                "attempts": 0,
                "outcomes": {outcome: 0 for outcome in TERMINAL_OUTCOMES},
                "transferred_bytes": 0,
                "response_sizes": [],
            }
        for terminal in self._terminals:
            attr = terminal.attribution
            key = (
                attr.stage.value,
                "browser",
                "chromium",
                terminal.request_class.value,
                attr.route,
                attr.provider,
                attr.capability.value,
            )
            row = rows[key]
            row["attempts"] += 1
            row["outcomes"][terminal.outcome.value] += 1
            row["transferred_bytes"] += terminal.encoded_bytes
            if terminal.outcome in {
                TerminalOutcome.COMPLETE_RESPONSE,
                TerminalOutcome.PARTIAL_RESPONSE,
            }:
                row["response_sizes"].append(terminal.encoded_bytes)
        return rows

    def _find_current(self, session_id: str, request_id: str) -> _RequestRecord | None:
        key = self._current.get((session_id, request_id))
        return self._records.get(key) if key is not None else None

    def _terminalize(self, record: _RequestRecord, desired: TerminalOutcome) -> None:
        if record.terminal is not None:
            if record.terminal != desired and not (
                record.terminal is TerminalOutcome.PARTIAL_RESPONSE and record.encoded_bytes > 0
            ):
                self.instrument(record.attribution.stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        outcome = (
            TerminalOutcome.PARTIAL_RESPONSE
            if record.encoded_bytes > 0 and desired is not TerminalOutcome.COMPLETE_RESPONSE
            else desired
        )
        record.terminal = outcome
        self._terminals.append(
            TerminalRecord(
                key=record.key,
                attribution=record.attribution,
                request_class=record.request_class,
                outcome=outcome,
                encoded_bytes=record.encoded_bytes,
                observer_complete=record.observer_complete,
                target_type=record.target_type,
            )
        )


type RequestClassifier = Callable[[str, str, Mapping[str, Any]], RequestDeclaration | None]

LOOPBACK_CDP_ADDRESS = "127.0.0.1"
CDP_SETUP_TIMEOUT_SECONDS = 5.0
CDP_TARGET_FILTER = (
    {"type": "page"},
    {"type": "iframe"},
    {"type": "worker"},
    {"type": "shared_worker"},
    {"type": "service_worker"},
    {"exclude": True},
)
ACCOUNTING_NETWORK_METHODS = frozenset(
    {
        "Network.requestWillBeSent",
        "Network.dataReceived",
        "Network.loadingFinished",
        "Network.loadingFailed",
    }
)
CORRELATION_NETWORK_METHODS = frozenset(
    {"Network.requestServedFromCache", "Network.responseReceived"}
)


class RawCDPWebSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


type RawCDPConnector = Callable[[str, float], Awaitable[RawCDPWebSocket]]


def _validate_debug_port(debug_port: int) -> int:
    if (
        not isinstance(debug_port, int)
        or isinstance(debug_port, bool)
        or not 1 <= debug_port <= 65_535
    ):
        raise ValueError("debug_port must be an integer from 1 through 65535")
    return debug_port


def reserve_loopback_debug_port() -> int:
    """Reserve an ephemeral loopback port for the immediately following launch."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((LOOPBACK_CDP_ADDRESS, 0))
        listener.listen(1)
        return cast(int, listener.getsockname()[1])


def chromium_loopback_debug_args(debug_port: int) -> tuple[str, str]:
    """Return the only permitted Chromium remote-debugging launch flags."""

    port = _validate_debug_port(debug_port)
    return (
        f"--remote-debugging-address={LOOPBACK_CDP_ADDRESS}",
        f"--remote-debugging-port={port}",
    )


def _validate_loopback_websocket_url(value: object, debug_port: int) -> str:
    if not isinstance(value, str):
        raise ValueError("CDP endpoint omitted its WebSocket URL")
    parsed = urlsplit(value)
    browser_path = parsed.path.removeprefix("/devtools/browser/")
    if (
        parsed.scheme != "ws"
        or parsed.hostname != LOOPBACK_CDP_ADDRESS
        or parsed.port != debug_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not browser_path
        or "/" in browser_path
    ):
        raise ValueError("CDP WebSocket endpoint is outside the bounded loopback origin")
    return value


async def _fetch_loopback_websocket_url(debug_port: int, timeout: float) -> str:
    port = _validate_debug_port(debug_port)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    endpoint = f"http://{LOOPBACK_CDP_ADDRESS}:{port}/json/version"
    last_error: Exception | None = None
    async with httpx.AsyncClient(trust_env=False, timeout=min(timeout, 1.0)) as client:
        while loop.time() < deadline:
            try:
                response = await client.get(endpoint)
                response.raise_for_status()
                document = response.json()
                if not isinstance(document, dict):
                    raise ValueError("CDP version endpoint did not return an object")
                return _validate_loopback_websocket_url(
                    document.get("webSocketDebuggerUrl"),
                    port,
                )
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.05, remaining))
    raise TimeoutError("loopback CDP endpoint did not become ready") from last_error


async def _connect_raw_cdp(url: str, timeout: float) -> RawCDPWebSocket:
    return await connect_websocket(
        url,
        open_timeout=timeout,
        compression=None,
        ping_interval=None,
        max_size=16 * 1024 * 1024,
    )


def _auto_attach_params(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "autoAttach": enabled,
        "waitForDebuggerOnStart": enabled,
        "flatten": True,
        "filter": [dict(item) for item in CDP_TARGET_FILTER],
    }


def _network_event_fingerprint(method: str, params: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"method": method, "params": dict(params)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class ChromiumAutoAttachObserver:
    """Bounded raw-CDP observer for a normally launched Playwright Chromium.

    The controller uses Chromium's public loopback DevTools WebSocket in
    parallel with Playwright's native connection. It never reads or patches a
    Playwright connection, channel, session, or transport object.
    """

    def __init__(
        self,
        websocket: RawCDPWebSocket,
        tracker: BrowserTransportTracker,
        classifier: RequestClassifier,
        *,
        stage: Stage,
    ) -> None:
        self._websocket = websocket
        self._tracker = tracker
        self._classifier = classifier
        self._stage = stage
        self._initialization_tasks: set[asyncio.Task[None]] = set()
        self._target_by_session: dict[str, tuple[str, str]] = {}
        self._session_by_target: dict[str, str] = {}
        self._ignored_duplicate_sessions: set[str] = set()
        self._network_ready_sessions: set[str] = set()
        self._resumed_sessions: set[str] = set()
        self._preboundary_request_slots: set[tuple[str, str]] = set()
        self._pending_commands: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._pending_command_details: dict[int, tuple[str, str | None]] = {}
        self._command_id = 0
        self._send_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._envelope_generation = 0
        self._task_generation = 0
        self._setup_trace: list[tuple[str, str, str, str]] = []
        self._ready = False
        self._draining = False
        self._closing = False
        self._closed = False
        self._reader_failed = False

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    @property
    def observed_target_types(self) -> frozenset[str]:
        return frozenset(
            target_type for _target_id, target_type in self._target_by_session.values()
        )

    @property
    def setup_trace(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(self._setup_trace)

    @property
    def reader_done(self) -> bool:
        return self._reader_task is None or self._reader_task.done()

    @classmethod
    async def attach(
        cls,
        debug_port: int,
        tracker: BrowserTransportTracker,
        classifier: RequestClassifier,
        *,
        stage: Stage,
        setup_timeout: float = CDP_SETUP_TIMEOUT_SECONDS,
        connector: RawCDPConnector = _connect_raw_cdp,
    ) -> ChromiumAutoAttachObserver | None:
        websocket: RawCDPWebSocket | None = None
        observer: ChromiumAutoAttachObserver | None = None
        try:
            if setup_timeout <= 0:
                raise ValueError("setup_timeout must be positive")
            websocket_url = await _fetch_loopback_websocket_url(debug_port, setup_timeout)
            websocket = await connector(websocket_url, setup_timeout)
            observer = cls(websocket, tracker, classifier, stage=stage)
            async with asyncio.timeout(setup_timeout):
                await observer._start()
            return observer
        except asyncio.CancelledError:
            if observer is not None:
                await observer._close_transport()
            elif websocket is not None:
                await websocket.close()
            raise
        except Exception:
            tracker.instrument(stage, InstrumentationReason.OBSERVER_ATTACH_FAILED)
            if observer is not None:
                await observer._close_transport()
            elif websocket is not None:
                try:
                    await websocket.close()
                except Exception:
                    tracker.instrument(stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return None

    @classmethod
    async def attach_websocket(
        cls,
        websocket: RawCDPWebSocket,
        tracker: BrowserTransportTracker,
        classifier: RequestClassifier,
        *,
        stage: Stage,
        setup_timeout: float = CDP_SETUP_TIMEOUT_SECONDS,
    ) -> ChromiumAutoAttachObserver | None:
        """Attach an already-open raw transport for deterministic conformance tests."""

        observer = cls(websocket, tracker, classifier, stage=stage)
        try:
            async with asyncio.timeout(setup_timeout):
                await observer._start()
            return observer
        except asyncio.CancelledError:
            await observer._close_transport()
            raise
        except Exception:
            tracker.instrument(stage, InstrumentationReason.OBSERVER_ATTACH_FAILED)
            await observer._close_transport()
            return None

    async def _start(self) -> None:
        self._reader_task = asyncio.create_task(self._reader_loop())
        await self._send_command("Target.setAutoAttach", _auto_attach_params())
        await self._send_command("Target.getTargets", {})
        deadline = asyncio.get_running_loop().time() + CDP_SETUP_TIMEOUT_SECONDS
        await self._await_stable_initialization(deadline)
        if self._reader_failed:
            raise RuntimeError("raw CDP reader failed during setup")
        self._ready = True

    def _spawn_initialization(self, params: Mapping[str, Any]) -> None:
        if self._closing:
            return
        task = asyncio.create_task(self._initialize_target(params))
        self._task_generation += 1
        self._initialization_tasks.add(task)
        task.add_done_callback(self._initialization_done)

    def _initialization_done(self, task: asyncio.Task[None]) -> None:
        self._initialization_tasks.discard(task)
        self._task_generation += 1
        if task.cancelled():
            return
        if task.exception() is not None:
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)

    async def _reader_loop(self) -> None:
        try:
            while True:
                raw_message = await self._websocket.recv()
                self._envelope_generation += 1
                self._dispatch_envelope(raw_message)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                self._reader_failed = True
                self._tracker.observer_transport_lost(self._stage)
        finally:
            failure = RuntimeError("raw CDP reader stopped")
            for future in tuple(self._pending_commands.values()):
                if not future.done():
                    future.set_exception(failure)

    def _dispatch_envelope(self, raw_message: str | bytes) -> None:
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            message = json.loads(raw_message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        if not isinstance(message, dict):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        command_id = message.get("id")
        if isinstance(command_id, int) and not isinstance(command_id, bool):
            future = self._pending_commands.pop(command_id, None)
            command_details = self._pending_command_details.pop(command_id, None)
            if future is None or future.done():
                self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
                return
            if "error" in message:
                future.set_exception(RuntimeError("raw CDP command failed"))
            else:
                result = message.get("result", {})
                if not isinstance(result, dict):
                    future.set_exception(RuntimeError("raw CDP response result was malformed"))
                else:
                    if command_details is not None:
                        method, response_session_id = command_details
                        if method == "Network.enable" and response_session_id is not None:
                            self._network_ready_sessions.add(response_session_id)
                            self._tracker.observer_network_enabled(response_session_id)
                        elif (
                            method == "Runtime.runIfWaitingForDebugger"
                            and response_session_id is not None
                        ):
                            self._resumed_sessions.add(response_session_id)
                    future.set_result(result)
            return
        method = message.get("method")
        params = message.get("params", {})
        session_id = message.get("sessionId")
        if not isinstance(method, str) or not isinstance(params, dict):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        if method == "Target.attachedToTarget":
            self._spawn_initialization(params)
        elif method == "Target.detachedFromTarget":
            detached_session = params.get("sessionId")
            if isinstance(detached_session, str):
                self._ignored_duplicate_sessions.discard(detached_session)
                self._tracker.target_closed(session_id=detached_session)
                detached_target = self._target_by_session.pop(detached_session, None)
                if (
                    detached_target is not None
                    and self._session_by_target.get(detached_target[0]) == detached_session
                ):
                    self._session_by_target.pop(detached_target[0], None)
                self._network_ready_sessions.discard(detached_session)
                self._resumed_sessions.discard(detached_session)
                self._preboundary_request_slots = {
                    slot for slot in self._preboundary_request_slots if slot[0] != detached_session
                }
            else:
                self._tracker.instrument(
                    self._stage,
                    InstrumentationReason.OBSERVER_PROTOCOL_ERROR,
                )
        elif method.startswith("Network."):
            self._handle_network_event(session_id, method, params)

    async def _initialize_target(self, params: Mapping[str, Any]) -> None:
        session_id = params.get("sessionId")
        target_info = params.get("targetInfo")
        if not isinstance(session_id, str) or not isinstance(target_info, dict):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        target_id = target_info.get("targetId")
        target_type = target_info.get("type")
        if not isinstance(target_id, str) or not isinstance(target_type, str):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        existing = self._target_by_session.get(session_id)
        if existing is not None:
            if existing != (target_id, target_type):
                self._tracker.instrument(self._stage, InstrumentationReason.LIFECYCLE_CONFLICT)
            return
        owner_session = self._session_by_target.get(target_id)
        if owner_session is not None and owner_session != session_id:
            self._ignored_duplicate_sessions.add(session_id)
            with suppress(Exception):
                await self._send_command(
                    "Runtime.runIfWaitingForDebugger",
                    {},
                    session_id=session_id,
                )
            with suppress(Exception):
                await self._send_command(
                    "Target.detachFromTarget",
                    {"sessionId": session_id},
                )
            return
        self._session_by_target[target_id] = session_id
        self._target_by_session[session_id] = (target_id, target_type)
        self._tracker.observer_target_attached(session_id, target_id, target_type)
        if target_type not in PUBLIC_CDP_TARGET_TYPES:
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            with suppress(Exception):
                await self._send_command(
                    "Runtime.runIfWaitingForDebugger",
                    {},
                    session_id=session_id,
                )
            return
        try:
            await self._send_command(
                "Target.setAutoAttach",
                _auto_attach_params(),
                session_id=session_id,
            )
            self._setup_trace.append((session_id, target_id, target_type, "Target.setAutoAttach"))
            await self._send_command("Network.enable", {}, session_id=session_id)
            self._setup_trace.append((session_id, target_id, target_type, "Network.enable"))
            await self._send_command(
                "Runtime.runIfWaitingForDebugger",
                {},
                session_id=session_id,
            )
            self._setup_trace.append(
                (session_id, target_id, target_type, "Runtime.runIfWaitingForDebugger")
            )
        except Exception:
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)

    async def _send_command(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._closing:
            raise RuntimeError("raw CDP controller is closing")
        self._command_id += 1
        command_id = self._command_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_commands[command_id] = future
        self._pending_command_details[command_id] = (method, session_id)
        envelope: dict[str, Any] = {
            "id": command_id,
            "method": method,
            "params": dict(params),
        }
        if session_id is not None:
            envelope["sessionId"] = session_id
        message = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        try:
            async with self._send_lock:
                await self._websocket.send(message)
            return await future
        finally:
            self._pending_commands.pop(command_id, None)
            self._pending_command_details.pop(command_id, None)

    def _handle_network_event(
        self, session_id: object, method: str, params: Mapping[str, Any]
    ) -> None:
        if method not in ACCOUNTING_NETWORK_METHODS | CORRELATION_NETWORK_METHODS:
            return
        if not isinstance(session_id, str):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        if session_id in self._ignored_duplicate_sessions:
            return
        target = self._target_by_session.get(session_id)
        request_id = params.get("requestId")
        if target is None or not isinstance(request_id, str):
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
            return
        target_id, target_type = target
        if target_type not in PUBLIC_CDP_TARGET_TYPES:
            return
        slot = (session_id, request_id)
        if session_id not in self._network_ready_sessions:
            # Remember request ids exposed while Network.enable is in flight so
            # their later sparse tails cannot masquerade as covered attempts.
            self._preboundary_request_slots.add(slot)
            return
        if slot in self._preboundary_request_slots:
            if method in {"Network.loadingFinished", "Network.loadingFailed"}:
                self._preboundary_request_slots.discard(slot)
            return
        fingerprint = _network_event_fingerprint(method, params)
        if method != "Network.requestWillBeSent":
            association = self._tracker.associate_replayed_tail(
                session_id,
                request_id,
                stage=self._stage,
            )
            if association is False:
                return
            if association is None and session_id not in self._resumed_sessions:
                # A target's bootstrap request can begin before Network is
                # enabled and expose only a sparse tail while it is paused.
                return
            if association is None and method == "Network.responseReceived":
                self._tracker.instrument(
                    self._stage,
                    InstrumentationReason.LIFECYCLE_CONFLICT,
                )
                return
        if method == "Network.requestServedFromCache":
            self._tracker.mark_non_transport(
                session_id=session_id,
                request_id=request_id,
                stage=self._stage,
            )
            return
        if method == "Network.responseReceived":
            response = params.get("response")
            if not isinstance(response, dict):
                self._tracker.instrument(
                    self._stage,
                    InstrumentationReason.OBSERVER_PROTOCOL_ERROR,
                )
                return
            if any(
                response.get(name) is True
                for name in ("fromDiskCache", "fromPrefetchCache", "fromServiceWorker")
            ):
                self._tracker.mark_non_transport(
                    session_id=session_id,
                    request_id=request_id,
                    stage=self._stage,
                )
            return
        if method == "Network.requestWillBeSent":
            try:
                declaration = self._classifier(target_id, target_type, params)
            except Exception:
                self._tracker.instrument(self._stage, InstrumentationReason.CLASSIFICATION_MISSING)
                return
            self._tracker.request_will_be_sent(
                session_id=session_id,
                target_id=target_id,
                request_id=request_id,
                event_fingerprint=fingerprint,
                declaration=declaration,
                redirect_response=isinstance(params.get("redirectResponse"), dict),
                stage=self._stage,
            )
            return
        if method == "Network.dataReceived":
            self._tracker.data_received(
                session_id=session_id,
                request_id=request_id,
                encoded_data_length=params.get("encodedDataLength"),
                event_fingerprint=fingerprint,
                stage=self._stage,
            )
        elif method == "Network.loadingFinished":
            self._tracker.loading_finished(
                session_id=session_id,
                request_id=request_id,
                event_fingerprint=fingerprint,
                stage=self._stage,
            )
        elif method == "Network.loadingFailed":
            self._tracker.loading_failed(
                session_id=session_id,
                request_id=request_id,
                event_fingerprint=fingerprint,
                cancelled=params.get("canceled") is True,
                policy_rejected=isinstance(params.get("blockedReason"), str),
                stage=self._stage,
            )

    async def _await_stable_initialization(self, deadline: float) -> None:
        stable_observations = 0
        previous: tuple[int, int] | None = None
        loop = asyncio.get_running_loop()
        while stable_observations < 2:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("raw CDP target initialization did not drain")
            pending = tuple(self._initialization_tasks)
            if pending:
                done, _still_pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise TimeoutError("raw CDP target initialization did not drain")
                stable_observations = 0
                previous = None
                continue
            state = (self._envelope_generation, self._task_generation)
            await asyncio.sleep(0)
            if self._initialization_tasks:
                stable_observations = 0
                previous = None
                continue
            current = (self._envelope_generation, self._task_generation)
            if current == state == previous:
                stable_observations += 1
            else:
                stable_observations = 1 if current == state else 0
            previous = current

    async def drain_and_detach(self) -> None:
        """Drain under one deadline and close raw sessions, even if cancelled."""

        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_impl())
        cancellation: asyncio.CancelledError | None = None
        while not self._drain_task.done():
            try:
                await asyncio.shield(self._drain_task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
        self._drain_task.result()
        if cancellation is not None:
            raise cancellation

    async def _drain_impl(self) -> None:
        if self._closed:
            return
        self._draining = True
        self._tracker.freeze_new_admission()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DRAIN_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                await self._send_command("Target.getTargets", {})
                await self._await_stable_initialization(deadline)
                self._tracker.terminalize_live(stage=self._stage)
                await self._send_command("Target.getTargets", {})
                await self._await_stable_initialization(deadline)
        except TimeoutError:
            self._tracker.instrument(self._stage, InstrumentationReason.DRAIN_TIMEOUT)
        except Exception:
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        finally:
            self._tracker.terminalize_live(stage=self._stage)
            await self._close_transport()
            self._closed = True
            self._draining = False
            self._ready = False

    async def _close_transport(self) -> None:
        if self._closing:
            reader = self._reader_task
            if reader is not None and reader is not asyncio.current_task():
                await asyncio.gather(reader, return_exceptions=True)
            return
        self._closing = True
        for task in tuple(self._initialization_tasks):
            task.cancel()
        if self._initialization_tasks:
            await asyncio.gather(*tuple(self._initialization_tasks), return_exceptions=True)
        for future in tuple(self._pending_commands.values()):
            if not future.done():
                future.set_exception(RuntimeError("raw CDP controller closed"))
        try:
            async with asyncio.timeout(1.0):
                await self._websocket.close()
        except Exception:
            self._tracker.instrument(self._stage, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        reader = self._reader_task
        if reader is not None and reader is not asyncio.current_task():
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class StageCapture:
    attempts: int
    outcomes: Mapping[str, int]
    transferred_bytes: int
    histogram_count: int
    histogram_sum: int
    histogram_buckets: Mapping[str, int]
    accepted_tasks: int
    pretransport_events: int


@dataclass(frozen=True, slots=True)
class CaptureValidation:
    registry_digest: str | None
    series_count: int | None
    blockers: Mapping[str, tuple[str, ...]]
    stages: Mapping[str, StageCapture | None]

    @property
    def valid(self) -> bool:
        return not any(self.blockers.values()) and all(
            value is not None for value in self.stages.values()
        )


def _block(blockers: dict[str, set[str]], stages: Iterable[str], reason: str) -> None:
    if reason not in ADAPTER_BLOCKERS:
        raise AssertionError(f"unbounded blocker: {reason}")
    for stage in stages:
        blockers[stage].add(reason)


def _strict_nonnegative(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _row_key(labels: object) -> tuple[str, ...] | None:
    if not isinstance(labels, dict) or set(labels) != set(REQUEST_LABELS):
        return None
    try:
        key = tuple(labels[name] for name in REQUEST_LABELS)
    except KeyError:
        return None
    if not all(isinstance(value, str) for value in key):
        return None
    if (
        key[0] not in STAGES
        or key[1] != "browser"
        or key[2] != "chromium"
        or key[3] not in REQUEST_CLASSES
        or (key[4], key[5]) not in VALID_ROUTE_PROVIDER_PAIRS
        or key[6] not in CAPABILITIES
    ):
        return None
    return key


def _support_key(item: object, reasons: tuple[str, ...]) -> tuple[str, str] | None:
    if not isinstance(item, dict) or set(item) != {"labels", "value"}:
        return None
    labels = item.get("labels")
    if not isinstance(labels, dict) or set(labels) != set(SUPPORT_LABELS):
        return None
    if labels.get("execution_class") != "browser" or labels.get("browser_backend") != "chromium":
        return None
    stage, reason = labels.get("stage"), labels.get("reason")
    if stage not in STAGES or reason not in reasons:
        return None
    return cast(str, stage), cast(str, reason)


def _task_key(item: object) -> tuple[str, str, str, str] | None:
    if not isinstance(item, dict) or set(item) != {"labels", "value"}:
        return None
    labels = item.get("labels")
    if not isinstance(labels, dict) or set(labels) != set(TASK_LABELS):
        return None
    if labels.get("execution_class") != "browser" or labels.get("browser_backend") != "chromium":
        return None
    stage = labels.get("stage")
    route = labels.get("route")
    provider = labels.get("provider")
    capability = labels.get("capability")
    if (
        stage not in STAGES
        or (route, provider) not in VALID_ROUTE_PROVIDER_PAIRS
        or capability not in CAPABILITIES
    ):
        return None
    return cast(str, stage), cast(str, route), cast(str, provider), cast(str, capability)


def _boundary_maps(
    boundary: object,
    blockers: dict[str, set[str]],
) -> (
    tuple[
        dict[tuple[str, ...], dict[str, Any]],
        dict[tuple[str, str, str, str], int],
        dict[tuple[str, str], int],
        dict[tuple[str, str], int],
    ]
    | None
):
    if not isinstance(boundary, dict) or set(boundary) != {
        "rows",
        "accepted_tasks",
        "pretransport",
        "instrumentation",
    }:
        _block(blockers, STAGES, "missing_series")
        return None
    expected_keys = {
        tuple(labels[name] for name in REQUEST_LABELS) for labels in _request_label_sets()
    }
    rows: dict[tuple[str, ...], dict[str, Any]] = {}
    raw_rows = boundary.get("rows")
    if not isinstance(raw_rows, list):
        _block(blockers, STAGES, "missing_series")
        return None
    for raw in raw_rows:
        if not isinstance(raw, dict) or set(raw) != {
            "labels",
            "attempts",
            "outcomes",
            "transferred_bytes",
            "histogram",
        }:
            _block(blockers, STAGES, "illegal_label")
            continue
        key = _row_key(raw.get("labels"))
        if key is None:
            labels = raw.get("labels")
            if (
                isinstance(labels, dict)
                and (labels.get("route"), labels.get("provider")) not in VALID_ROUTE_PROVIDER_PAIRS
            ):
                _block(blockers, STAGES, "illegal_route_provider_pair")
            else:
                _block(blockers, STAGES, "illegal_label")
            continue
        if key in rows:
            _block(blockers, (key[0],), "extra_series")
            continue
        rows[key] = raw
    missing = expected_keys - set(rows)
    extra = set(rows) - expected_keys
    if missing:
        _block(blockers, {key[0] for key in missing}, "missing_series")
    if extra:
        _block(blockers, {key[0] for key in extra}, "extra_series")

    def support_map(
        name: str,
        expected: set[tuple[Any, ...]],
        key_func: Callable[[object], tuple[Any, ...] | None],
    ) -> dict[tuple[Any, ...], int]:
        result: dict[tuple[Any, ...], int] = {}
        raw_items = boundary.get(name)
        if not isinstance(raw_items, list):
            _block(blockers, STAGES, "missing_series")
            return result
        for item in raw_items:
            key = key_func(item)
            if key is None or key in result:
                _block(blockers, STAGES, "illegal_label" if key is None else "extra_series")
                continue
            value = _strict_nonnegative(item.get("value") if isinstance(item, dict) else None)
            if value is None:
                _block(blockers, (str(key[0]),), "fractional_delta")
                continue
            result[key] = value
        absent = expected - set(result)
        if absent:
            _block(blockers, {str(key[0]) for key in absent}, "missing_series")
        if set(result) - expected:
            _block(blockers, STAGES, "extra_series")
        return result

    task_expected = {
        (stage, route, provider, capability)
        for stage in STAGES
        for route, provider in VALID_ROUTE_PROVIDER_PAIRS
        for capability in CAPABILITIES
    }
    pre_expected = {(stage, reason) for stage in STAGES for reason in PRETRANSPORT_REASONS}
    instr_expected = {(stage, reason) for stage in STAGES for reason in INSTRUMENTATION_REASONS}
    accepted = cast(
        dict[tuple[str, str, str, str], int],
        support_map("accepted_tasks", task_expected, _task_key),
    )
    pretransport = cast(
        dict[tuple[str, str], int],
        support_map(
            "pretransport",
            pre_expected,
            lambda item: _support_key(item, PRETRANSPORT_REASONS),
        ),
    )
    instrumentation = cast(
        dict[tuple[str, str], int],
        support_map(
            "instrumentation",
            instr_expected,
            lambda item: _support_key(item, INSTRUMENTATION_REASONS),
        ),
    )
    return rows, accepted, pretransport, instrumentation


def _counter_delta(
    start: object,
    end: object,
    blockers: dict[str, set[str]],
    stage: str,
) -> int | None:
    start_value = _strict_nonnegative(start)
    end_value = _strict_nonnegative(end)
    if start_value is None or end_value is None:
        _block(blockers, (stage,), "fractional_delta")
        return None
    if end_value < start_value:
        _block(blockers, (stage,), "counter_reset")
        return None
    return end_value - start_value


def _event_tape_totals(
    tape: object,
    blockers: dict[str, set[str]],
) -> tuple[Counter[tuple[str, ...]], Counter[tuple[str, ...]], Counter[tuple[str, ...]]] | None:
    if not isinstance(tape, list):
        _block(blockers, STAGES, "event_tape_mismatch")
        return None
    requests: Counter[tuple[str, ...]] = Counter()
    tasks: Counter[tuple[str, ...]] = Counter()
    pretransport: Counter[tuple[str, ...]] = Counter()
    ordinals: set[int] = set()
    terminal_ids: set[str] = set()
    task_ids: dict[str, tuple[str, str, str, str]] = {}
    for index, event in enumerate(tape):
        if not isinstance(event, dict) or event.get("ordinal") != index or index in ordinals:
            _block(blockers, STAGES, "event_tape_mismatch")
            continue
        ordinals.add(index)
        kind = event.get("kind")
        stage = event.get("stage")
        affected = (stage,) if stage in STAGES else STAGES
        if kind == "accepted_task":
            task_id = event.get("task_id")
            route, provider = event.get("route"), event.get("provider")
            capability = event.get("capability")
            if (
                not isinstance(task_id, str)
                or stage not in STAGES
                or (route, provider) not in VALID_ROUTE_PROVIDER_PAIRS
                or capability not in CAPABILITIES
                or task_id in task_ids
            ):
                _block(blockers, affected, "event_tape_mismatch")
                continue
            key = (cast(str, stage), cast(str, route), cast(str, provider), cast(str, capability))
            task_ids[task_id] = key
            tasks[key] += 1
        elif kind == "pretransport":
            reason = event.get("reason")
            if stage not in STAGES or reason not in PRETRANSPORT_REASONS:
                _block(blockers, affected, "event_tape_mismatch")
                continue
            pretransport[(cast(str, stage), cast(str, reason))] += 1
        elif kind == "request_terminal":
            terminal_id = event.get("terminal_id")
            task_id = event.get("task_id")
            task_key = task_ids.get(task_id) if isinstance(task_id, str) else None
            if not isinstance(terminal_id, str) or terminal_id in terminal_ids:
                _block(blockers, affected, "terminal_duplication")
                continue
            terminal_ids.add(terminal_id)
            if task_key is None or stage != task_key[0]:
                _block(blockers, affected, "event_tape_mismatch")
                continue
            route, provider, capability = task_key[1:]
            if (event.get("route"), event.get("provider"), event.get("capability")) != (
                route,
                provider,
                capability,
            ):
                _block(blockers, affected, "event_tape_mismatch")
                continue
            configured_provider = event.get("configured_proxy_provider")
            if configured_provider not in {"none", "static-proxy"}:
                _block(blockers, affected, "event_tape_mismatch")
                continue
            if configured_provider == "none" and (
                route,
                provider,
            ) != ("direct", "direct"):
                _block(blockers, affected, "provider_none_misclassification")
            if (route, provider) == ("proxy", "static-proxy") and configured_provider != (
                "static-proxy"
            ):
                _block(blockers, affected, "event_tape_mismatch")
            request_class = event.get("request_class")
            expected_class = (
                "redirect"
                if event.get("redirect_predecessor") is True
                else "warmup"
                if event.get("explicitly_warmup") is True
                else "main"
                if event.get("initial_navigation") is True
                else "subresource"
            )
            if request_class != expected_class:
                _block(blockers, affected, "event_tape_mismatch")
                continue
            outcome = event.get("outcome")
            encoded_bytes = _strict_nonnegative(event.get("encoded_bytes"))
            if outcome not in TERMINAL_OUTCOMES or encoded_bytes is None:
                _block(blockers, affected, "event_tape_mismatch")
                continue
            if event.get("byte_lifecycle_complete") is not True:
                _block(blockers, affected, "observer_lifecycle_gap")
            if outcome == "partial_response" and encoded_bytes == 0:
                _block(blockers, affected, "conservation_mismatch")
            if outcome not in {"complete_response", "partial_response"} and encoded_bytes != 0:
                _block(blockers, affected, "conservation_mismatch")
            if encoded_bytes > 0 and outcome in {
                "transport_failure",
                "policy_rejected",
                "cancelled",
                "target_closed",
            }:
                _block(blockers, affected, "conservation_mismatch")
            key = (
                cast(str, stage),
                cast(str, request_class),
                route,
                provider,
                capability,
                cast(str, outcome),
                str(encoded_bytes),
            )
            requests[key] += 1
        else:
            _block(blockers, affected, "event_tape_mismatch")
    return requests, tasks, pretransport


def validate_capture_fixture(
    document: object,
    registry: BrowserTransportRegistry | None = None,
) -> CaptureValidation:
    """Fail-closed exact-boundary validation for the standalone 86,400s fixture."""

    blockers: dict[str, set[str]] = {stage: set() for stage in STAGES}
    stages: dict[str, StageCapture | None] = {stage: None for stage in STAGES}
    if registry is None:
        try:
            registry = load_registry()
        except RegistryError:
            _block(blockers, STAGES, "registry_mismatch")
            return CaptureValidation(
                None,
                None,
                MappingProxyType(
                    {stage: tuple(sorted(value)) for stage, value in blockers.items()}
                ),
                MappingProxyType(stages),
            )
    series_count: int | None = None
    try:
        series_count = len(audit_metric_series(registry))
    except RegistryError:
        _block(blockers, STAGES, "registry_mismatch")
    if not isinstance(document, dict) or document.get("schema_version") != CAPTURE_SCHEMA:
        _block(blockers, STAGES, "missing_series")
        document = {}
    metadata = document.get("registry")
    if not isinstance(metadata, dict) or metadata != {
        "version": registry.version,
        "sha256": registry.digest,
    }:
        _block(blockers, STAGES, "registry_mismatch")
    window = document.get("window")
    if not isinstance(window, dict) or window.get("seconds") != 86_400:
        _block(blockers, STAGES, "window_mismatch")
    else:
        start = window.get("start_unix")
        end = window.get("end_unix")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or end - start != 86_400
        ):
            _block(blockers, STAGES, "window_mismatch")
    boundaries = document.get("boundaries")
    parsed_start = parsed_end = None
    if isinstance(boundaries, dict) and set(boundaries) == {"start", "end"}:
        parsed_start = _boundary_maps(boundaries["start"], blockers)
        parsed_end = _boundary_maps(boundaries["end"], blockers)
    else:
        _block(blockers, STAGES, "missing_series")
    tape_totals = _event_tape_totals(document.get("event_tape"), blockers)
    if parsed_start is not None and parsed_end is not None:
        start_rows, start_tasks, start_pre, start_instr = parsed_start
        end_rows, end_tasks, end_pre, end_instr = parsed_end
        event_request_rows: Counter[tuple[str, ...]] = Counter()
        event_tasks: Counter[tuple[str, ...]] = Counter()
        event_pre: Counter[tuple[str, ...]] = Counter()
        if tape_totals is not None:
            event_requests, event_tasks, event_pre = tape_totals
            for event_key, count in event_requests.items():
                stage, request_class, route, provider, capability, outcome, byte_text = event_key
                event_request_rows[
                    (
                        stage,
                        "browser",
                        "chromium",
                        request_class,
                        route,
                        provider,
                        capability,
                        outcome,
                        byte_text,
                    )
                ] += count
        for stage in STAGES:
            total_attempts = 0
            total_outcomes = Counter[str]()
            total_bytes = 0
            total_hist_count = 0
            total_hist_sum = 0
            total_buckets = Counter[str]()
            for key in sorted(key for key in start_rows if key[0] == stage):
                start_row = start_rows[key]
                end_row = end_rows.get(key)
                if end_row is None:
                    continue
                attempts = _counter_delta(
                    start_row["attempts"], end_row["attempts"], blockers, stage
                )
                outcomes_start, outcomes_end = start_row["outcomes"], end_row["outcomes"]
                if (
                    not isinstance(outcomes_start, dict)
                    or not isinstance(outcomes_end, dict)
                    or set(outcomes_start) != set(TERMINAL_OUTCOMES)
                    or set(outcomes_end) != set(TERMINAL_OUTCOMES)
                ):
                    _block(blockers, (stage,), "missing_series")
                    continue
                outcome_deltas: dict[str, int] = {}
                for outcome in TERMINAL_OUTCOMES:
                    delta = _counter_delta(
                        outcomes_start[outcome], outcomes_end[outcome], blockers, stage
                    )
                    if delta is not None:
                        outcome_deltas[outcome] = delta
                bytes_delta = _counter_delta(
                    start_row["transferred_bytes"],
                    end_row["transferred_bytes"],
                    blockers,
                    stage,
                )
                hist_start, hist_end = start_row["histogram"], end_row["histogram"]
                if not isinstance(hist_start, dict) or not isinstance(hist_end, dict):
                    _block(blockers, (stage,), "bucket_mismatch")
                    continue
                expected_hist_keys = {"buckets", "sum", "count"}
                if set(hist_start) != expected_hist_keys or set(hist_end) != expected_hist_keys:
                    _block(blockers, (stage,), "bucket_mismatch")
                    continue
                bucket_start, bucket_end = hist_start["buckets"], hist_end["buckets"]
                expected_bucket_keys = {str(item) for item in HISTOGRAM_BUCKETS}
                if (
                    not isinstance(bucket_start, dict)
                    or not isinstance(bucket_end, dict)
                    or set(bucket_start) != expected_bucket_keys
                    or set(bucket_end) != expected_bucket_keys
                ):
                    _block(blockers, (stage,), "bucket_mismatch")
                    continue
                for raw_buckets, raw_histogram in (
                    (bucket_start, hist_start),
                    (bucket_end, hist_end),
                ):
                    raw_values = [
                        _strict_nonnegative(raw_buckets[str(bucket)])
                        for bucket in HISTOGRAM_BUCKETS
                    ]
                    raw_count = _strict_nonnegative(raw_histogram["count"])
                    if any(value is None for value in raw_values) or raw_count is None:
                        _block(blockers, (stage,), "bucket_mismatch")
                    else:
                        integer_raw_values = cast(list[int], raw_values)
                        if (
                            integer_raw_values != sorted(integer_raw_values)
                            or integer_raw_values[-1] != raw_count
                        ):
                            _block(blockers, (stage,), "bucket_mismatch")
                bucket_deltas: dict[str, int] = {}
                for bucket in HISTOGRAM_BUCKETS:
                    text = str(bucket)
                    delta = _counter_delta(bucket_start[text], bucket_end[text], blockers, stage)
                    if delta is not None:
                        bucket_deltas[text] = delta
                hist_sum = _counter_delta(hist_start["sum"], hist_end["sum"], blockers, stage)
                hist_count = _counter_delta(hist_start["count"], hist_end["count"], blockers, stage)
                if (
                    attempts is None
                    or bytes_delta is None
                    or hist_sum is None
                    or hist_count is None
                    or len(outcome_deltas) != len(TERMINAL_OUTCOMES)
                    or len(bucket_deltas) != len(HISTOGRAM_BUCKETS)
                ):
                    continue
                ordered_buckets = [bucket_deltas[str(item)] for item in HISTOGRAM_BUCKETS]
                if ordered_buckets != sorted(ordered_buckets) or ordered_buckets[-1] != hist_count:
                    _block(blockers, (stage,), "conservation_mismatch")
                if attempts != sum(outcome_deltas.values()):
                    _block(blockers, (stage,), "conservation_mismatch")
                if hist_count != (
                    outcome_deltas["complete_response"] + outcome_deltas["partial_response"]
                ):
                    _block(blockers, (stage,), "conservation_mismatch")
                if hist_sum != bytes_delta:
                    _block(blockers, (stage,), "conservation_mismatch")
                event_attempts = sum(
                    count for event_key, count in event_request_rows.items() if event_key[:7] == key
                )
                if event_attempts != attempts:
                    _block(blockers, (stage,), "event_tape_mismatch")
                for outcome in TERMINAL_OUTCOMES:
                    event_outcome = sum(
                        count
                        for event_key, count in event_request_rows.items()
                        if event_key[:7] == key and event_key[7] == outcome
                    )
                    if event_outcome != outcome_deltas[outcome]:
                        _block(blockers, (stage,), "event_tape_mismatch")
                event_bytes = sum(
                    int(event_key[8]) * count
                    for event_key, count in event_request_rows.items()
                    if event_key[:7] == key
                )
                if event_bytes != bytes_delta:
                    _block(blockers, (stage,), "event_tape_mismatch")
                event_response_sizes = [
                    int(event_key[8])
                    for event_key, count in event_request_rows.items()
                    if event_key[:7] == key
                    and event_key[7] in {"complete_response", "partial_response"}
                    for _ in range(count)
                ]
                for bucket in HISTOGRAM_BUCKETS:
                    expected_bucket_count = (
                        len(event_response_sizes)
                        if bucket == "+Inf"
                        else sum(size <= int(bucket) for size in event_response_sizes)
                    )
                    if bucket_deltas[str(bucket)] != expected_bucket_count:
                        _block(blockers, (stage,), "event_tape_mismatch")
                total_attempts += attempts
                total_outcomes.update(outcome_deltas)
                total_bytes += bytes_delta
                total_hist_count += hist_count
                total_hist_sum += hist_sum
                total_buckets.update(bucket_deltas)
            accepted_tasks = 0
            for key in sorted(key for key in start_tasks if key[0] == stage):
                delta = _counter_delta(start_tasks[key], end_tasks.get(key), blockers, stage)
                if delta is not None:
                    accepted_tasks += delta
                    if event_tasks[key] != delta:
                        _block(blockers, (stage,), "event_tape_mismatch")
            pretransport_events = 0
            for key in sorted(key for key in start_pre if key[0] == stage):
                delta = _counter_delta(start_pre[key], end_pre.get(key), blockers, stage)
                if delta is not None:
                    pretransport_events += delta
                    if event_pre[key] != delta:
                        _block(blockers, (stage,), "event_tape_mismatch")
            for key in sorted(key for key in start_instr if key[0] == stage):
                delta = _counter_delta(start_instr[key], end_instr.get(key), blockers, stage)
                if delta is not None and delta > 0:
                    _block(blockers, (stage,), "instrumentation_failure")
            if not blockers[stage]:
                stages[stage] = StageCapture(
                    attempts=total_attempts,
                    outcomes=MappingProxyType(dict(total_outcomes)),
                    transferred_bytes=total_bytes,
                    histogram_count=total_hist_count,
                    histogram_sum=total_hist_sum,
                    histogram_buckets=MappingProxyType(dict(total_buckets)),
                    accepted_tasks=accepted_tasks,
                    pretransport_events=pretransport_events,
                )
    frozen_blockers = MappingProxyType(
        {stage: tuple(sorted(reasons)) for stage, reasons in blockers.items()}
    )
    return CaptureValidation(
        registry.digest,
        series_count,
        frozen_blockers,
        MappingProxyType(stages),
    )
