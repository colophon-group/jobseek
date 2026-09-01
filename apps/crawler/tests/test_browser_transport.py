"""Standalone Chromium transport tracker and raw-CDP conformance tests."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from contextlib import suppress
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
    ChromiumLoopbackOwnership,
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
    assert tracker.observer_ready()
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


def test_task_admission_opens_only_after_observer_readiness_and_never_reopens():
    tracker = BrowserTransportTracker()

    assert not tracker.accept_task("too-early", _attribution())
    assert not tracker.accepted_task_counts
    assert tracker.observer_ready()
    assert tracker.accept_task("ready", _attribution())
    tracker.freeze_new_admission()
    assert not tracker.accept_task("too-late", _attribution())
    assert not tracker.observer_ready()
    assert list(tracker.accepted_task_counts.values()) == [1]


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
    tracker.loading_finished(
        session_id="session", request_id="redirect-chain", event_fingerprint="finished-1"
    )
    tracker.loading_finished(
        session_id="session", request_id="redirect-chain", event_fingerprint="finished-1"
    )

    retry = tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="retry-request",
        event_fingerprint="request-3",
        declaration=declaration,
        redirect_response=False,
    )
    assert retry is not None
    tracker.loading_finished(
        session_id="session", request_id="retry-request", event_fingerprint="finished-2"
    )

    assert [(item.request_class, item.outcome) for item in tracker.terminals] == [
        (RequestClass.REDIRECT, TerminalOutcome.COMPLETE_RESPONSE),
        (RequestClass.MAIN, TerminalOutcome.COMPLETE_RESPONSE),
        (RequestClass.MAIN, TerminalOutcome.COMPLETE_RESPONSE),
    ]
    assert tracker.live_count == 0


def test_target_reattachment_and_service_worker_replay_do_not_duplicate_attempt():
    tracker = BrowserTransportTracker()
    tracker.observer_ready()
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
    tracker.data_received(
        session_id="session-b",
        request_id="request",
        encoded_data_length=17,
        event_fingerprint="data",
    )
    tracker.loading_finished(
        session_id="session-b", request_id="request", event_fingerprint="finished"
    )

    assert replay == tracker.terminals[0].key
    assert len(tracker.terminals) == 1
    assert tracker.terminals[0].encoded_bytes == 17


def test_sparse_reparented_tail_binds_only_to_one_unambiguous_current_request():
    tracker = BrowserTransportTracker()
    tracker.observer_ready()
    tracker.accept_task("task", _attribution())
    for session_id in ("page-a", "page-b"):
        tracker.observer_target_attached(session_id, session_id, "page")
        tracker.observer_network_enabled(session_id)
    tracker.request_will_be_sent(
        session_id="page-a",
        target_id="page-a",
        request_id="bootstrap",
        event_fingerprint="declaration",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )

    assert tracker.associate_replayed_tail("worker", "bootstrap") is True
    tracker.data_received(
        session_id="worker",
        request_id="bootstrap",
        encoded_data_length=23,
        event_fingerprint="data",
    )
    tracker.loading_finished(
        session_id="worker",
        request_id="bootstrap",
        event_fingerprint="finished",
    )

    assert len(tracker.terminals) == 1
    assert tracker.terminals[0].encoded_bytes == 23
    assert not tracker.instrumentation_counts


def test_sparse_reparented_tail_with_ambiguous_reused_id_fails_closed():
    tracker = BrowserTransportTracker()
    tracker.observer_ready()
    tracker.accept_task("task", _attribution())
    for session_id, fingerprint in (("page-a", "declaration-a"), ("page-b", "declaration-b")):
        tracker.observer_target_attached(session_id, session_id, "page")
        tracker.observer_network_enabled(session_id)
        tracker.request_will_be_sent(
            session_id=session_id,
            target_id=session_id,
            request_id="reused",
            event_fingerprint=fingerprint,
            declaration=RequestDeclaration("task"),
            redirect_response=False,
        )

    assert tracker.associate_replayed_tail("worker", "reused", stage=Stage.MONITOR) is False
    assert (
        tracker.instrumentation_counts[(Stage.MONITOR, InstrumentationReason.LIFECYCLE_CONFLICT)]
        == 1
    )


def test_cross_session_reused_request_id_is_a_new_conserved_attempt():
    tracker = BrowserTransportTracker(browser_generation=19)
    tracker.observer_ready()
    tracker.accept_task("task", _attribution())
    declaration = RequestDeclaration("task")
    for session_id, fingerprint in (("session-a", "event-a"), ("session-b", "event-b")):
        tracker.observer_target_attached(session_id, "same-target", "service_worker")
        tracker.observer_network_enabled(session_id)
        key = tracker.request_will_be_sent(
            session_id=session_id,
            target_id="same-target",
            request_id="reused",
            event_fingerprint=fingerprint,
            declaration=declaration,
            redirect_response=False,
        )
        assert key is not None
        tracker.loading_finished(
            session_id=session_id,
            request_id="reused",
            event_fingerprint=f"finished-{session_id}",
        )

    assert len(tracker.terminals) == 2
    assert {item.key.session_id for item in tracker.terminals} == {"session-a", "session-b"}
    assert {item.key.browser_generation for item in tracker.terminals} == {19}
    assert sum(row["attempts"] for row in tracker.aggregate_rows().values()) == 2


def test_late_request_admission_fails_closed_once_per_real_event():
    tracker = _covered_tracker()
    tracker.freeze_new_admission()
    for _ in range(2):
        assert (
            tracker.request_will_be_sent(
                session_id="session",
                target_id="target",
                request_id="late",
                event_fingerprint="same-late-event",
                declaration=RequestDeclaration("task"),
                redirect_response=False,
                stage=Stage.MONITOR,
            )
            is None
        )

    assert not tracker.terminals
    assert (
        tracker.instrumentation_counts[(Stage.MONITOR, InstrumentationReason.LIFECYCLE_CONFLICT)]
        == 1
    )


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
            session_id="session",
            request_id="partial",
            encoded_data_length=125,
            event_fingerprint=fingerprint,
        )
    tracker.loading_failed(
        session_id="session",
        request_id="partial",
        event_fingerprint="failed",
        cancelled=True,
    )
    tracker.target_closed(target_id="target")

    assert len(tracker.terminals) == 1
    terminal = tracker.terminals[0]
    assert terminal.encoded_bytes == 250
    assert terminal.outcome is TerminalOutcome.PARTIAL_RESPONSE


def test_cache_or_service_worker_wrapper_is_not_a_transport_attempt():
    tracker = _covered_tracker()
    tracker.request_will_be_sent(
        session_id="session",
        target_id="target",
        request_id="cached",
        event_fingerprint="request",
        declaration=RequestDeclaration("task"),
        redirect_response=False,
    )
    tracker.mark_non_transport(session_id="session", request_id="cached")
    tracker.mark_non_transport(session_id="session", request_id="cached")
    tracker.data_received(
        session_id="session",
        request_id="cached",
        encoded_data_length=0,
        event_fingerprint="zero-chunk",
    )
    tracker.loading_finished(
        session_id="session",
        request_id="cached",
        event_fingerprint="finished",
    )

    assert tracker.live_count == 0
    assert not tracker.terminals
    assert not tracker.instrumentation_counts


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
            session_id="session",
            request_id="zero",
            event_fingerprint=f"failed-{terminalizer}",
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
    tracker.observer_ready()
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


def test_chromium_debugging_boundary_is_exactly_loopback_and_never_relaxes_origins():
    ownership = ChromiumLoopbackOwnership.create(debug_port=9222)
    assert ownership.launch_args == (
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
    )
    assert not hasattr(transport, "reserve_loopback_debug_port")
    assert transport._validate_loopback_websocket_url(  # type: ignore[attr-defined]
        "ws://127.0.0.1:9222/devtools/browser/browser-id",
        9222,
    ).startswith("ws://127.0.0.1:9222/")
    for endpoint in (
        "wss://127.0.0.1:9222/devtools/browser/browser-id",
        "ws://localhost:9222/devtools/browser/browser-id",
        "ws://127.0.0.1:9223/devtools/browser/browser-id",
        "ws://user@127.0.0.1:9222/devtools/browser/browser-id",
        "ws://127.0.0.1:9222/devtools/page/page-id",
        "ws://127.0.0.1:9222/devtools/browser/browser-id?token=secret",
        "ws://127.0.0.1:9222/devtools/browser/browser-id#fragment",
    ):
        with pytest.raises(ValueError):
            transport._validate_loopback_websocket_url(endpoint, 9222)  # type: ignore[attr-defined]

    assert all("remote-allow-origins" not in arg for arg in ownership.launch_args)


class FakeRawWebSocket:
    _CLOSED = object()

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.blocked_methods: set[str] = set()
        self.errored_commands: set[tuple[str, str | None]] = set()
        self.result_overrides: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.on_command: Callable[[dict[str, Any]], None] | None = None
        self.closed = False

    async def send(self, message: str) -> None:
        parsed: dict[str, Any] = json.loads(message)
        self.sent.append(parsed)
        callback = self.on_command
        if callback is not None:
            callback(parsed)
        if parsed["method"] not in self.blocked_methods:
            command = (parsed["method"], parsed.get("sessionId"))
            response: dict[str, Any] = {"id": parsed["id"]}
            if command in self.errored_commands:
                response["error"] = {"code": -32_000, "message": "simulated command failure"}
            else:
                response["result"] = self.result_overrides.get(command, {})
            if "sessionId" in parsed:
                response["sessionId"] = parsed["sessionId"]
            self.incoming.put_nowait(json.dumps(response))

    async def recv(self) -> str | bytes:
        item = await self.incoming.get()
        if item is self._CLOSED:
            raise EOFError("fake WebSocket closed")
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, (str, bytes))
        return item

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        if not self.closed:
            self.closed = True
            self.incoming.put_nowait(self._CLOSED)

    def emit_event(
        self,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> None:
        message: dict[str, Any] = {"method": method, "params": params}
        if session_id is not None:
            message["sessionId"] = session_id
        self.incoming.put_nowait(json.dumps(message))

    def fail(self) -> None:
        self.incoming.put_nowait(ConnectionError("simulated WebSocket loss"))

    def methods_for(self, session_id: str | None) -> list[str]:
        return [
            message["method"] for message in self.sent if message.get("sessionId") == session_id
        ]


async def _flush_tasks() -> None:
    for _ in range(12):
        await asyncio.sleep(0)


async def _post_ready_observer_with_live_request() -> tuple[
    FakeRawWebSocket,
    BrowserTransportTracker,
    ChromiumAutoAttachObserver,
]:
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("task"),
        stage=Stage.DETAIL,
    )
    assert observer is not None and observer.ready
    assert tracker.accept_task("task", _attribution(stage=Stage.DETAIL))
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "owner-session",
            "targetInfo": {"targetId": "owner-target", "type": "page"},
        },
    )
    await _flush_tasks()
    websocket.emit_event(
        "Network.requestWillBeSent",
        {"requestId": "live-request", "timestamp": 1},
        session_id="owner-session",
    )
    await _flush_tasks()
    assert tracker.live_count == 1
    return websocket, tracker, observer


def _assert_initialization_timeout_failed_closed(
    websocket: FakeRawWebSocket,
    tracker: BrowserTransportTracker,
    observer: ChromiumAutoAttachObserver,
) -> None:
    assert not observer.ready
    assert websocket.closed
    assert not observer._initialization_tasks
    assert tracker.live_count == 0
    assert len(tracker.terminals) == 1
    assert tracker.terminals[0].outcome is TerminalOutcome.TRANSPORT_FAILURE
    assert not tracker.accept_task("after-timeout", _attribution(stage=Stage.DETAIL))
    assert (
        tracker.instrumentation_counts[
            (Stage.DETAIL, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )


async def _attach_fake_ownership_protocol(
    monkeypatch: pytest.MonkeyPatch,
    websocket: FakeRawWebSocket,
    tracker: BrowserTransportTracker,
    *,
    emit_proof_attachment: bool,
) -> tuple[ChromiumAutoAttachObserver | None, list[str]]:
    ownership = ChromiumLoopbackOwnership.create(debug_port=9222)
    await ownership.prepare(object())
    proof_url = f"about:blank#jobseek-cdp-owner={ownership._proof_token}"
    proof_queries = 0

    async def install_proof(_ownership: ChromiumLoopbackOwnership) -> None:
        return None

    async def fetch_endpoint(_debug_port: int, _timeout: float) -> str:
        return "ws://127.0.0.1:9222/devtools/browser/fake-browser"

    async def connect_endpoint(_url: str, _timeout: float) -> FakeRawWebSocket:
        return websocket

    def expose_proof(message: dict[str, Any]) -> None:
        nonlocal proof_queries
        if message["method"] != "Target.getTargets" or "sessionId" in message:
            return
        proof_queries += 1
        targets: list[dict[str, str]] = []
        if proof_queries >= 2:
            targets.append(
                {
                    "targetId": "proof-target",
                    "type": "page",
                    "url": proof_url,
                }
            )
            if emit_proof_attachment and proof_queries == 2:
                websocket.emit_event(
                    "Target.attachedToTarget",
                    {
                        "sessionId": "proof-session",
                        "targetInfo": {"targetId": "proof-target", "type": "page"},
                    },
                )
        websocket.result_overrides[("Target.getTargets", None)] = {"targetInfos": targets}

    classifier_calls: list[str] = []
    websocket.on_command = expose_proof
    monkeypatch.setattr(ChromiumLoopbackOwnership, "_install_proof", install_proof)
    monkeypatch.setattr(transport, "_fetch_loopback_websocket_url", fetch_endpoint)
    observer = await ChromiumAutoAttachObserver.attach(
        ownership,
        tracker,
        lambda target_id, *_args: classifier_calls.append(target_id),
        stage=Stage.MONITOR,
        setup_timeout=0.25,
        connector=connect_endpoint,
    )
    return observer, classifier_calls


@pytest.mark.asyncio
async def test_raw_cdp_observer_recursively_enables_targets_and_uses_frozen_attribution():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    classifications = 0

    def classifier(_target_id: str, _target_type: str, _params: Any) -> RequestDeclaration:
        nonlocal classifications
        classifications += 1
        return RequestDeclaration("task")

    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        classifier,
        stage=Stage.MONITOR,
    )
    assert observer is not None
    assert observer.ready
    assert tracker.accept_task("task", _attribution())
    assert websocket.sent[0]["method"] == "Target.setAutoAttach"
    assert websocket.sent[0]["params"]["flatten"] is True
    assert websocket.sent[0]["params"]["waitForDebuggerOnStart"] is True
    assert websocket.sent[0]["params"]["filter"] == [
        {"type": "page"},
        {"type": "iframe"},
        {"type": "worker"},
        {"type": "shared_worker"},
        {"type": "service_worker"},
        {"exclude": True},
    ]

    def emit_preboundary_bootstrap_tail(message: dict[str, Any]) -> None:
        if (
            message["method"]
            in {
                "Network.enable",
                "Runtime.runIfWaitingForDebugger",
            }
            and "sessionId" in message
        ):
            websocket.emit_event(
                "Network.loadingFinished",
                {"requestId": f"bootstrap-{message['method']}-{message['sessionId']}"},
                session_id=message["sessionId"],
            )

    websocket.on_command = emit_preboundary_bootstrap_tail

    for index, target_type in enumerate(sorted(PUBLIC_CDP_TARGET_TYPES)):
        session_id = f"session-{index}"
        target_id = f"target-{index}"
        websocket.emit_event(
            "Target.attachedToTarget",
            {
                "sessionId": session_id,
                "targetInfo": {"targetId": target_id, "type": target_type},
            },
        )
    await _flush_tasks()
    websocket.on_command = None

    for index, _target_type in enumerate(sorted(PUBLIC_CDP_TARGET_TYPES)):
        session_id = f"session-{index}"
        target_id = f"target-{index}"
        request_id = f"request-{index}"
        websocket.emit_event(
            "Network.requestWillBeSent",
            {"requestId": request_id, "timestamp": index},
            session_id=session_id,
        )
        websocket.emit_event(
            "Network.dataReceived",
            {"requestId": request_id, "timestamp": index + 0.1, "encodedDataLength": index + 1},
            session_id=session_id,
        )
        websocket.emit_event(
            "Network.loadingFinished",
            {"requestId": request_id, "timestamp": index + 0.2},
            session_id=session_id,
        )
        websocket.emit_event("Network.policyUpdated", {}, session_id=session_id)
    await _flush_tasks()

    assert len(tracker.terminals) == len(PUBLIC_CDP_TARGET_TYPES)
    assert classifications == len(PUBLIC_CDP_TARGET_TYPES)
    assert sum(item.encoded_bytes for item in tracker.terminals) == sum(
        range(1, len(PUBLIC_CDP_TARGET_TYPES) + 1)
    )
    for index in range(len(PUBLIC_CDP_TARGET_TYPES)):
        assert websocket.methods_for(f"session-{index}") == [
            "Target.setAutoAttach",
            "Network.enable",
            "Runtime.runIfWaitingForDebugger",
        ]
    assert observer.observed_target_types == PUBLIC_CDP_TARGET_TYPES
    assert not tracker.instrumentation_counts
    await observer.drain_and_detach()
    assert websocket.closed is True
    assert observer.reader_done


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_method",
    [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ],
)
async def test_initial_attach_fails_closed_at_every_child_setup_boundary(failed_method):
    websocket = FakeRawWebSocket()
    websocket.errored_commands.add((failed_method, "child-session"))
    emitted_child = False

    def emit_initial_child(message: dict[str, Any]) -> None:
        nonlocal emitted_child
        if (
            message["method"] == "Target.getTargets"
            and "sessionId" not in message
            and not emitted_child
        ):
            emitted_child = True
            websocket.emit_event(
                "Target.attachedToTarget",
                {
                    "sessionId": "child-session",
                    "targetInfo": {"targetId": "child-target", "type": "worker"},
                },
            )

    websocket.on_command = emit_initial_child
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("never-admitted"),
        stage=Stage.MONITOR,
    )

    assert emitted_child
    assert observer is None
    assert websocket.closed
    assert not tracker.accepted_task_counts
    assert not tracker.accept_task("after-failed-attach", _attribution())
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_ATTACH_FAILED)
        ]
        == 1
    )
    if failed_method == "Runtime.runIfWaitingForDebugger":
        assert "Target.closeTarget" in websocket.methods_for(None)
    else:
        assert "Runtime.runIfWaitingForDebugger" in websocket.methods_for("child-session")


@pytest.mark.asyncio
async def test_post_ready_child_network_failure_revokes_readiness_and_admission():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("task"),
        stage=Stage.DETAIL,
    )
    assert observer is not None and observer.ready
    assert tracker.accept_task("task", _attribution(stage=Stage.DETAIL))
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "primary-session",
            "targetInfo": {"targetId": "primary-target", "type": "page"},
        },
    )
    await _flush_tasks()
    websocket.emit_event(
        "Network.requestWillBeSent",
        {"requestId": "live-request", "timestamp": 1},
        session_id="primary-session",
    )
    await _flush_tasks()
    assert tracker.live_count == 1
    websocket.errored_commands.add(("Network.enable", "late-session"))
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "late-session",
            "targetInfo": {"targetId": "late-target", "type": "service_worker"},
        },
    )
    await _flush_tasks()

    assert not observer.ready
    assert websocket.closed
    assert tracker.terminals[-1].outcome is TerminalOutcome.TRANSPORT_FAILURE
    assert not tracker.accept_task("after-child-failure", _attribution(stage=Stage.DETAIL))
    assert (
        tracker.instrumentation_counts[
            (Stage.DETAIL, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_known_child_setup_error_revokes_admission_before_hung_cleanup(
    monkeypatch,
):
    monkeypatch.setattr(transport, "CDP_TARGET_INITIALIZATION_TIMEOUT_SECONDS", 0.05)
    websocket, tracker, observer = await _post_ready_observer_with_live_request()
    websocket.errored_commands.add(("Network.enable", "late-session"))
    websocket.blocked_methods.add("Runtime.runIfWaitingForDebugger")
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "late-session",
            "targetInfo": {"targetId": "late-target", "type": "worker"},
        },
    )
    await _flush_tasks()

    assert websocket.methods_for("late-session") == [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ]
    assert not observer.ready
    assert not websocket.closed
    assert len(observer._initialization_tasks) == 1
    assert tracker.live_count == 0
    assert len(tracker.terminals) == 1
    assert tracker.terminals[0].outcome is TerminalOutcome.TRANSPORT_FAILURE
    assert not tracker.accept_task(
        "during-known-failure-cleanup",
        _attribution(stage=Stage.DETAIL),
    )

    await asyncio.sleep(0.07)
    await _flush_tasks()

    _assert_initialization_timeout_failed_closed(websocket, tracker, observer)


@pytest.mark.asyncio
async def test_post_ready_child_missing_network_ack_expires_one_target_deadline(
    monkeypatch,
):
    monkeypatch.setattr(transport, "CDP_TARGET_INITIALIZATION_TIMEOUT_SECONDS", 0.02)
    websocket, tracker, observer = await _post_ready_observer_with_live_request()
    websocket.blocked_methods.add("Network.enable")
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "late-session",
            "targetInfo": {"targetId": "late-target", "type": "worker"},
        },
    )

    await asyncio.sleep(0.04)
    await _flush_tasks()

    assert websocket.methods_for("late-session") == [
        "Target.setAutoAttach",
        "Network.enable",
    ]
    _assert_initialization_timeout_failed_closed(websocket, tracker, observer)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_commands",
    [
        frozenset({("Runtime.runIfWaitingForDebugger", "duplicate-session")}),
        frozenset({("Target.detachFromTarget", None)}),
        frozenset(
            {
                ("Runtime.runIfWaitingForDebugger", "duplicate-session"),
                ("Target.detachFromTarget", None),
            }
        ),
    ],
    ids=["resume", "detach", "resume-and-detach"],
)
async def test_duplicate_session_cleanup_failure_revokes_readiness_and_admission(
    failed_commands,
):
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("task"),
        stage=Stage.DETAIL,
    )
    assert observer is not None and observer.ready
    assert tracker.accept_task("task", _attribution(stage=Stage.DETAIL))
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "owner-session",
            "targetInfo": {"targetId": "shared-target", "type": "page"},
        },
    )
    await _flush_tasks()
    websocket.emit_event(
        "Network.requestWillBeSent",
        {"requestId": "live-request", "timestamp": 1},
        session_id="owner-session",
    )
    await _flush_tasks()
    assert tracker.live_count == 1

    websocket.errored_commands.update(failed_commands)
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "duplicate-session",
            "targetInfo": {"targetId": "shared-target", "type": "page"},
        },
    )
    await _flush_tasks()

    assert websocket.methods_for("duplicate-session") == ["Runtime.runIfWaitingForDebugger"]
    assert "Target.detachFromTarget" in websocket.methods_for(None)
    if len(failed_commands) == 2:
        assert "Target.closeTarget" in websocket.methods_for(None)
    assert not observer.ready
    assert websocket.closed
    assert tracker.terminals[-1].outcome is TerminalOutcome.TRANSPORT_FAILURE
    assert not tracker.accept_task("after-duplicate-failure", _attribution(stage=Stage.DETAIL))
    assert (
        tracker.instrumentation_counts[
            (Stage.DETAIL, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_nested_duplicate_session_detaches_through_its_parent_session():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: None,
        stage=Stage.MONITOR,
    )
    assert observer is not None and observer.ready
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "owner-session",
            "targetInfo": {"targetId": "shared-target", "type": "service_worker"},
        },
    )
    await _flush_tasks()
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "duplicate-session",
            "targetInfo": {"targetId": "shared-target", "type": "service_worker"},
        },
        session_id="owner-session",
    )
    await _flush_tasks()

    detach = [
        message for message in websocket.sent if message["method"] == "Target.detachFromTarget"
    ]
    assert len(detach) == 1
    assert detach[0]["params"] == {"sessionId": "duplicate-session"}
    assert detach[0]["sessionId"] == "owner-session"
    assert observer.ready
    assert not tracker.instrumentation_counts
    await observer.drain_and_detach()


@pytest.mark.asyncio
async def test_duplicate_missing_resume_ack_expires_one_target_deadline(monkeypatch):
    monkeypatch.setattr(transport, "CDP_TARGET_INITIALIZATION_TIMEOUT_SECONDS", 0.02)
    websocket, tracker, observer = await _post_ready_observer_with_live_request()
    websocket.blocked_methods.add("Runtime.runIfWaitingForDebugger")
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "duplicate-session",
            "targetInfo": {"targetId": "owner-target", "type": "page"},
        },
        session_id="owner-session",
    )

    await asyncio.sleep(0.04)
    await _flush_tasks()

    assert websocket.methods_for("duplicate-session") == ["Runtime.runIfWaitingForDebugger"]
    assert not [
        message for message in websocket.sent if message["method"] == "Target.detachFromTarget"
    ]
    _assert_initialization_timeout_failed_closed(websocket, tracker, observer)


@pytest.mark.asyncio
async def test_parent_routed_duplicate_missing_detach_ack_expires_target_deadline(
    monkeypatch,
):
    monkeypatch.setattr(transport, "CDP_TARGET_INITIALIZATION_TIMEOUT_SECONDS", 0.02)
    websocket, tracker, observer = await _post_ready_observer_with_live_request()
    websocket.blocked_methods.add("Target.detachFromTarget")
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "duplicate-session",
            "targetInfo": {"targetId": "owner-target", "type": "page"},
        },
        session_id="owner-session",
    )

    await asyncio.sleep(0.04)
    await _flush_tasks()

    detach = [
        message for message in websocket.sent if message["method"] == "Target.detachFromTarget"
    ]
    assert len(detach) == 1
    assert detach[0]["params"] == {"sessionId": "duplicate-session"}
    assert detach[0]["sessionId"] == "owner-session"
    _assert_initialization_timeout_failed_closed(websocket, tracker, observer)


@pytest.mark.asyncio
async def test_duplicate_error_cleanup_cannot_outlive_remaining_target_budget(monkeypatch):
    monkeypatch.setattr(transport, "CDP_TARGET_INITIALIZATION_TIMEOUT_SECONDS", 0.05)
    websocket, tracker, observer = await _post_ready_observer_with_live_request()
    websocket.errored_commands.add(("Runtime.runIfWaitingForDebugger", "duplicate-session"))
    websocket.blocked_methods.add("Target.detachFromTarget")
    websocket.emit_event(
        "Target.attachedToTarget",
        {
            "sessionId": "duplicate-session",
            "targetInfo": {"targetId": "owner-target", "type": "page"},
        },
        session_id="owner-session",
    )
    await _flush_tasks()

    assert not observer.ready
    assert tracker.live_count == 0
    assert len(tracker.terminals) == 1
    assert not tracker.accept_task("during-cleanup", _attribution(stage=Stage.DETAIL))
    detach = [
        message for message in websocket.sent if message["method"] == "Target.detachFromTarget"
    ]
    assert len(detach) == 1
    assert detach[0]["sessionId"] == "owner-session"

    await asyncio.sleep(0.07)
    await _flush_tasks()

    _assert_initialization_timeout_failed_closed(websocket, tracker, observer)


@pytest.mark.asyncio
async def test_ownership_proof_listing_without_attachment_never_opens_admission(monkeypatch):
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer, classifier_calls = await _attach_fake_ownership_protocol(
        monkeypatch,
        websocket,
        tracker,
        emit_proof_attachment=False,
    )

    assert observer is None
    assert websocket.methods_for(None) == [
        "Target.setAutoAttach",
        "Target.getTargets",
        "Target.getTargets",
    ]
    assert not [message for message in websocket.sent if "sessionId" in message]
    assert websocket.closed
    assert not classifier_calls
    assert not tracker.accepted_task_counts
    assert not tracker.accept_task("application-task", _attribution())
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_ATTACH_FAILED)
        ]
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_ack",
    [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ],
)
async def test_ownership_proof_session_missing_each_required_ack_fails_closed(
    monkeypatch,
    missing_ack,
):
    websocket = FakeRawWebSocket()
    websocket.errored_commands.add((missing_ack, "proof-session"))
    tracker = BrowserTransportTracker()
    observer, classifier_calls = await _attach_fake_ownership_protocol(
        monkeypatch,
        websocket,
        tracker,
        emit_proof_attachment=True,
    )

    assert observer is None
    assert missing_ack in websocket.methods_for("proof-session")
    assert websocket.closed
    assert not classifier_calls
    assert not tracker.accepted_task_counts
    assert not tracker.accept_task("application-task", _attribution())
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_ATTACH_FAILED)
        ]
        == 1
    )


@pytest.mark.parametrize(
    "missing_ack",
    [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ],
)
def test_exact_proof_target_state_rejects_each_missing_ack(missing_ack):
    tracker = BrowserTransportTracker()
    observer = ChromiumAutoAttachObserver(
        FakeRawWebSocket(),
        tracker,
        lambda *_args: None,
        stage=Stage.MONITOR,
    )
    observer._target_by_session["proof-session"] = ("proof-target", "page")
    observer._session_by_target["proof-target"] = "proof-session"
    acknowledgements = [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ]
    for method in acknowledgements:
        if method == missing_ack:
            continue
        observer._setup_trace.append(("proof-session", "proof-target", "page", method))
        if method == "Target.setAutoAttach":
            observer._auto_attach_ready_sessions.add("proof-session")
        elif method == "Network.enable":
            observer._network_ready_sessions.add("proof-session")
        else:
            observer._resumed_sessions.add("proof-session")

    with pytest.raises(RuntimeError, match="ownership target"):
        observer._require_initialized_proof_target("proof-target")
    assert not tracker.accept_task("application-task", _attribution())


@pytest.mark.asyncio
async def test_attach_failure_and_drain_timeout_are_bounded(monkeypatch):
    tracker = BrowserTransportTracker()
    blocked = FakeRawWebSocket()
    blocked.blocked_methods.add("Target.setAutoAttach")
    failed = await ChromiumAutoAttachObserver.attach_websocket(
        blocked,
        tracker,
        lambda *_args: None,
        stage=Stage.DETAIL,
        setup_timeout=0.001,
    )
    assert failed is None
    assert (
        tracker.instrumentation_counts[(Stage.DETAIL, InstrumentationReason.OBSERVER_ATTACH_FAILED)]
        == 1
    )

    drain_tracker = BrowserTransportTracker()
    websocket = FakeRawWebSocket()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        drain_tracker,
        lambda *_args: RequestDeclaration("missing"),
        stage=Stage.DETAIL,
    )
    assert observer is not None
    websocket.emit_event(
        "Target.attachedToTarget",
        {"sessionId": "stuck", "targetInfo": {"targetId": "target", "type": "page"}},
    )
    await _flush_tasks()
    websocket.blocked_methods.add("Target.getTargets")
    monkeypatch.setattr(transport, "DRAIN_TIMEOUT_SECONDS", 0.001)
    await observer.drain_and_detach()
    assert (
        drain_tracker.instrumentation_counts[(Stage.DETAIL, InstrumentationReason.DRAIN_TIMEOUT)]
        == 1
    )
    assert websocket.closed is True


@pytest.mark.asyncio
async def test_unknown_sparse_tail_after_target_setup_fails_closed():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: None,
        stage=Stage.MONITOR,
    )
    assert observer is not None
    websocket.emit_event(
        "Target.attachedToTarget",
        {"sessionId": "session", "targetInfo": {"targetId": "target", "type": "worker"}},
    )
    await _flush_tasks()
    websocket.emit_event(
        "Network.loadingFinished",
        {"requestId": "orphan"},
        session_id="session",
    )
    await _flush_tasks()

    assert (
        tracker.instrumentation_counts[(Stage.MONITOR, InstrumentationReason.LIFECYCLE_CONFLICT)]
        == 1
    )
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )
    assert not observer.ready
    assert not tracker.accept_task("after-failure", _attribution())
    await observer.drain_and_detach()


@pytest.mark.asyncio
async def test_websocket_loss_terminalizes_live_request_and_blocks_stage():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker(browser_generation=7)
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("task"),
        stage=Stage.MONITOR,
    )
    assert observer is not None
    assert tracker.accept_task("task", _attribution())
    websocket.emit_event(
        "Target.attachedToTarget",
        {"sessionId": "session", "targetInfo": {"targetId": "target", "type": "page"}},
    )
    await _flush_tasks()
    websocket.emit_event(
        "Network.requestWillBeSent",
        {"requestId": "request", "timestamp": 1},
        session_id="session",
    )
    await _flush_tasks()
    websocket.fail()
    await _flush_tasks()

    assert tracker.terminals[0].key.browser_generation == 7
    assert tracker.terminals[0].outcome is TerminalOutcome.TRANSPORT_FAILURE
    assert not observer.ready
    assert not tracker.accept_task("after-websocket-loss", _attribution())
    assert list(tracker.accepted_task_counts.values()) == [1]
    assert (
        tracker.instrumentation_counts[
            (Stage.MONITOR, InstrumentationReason.OBSERVER_PROTOCOL_ERROR)
        ]
        == 1
    )
    await observer.drain_and_detach()


@pytest.mark.asyncio
async def test_drain_tracks_tasks_and_late_events_spawned_during_drain():
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: RequestDeclaration("task"),
        stage=Stage.MONITOR,
    )
    assert observer is not None
    assert tracker.accept_task("task", _attribution())
    spawned = False
    emitted_late_request = False

    def spawn_during_barrier(message: dict[str, Any]) -> None:
        nonlocal emitted_late_request, spawned
        if message["method"] == "Target.getTargets" and not spawned:
            spawned = True
            websocket.emit_event(
                "Target.attachedToTarget",
                {
                    "sessionId": "late-session",
                    "targetInfo": {"targetId": "late-target", "type": "worker"},
                },
            )
        elif (
            message["method"] == "Runtime.runIfWaitingForDebugger"
            and message.get("sessionId") == "late-session"
            and not emitted_late_request
        ):
            emitted_late_request = True
            websocket.emit_event(
                "Network.requestWillBeSent",
                {"requestId": "late-request", "timestamp": 1},
                session_id="late-session",
            )

    websocket.on_command = spawn_during_barrier
    await observer.drain_and_detach()

    assert spawned
    assert websocket.methods_for("late-session") == [
        "Target.setAutoAttach",
        "Network.enable",
        "Runtime.runIfWaitingForDebugger",
    ]
    assert not tracker.terminals
    assert (
        tracker.instrumentation_counts[(Stage.MONITOR, InstrumentationReason.LIFECYCLE_CONFLICT)]
        == 1
    )
    assert (Stage.MONITOR, InstrumentationReason.DRAIN_TIMEOUT) not in (
        tracker.instrumentation_counts
    )


@pytest.mark.asyncio
async def test_drain_cleanup_is_cancellation_safe_and_reraises(monkeypatch):
    websocket = FakeRawWebSocket()
    tracker = BrowserTransportTracker()
    observer = await ChromiumAutoAttachObserver.attach_websocket(
        websocket,
        tracker,
        lambda *_args: None,
        stage=Stage.DETAIL,
    )
    assert observer is not None
    websocket.blocked_methods.add("Target.getTargets")
    monkeypatch.setattr(transport, "DRAIN_TIMEOUT_SECONDS", 0.01)
    draining = asyncio.create_task(observer.drain_and_detach())
    await asyncio.sleep(0)
    draining.cancel("caller cancelled")

    with pytest.raises(asyncio.CancelledError):
        await draining
    assert websocket.closed
    assert observer.reader_done
    assert tracker.instrumentation_counts[(Stage.DETAIL, InstrumentationReason.DRAIN_TIMEOUT)] == 1


type _Route = tuple[str, dict[str, str], bytes, float]


class _LoopbackOrigin:
    def __init__(self) -> None:
        self.routes: dict[str, _Route] = {}
        self.requests: list[str] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def __aenter__(self) -> _LoopbackOrigin:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self.server.sockets
        assert sockets
        self.port = sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_args: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ", 2)[1].decode("ascii")
            self.requests.append(path)
            status, headers, body, stream_delay = self.routes.get(
                path,
                ("404 Not Found", {}, b"not found", 0.0),
            )
            response_headers = {
                "Connection": "close",
                "Content-Length": str(len(body)),
                **headers,
            }
            head = (
                f"HTTP/1.1 {status}\r\n"
                + "".join(f"{name}: {value}\r\n" for name, value in response_headers.items())
                + "\r\n"
            ).encode()
            if stream_delay:
                writer.write(head + body[:512])
                await writer.drain()
                await asyncio.sleep(stream_delay)
                writer.write(body[512:])
            else:
                writer.write(head + body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


def _installed_chromium_executable(playwright: Any) -> str | None:
    candidates = [
        Path(playwright.chromium.executable_path),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        executable = shutil.which(command)
        if executable:
            candidates.append(Path(executable))
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_competing_loopback_listener_cannot_claim_launched_browser_ownership():
    playwright_module = pytest.importorskip("playwright.async_api")
    async with _LoopbackOrigin() as competitor:
        websocket_url = f"ws://127.0.0.1:{competitor.port}/devtools/browser/competing-process"
        competitor.routes = {
            "/json/version": (
                "200 OK",
                {"Content-Type": "application/json"},
                json.dumps({"webSocketDebuggerUrl": websocket_url}).encode(),
                0.0,
            )
        }
        async with playwright_module.async_playwright() as playwright:
            executable = _installed_chromium_executable(playwright)
            if executable is None:
                pytest.skip("no real Chromium executable is installed")
            ownership = ChromiumLoopbackOwnership.create(debug_port=competitor.port)
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                args=list(ownership.launch_args),
            )
            await ownership.prepare(browser)
            competing_websocket = FakeRawWebSocket()
            competing_websocket.result_overrides[("Target.getTargets", None)] = {"targetInfos": []}
            connected_urls: list[str] = []

            async def connect_competing_endpoint(
                url: str,
                timeout: float,
            ) -> FakeRawWebSocket:
                assert timeout > 0
                connected_urls.append(url)
                return competing_websocket

            tracker = BrowserTransportTracker()
            classifier_calls = 0

            def classifier(*_args: Any) -> RequestDeclaration | None:
                nonlocal classifier_calls
                classifier_calls += 1
                return RequestDeclaration("must-not-run")

            try:
                observer = await ChromiumAutoAttachObserver.attach(
                    ownership,
                    tracker,
                    classifier,
                    stage=Stage.MONITOR,
                    connector=connect_competing_endpoint,
                )
                assert observer is None
                assert connected_urls == [websocket_url]
                assert competitor.requests == ["/json/version"]
                assert competing_websocket.methods_for(None) == [
                    "Target.setAutoAttach",
                    "Target.getTargets",
                    "Target.getTargets",
                ]
                assert competing_websocket.closed
                assert classifier_calls == 0
                assert not tracker.accepted_task_counts
                assert not tracker.accept_task("application-task", _attribution())
                assert (
                    tracker.instrumentation_counts[
                        (Stage.MONITOR, InstrumentationReason.OBSERVER_ATTACH_FAILED)
                    ]
                    == 1
                )
            finally:
                await browser.close()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_real_chromium_loopback_controller_covers_all_targets_and_conserves_bytes():
    playwright_module = pytest.importorskip("playwright.async_api")
    async with _LoopbackOrigin() as origin:
        direct_origin = f"http://127.0.0.1:{origin.port}"
        cross_site_origin = f"http://localhost:{origin.port}"
        main_html = f"""<!doctype html>
