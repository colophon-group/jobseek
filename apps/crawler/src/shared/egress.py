"""Bounded runtime-attribution metrics for crawler cost evidence.

The counters in this module deliberately describe only requests that cross the
shared ``httpx`` transport while a worker task has bound an attribution
context.  Browser page/subresource traffic and support-process traffic are not
silently folded into these totals.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from prometheus_client import Counter

EGRESS_STAGES = frozenset({"monitor", "detail"})
EXECUTION_CLASSES = frozenset({"http", "browser"})
EGRESS_ROUTES = frozenset({"direct", "proxy"})
RUNTIME_STAGES = frozenset({"monitor", "scrape"})
RUNTIME_OUTCOMES = {
    "monitor": ("success", "cancelled", "error", "incomplete"),
    "scrape": ("success", "cancelled", "error"),
}
UNKNOWN_CAPABILITY = "_unknown"


origin_attempts_total = Counter(
    "crawler_runtime_origin_attempts_total",
    "SSRF-approved shared-HTTP origin attempts by runtime lane and actual route",
    ["stage", "execution_class", "egress"],
)

origin_outcomes_total = Counter(
    "crawler_runtime_origin_outcomes_total",
    "Shared-HTTP origin attempt outcomes used for conservation checks",
    ["stage", "execution_class", "egress", "outcome"],
)

response_body_bytes_total = Counter(
    "crawler_runtime_response_body_bytes_total",
    "Response-body bytes actually consumed from the shared HTTP transport",
    ["stage", "execution_class", "egress"],
)

capability_executions_total = Counter(
    "crawler_runtime_capability_executions_total",
    "Extraction runtime executions by registry-bounded capability and outcome",
    ["stage", "implementation", "capability", "outcome"],
)


@dataclass(frozen=True, slots=True)
class EgressAttribution:
    stage: str
    execution_class: str


_egress_attribution: contextvars.ContextVar[EgressAttribution | None] = contextvars.ContextVar(
    "runtime_egress_attribution", default=None
)


def _validate_member(value: str, allowed: frozenset[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"unsupported {label}: {value!r}")
    return value


@contextmanager
def bind_runtime_egress(stage: str, execution_class: str) -> Iterator[EgressAttribution]:
    """Bind one worker task's bounded egress lane for child async tasks."""

    attribution = EgressAttribution(
        stage=_validate_member(stage, EGRESS_STAGES, "egress stage"),
        execution_class=_validate_member(execution_class, EXECUTION_CLASSES, "execution class"),
    )
    token = _egress_attribution.set(attribution)
    try:
        yield attribution
    finally:
        _egress_attribution.reset(token)


def current_egress_attribution() -> EgressAttribution | None:
    """Return the current immutable attribution token, if worker-bound."""

    return _egress_attribution.get()


def record_origin_attempt(attribution: EgressAttribution | None, egress: str) -> None:
    if attribution is None:
        return
    route = _validate_member(egress, EGRESS_ROUTES, "egress route")
    origin_attempts_total.labels(
        stage=attribution.stage,
        execution_class=attribution.execution_class,
        egress=route,
    ).inc()


def record_origin_outcome(attribution: EgressAttribution | None, egress: str, outcome: str) -> None:
    if attribution is None:
        return
    route = _validate_member(egress, EGRESS_ROUTES, "egress route")
    if outcome not in {"response", "transport_error"}:
        raise ValueError(f"unsupported origin outcome: {outcome!r}")
    origin_outcomes_total.labels(
        stage=attribution.stage,
        execution_class=attribution.execution_class,
        egress=route,
        outcome=outcome,
    ).inc()


def record_response_body_bytes(
    attribution: EgressAttribution | None, egress: str, byte_count: int
) -> None:
    if attribution is None or byte_count == 0:
        return
    if not isinstance(byte_count, int) or byte_count < 0:
        raise ValueError("response byte count must be a non-negative integer")
    route = _validate_member(egress, EGRESS_ROUTES, "egress route")
    response_body_bytes_total.labels(
        stage=attribution.stage,
        execution_class=attribution.execution_class,
        egress=route,
    ).inc(byte_count)


def _bounded_capability(capability: str, allowed_capabilities: frozenset[str]) -> str:
    return capability if capability in allowed_capabilities else UNKNOWN_CAPABILITY


def seed_runtime_capabilities(
    *,
    stage: str,
    implementation: str,
    capabilities: Iterable[str],
) -> None:
    """Expose zero-valued bounded series before a measurement window starts."""

    _validate_member(stage, RUNTIME_STAGES, "runtime stage")
    bounded = frozenset(capabilities)
    for capability in sorted(bounded | {UNKNOWN_CAPABILITY}):
        for outcome in RUNTIME_OUTCOMES[stage]:
            capability_executions_total.labels(
                stage=stage,
                implementation=implementation,
                capability=capability,
                outcome=outcome,
            )


def record_runtime_capability(
    *,
    stage: str,
    implementation: str,
    capability: str,
    allowed_capabilities: frozenset[str],
    outcome: str,
) -> None:
    """Count one execution without accepting arbitrary config text as a label."""

    _validate_member(stage, RUNTIME_STAGES, "runtime stage")
    if outcome not in RUNTIME_OUTCOMES[stage]:
        raise ValueError(f"unsupported {stage} outcome: {outcome!r}")
    capability_executions_total.labels(
        stage=stage,
        implementation=implementation,
        capability=_bounded_capability(capability, allowed_capabilities),
        outcome=outcome,
    ).inc()


def _seed_egress_series() -> None:
    for stage in sorted(EGRESS_STAGES):
        for execution_class in sorted(EXECUTION_CLASSES):
            for route in sorted(EGRESS_ROUTES):
                origin_attempts_total.labels(
                    stage=stage,
                    execution_class=execution_class,
                    egress=route,
                )
                response_body_bytes_total.labels(
                    stage=stage,
                    execution_class=execution_class,
                    egress=route,
                )
                for outcome in ("response", "transport_error"):
                    origin_outcomes_total.labels(
                        stage=stage,
                        execution_class=execution_class,
                        egress=route,
                        outcome=outcome,
                    )


_seed_egress_series()
