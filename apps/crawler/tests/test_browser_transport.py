"""Standalone Chromium transport tracker/observer tests; no real browser."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

import src.shared.browser_transport as transport
from src.shared.browser_transport import (
    EXACT_TIME_SERIES_BUDGET,
    INSTRUMENTATION_REASONS,
    PUBLIC_CDP_TARGET_TYPES,
    BrowserTransportTracker,
    Capability,
    ChromiumAutoAttachObserver,
    InstrumentationReason,
    PretransportReason,
    RegistryError,
    RequestClass,
    RequestDeclaration,
    Stage,
    TaskAttribution,
    TerminalOutcome,
    audit_metric_series,
    build_registry_document,
    canonical_registry_bytes,
    classify_request,
    load_registry,
    registry_digest,
    resolve_capability,
    resolve_route,
    validate_registry_document,
)

CRAWLER_ROOT = Path(__file__).parents[1]
REGISTRY_DIR = CRAWLER_ROOT / "contracts/browser/transport/v1"


def _attribution(
    *,
    stage: Stage = Stage.MONITOR,
    capability: Capability = Capability.NAVIGATION_EVALUATION,
    route: str = "direct",
    provider: str = "direct",
) -> TaskAttribution:
    return TaskAttribution(stage, capability, route, provider)


def _covered_tracker(task_id: str = "task") -> BrowserTransportTracker:
    tracker = BrowserTransportTracker()
    assert tracker.accept_task(task_id, _attribution())
    tracker.observer_target_attached("session", "target", "page")
    tracker.observer_network_enabled("session")
    return tracker


def test_registry_bytes_digest_sidecar_and_generator_are_idempotent():
    registry = load_registry()
    raw = (REGISTRY_DIR / "registry.json").read_bytes()

    assert json.loads(raw) == build_registry_document()
    assert raw == canonical_registry_bytes()
    assert registry.digest == registry_digest()
    assert (REGISTRY_DIR / "registry.sha256").read_text() == (f"{registry.digest}  registry.json\n")


def test_registry_enumerates_exact_private_bounded_exposition():
    registry = load_registry()
    series = audit_metric_series(registry)

    assert len(series) == EXACT_TIME_SERIES_BUDGET == 1_449
    assert len(set(series)) == 1_449
    source = Path(transport.__file__).read_text()
    assert "Content-Length" not in source
    assert "loadingFinished.encodedDataLength" not in source
    assert "_connection" not in source
    assert "_channel" not in source
    for forbidden in (
        "url",
        "origin",
        "hostname",
        "company",
        "board",
        "posting",
        "target_id",
        "session_id",
        "request_id",
    ):
        assert all(forbidden not in dict(item.labels) for item in series)


def test_registry_rejects_any_cardinality_or_label_broadening():
    cardinality_drift = build_registry_document()
    cardinality_drift["cardinality_budget"]["ledger"][0]["series"] += 1
    label_drift = build_registry_document()
    label_drift["privacy"]["allowed_label_names"].append("url")

    with pytest.raises(RegistryError, match="differs from the frozen"):
        validate_registry_document(cardinality_drift)
    with pytest.raises(RegistryError, match="differs from the frozen"):
        validate_registry_document(label_drift)


def test_route_resolution_preserves_optional_provider_none_direct_attempt():
    optional_none = resolve_route(
        use_proxy=True,
        configured_provider="none",
        proxy_acquired=False,
    )
    proxy = resolve_route(
        use_proxy=True,
        configured_provider="static-proxy",
        proxy_acquired=True,
        proxy_required=True,
    )
    unavailable = resolve_route(
        use_proxy=True,
        configured_provider="static-proxy",
        proxy_acquired=False,
        proxy_required=True,
    )
    unknown = resolve_route(
        use_proxy=True,
        configured_provider="arbitrary-provider",
        proxy_acquired=False,
    )

    assert optional_none.attribution == ("direct", "direct")
    assert optional_none.pretransport_reason is None
    assert resolve_route(
        use_proxy=True,
        configured_provider="none",
        proxy_acquired=False,
        proxy_required=True,
    ).attribution == ("direct", "direct")
    assert proxy.attribution == ("proxy", "static-proxy")
    assert unavailable.pretransport_reason is PretransportReason.REQUIRED_PROXY_UNAVAILABLE
    assert unknown.pretransport_reason is PretransportReason.UNKNOWN_PROVIDER


def test_capability_resolution_is_registry_bounded():
    known = resolve_capability("identity-transport")
    unknown = resolve_capability("arbitrary-capability")

    assert known.capability is Capability.IDENTITY_TRANSPORT
    assert known.pretransport_reason is None
    assert unknown.capability is None
    assert unknown.pretransport_reason is PretransportReason.UNKNOWN_CAPABILITY


def test_route_provider_cross_product_is_rejected():
    with pytest.raises(ValueError, match="illegal route/provider pair"):
        _attribution(route="direct", provider="static-proxy")


@pytest.mark.parametrize(
    ("warmup", "main", "expected"),
    [
        (True, True, RequestClass.WARMUP),
        (True, False, RequestClass.WARMUP),
        (False, True, RequestClass.MAIN),
        (False, False, RequestClass.SUBRESOURCE),
    ],
)
def test_non_redirect_request_class_precedence(warmup, main, expected):
    assert classify_request(explicitly_warmup=warmup, initial_navigation=main) is expected


def test_redirect_predecessor_precedence_retry_main_and_idempotent_callbacks():
    tracker = _covered_tracker()
    declaration = RequestDeclaration("task", initial_navigation=True)

    first = tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="redirect-chain",
        event_fingerprint="request-1",
        declaration=declaration,
        redirect_response=False,
    )
    assert first is not None
    second = tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="redirect-chain",
        event_fingerprint="request-2",
        declaration=declaration,
        redirect_response=True,
    )
    assert second is not None and second.redirect_hop == 1
    tracker.loading_finished(task_id="task", target_id="target", request_id="redirect-chain")
    tracker.loading_finished(task_id="task", target_id="target", request_id="redirect-chain")

    retry = tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="retry-request",
        event_fingerprint="request-3",
        declaration=declaration,
        redirect_response=False,
    )
    assert retry is not None
    tracker.loading_finished(task_id="task", target_id="target", request_id="retry-request")

    assert [(item.request_class, item.outcome) for item in tracker.terminals] == [
        (RequestClass.REDIRECT, TerminalOutcome.COMPLETE_RESPONSE),
        (RequestClass.MAIN, TerminalOutcome.COMPLETE_RESPONSE),
        (RequestClass.MAIN, TerminalOutcome.COMPLETE_RESPONSE),
    ]
    assert tracker.live_count == 0


def test_target_reattachment_and_service_worker_replay_do_not_duplicate_attempt():
    tracker = BrowserTransportTracker()
    tracker.accept_task("task", _attribution())
    declaration = RequestDeclaration("task")
    tracker.observer_target_attached("session-a", "service-worker-target", "service_worker")
    tracker.observer_network_enabled("session-a")
    tracker.request_will_be_sent(
        session_id="session-a",
        target_id="service-worker-target",
        request_id="request",
        event_fingerprint="same-public-event",
        declaration=declaration,
        redirect_response=False,
    )
    tracker.loading_finished(
        task_id="task", target_id="service-worker-target", request_id="request"
    )
    tracker.observer_target_attached("session-b", "service-worker-target", "service_worker")
    tracker.observer_network_enabled("session-b")
    replay = tracker.request_will_be_sent(
        session_id="session-b",
        target_id="service-worker-target",
        request_id="request",
        event_fingerprint="same-public-event",
        declaration=declaration,
        redirect_response=False,
    )

    assert replay == tracker.terminals[0].key
    assert len(tracker.terminals) == 1


def test_partial_bytes_override_cancellation_and_duplicate_data_is_idempotent():
    tracker = _covered_tracker()
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="partial",
        event_fingerprint="request",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )
    for fingerprint in ("chunk-a", "chunk-a", "chunk-b"):
        tracker.data_received(
            task_id="task",
            target_id="target",
            request_id="partial",
            encoded_data_length=125,
            event_fingerprint=fingerprint,
        )
    tracker.loading_failed(
        task_id="task",
        target_id="target",
        request_id="partial",
        cancelled=True,
    )
    tracker.target_closed(target_id="target")

    assert len(tracker.terminals) == 1
    terminal = tracker.terminals[0]
    assert terminal.encoded_bytes == 250
    assert terminal.outcome is TerminalOutcome.PARTIAL_RESPONSE


@pytest.mark.parametrize(
    ("terminalizer", "expected"),
    [
        ("transport", TerminalOutcome.TRANSPORT_FAILURE),
        ("policy", TerminalOutcome.POLICY_REJECTED),
        ("cancel", TerminalOutcome.CANCELLED),
        ("close", TerminalOutcome.TARGET_CLOSED),
    ],
)
def test_zero_byte_terminal_mapping(terminalizer, expected):
    tracker = _covered_tracker()
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="zero",
        event_fingerprint="request",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )
    if terminalizer == "close":
        tracker.target_closed(target_id="target")
    else:
        tracker.loading_failed(
            task_id="task",
            target_id="target",
            request_id="zero",
            cancelled=terminalizer == "cancel",
            policy_rejected=terminalizer == "policy",
        )
    assert tracker.terminals[0].outcome is expected


def test_teardown_freezes_admission_terminalizes_once_and_conserves():
    tracker = _covered_tracker()
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="live",
        event_fingerprint="request",
        declaration=RequestDeclaration("task", initial_navigation=True),
        redirect_response=False,
    )
    tracker.freeze_new_admission()
    tracker.terminalize_live()
    tracker.terminalize_live()
    blocked = tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="late",
        event_fingerprint="late-request",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )

    assert blocked is None
    assert len(tracker.terminals) == 1
    assert tracker.terminals[0].outcome is TerminalOutcome.CANCELLED
    rows = tracker.aggregate_rows()
    assert sum(row["attempts"] for row in rows.values()) == 1
    assert sum(sum(row["outcomes"].values()) for row in rows.values()) == 1


def test_missing_byte_coverage_and_classification_are_bounded_blockers():
    tracker = BrowserTransportTracker()
    tracker.accept_task("task", _attribution())
    tracker.observer_target_attached("session", "target", "page")
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="uncovered",
        event_fingerprint="request",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="unclassified",
        event_fingerprint="request-2",
        declaration=None,
        redirect_response=False,
        stage=Stage.MONITOR,
    )

    counts = tracker.instrumentation_counts
    assert counts[(Stage.MONITOR, InstrumentationReason.BYTE_LIFECYCLE_MISSING)] == 1
    assert counts[(Stage.MONITOR, InstrumentationReason.CLASSIFICATION_MISSING)] == 1
    assert set(reason.value for reason in InstrumentationReason) == set(INSTRUMENTATION_REASONS)


class FakeSession:
    def __init__(self, *, answer_child_commands: bool = True) -> None:
        self.handlers: dict[str, list[Any]] = defaultdict(list)
        self.sent: list[tuple[str, dict[str, Any] | None]] = []
        self.child_commands: list[tuple[str, str]] = []
        self.answer_child_commands = answer_child_commands
        self.detached = False

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append((method, params))
        if method == "Target.sendMessageToTarget" and params is not None:
            message = json.loads(params["message"])
            session_id = params["sessionId"]
            self.child_commands.append((session_id, message["method"]))
            if self.answer_child_commands:
                self.emit(
                    "Target.receivedMessageFromTarget",
                    {
                        "sessionId": session_id,
                        "message": json.dumps({"id": message["id"], "result": {}}),
                    },
                )
        return {}

    def on(self, event: str, handler) -> None:
        self.handlers[event].append(handler)

    def remove_listener(self, event: str, handler) -> None:
        self.handlers[event].remove(handler)

    async def detach(self) -> None:
        self.detached = True

    def emit(self, event: str, params: dict[str, Any]) -> None:
        for handler in tuple(self.handlers[event]):
            handler(params)

    def emit_child(self, session_id: str, method: str, params: dict[str, Any]) -> None:
        self.emit(
            "Target.receivedMessageFromTarget",
            {
                "sessionId": session_id,
                "message": json.dumps({"method": method, "params": params}),
            },
        )


class FakeBrowser:
    def __init__(self, session: FakeSession | None = None, *, fail: bool = False) -> None:
        self.session = session or FakeSession()
        self.fail = fail

    async def new_browser_cdp_session(self) -> FakeSession:
        if self.fail:
            raise RuntimeError("attach failed")
        return self.session


async def _flush_tasks() -> None:
    for _ in range(12):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_public_cdp_observer_recursively_enables_all_relevant_targets_and_counts_bytes():
    session = FakeSession()
    browser = FakeBrowser(session)
    tracker = BrowserTransportTracker()
    tracker.accept_task("task", _attribution())

    def classifier(_target_id: str, _target_type: str, _params: Any) -> RequestDeclaration:
        return RequestDeclaration("task")

    observer = await ChromiumAutoAttachObserver.attach(
        browser, tracker, classifier, stage=Stage.MONITOR
    )
    assert observer is not None
    assert session.sent[0] == (
        "Target.setAutoAttach",
        {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": False},
    )

    for index, target_type in enumerate(sorted(PUBLIC_CDP_TARGET_TYPES)):
        session_id = f"session-{index}"
        target_id = f"target-{index}"
        if index == 0:
            session.emit(
                "Target.attachedToTarget",
                {
                    "sessionId": session_id,
                    "targetInfo": {"targetId": target_id, "type": target_type},
                },
            )
        else:
            session.emit_child(
                "session-0",
                "Target.attachedToTarget",
                {
                    "sessionId": session_id,
                    "targetInfo": {"targetId": target_id, "type": target_type},
                },
            )
    await _flush_tasks()

    for index, _target_type in enumerate(sorted(PUBLIC_CDP_TARGET_TYPES)):
        session_id = f"session-{index}"
        target_id = f"target-{index}"
        request_id = f"request-{index}"
        session.emit_child(
            session_id,
            "Network.requestWillBeSent",
            {"requestId": request_id, "timestamp": index},
        )
        session.emit_child(
            session_id,
            "Network.dataReceived",
            {"requestId": request_id, "timestamp": index + 0.1, "encodedDataLength": index + 1},
        )
        session.emit_child(
            session_id,
            "Network.loadingFinished",
            {"requestId": request_id, "timestamp": index + 0.2},
        )
    await _flush_tasks()

    assert len(tracker.terminals) == len(PUBLIC_CDP_TARGET_TYPES)
    assert sum(item.encoded_bytes for item in tracker.terminals) == sum(
        range(1, len(PUBLIC_CDP_TARGET_TYPES) + 1)
    )
    for index in range(len(PUBLIC_CDP_TARGET_TYPES)):
        commands = [method for sid, method in session.child_commands if sid == f"session-{index}"]
        assert commands == [
            "Target.setAutoAttach",
            "Network.enable",
            "Runtime.runIfWaitingForDebugger",
        ]
    await observer.drain_and_detach()
    assert session.detached is True
    assert all(session.handlers[event] == [] for event in session.handlers)


@pytest.mark.asyncio
async def test_attach_failure_and_drain_timeout_are_bounded(monkeypatch):
    tracker = BrowserTransportTracker()
    failed = await ChromiumAutoAttachObserver.attach(
        FakeBrowser(fail=True),
        tracker,
        lambda *_args: None,
        stage=Stage.DETAIL,
    )
    assert failed is None
    assert (
        tracker.instrumentation_counts[(Stage.DETAIL, InstrumentationReason.OBSERVER_ATTACH_FAILED)]
        == 1
    )

    session = FakeSession(answer_child_commands=False)
    observer = await ChromiumAutoAttachObserver.attach(
        FakeBrowser(session),
        tracker,
        lambda *_args: RequestDeclaration("missing"),
        stage=Stage.DETAIL,
    )
    assert observer is not None
    session.emit(
        "Target.attachedToTarget",
        {"sessionId": "stuck", "targetInfo": {"targetId": "target", "type": "page"}},
    )
    await asyncio.sleep(0)
    monkeypatch.setattr(transport, "DRAIN_TIMEOUT_SECONDS", 0.001)
    await observer.drain_and_detach()
    assert tracker.instrumentation_counts[(Stage.DETAIL, InstrumentationReason.DRAIN_TIMEOUT)] == 1
    assert session.detached is True