<iframe src="{cross_site_origin}/frame"></iframe>
<script>
window.__worker = new Worker('/worker.js');
window.__shared = new SharedWorker('/shared.js');
window.__shared.port.start();
window.__shared.port.postMessage('start');
window.__ready = (async () => {{
  const redirect = fetch('/redirect').then(response => response.text());
  const warmup = fetch('/warmup').then(response => response.text());
  const controller = new AbortController();
  const partial = fetch('/stream', {{signal: controller.signal}}).then(async response => {{
    const reader = response.body.getReader();
    await reader.read();
    controller.abort();
    try {{ await reader.read(); }} catch (_error) {{}}
  }}).catch(() => undefined);
  await Promise.all([redirect, warmup, partial]);
  await fetch('/cacheable').then(response => response.text());
  await fetch('/cacheable').then(response => response.text());
  await navigator.serviceWorker.register('/service.js');
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) {{
    await new Promise(resolve => navigator.serviceWorker.addEventListener(
      'controllerchange', resolve, {{once: true}}
    ));
  }}
  await fetch('/cached').then(response => response.text());
  return true;
}})();
</script>""".encode()
        origin.routes = {
            "/": ("200 OK", {"Content-Type": "text/html"}, main_html, 0.0),
            "/frame": (
                "200 OK",
                {"Content-Type": "text/html"},
                b"<script>fetch('/frame-data'); setInterval(() => {}, 1000)</script>",
                0.0,
            ),
            "/frame-data": ("200 OK", {}, b"frame", 0.0),
            "/worker.js": (
                "200 OK",
                {"Content-Type": "application/javascript"},
                b"fetch('/worker-data'); setInterval(() => {}, 1000)",
                0.0,
            ),
            "/worker-data": ("200 OK", {}, b"worker", 0.0),
            "/shared.js": (
                "200 OK",
                {"Content-Type": "application/javascript"},
                b"onconnect=e=>{const p=e.ports[0];p.onmessage=()=>fetch('/shared-data');"
                b"p.start();setInterval(()=>{},1000)}",
                0.0,
            ),
            "/shared-data": ("200 OK", {}, b"shared", 0.0),
            "/service.js": (
                "200 OK",
                {
                    "Content-Type": "application/javascript",
                    "Service-Worker-Allowed": "/",
                },
                b"self.addEventListener('install',e=>e.waitUntil((async()=>{"
                b"const c=await caches.open('v1');await c.add('/cached');"
                b"await fetch('/service-data')})()));"
                b"self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
                b"self.addEventListener('fetch',e=>{if(new URL(e.request.url).pathname==='/cached')"
                b"e.respondWith(caches.match(e.request))})",
                0.0,
            ),
            "/service-data": ("200 OK", {}, b"service", 0.0),
            "/cached": ("200 OK", {}, b"cached", 0.0),
            "/redirect": ("302 Found", {"Location": "/redirect-final"}, b"", 0.0),
            "/redirect-final": ("200 OK", {}, b"redirected", 0.0),
            "/warmup": ("200 OK", {}, b"warmup", 0.0),
            "/cacheable": (
                "200 OK",
                {"Cache-Control": "public, max-age=3600"},
                b"cacheable",
                0.0,
            ),
            "/stream": ("200 OK", {}, b"x" * 65_536, 1.0),
            "/favicon.ico": ("204 No Content", {}, b"", 0.0),
        }
        tracker = BrowserTransportTracker(browser_generation=23)

        def classifier(
            _target_id: str,
            _target_type: str,
            params: Any,
        ) -> RequestDeclaration | None:
            request = params.get("request")
            url = request.get("url") if isinstance(request, dict) else None
            if not isinstance(url, str) or not url.startswith(
                (f"{direct_origin}/", f"{cross_site_origin}/")
            ):
                return None
            return RequestDeclaration(
                "real-task",
                explicitly_warmup=url == f"{direct_origin}/warmup",
                initial_navigation=url == f"{direct_origin}/",
            )

        async with playwright_module.async_playwright() as playwright:
            executable = _installed_chromium_executable(playwright)
            if executable is None:
                pytest.skip("no real Chromium executable is installed")
            ownership = ChromiumLoopbackOwnership.create()
            browser = await playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                args=[
                    *ownership.launch_args,
                    "--site-per-process",
                ],
            )
            await ownership.prepare(browser)
            observer = await ChromiumAutoAttachObserver.attach(
                ownership,
                tracker,
                classifier,
                stage=Stage.MONITOR,
            )
            assert observer is not None and observer.ready
            assert observer.ownership_verified
            assert tracker.accept_task("real-task", _attribution())
            context, page = ownership.take_verified_context_and_page()
            try:
                await page.goto(f"{direct_origin}/")
                try:
                    async with asyncio.timeout(10):
                        while not (
                            observer.observed_target_types >= PUBLIC_CDP_TARGET_TYPES
                            and {
                                "/frame-data",
                                "/worker-data",
                                "/shared-data",
                                "/service-data",
                                "/redirect",
                                "/redirect-final",
                                "/warmup",
                                "/stream",
                                "/cacheable",
                            }
                            <= set(origin.requests)
                        ):
                            await asyncio.sleep(0.05)
                except TimeoutError:
                    pytest.fail(
                        "real target probe did not converge: "
                        f"types={sorted(observer.observed_target_types)!r} "
                        f"requests={origin.requests!r} "
                        f"trace={observer.setup_trace!r} "
                        f"instrumentation={dict(tracker.instrumentation_counts)!r}"
                    )
                await asyncio.sleep(1.25)

                assert observer.ready
                assert observer.observed_target_types == PUBLIC_CDP_TARGET_TYPES
                trace_by_session: dict[str, list[str]] = {}
                for session_id, _target_id, _target_type, method in observer.setup_trace:
                    trace_by_session.setdefault(session_id, []).append(method)
                assert trace_by_session
                incomplete_trace = {
                    session_id: methods
                    for session_id, methods in trace_by_session.items()
                    if methods
                    != [
                        "Target.setAutoAttach",
                        "Network.enable",
                        "Runtime.runIfWaitingForDebugger",
                    ]
                }
                assert not incomplete_trace, incomplete_trace
                terminal_types = {terminal.target_type for terminal in tracker.terminals}
                assert terminal_types >= PUBLIC_CDP_TARGET_TYPES, {
                    "observer_ready": observer.ready,
                    "instrumentation": dict(tracker.instrumentation_counts),
                    "terminal_types": terminal_types,
                }
                assert any(
                    terminal.request_class is RequestClass.REDIRECT
                    for terminal in tracker.terminals
                )
                assert any(
                    terminal.request_class is RequestClass.WARMUP for terminal in tracker.terminals
                )
                partials = [
                    terminal
                    for terminal in tracker.terminals
                    if terminal.outcome is TerminalOutcome.PARTIAL_RESPONSE
                ]
                assert partials and all(terminal.encoded_bytes > 0 for terminal in partials)
                assert tracker.non_transport_count >= 1
                assert origin.requests.count("/cacheable") == 1
                assert origin.requests.count("/cached") == 1
                assert not tracker.instrumentation_counts
                rows = tracker.aggregate_rows()
                assert sum(row["attempts"] for row in rows.values()) == len(tracker.terminals)
                assert sum(sum(row["outcomes"].values()) for row in rows.values()) == len(
                    tracker.terminals
                )
            finally:
                await observer.drain_and_detach()
                await context.close()
                await browser.close()
