from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conformance.python.contract import (
    HARD_LIMITS,
    captured_request_fingerprint,
    project_frames,
    semantic_hash,
)
from crawler_runtime_contracts.v1 import runtime_pb2 as pb
from google.protobuf import json_format
from redaction import redact, redact_email

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "fixtures"
VERSION = "crawler.runtime/v1"
ZERO_HASH = hashlib.sha256(b"").hexdigest()


def extension(schema_id: str, value: object) -> pb.ExtensionEnvelope:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return pb.ExtensionEnvelope(
        schema_id=schema_id,
        schema_version=1,
        encoding=pb.EXTENSION_ENCODING_CANONICAL_JSON,
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
    )


def chunk_manifest(*parts: bytes) -> pb.ChunkManifest:
    body = b"".join(parts)
    return pb.ChunkManifest(
        chunks=[
            pb.DataChunk(
                sequence=sequence,
                size_bytes=len(part),
                sha256=hashlib.sha256(part).hexdigest(),
                inline_body=part,
            )
            for sequence, part in enumerate(parts)
            if part
        ],
        total_size_bytes=len(body),
        total_sha256=hashlib.sha256(body).hexdigest(),
        complete=True,
    )


def artifact_chunk_manifest(chunk_count: int = 8) -> pb.ChunkManifest:
    size = HARD_LIMITS.max_artifact_chunk_bytes
    chunks = []
    for sequence in range(chunk_count):
        digest = hashlib.sha256(f"fixture-artifact-chunk-{sequence}".encode()).hexdigest()
        chunks.append(
            pb.DataChunk(
                sequence=sequence,
                size_bytes=size,
                sha256=digest,
                artifact=pb.ArtifactHandle(
                    handle=f"artifact:browser:{sequence:02d}",
                    media_type="application/octet-stream",
                    size_bytes=size,
                    sha256=digest,
                    redacted=True,
                ),
            )
        )
    return pb.ChunkManifest(
        chunks=chunks,
        total_size_bytes=size * chunk_count,
        total_sha256=hashlib.sha256(b"fixture-browser-transfer-total").hexdigest(),
        complete=True,
    )


def write_message(path: Path, message) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json_format.MessageToJson(
            message,
            preserving_proto_field_name=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def operation(sequence: int, role: str, *, parent: str | None = None) -> pb.OriginOperationRef:
    op = pb.OriginOperationRef(
        origin_request_id=f"monitor:board-1:revision-7:due-42/op/{sequence:03d}",
        operation_sequence=sequence,
        role=role,
    )
    if parent is not None:
        op.parent_origin_request_id = parent
    return op


def refresh_fence_digest(context: pb.FencingContext) -> None:
    values = (
        context.shard_id,
        str(context.routing_epoch),
        str(context.engine_owner),
        context.claim_token,
        context.lease_id,
        context.config_revision,
    )
    raw = b"crawler.runtime/v1/fence\0" + "\0".join(values).encode()
    context.fence_digest = hashlib.sha256(raw).digest()


def fencing_context() -> pb.FencingContext:
    context = pb.FencingContext(
        shard_id="crawler-http-00",
        routing_epoch=7,
        engine_owner=pb.ENGINE_OWNER_PYTHON,
        claim_token="claim:board-1:due-42",
        lease_id="lease:board-1:attempt-1",
        config_revision="revision-7",
    )
    refresh_fence_digest(context)
    return context


def manifest(*, scrape: bool = False) -> pb.BoardManifest:
    result = pb.BoardManifest(
        contract_version=VERSION,
        manifest_id="board-1:revision-7",
        board_id="board-1",
        company_id="company-1",
        config_revision="revision-7",
        config_fingerprint=hashlib.sha256(b"fixture-manifest-v7").hexdigest(),
        board_url="https://careers.example.invalid/jobs",
        provider_family="representative-json",
        monitor_type="fixture-json",
        check_interval_ms=3_600_000,
        scrape_interval_ms=86_400_000,
        throttle_key="example.invalid",
        config_extensions=[
            extension("jobseek.runtime.v1/representative-json/monitor-config", {"pages": 2}),
            extension(
                "jobseek.runtime.v1/representative-json/scraper-config",
                {"fields": ["title", "description"]},
            ),
        ],
    )
    if scrape:
        result.scraper_type = "fixture-json"
    return result


def request(
    *, scrape: bool = False, operations: list[pb.OriginOperationRef] | None = None
) -> pb.ExecutionRequest:
    operations = operations or [operation(0, "initial")]
    result = pb.ExecutionRequest(
        contract_version=VERSION,
        request_id="request-scrape-1" if scrape else "request-monitor-1",
        origin_request_id=operations[0].origin_request_id,
        attempt_id="attempt-1",
        kind=pb.EXECUTION_KIND_SCRAPE if scrape else pb.EXECUTION_KIND_MONITOR,
        deadline_rfc3339="2026-08-26T12:00:00Z",
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        tracestate="vendor=value,tenant@system=opaque value",
        board_manifest=manifest(scrape=scrape),
        fencing_context=fencing_context(),
        origin_operations=operations,
    )
    if scrape:
        result.scrape.CopyFrom(
            pb.ScrapeInput(
                source_url="https://careers.example.invalid/jobs/42",
                scraper_type="fixture-json",
                scrape_step=0,
            )
        )
    else:
        result.monitor.CopyFrom(pb.MonitorInput(monitor_type="fixture-json"))
    return result


def browser_plan() -> pb.BrowserPlan:
    navigation = operation(0, "browser-navigation")
    click = operation(1, "browser-action-click", parent=navigation.origin_request_id)
    return pb.BrowserPlan(
        contract_version=VERSION,
        target_url="https://careers.example.invalid/jobs",
        required_capabilities=[
            pb.BROWSER_CAPABILITY_RENDER,
            pb.BROWSER_CAPABILITY_ACTIONS,
            pb.BROWSER_CAPABILITY_RESPONSE_CAPTURE,
            pb.BROWSER_CAPABILITY_EVALUATE,
        ],
        navigation=pb.NavigationPlan(
            wait_until=pb.WAIT_CONDITION_LOAD,
            timeout_ms=30_000,
            origin_request_id=navigation.origin_request_id,
        ),
        actions=[
            pb.BrowserAction(
                action_id="load-details",
                click=pb.ClickAction(
                    selector=pb.Selector(kind=pb.SELECTOR_KIND_CSS, value="button.load-details"),
                    timeout_ms=10_000,
                ),
                origin_request_id=click.origin_request_id,
                network_effect=pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT,
            ),
            pb.BrowserAction(
                action_id="settle-layout",
                wait=pb.WaitAction(duration_ms=50, timeout_ms=1_000),
                network_effect=pb.BROWSER_NETWORK_EFFECT_NONE,
            ),
        ],
        captures=[
            pb.CapturePlan(
                capture_id="jobs-json",
                kind=pb.CAPTURE_KIND_RESPONSE_BODY,
                url_pattern="*/api/jobs*",
                max_bytes=4_096,
            )
        ],
        evaluations=[
            pb.EvaluationPlan(
                evaluation_id="job-count",
                expression="document.querySelectorAll('[data-job]').length",
                max_result_bytes=1_024,
                network_effect=pb.BROWSER_NETWORK_EFFECT_NONE,
            )
        ],
        origin_operations=[navigation, click],
    )


def browser_request(plan: pb.BrowserPlan | None = None) -> pb.ExecutionRequest:
    plan = plan or browser_plan()
    return pb.ExecutionRequest(
        contract_version=VERSION,
        request_id="request-browser-1",
        origin_request_id=plan.origin_operations[0].origin_request_id,
        attempt_id="attempt-1",
        kind=pb.EXECUTION_KIND_BROWSER,
        deadline_rfc3339="2026-08-26T12:00:00Z",
        board_manifest=manifest(),
        fencing_context=fencing_context(),
        browser=pb.BrowserExecutionInput(plan=plan),
        origin_operations=plan.origin_operations,
    )


def browser_result_success(plan: pb.BrowserPlan) -> pb.BrowserResult:
    return pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        success=pb.BrowserSuccess(
            final_url=plan.target_url,
            status=200,
            html=chunk_manifest(b"<html>", b"</html>"),
            action_outcomes=[
                pb.ActionOutcome(action_id=action.action_id, completed=True, duration_ms=5)
                for action in plan.actions
            ],
            captures=[
                pb.CapturedValue(
                    capture_id=plan.captures[0].capture_id,
                    body=chunk_manifest(b'{"jobs":[]}'),
                )
            ],
            evaluations=[
                pb.EvaluationValue(
                    evaluation_id=plan.evaluations[0].evaluation_id,
                    value=extension("jobseek.runtime.v1/browser/evaluation-json", {"value": 0}),
                )
            ],
        ),
    )


def hello_events() -> list[pb.ProtocolEvent]:
    client = pb.ClientHello(
        supported_contract_versions=[VERSION],
        implementation=pb.IMPLEMENTATION_PYTHON,
        requested_limits=HARD_LIMITS,
    )
    accepted = pb.Limits()
    accepted.CopyFrom(HARD_LIMITS)
    accepted.max_in_flight_frames = 4
    server = pb.ServerHello(
        selected_contract_version=VERSION,
        implementation=pb.IMPLEMENTATION_GO,
        accepted_limits=accepted,
        initial_window_frames=4,
        resume_by_origin_request_id=True,
    )
    return [
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(hello=client),
        ),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_SERVER,
            server=pb.ServerMessage(hello=server),
        ),
    ]


def start_event(value: pb.ExecutionRequest) -> pb.ProtocolEvent:
    return pb.ProtocolEvent(
        direction=pb.EVENT_DIRECTION_CLIENT,
        client=pb.ClientMessage(start=value),
    )


def server_frame(value: pb.ExecutionFrame) -> pb.ProtocolEvent:
    return pb.ProtocolEvent(
        direction=pb.EVENT_DIRECTION_SERVER,
        server=pb.ServerMessage(frame=value),
    )


def frame_for_attempt(frame: pb.ExecutionFrame, attempt_id: str) -> pb.ExecutionFrame:
    value = pb.ExecutionFrame()
    value.CopyFrom(frame)
    value.attempt_id = attempt_id
    return value


def origin_frame(
    req: pb.ExecutionRequest, op: pb.OriginOperationRef, sequence: int, *, dedup=False
) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        origin_contact=pb.OriginContact(
            operation=op,
            disposition=(
                pb.ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED
                if dedup
                else pb.ORIGIN_CONTACT_DISPOSITION_DISPATCHED
            ),
            request_fingerprint=hashlib.sha256(op.origin_request_id.encode()).hexdigest(),
        ),
    )


def origin_declaration_frame(
    req: pb.ExecutionRequest, op: pb.OriginOperationRef, sequence: int
) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        origin_operation_declared=pb.OriginOperationDeclared(operation=op),
    )


def monitor_batch(
    *, page: int = 1, hybrid: bool = False, truncated: bool = False
) -> pb.MonitorResult:
    url = f"https://careers.example.invalid/jobs/{page}"
    content = pb.JobContent(title=f"Engineer {page}", description_html="<p>Build safely</p>")
    # Page one proves explicit-empty locations; page two proves missing locations.
    if page == 1:
        content.locations.SetInParent()
    result = pb.MonitorResult(
        urls=[url],
        jobs=[pb.DiscoveredJob(url=url, content=content)],
        filtered_count=2 if page == 1 else 0,
        security_filtered_count=1 if page == 1 else 0,
        metadata_updates=pb.MonitorMetadataUpdates(
            cursor=f"page-{page}",
            extensions=[
                extension(
                    "jobseek.runtime.v1/representative-json/runtime-metadata",
                    {"source": "offline-fixture"},
                )
            ],
        ),
        hybrid=hybrid,
        truncated=truncated,
    )
    if page == 1:
        result.new_sitemap_url = "https://careers.example.invalid/sitemap-current.xml"
    return result


def monitor_frame(
    req: pb.ExecutionRequest, sequence: int, page: int, **kwargs
) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        monitor_batch=monitor_batch(page=page, **kwargs),
    )


def scrape_frame(req: pb.ExecutionRequest, sequence: int) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        scrape_result=pb.ScrapeResult(
            content=pb.JobContent(
                title="Systems Engineer",
                description_html="<p>Deterministic offline content</p>",
                locations=pb.StringList(values=[]),
                language="en",
                skills=["Go", "Python"],
            )
        ),
    )


def browser_frame(
    req: pb.ExecutionRequest, result: pb.BrowserResult, sequence: int
) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        browser_result=result,
    )


def terminal_frame(
    req: pb.ExecutionRequest,
    sequence: int,
    *,
    output: int,
    batches: int,
    origins: int,
    artifacts: int = 0,
    status: int = pb.TERMINAL_STATUS_SUCCESS,
    eligible_for_commit: bool = True,
) -> pb.ExecutionFrame:
    return pb.ExecutionFrame(
        contract_version=VERSION,
        request_id=req.request_id,
        sequence=sequence,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        terminal=pb.Terminal(
            status=status,
            frame_count=sequence,
            output_items=output,
            monitor_batches=batches,
            artifact_count=artifacts,
            origin_operation_count=origins,
            active_duration_ms=25,
            eligible_for_commit=eligible_for_commit,
        ),
    )


def case(
    name: str, transcript: pb.ProtocolTranscript, expected_valid=True, expected_error=""
) -> pb.ConformanceCase:
    return pb.ConformanceCase(
        name=name,
        expected_valid=expected_valid,
        expected_error=expected_error,
        transcript=transcript,
    )


def reconnect_events(
    req: pb.ExecutionRequest, after_sequence: int | None
) -> list[pb.ProtocolEvent]:
    events = hello_events()
    resume = pb.ResumeRequest(
        contract_version=VERSION,
        request_id=req.request_id,
        origin_request_id=req.origin_request_id,
        attempt_id=f"attempt-resume-{0 if after_sequence is None else after_sequence + 1}",
        fencing_context=req.fencing_context,
    )
    if after_sequence is not None:
        resume.after_sequence = after_sequence
    events.append(
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(resume=resume),
        )
    )
    return events


def fault_event(
    point: int, origin_id: str, *, dispatched: bool, sequence: int | None = None
) -> pb.ProtocolEvent:
    fault = pb.DisconnectFault(
        point=point,
        origin_was_dispatched=dispatched,
        origin_request_id=origin_id,
    )
    if sequence is not None:
        fault.sequence = sequence
    return pb.ProtocolEvent(direction=pb.EVENT_DIRECTION_FAULT, fault=fault)


def build_positive() -> None:
    op0 = operation(0, "page-1")
    op1 = operation(1, "page-2", parent=op0.origin_request_id)
    req = request(operations=[op0, op1])
    frames = [
        origin_frame(req, op0, 0),
        monitor_frame(req, 1, 1, hybrid=True),
        origin_frame(req, op1, 2),
        monitor_frame(req, 3, 2, hybrid=True, truncated=True),
        terminal_frame(req, 4, output=2, batches=2, origins=2),
    ]
    events = hello_events() + [start_event(req)] + [server_frame(frame) for frame in frames[:3]]
    events.append(
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                window_update=pb.WindowUpdate(
                    request_id=req.request_id,
                    additional_frames=3,
                    attempt_id=req.attempt_id,
                    fence_digest=req.fencing_context.fence_digest,
                )
            ),
        )
    )
    events.append(server_frame(frames[3]))
    events += [
        fault_event(
            pb.DISCONNECT_POINT_RESULT_BEFORE_TERMINAL,
            op1.origin_request_id,
            dispatched=True,
            sequence=3,
        )
    ]
    events += reconnect_events(req, 3) + [
        server_frame(frame_for_attempt(frames[4], "attempt-resume-4"))
    ]
    transcript = pb.ProtocolTranscript(
        contract_version=VERSION, name="monitor-multi-origin", events=events
    )
    write_message(
        FIXTURES / "conformance/positive/monitor-multi-origin.json",
        case("monitor-multi-origin", transcript),
    )

    scrape_req = request(scrape=True)
    op = scrape_req.origin_operations[0]
    complete = [
        origin_frame(scrape_req, op, 0),
        scrape_frame(scrape_req, 1),
        terminal_frame(scrape_req, 2, output=1, batches=0, origins=1),
    ]
    scenarios = [
        (
            "disconnect-after-dispatch",
            [
                fault_event(
                    pb.DISCONNECT_POINT_AFTER_DISPATCH, op.origin_request_id, dispatched=True
                )
            ],
            None,
            [origin_frame(scrape_req, op, 0, dedup=True), complete[1], complete[2]],
        ),
        (
            "disconnect-before-result-frame",
            [
                server_frame(complete[0]),
                fault_event(
                    pb.DISCONNECT_POINT_BEFORE_FRAME,
                    op.origin_request_id,
                    dispatched=True,
                    sequence=1,
                ),
            ],
            0,
            complete[1:],
        ),
        (
            "disconnect-result-before-terminal",
            [
                server_frame(complete[0]),
                server_frame(complete[1]),
                fault_event(
                    pb.DISCONNECT_POINT_RESULT_BEFORE_TERMINAL,
                    op.origin_request_id,
                    dispatched=True,
                    sequence=1,
                ),
            ],
            1,
            complete[2:],
        ),
        (
            "disconnect-unacknowledged-frame-zero",
            [
                server_frame(complete[0]),
                fault_event(
                    pb.DISCONNECT_POINT_AFTER_FRAME,
                    op.origin_request_id,
                    dispatched=True,
                    sequence=0,
                ),
            ],
            None,
            complete,
        ),
    ]
    for name, before, after, remaining in scenarios:
        events = hello_events() + [start_event(scrape_req)] + before
        resumed_attempt = f"attempt-resume-{0 if after is None else after + 1}"
        events += reconnect_events(scrape_req, after) + [
            server_frame(frame_for_attempt(frame, resumed_attempt)) for frame in remaining
        ]
        transcript = pb.ProtocolTranscript(contract_version=VERSION, name=name, events=events)
        write_message(FIXTURES / f"conformance/positive/{name}.json", case(name, transcript))

    events = hello_events() + [
        start_event(scrape_req),
        server_frame(complete[0]),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_FRAME,
            op.origin_request_id,
            dispatched=True,
            sequence=0,
        ),
    ]
    events += reconnect_events(scrape_req, 0)
    events += [
        server_frame(frame_for_attempt(complete[1], "attempt-resume-1")),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_FRAME,
            op.origin_request_id,
            dispatched=True,
            sequence=1,
        ),
    ]
    events += reconnect_events(scrape_req, None)
    events += [
        server_frame(frame_for_attempt(complete[1], "attempt-resume-0")),
        server_frame(frame_for_attempt(complete[2], "attempt-resume-0")),
    ]
    write_message(
        FIXTURES / "conformance/positive/multi-resume-retains-ack.json",
        case(
            "multi-resume-retains-ack",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="multi-resume-retains-ack",
                events=events,
            ),
        ),
    )

    cancel_req = request(scrape=True)
    events = hello_events() + [start_event(cancel_req)]
    events += [
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                cancel=pb.CancelRequest(
                    request_id=cancel_req.request_id,
                    attempt_id=cancel_req.attempt_id,
                    reason="lease lost",
                    fencing_context=cancel_req.fencing_context,
                )
            ),
        ),
        server_frame(
            terminal_frame(
                cancel_req,
                0,
                output=0,
                batches=0,
                origins=0,
                status=pb.TERMINAL_STATUS_CANCELLED,
                eligible_for_commit=False,
            )
        ),
    ]
    write_message(
        FIXTURES / "conformance/positive/cancelled.json",
        case(
            "cancelled",
            pb.ProtocolTranscript(contract_version=VERSION, name="cancelled", events=events),
        ),
    )

    rejected_req = request(scrape=True)
    events = hello_events() + [start_event(rejected_req)]
    events += [
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH, rejected_req.origin_request_id, dispatched=True
        )
    ]
    events += reconnect_events(rejected_req, None)
    events.append(
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_SERVER,
            server=pb.ServerMessage(
                resume_rejected=pb.ResumeRejected(
                    request_id=rejected_req.request_id,
                    origin_request_id=rejected_req.origin_request_id,
                    attempt_id="attempt-resume-0",
                    fence_digest=rejected_req.fencing_context.fence_digest,
                    error=pb.RuntimeError(
                        code=pb.ERROR_CODE_AMBIGUOUS_ORIGIN,
                        disposition=pb.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
                        message="dedupe record unavailable; reschedule by policy",
                    ),
                )
            ),
        )
    )
    write_message(
        FIXTURES / "conformance/positive/ambiguous-fail-closed.json",
        case(
            "ambiguous-fail-closed",
            pb.ProtocolTranscript(
                contract_version=VERSION, name="ambiguous-fail-closed", events=events
            ),
        ),
    )

    browser_op0 = operation(0, "navigation")
    browser_op1 = operation(1, "pagination", parent=browser_op0.origin_request_id)
    plan = pb.BrowserPlan(
        contract_version=VERSION,
        target_url="https://careers.example.invalid/jobs",
        required_capabilities=[
            pb.BROWSER_CAPABILITY_RENDER,
            pb.BROWSER_CAPABILITY_ACTIONS,
            pb.BROWSER_CAPABILITY_PAGINATION,
        ],
        navigation=pb.NavigationPlan(
            wait_until=pb.WAIT_CONDITION_LOAD,
            timeout_ms=30_000,
            origin_request_id=browser_op0.origin_request_id,
        ),
        session=pb.SessionPlan(),
        actions=[
            pb.BrowserAction(
                action_id="next-page",
                paginate=pb.PaginationAction(
                    next_selector=pb.Selector(kind=pb.SELECTOR_KIND_CSS, value="button.next"),
                    max_pages=2,
                    page_timeout_ms=10_000,
                    dynamic_origin_per_additional_page=True,
                ),
                origin_request_id=browser_op1.origin_request_id,
                network_effect=pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT,
            )
        ],
        origin_operations=[browser_op0, browser_op1],
    )
    write_message(
        FIXTURES / "conformance/positive/browser-plan-multi-origin.json",
        pb.ConformanceCase(
            name="browser-plan-multi-origin", expected_valid=True, browser_plan=plan
        ),
    )
    pagination_req = browser_request(plan)
    dynamic_page = pb.OriginOperationRef(
        origin_request_id="origin:browser-pagination:page-2",
        operation_sequence=2,
        role="pagination-page-2",
        parent_origin_request_id=browser_op1.origin_request_id,
    )
    pagination_result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        success=pb.BrowserSuccess(
            final_url=plan.target_url,
            status=200,
            action_outcomes=[
                pb.ActionOutcome(action_id="next-page", completed=True, duration_ms=10)
            ],
        ),
    )
    pagination_frames = [
        origin_frame(pagination_req, browser_op0, 0),
        origin_frame(pagination_req, browser_op1, 1),
        origin_declaration_frame(pagination_req, dynamic_page, 2),
        origin_frame(pagination_req, dynamic_page, 3),
        browser_frame(pagination_req, pagination_result, 4),
        terminal_frame(pagination_req, 5, output=1, batches=0, origins=3),
    ]
    pagination_events = (
        hello_events()
        + [start_event(pagination_req)]
        + [server_frame(frame) for frame in pagination_frames[:4]]
    )
    pagination_events.append(
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                window_update=pb.WindowUpdate(
                    request_id=pagination_req.request_id,
                    additional_frames=2,
                    attempt_id=pagination_req.attempt_id,
                    fence_digest=pagination_req.fencing_context.fence_digest,
                )
            ),
        )
    )
    pagination_events.extend(server_frame(frame) for frame in pagination_frames[4:])
    write_message(
        FIXTURES / "conformance/positive/browser-pagination-dynamic-origin.json",
        case(
            "browser-pagination-dynamic-origin",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="browser-pagination-dynamic-origin",
                events=pagination_events,
            ),
        ),
    )
    for name, result in {
        "browser-success": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            success=pb.BrowserSuccess(
                final_url="https://careers.example.invalid/jobs",
                status=200,
                html=chunk_manifest(b"<html>", b"</html>"),
            ),
        ),
        "browser-unsupported": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            unsupported=pb.BrowserUnsupported(
                capabilities=[pb.BROWSER_CAPABILITY_PERSISTENT_SESSION]
            ),
        ),
        "browser-target-lost": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            error=pb.BrowserFailure(
                error=pb.RuntimeError(
                    code=pb.ERROR_CODE_TARGET_LOST,
                    disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                    message="target closed",
                )
            ),
        ),
        "browser-session-lost": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            error=pb.BrowserFailure(
                error=pb.RuntimeError(
                    code=pb.ERROR_CODE_SESSION_LOST,
                    disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                    message="session disappeared",
                )
            ),
        ),
        "retry-after-one-day": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            error=pb.BrowserFailure(
                error=pb.RuntimeError(
                    code=pb.ERROR_CODE_TIMEOUT,
                    disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                    message="retry on the next daily window",
                    retry_after_ms=86_400_000,
                )
            ),
        ),
    }.items():
        write_message(
            FIXTURES / f"conformance/positive/{name}.json",
            pb.ConformanceCase(name=name, expected_valid=True, browser_result=result),
        )

    write_message(
        FIXTURES / "conformance/positive/browser-artifact-chunks-64m.json",
        pb.ConformanceCase(
            name="browser-artifact-chunks-64m",
            expected_valid=True,
            browser_result=pb.BrowserResult(
                contract_version=VERSION,
                backend=pb.BROWSER_BACKEND_LIGHTPANDA,
                success=pb.BrowserSuccess(
                    final_url="https://careers.example.invalid/jobs",
                    status=200,
                    html=artifact_chunk_manifest(),
                ),
            ),
        ),
    )

    artifact_req = request(scrape=True)
    artifact_op = artifact_req.origin_operations[0]
    artifact = pb.ArtifactHandle(
        handle="artifact:exchange:001",
        media_type="application/json",
        size_bytes=17,
        sha256=hashlib.sha256(b"diagnostic fixture").hexdigest(),
        redacted=True,
    )
    artifact_frames = [
        origin_frame(artifact_req, artifact_op, 0),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=artifact_req.request_id,
            sequence=1,
            attempt_id=artifact_req.attempt_id,
            fence_digest=artifact_req.fencing_context.fence_digest,
            artifact=pb.ArtifactFrame(artifact=artifact, diagnostic_only=True),
        ),
        scrape_frame(artifact_req, 2),
        terminal_frame(
            artifact_req,
            3,
            output=1,
            batches=0,
            origins=1,
            artifacts=1,
        ),
    ]
    artifact_events = (
        hello_events()
        + [start_event(artifact_req)]
        + [server_frame(frame) for frame in artifact_frames]
    )
    write_message(
        FIXTURES / "conformance/positive/artifact-handle.json",
        case(
            "artifact-handle",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="artifact-handle",
                events=artifact_events,
            ),
        ),
    )

    plan = browser_plan()
    browser_req = browser_request(plan)
    browser_ops = list(browser_req.origin_operations)
    browser_result = browser_result_success(plan)
    browser_frames = [
        origin_frame(browser_req, browser_ops[0], 0),
        origin_frame(browser_req, browser_ops[1], 1),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=browser_req.request_id,
            sequence=2,
            attempt_id=browser_req.attempt_id,
            fence_digest=browser_req.fencing_context.fence_digest,
            browser_result=browser_result,
        ),
        terminal_frame(browser_req, 3, output=1, batches=0, origins=2),
    ]
    browser_events = hello_events() + [start_event(browser_req)]
    browser_events += [server_frame(frame) for frame in browser_frames]
    write_message(
        FIXTURES / "conformance/positive/browser-correlated-success.json",
        case(
            "browser-correlated-success",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="browser-correlated-success",
                events=browser_events,
            ),
        ),
    )

    retry_events = hello_events() + [
        start_event(browser_req),
        server_frame(browser_frames[0]),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH,
            browser_ops[1].origin_request_id,
            dispatched=True,
        ),
    ]
    retry_events += reconnect_events(browser_req, 0)
    retry_events += [
        server_frame(
            frame_for_attempt(
                origin_frame(browser_req, browser_ops[1], 1, dedup=True),
                "attempt-resume-1",
            )
        ),
        server_frame(frame_for_attempt(browser_frames[2], "attempt-resume-1")),
        server_frame(frame_for_attempt(browser_frames[3], "attempt-resume-1")),
    ]
    write_message(
        FIXTURES / "conformance/positive/browser-action-retry-deduplicated.json",
        case(
            "browser-action-retry-deduplicated",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="browser-action-retry-deduplicated",
                events=retry_events,
            ),
        ),
    )

    for name, failed_result in {
        "browser-correlated-error": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            error=pb.BrowserFailure(
                error=pb.RuntimeError(
                    code=pb.ERROR_CODE_TARGET_LOST,
                    disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                    message="target closed",
                )
            ),
        ),
        "browser-correlated-unsupported": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            unsupported=pb.BrowserUnsupported(capabilities=[pb.BROWSER_CAPABILITY_EVALUATE]),
        ),
    }.items():
        fail_req = browser_request(plan)
        fail_frames = [
            origin_frame(fail_req, fail_req.origin_operations[0], 0),
            pb.ExecutionFrame(
                contract_version=VERSION,
                request_id=fail_req.request_id,
                sequence=1,
                attempt_id=fail_req.attempt_id,
                fence_digest=fail_req.fencing_context.fence_digest,
                browser_result=failed_result,
            ),
            terminal_frame(
                fail_req,
                2,
                output=0,
                batches=0,
                origins=1,
                status=pb.TERMINAL_STATUS_ERROR,
                eligible_for_commit=False,
            ),
        ]
        fail_events = hello_events() + [start_event(fail_req)]
        fail_events += [server_frame(frame) for frame in fail_frames]
        write_message(
            FIXTURES / f"conformance/positive/{name}.json",
            case(
                name,
                pb.ProtocolTranscript(contract_version=VERSION, name=name, events=fail_events),
            ),
        )

    monitor_req = request()
    monitor_frames = [
        origin_frame(monitor_req, monitor_req.origin_operations[0], 0),
        monitor_frame(monitor_req, 1, 1),
        terminal_frame(monitor_req, 2, output=1, batches=1, origins=1),
    ]
    artifact_req = request(scrape=True)
    artifact_value = pb.ArtifactHandle(
        handle="artifact:fault-table:01",
        media_type="application/octet-stream",
        size_bytes=16,
        sha256=hashlib.sha256(b"fault-table-artifact").hexdigest(),
        redacted=True,
    )
    artifact_frames = [
        origin_frame(artifact_req, artifact_req.origin_operations[0], 0),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=artifact_req.request_id,
            sequence=1,
            attempt_id=artifact_req.attempt_id,
            fence_digest=artifact_req.fencing_context.fence_digest,
            artifact=pb.ArtifactFrame(
                artifact=artifact_value,
                diagnostic_only=True,
            ),
        ),
        scrape_frame(artifact_req, 2),
        terminal_frame(artifact_req, 3, output=1, batches=0, origins=1, artifacts=1),
    ]
    error_req = request(scrape=True)
    error_frames = [
        origin_frame(error_req, error_req.origin_operations[0], 0),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=error_req.request_id,
            sequence=1,
            attempt_id=error_req.attempt_id,
            fence_digest=error_req.fencing_context.fence_digest,
            error=pb.RuntimeError(
                code=pb.ERROR_CODE_TRANSPORT,
                disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                message="representative transport failure",
            ),
        ),
        terminal_frame(
            error_req,
            2,
            output=0,
            batches=0,
            origins=1,
            status=pb.TERMINAL_STATUS_ERROR,
            eligible_for_commit=False,
        ),
    ]
    dynamic_base = operation(0, "initial")
    dynamic_req = request(operations=[dynamic_base])
    dynamic_op = operation(1, "dynamic-detail", parent=dynamic_base.origin_request_id)
    dynamic_frames = [
        origin_frame(dynamic_req, dynamic_base, 0),
        origin_declaration_frame(dynamic_req, dynamic_op, 1),
        origin_frame(dynamic_req, dynamic_op, 2),
        monitor_frame(dynamic_req, 3, 1),
        terminal_frame(dynamic_req, 4, output=1, batches=1, origins=2),
    ]

    dynamic_resume_events = hello_events() + [
        start_event(dynamic_req),
        server_frame(dynamic_frames[0]),
        server_frame(dynamic_frames[1]),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH,
            dynamic_op.origin_request_id,
            dispatched=True,
        ),
    ]
    dynamic_resume_events += reconnect_events(dynamic_req, 1)
    dynamic_resume_events += [
        server_frame(
            frame_for_attempt(
                origin_frame(dynamic_req, dynamic_op, 2, dedup=True),
                "attempt-resume-2",
            )
        ),
        server_frame(frame_for_attempt(dynamic_frames[3], "attempt-resume-2")),
        server_frame(frame_for_attempt(dynamic_frames[4], "attempt-resume-2")),
    ]
    write_message(
        FIXTURES / "conformance/positive/disconnect-after-dynamic-dispatch-before-contact.json",
        case(
            "disconnect-after-dynamic-dispatch-before-contact",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="disconnect-after-dynamic-dispatch-before-contact",
                events=dynamic_resume_events,
            ),
        ),
    )
    browser_success_req = browser_request(browser_plan())
    browser_success_frames = [
        origin_frame(browser_success_req, browser_success_req.origin_operations[0], 0),
        origin_frame(browser_success_req, browser_success_req.origin_operations[1], 1),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=browser_success_req.request_id,
            sequence=2,
            attempt_id=browser_success_req.attempt_id,
            fence_digest=browser_success_req.fencing_context.fence_digest,
            browser_result=browser_result_success(browser_success_req.browser.plan),
        ),
        terminal_frame(browser_success_req, 3, output=1, batches=0, origins=2),
    ]
    browser_failure_specs = []
    for suffix, result in (
        (
            "error",
            pb.BrowserResult(
                contract_version=VERSION,
                backend=pb.BROWSER_BACKEND_LIGHTPANDA,
                error=pb.BrowserFailure(
                    error=pb.RuntimeError(
                        code=pb.ERROR_CODE_TARGET_LOST,
                        disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                        message="target lost",
                    )
                ),
            ),
        ),
        (
            "unsupported",
            pb.BrowserResult(
                contract_version=VERSION,
                backend=pb.BROWSER_BACKEND_LIGHTPANDA,
                unsupported=pb.BrowserUnsupported(capabilities=[pb.BROWSER_CAPABILITY_EVALUATE]),
            ),
        ),
    ):
        req = browser_request(browser_plan())
        frames = [
            origin_frame(req, req.origin_operations[0], 0),
            pb.ExecutionFrame(
                contract_version=VERSION,
                request_id=req.request_id,
                sequence=1,
                attempt_id=req.attempt_id,
                fence_digest=req.fencing_context.fence_digest,
                browser_result=result,
            ),
            terminal_frame(
                req,
                2,
                output=0,
                batches=0,
                origins=1,
                status=pb.TERMINAL_STATUS_ERROR,
                eligible_for_commit=False,
            ),
        ]
        browser_failure_specs.append((f"browser-{suffix}", req, frames, 1))

    disconnect_specs = [
        ("monitor-batch", monitor_req, monitor_frames, 1),
        ("artifact", artifact_req, artifact_frames, 1),
        ("runtime-error", error_req, error_frames, 1),
        ("dynamic-origin", dynamic_req, dynamic_frames, 2),
        ("browser-success", browser_success_req, browser_success_frames, 2),
        *browser_failure_specs,
    ]
    for frame_name, req, frames, target_index in disconnect_specs:
        for placement in ("before", "after"):
            if placement == "before":
                emitted = frames[:target_index]
                acknowledged = target_index - 1 if emitted else None
                remaining = frames[target_index:]
                point = pb.DISCONNECT_POINT_BEFORE_FRAME
                sequence = None
            else:
                emitted = frames[: target_index + 1]
                acknowledged = target_index
                remaining = frames[target_index + 1 :]
                point = pb.DISCONNECT_POINT_AFTER_FRAME
                sequence = target_index
            dispatched = bool(emitted)
            events = hello_events() + [start_event(req)]
            events += [server_frame(frame) for frame in emitted]
            events.append(
                fault_event(
                    point,
                    req.origin_operations[0].origin_request_id,
                    dispatched=dispatched,
                    sequence=sequence,
                )
            )
            events += reconnect_events(req, acknowledged)
            resumed_attempt = f"attempt-resume-{0 if acknowledged is None else acknowledged + 1}"
            events += [
                server_frame(frame_for_attempt(frame, resumed_attempt)) for frame in remaining
            ]
            name = f"disconnect-{placement}-{frame_name}"
            write_message(
                FIXTURES / f"conformance/positive/{name}.json",
                case(
                    name,
                    pb.ProtocolTranscript(contract_version=VERSION, name=name, events=events),
                ),
            )


def base_scrape_case(name: str) -> pb.ConformanceCase:
    req = request(scrape=True)
    op = req.origin_operations[0]
    frames = [
        origin_frame(req, op, 0),
        scrape_frame(req, 1),
        terminal_frame(req, 2, output=1, batches=0, origins=1),
    ]
    events = hello_events() + [start_event(req)] + [server_frame(frame) for frame in frames]
    return case(name, pb.ProtocolTranscript(contract_version=VERSION, name=name, events=events))


def base_browser_case(name: str) -> pb.ConformanceCase:
    plan = browser_plan()
    req = browser_request(plan)
    result = browser_result_success(plan)
    frames = [
        origin_frame(req, req.origin_operations[0], 0),
        origin_frame(req, req.origin_operations[1], 1),
        pb.ExecutionFrame(
            contract_version=VERSION,
            request_id=req.request_id,
            sequence=2,
            attempt_id=req.attempt_id,
            fence_digest=req.fencing_context.fence_digest,
            browser_result=result,
        ),
        terminal_frame(req, 3, output=1, batches=0, origins=2),
    ]
    events = hello_events() + [start_event(req)] + [server_frame(frame) for frame in frames]
    return case(name, pb.ProtocolTranscript(contract_version=VERSION, name=name, events=events))


def write_invalid(name: str, code: str, value: pb.ConformanceCase) -> None:
    value.name = name
    value.expected_valid = False
    value.expected_error = code
    write_message(FIXTURES / f"conformance/negative/{name}.json", value)


def build_negative() -> None:
    mutations: dict[str, tuple[str, callable]] = {
        "sequence-gap": (
            "sequence",
            lambda c: setattr(c.transcript.events[-2].server.frame, "sequence", 3),
        ),
        "terminal-count": (
            "count",
            lambda c: setattr(c.transcript.events[-1].server.frame.terminal, "output_items", 9),
        ),
        "wrong-kind-frame": (
            "kind",
            lambda c: setattr(
                c.transcript.events[2].client.start, "kind", pb.EXECUTION_KIND_MONITOR
            ),
        ),
        "duplicate-origin-dispatch": (
            "at_most_once",
            lambda c: c.transcript.events.insert(
                -1,
                server_frame(
                    origin_frame(
                        c.transcript.events[2].client.start,
                        c.transcript.events[2].client.start.origin_operations[0],
                        2,
                    )
                ),
            ),
        ),
        "body-limit": (
            "body_limit",
            lambda c: (
                setattr(
                    c.transcript.events[1].server.hello.accepted_limits,
                    "max_inline_body_bytes",
                    16,
                ),
                setattr(
                    c.transcript.events[0].client.hello.requested_limits,
                    "max_inline_body_bytes",
                    16,
                ),
            ),
        ),
        "backpressure": (
            "backpressure",
            lambda c: setattr(
                c.transcript.events[1].server.hello,
                "initial_window_frames",
                1,
            ),
        ),
        "negotiated-limit": (
            "negotiation",
            lambda c: setattr(
                c.transcript.events[0].client.hello.requested_limits,
                "max_frame_bytes",
                1_000,
            ),
        ),
        "artifact-limit": (
            "artifact_limit",
            lambda c: c.transcript.events[3].server.frame.origin_contact.exchange_artifact.CopyFrom(
                pb.ArtifactHandle(
                    handle="artifact:too-large",
                    media_type="application/octet-stream",
                    size_bytes=HARD_LIMITS.max_artifact_chunk_bytes + 1,
                    sha256=ZERO_HASH,
                    redacted=True,
                )
            ),
        ),
        "missing-terminal": ("terminal", lambda c: c.transcript.events.pop()),
        "cancel-then-result": (
            "cancel",
            lambda c: c.transcript.events.insert(
                -2,
                pb.ProtocolEvent(
                    direction=pb.EVENT_DIRECTION_CLIENT,
                    client=pb.ClientMessage(
                        cancel=pb.CancelRequest(
                            request_id="request-scrape-1",
                            attempt_id="attempt-1",
                            reason="test",
                            fencing_context=c.transcript.events[2].client.start.fencing_context,
                        )
                    ),
                ),
            ),
        ),
        "error-policy": (
            "error_policy",
            lambda c: (
                c.transcript.events[-2].server.frame.ClearField("scrape_result"),
                c.transcript.events[-2].server.frame.error.CopyFrom(
                    pb.RuntimeError(
                        code=pb.ERROR_CODE_PROVIDER_GONE,
                        disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                        message="gone",
                    )
                ),
                setattr(
                    c.transcript.events[-1].server.frame.terminal,
                    "status",
                    pb.TERMINAL_STATUS_ERROR,
                ),
                setattr(
                    c.transcript.events[-1].server.frame.terminal,
                    "eligible_for_commit",
                    False,
                ),
                setattr(c.transcript.events[-1].server.frame.terminal, "output_items", 0),
            ),
        ),
        "unknown-error-enum": (
            "enum",
            lambda c: (
                c.transcript.events[-2].server.frame.ClearField("scrape_result"),
                c.transcript.events[-2].server.frame.error.CopyFrom(
                    pb.RuntimeError(
                        code=999,
                        disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                        message="future enum cannot drive v1 policy",
                    )
                ),
                setattr(
                    c.transcript.events[-1].server.frame.terminal,
                    "status",
                    pb.TERMINAL_STATUS_ERROR,
                ),
                setattr(
                    c.transcript.events[-1].server.frame.terminal,
                    "eligible_for_commit",
                    False,
                ),
                setattr(
                    c.transcript.events[-1].server.frame.terminal,
                    "output_items",
                    0,
                ),
            ),
        ),
    }
    for name, (code, mutate) in mutations.items():
        value = base_scrape_case(name)
        mutate(value)
        value.expected_valid = False
        value.expected_error = code
        write_message(FIXTURES / f"conformance/negative/{name}.json", value)

    value = base_scrape_case("cancel-then-success-terminal")
    active_request = value.transcript.events[2].client.start
    value.transcript.events.insert(
        -1,
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                cancel=pb.CancelRequest(
                    request_id=active_request.request_id,
                    attempt_id=active_request.attempt_id,
                    reason="cancel after result",
                    fencing_context=active_request.fencing_context,
                )
            ),
        ),
    )
    write_invalid("cancel-then-success-terminal", "cancel", value)

    monitor_req = request()
    bad = monitor_frame(monitor_req, 1, 1)
    bad.monitor_batch.urls[:] = ["https://careers.example.invalid/jobs/other"]
    events = hello_events() + [
        start_event(monitor_req),
        server_frame(origin_frame(monitor_req, monitor_req.origin_operations[0], 0)),
        server_frame(bad),
        server_frame(terminal_frame(monitor_req, 2, output=1, batches=1, origins=1)),
    ]
    write_message(
        FIXTURES / "conformance/negative/url-job-mismatch.json",
        case(
            "url-job-mismatch",
            pb.ProtocolTranscript(contract_version=VERSION, name="url-job-mismatch", events=events),
            False,
            "url_job",
        ),
    )

    duplicate_req = request()
    duplicate_events = hello_events() + [
        start_event(duplicate_req),
        server_frame(origin_frame(duplicate_req, duplicate_req.origin_operations[0], 0)),
        server_frame(monitor_frame(duplicate_req, 1, 1)),
        server_frame(monitor_frame(duplicate_req, 2, 1)),
        server_frame(terminal_frame(duplicate_req, 3, output=2, batches=2, origins=1)),
    ]
    write_invalid(
        "monitor-url-duplicate-across-batches",
        "duplicate",
        case(
            "monitor-url-duplicate-across-batches",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="monitor-url-duplicate-across-batches",
                events=duplicate_events,
            ),
        ),
    )

    browser = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        unsupported=pb.BrowserUnsupported(),
    )
    write_message(
        FIXTURES / "conformance/negative/browser-unsupported-empty.json",
        pb.ConformanceCase(
            name="browser-unsupported-empty",
            expected_valid=False,
            expected_error="browser_union",
            browser_result=browser,
        ),
    )

    pagination_op0 = operation(0, "navigation")
    pagination_op1 = operation(1, "pagination", parent=pagination_op0.origin_request_id)
    pagination_plan = pb.BrowserPlan(
        contract_version=VERSION,
        target_url="https://careers.example.invalid/jobs",
        required_capabilities=[
            pb.BROWSER_CAPABILITY_RENDER,
            pb.BROWSER_CAPABILITY_ACTIONS,
            pb.BROWSER_CAPABILITY_PAGINATION,
        ],
        navigation=pb.NavigationPlan(
            wait_until=pb.WAIT_CONDITION_LOAD,
            timeout_ms=30_000,
            origin_request_id=pagination_op0.origin_request_id,
        ),
        session=pb.SessionPlan(),
        actions=[
            pb.BrowserAction(
                action_id="next-page",
                paginate=pb.PaginationAction(
                    next_selector=pb.Selector(kind=pb.SELECTOR_KIND_CSS, value="button.next"),
                    max_pages=2,
                    page_timeout_ms=10_000,
                    dynamic_origin_per_additional_page=True,
                ),
                origin_request_id=pagination_op1.origin_request_id,
                network_effect=pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT,
            )
        ],
        origin_operations=[pagination_op0, pagination_op1],
    )
    pagination_req = browser_request(pagination_plan)
    dynamic_page_2 = operation(2, "pagination-page-2", parent=pagination_op1.origin_request_id)
    dynamic_page_3 = operation(3, "pagination-page-3", parent=pagination_op1.origin_request_id)
    pagination_events = hello_events() + [
        start_event(pagination_req),
        server_frame(origin_frame(pagination_req, pagination_op0, 0)),
        server_frame(origin_frame(pagination_req, pagination_op1, 1)),
        server_frame(origin_declaration_frame(pagination_req, dynamic_page_2, 2)),
        server_frame(origin_frame(pagination_req, dynamic_page_2, 3)),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                window_update=pb.WindowUpdate(
                    request_id=pagination_req.request_id,
                    additional_frames=1,
                    attempt_id=pagination_req.attempt_id,
                    fence_digest=pagination_req.fencing_context.fence_digest,
                )
            ),
        ),
        server_frame(origin_declaration_frame(pagination_req, dynamic_page_3, 4)),
    ]
    write_invalid(
        "browser-pagination-exceeds-max-pages",
        "origin_limit",
        case(
            "browser-pagination-exceeds-max-pages",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="browser-pagination-exceeds-max-pages",
                events=pagination_events,
            ),
        ),
    )

    value = base_scrape_case("unknown-terminal-status")
    value.transcript.events[-1].server.frame.terminal.status = 999
    write_invalid("unknown-terminal-status", "enum", value)

    value = base_scrape_case("uppercase-url-host")
    value.transcript.events[
        2
    ].client.start.board_manifest.board_url = "https://Careers.example.invalid/jobs"
    write_invalid("uppercase-url-host", "url", value)

    value = base_scrape_case("non-rfc3339-deadline")
    value.transcript.events[2].client.start.deadline_rfc3339 = "2026-08-26 12:00:00Z"
    write_invalid("non-rfc3339-deadline", "deadline", value)

    for name, url in (
        ("url-port-out-of-range", "https://careers.example.invalid:99999/jobs"),
        ("url-invalid-percent-escape", "https://careers.example.invalid/jobs/%zz"),
        ("url-percent-encoded-host-letter", "https://%63areers.example.invalid/jobs"),
        ("url-percent-encoded-host-dot", "https://careers%2Eexample.invalid/jobs"),
        ("url-ascii-newline", "https://careers.example.invalid/jobs\nnext"),
        ("url-ascii-tab", "https://careers.example.invalid/jobs\tnext"),
        ("url-ascii-nul", "https://careers.example.invalid/jobs\x00next"),
    ):
        value = base_scrape_case(name)
        value.transcript.events[2].client.start.board_manifest.board_url = url
        write_invalid(name, "url", value)

    value = base_scrape_case("execution-frame-count-limit")
    value.transcript.events[0].client.hello.requested_limits.max_execution_frames = 2
    value.transcript.events[1].server.hello.accepted_limits.max_execution_frames = 2
    write_invalid("execution-frame-count-limit", "limit", value)

    value = base_scrape_case("retry-after-hard-limit-negotiation")
    value.transcript.events[0].client.hello.requested_limits.max_retry_after_ms = (
        HARD_LIMITS.max_retry_after_ms + 1
    )
    value.transcript.events[1].server.hello.accepted_limits.max_retry_after_ms = (
        HARD_LIMITS.max_retry_after_ms + 1
    )
    write_invalid("retry-after-hard-limit-negotiation", "limit", value)

    value = base_scrape_case("retry-after-accepted-over-request")
    value.transcript.events[0].client.hello.requested_limits.max_retry_after_ms = 86_400_000
    write_invalid("retry-after-accepted-over-request", "negotiation", value)

    value = base_scrape_case("missing-scrape-result-content")
    value.transcript.events[-2].server.frame.scrape_result.ClearField("content")
    write_invalid("missing-scrape-result-content", "body", value)

    value = base_scrape_case("job-content-domain-string-limit")
    value.transcript.events[-2].server.frame.scrape_result.content.language = "x" * 36
    write_invalid("job-content-domain-string-limit", "domain_limit", value)

    missing_job_req = request()
    missing_job_frame = monitor_frame(missing_job_req, 1, 1)
    missing_job_frame.monitor_batch.jobs[0].ClearField("content")
    missing_job_events = hello_events() + [
        start_event(missing_job_req),
        server_frame(origin_frame(missing_job_req, missing_job_req.origin_operations[0], 0)),
        server_frame(missing_job_frame),
        server_frame(terminal_frame(missing_job_req, 2, output=1, batches=1, origins=1)),
    ]
    write_invalid(
        "missing-discovered-job-content",
        "body",
        case(
            "missing-discovered-job-content",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="missing-discovered-job-content",
                events=missing_job_events,
            ),
        ),
    )

    for name, mutate in (
        (
            "traceparent-zero-trace-id",
            lambda active: setattr(
                active,
                "traceparent",
                "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            ),
        ),
        (
            "traceparent-zero-parent-id",
            lambda active: setattr(
                active,
                "traceparent",
                "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
            ),
        ),
        (
            "tracestate-duplicate-key",
            lambda active: setattr(active, "tracestate", "vendor=one,vendor=two"),
        ),
        (
            "tracestate-newline",
            lambda active: setattr(active, "tracestate", "vendor=one\nnext"),
        ),
        (
            "tracestate-over-512-bytes",
            lambda active: setattr(
                active,
                "tracestate",
                ",".join(f"k{index}={'x' * 20}" for index in range(24)),
            ),
        ),
    ):
        value = base_scrape_case(name)
        mutate(value.transcript.events[2].client.start)
        write_invalid(name, "trace", value)

    value = base_scrape_case("oversize-start-record")
    start_message = value.transcript.events[2].client
    payload_size = len(start_message.SerializeToString())
    start_size = payload_size + max(1, (payload_size.bit_length() + 6) // 7)
    value.transcript.events[0].client.hello.requested_limits.max_frame_bytes = start_size - 1
    value.transcript.events[1].server.hello.accepted_limits.max_frame_bytes = start_size - 1
    write_invalid("oversize-start-record", "frame_limit", value)

    req = request(scrape=True)
    cancel = pb.ClientMessage(
        cancel=pb.CancelRequest(
            request_id=req.request_id,
            attempt_id=req.attempt_id,
            reason="x" * 8_192,
            fencing_context=req.fencing_context,
        )
    )
    events = hello_events() + [
        start_event(req),
        pb.ProtocolEvent(direction=pb.EVENT_DIRECTION_CLIENT, client=cancel),
    ]
    control_case = case(
        "oversize-control-record",
        pb.ProtocolTranscript(
            contract_version=VERSION, name="oversize-control-record", events=events
        ),
    )
    ceiling = len(control_case.transcript.events[2].client.SerializeToString()) + 128
    control_case.transcript.events[0].client.hello.requested_limits.max_frame_bytes = ceiling
    control_case.transcript.events[1].server.hello.accepted_limits.max_frame_bytes = ceiling
    write_invalid("oversize-control-record", "frame_limit", control_case)

    value = base_scrape_case("unregistered-extension")
    extension_value = value.transcript.events[2].client.start.board_manifest.config_extensions[0]
    extension_value.schema_id = "jobseek.runtime.v1/unregistered"
    write_invalid("unregistered-extension", "extension", value)

    value = base_scrape_case("invalid-extension-schema")
    value.transcript.events[2].client.start.board_manifest.config_extensions[0].CopyFrom(
        extension(
            "jobseek.runtime.v1/representative-json/monitor-config",
            {"pages": "two"},
        )
    )
    write_invalid("invalid-extension-schema", "extension_schema", value)

    value = base_scrape_case("extension-forbidden-context")
    value.transcript.events[-2].server.frame.scrape_result.content.extensions.append(
        extension(
            "jobseek.runtime.v1/representative-json/monitor-config",
            {"pages": 2},
        )
    )
    write_invalid("extension-forbidden-context", "extension_context", value)

    req = request(scrape=True)
    rejected = pb.ResumeRejected(
        request_id=req.request_id,
        origin_request_id=req.origin_request_id,
        attempt_id=req.attempt_id,
        fence_digest=req.fencing_context.fence_digest,
        error=pb.RuntimeError(
            code=pb.ERROR_CODE_AMBIGUOUS_ORIGIN,
            disposition=pb.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
            message="not resumable",
        ),
    )
    events = hello_events() + [
        start_event(req),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_SERVER,
            server=pb.ServerMessage(resume_rejected=rejected),
        ),
    ]
    write_invalid(
        "resume-rejected-without-resume",
        "resume",
        case(
            "resume-rejected-without-resume",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="resume-rejected-without-resume",
                events=events,
            ),
        ),
    )

    req = request(scrape=True)
    events = hello_events() + [
        start_event(req),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                cancel=pb.CancelRequest(
                    request_id=req.request_id,
                    attempt_id="attempt-stale",
                    reason="stale caller",
                    fencing_context=req.fencing_context,
                )
            ),
        ),
    ]
    write_invalid(
        "cancel-stale-attempt",
        "cancel",
        case(
            "cancel-stale-attempt",
            pb.ProtocolTranscript(
                contract_version=VERSION, name="cancel-stale-attempt", events=events
            ),
        ),
    )

    for name, fault, expected in (
        (
            "disconnect-undeclared-origin",
            fault_event(
                pb.DISCONNECT_POINT_BEFORE_FRAME,
                "origin:undeclared",
                dispatched=False,
            ),
            "origin",
        ),
        (
            "after-dispatch-flag-false",
            fault_event(
                pb.DISCONNECT_POINT_AFTER_DISPATCH,
                req.origin_request_id,
                dispatched=False,
            ),
            "disconnect",
        ),
    ):
        events = hello_events() + [start_event(req), fault]
        write_invalid(
            name,
            expected,
            case(
                name,
                pb.ProtocolTranscript(contract_version=VERSION, name=name, events=events),
            ),
        )

    req = request(scrape=True)
    events = hello_events() + [
        start_event(req),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH,
            req.origin_request_id,
            dispatched=True,
        ),
    ]
    events += reconnect_events(req, None)
    events += [
        server_frame(frame_for_attempt(scrape_frame(req, 0), "attempt-resume-0")),
        server_frame(
            frame_for_attempt(
                terminal_frame(req, 1, output=1, batches=0, origins=1),
                "attempt-resume-0",
            )
        ),
    ]
    write_invalid(
        "after-dispatch-resume-missing-dedup",
        "dedupe",
        case(
            "after-dispatch-resume-missing-dedup",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="after-dispatch-resume-missing-dedup",
                events=events,
            ),
        ),
    )

    value = base_scrape_case("stale-frame-fence")
    value.transcript.events[3].server.frame.fence_digest = b"\0" * 32
    write_invalid("stale-frame-fence", "fence", value)

    value = base_scrape_case("invalid-request-fence-digest")
    value.transcript.events[2].client.start.fencing_context.fence_digest = b"\0" * 32
    write_invalid("invalid-request-fence-digest", "fence", value)

    collision_values = (
        ("fencing-nul-join-collision-left", "claim\x00lease", "tail"),
        ("fencing-nul-join-collision-right", "claim", "lease\x00tail"),
    )
    collision_digests: list[bytes] = []
    for name, claim_token, lease_id in collision_values:
        value = base_scrape_case(name)
        context = value.transcript.events[2].client.start.fencing_context
        context.claim_token = claim_token
        context.lease_id = lease_id
        refresh_fence_digest(context)
        collision_digests.append(context.fence_digest)
        for event in value.transcript.events[3:]:
            if event.WhichOneof("event") == "server":
                event.server.frame.fence_digest = context.fence_digest
        write_invalid(name, "fence", value)
    assert collision_digests[0] == collision_digests[1]

    value = base_scrape_case("stale-window-fence")
    active = value.transcript.events[2].client.start
    value.transcript.events.insert(
        3,
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                window_update=pb.WindowUpdate(
                    request_id=active.request_id,
                    additional_frames=1,
                    attempt_id=active.attempt_id,
                    fence_digest=b"\0" * 32,
                )
            ),
        ),
    )
    write_invalid("stale-window-fence", "fence", value)

    value = base_scrape_case("window-credit-uint32-overflow")
    active = value.transcript.events[2].client.start
    value.transcript.events.insert(
        3,
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                window_update=pb.WindowUpdate(
                    request_id=active.request_id,
                    additional_frames=2**32 - 1,
                    attempt_id=active.attempt_id,
                    fence_digest=active.fencing_context.fence_digest,
                )
            ),
        ),
    )
    write_invalid("window-credit-uint32-overflow", "backpressure", value)

    req = request(scrape=True)
    stale_context = pb.FencingContext()
    stale_context.CopyFrom(req.fencing_context)
    stale_context.routing_epoch -= 1
    refresh_fence_digest(stale_context)
    events = hello_events() + [
        start_event(req),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                cancel=pb.CancelRequest(
                    request_id=req.request_id,
                    attempt_id=req.attempt_id,
                    reason="stale fence",
                    fencing_context=stale_context,
                )
            ),
        ),
    ]
    write_invalid(
        "stale-cancel-fence",
        "fence",
        case(
            "stale-cancel-fence",
            pb.ProtocolTranscript(
                contract_version=VERSION, name="stale-cancel-fence", events=events
            ),
        ),
    )

    events = hello_events() + [
        start_event(req),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH,
            req.origin_request_id,
            dispatched=True,
        ),
    ]
    events += reconnect_events(req, None)
    events[-1].client.resume.fencing_context.CopyFrom(stale_context)
    write_invalid(
        "stale-resume-fence",
        "fence",
        case(
            "stale-resume-fence",
            pb.ProtocolTranscript(
                contract_version=VERSION, name="stale-resume-fence", events=events
            ),
        ),
    )

    events = hello_events() + [
        start_event(req),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_DISPATCH,
            req.origin_request_id,
            dispatched=True,
        ),
    ]
    events += reconnect_events(req, None)
    events.append(
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_SERVER,
            server=pb.ServerMessage(
                resume_rejected=pb.ResumeRejected(
                    request_id=req.request_id,
                    origin_request_id=req.origin_request_id,
                    attempt_id="attempt-resume-0",
                    fence_digest=b"\0" * 32,
                    error=pb.RuntimeError(
                        code=pb.ERROR_CODE_AMBIGUOUS_ORIGIN,
                        disposition=pb.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
                        message="stale fence",
                    ),
                )
            ),
        )
    )
    write_invalid(
        "stale-resume-rejection-fence",
        "fence",
        case(
            "stale-resume-rejection-fence",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="stale-resume-rejection-fence",
                events=events,
            ),
        ),
    )

    req = request(scrape=True)
    complete = [
        origin_frame(req, req.origin_operations[0], 0),
        scrape_frame(req, 1),
        terminal_frame(req, 2, output=1, batches=0, origins=1),
    ]
    events = hello_events() + [
        start_event(req),
        server_frame(complete[0]),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_FRAME,
            req.origin_request_id,
            dispatched=True,
            sequence=0,
        ),
    ]
    events += reconnect_events(req, 0)
    events += [
        server_frame(frame_for_attempt(complete[1], "attempt-resume-1")),
        fault_event(
            pb.DISCONNECT_POINT_AFTER_FRAME,
            req.origin_request_id,
            dispatched=True,
            sequence=1,
        ),
    ]
    events += reconnect_events(req, None)
    events.append(server_frame(frame_for_attempt(complete[0], "attempt-resume-0")))
    write_invalid(
        "multi-resume-ack-regression",
        "sequence",
        case(
            "multi-resume-ack-regression",
            pb.ProtocolTranscript(
                contract_version=VERSION,
                name="multi-resume-ack-regression",
                events=events,
            ),
        ),
    )

    for name, dynamic, expected in (
        (
            "dynamic-origin-empty-role",
            pb.OriginOperationRef(
                origin_request_id="dynamic:operation:1",
                operation_sequence=1,
                role="",
            ),
            "text",
        ),
        (
            "dynamic-origin-unknown-parent",
            pb.OriginOperationRef(
                origin_request_id="dynamic:operation:1",
                operation_sequence=1,
                role="detail",
                parent_origin_request_id="origin:unknown",
            ),
            "origin_parent",
        ),
    ):
        value = base_scrape_case(name)
        request_value = value.transcript.events[2].client.start
        dynamic_frame = origin_declaration_frame(request_value, dynamic, 2)
        value.transcript.events.insert(-1, server_frame(dynamic_frame))
        terminal = value.transcript.events[-1].server.frame
        terminal.sequence = 3
        terminal.terminal.frame_count = 3
        terminal.terminal.origin_operation_count = 2
        write_invalid(name, expected, value)

    value = base_scrape_case("dynamic-origin-contact-without-declaration")
    request_value = value.transcript.events[2].client.start
    undeclared = operation(
        1, "dynamic-detail", parent=request_value.origin_operations[0].origin_request_id
    )
    value.transcript.events.insert(-1, server_frame(origin_frame(request_value, undeclared, 2)))
    terminal = value.transcript.events[-1].server.frame
    terminal.sequence = 3
    terminal.terminal.frame_count = 3
    terminal.terminal.origin_operation_count = 2
    write_invalid("dynamic-origin-contact-without-declaration", "origin", value)

    value = base_browser_case("commit-eligible-missing-declared-origin")
    del value.transcript.events[4]
    result_frame = value.transcript.events[4].server.frame
    result_frame.sequence = 1
    terminal = value.transcript.events[5].server.frame
    terminal.sequence = 2
    terminal.terminal.frame_count = 2
    terminal.terminal.origin_operation_count = 1
    write_invalid("commit-eligible-missing-declared-origin", "terminal", value)

    plan = browser_plan()
    plan.actions[0].ClearField("origin_request_id")
    write_invalid(
        "origin-capable-action-missing-id",
        "origin",
        pb.ConformanceCase(browser_plan=plan),
    )

    plan = browser_plan()
    plan.actions[0].origin_request_id = plan.navigation.origin_request_id
    write_invalid(
        "browser-origin-id-reused",
        "origin",
        pb.ConformanceCase(browser_plan=plan),
    )

    plan = browser_plan()
    plan.origin_operations.append(
        operation(
            2,
            "unused-browser-operation",
            parent=plan.origin_operations[1].origin_request_id,
        )
    )
    write_invalid(
        "browser-origin-operation-unused",
        "origin",
        pb.ConformanceCase(browser_plan=plan),
    )

    plan = browser_plan()
    plan.actions[0].ClearField("click")
    plan.actions[0].paginate.CopyFrom(
        pb.PaginationAction(
            next_selector=pb.Selector(kind=pb.SELECTOR_KIND_CSS, value="button.next"),
            max_pages=2,
            page_timeout_ms=10_000,
        )
    )
    plan.required_capabilities.append(pb.BROWSER_CAPABILITY_PAGINATION)
    write_invalid(
        "browser-pagination-missing-dynamic-origin-allocation",
        "origin",
        pb.ConformanceCase(browser_plan=plan),
    )

    plan = browser_plan()
    plan.actions[0].network_effect = 999
    write_invalid(
        "unknown-browser-network-effect",
        "enum",
        pb.ConformanceCase(browser_plan=plan),
    )

    plan = browser_plan()
    plan.navigation.wait_until = 999
    write_invalid("unknown-wait-condition", "enum", pb.ConformanceCase(browser_plan=plan))

    capability_plans: list[tuple[str, pb.BrowserPlan]] = []
    plan = browser_plan()
    plan.required_capabilities.remove(pb.BROWSER_CAPABILITY_RENDER)
    capability_plans.append(("missing-render-capability", plan))
    plan = browser_plan()
    plan.required_capabilities.remove(pb.BROWSER_CAPABILITY_EVALUATE)
    capability_plans.append(("missing-evaluate-capability", plan))
    plan = browser_plan()
    plan.session.headful_identity = True
    capability_plans.append(("missing-headful-capability", plan))
    plan = browser_plan()
    plan.session.proxy_policy_ref = "proxy:policy:1"
    capability_plans.append(("missing-proxy-capability", plan))
    plan = browser_plan()
    plan.interceptions.append(pb.InterceptionRule(url_pattern="*/analytics/*", block=True))
    capability_plans.append(("missing-interception-capability", plan))
    plan = browser_plan()
    plan.actions[0].click.selector.frame_name = "jobs-frame"
    capability_plans.append(("missing-frames-capability", plan))
    plan = browser_plan()
    plan.required_capabilities.remove(pb.BROWSER_CAPABILITY_RESPONSE_CAPTURE)
    capability_plans.append(("missing-response-capture-capability", plan))
    plan = browser_plan()
    plan.navigation.headers.append(pb.Header(name="accept-language", value="en"))
    capability_plans.append(("missing-transport-overrides-capability", plan))
    for name, plan in capability_plans:
        write_invalid(name, "capability", pb.ConformanceCase(browser_plan=plan))

    plan = browser_plan()
    plan.session.persistent = True
    plan.required_capabilities.append(pb.BROWSER_CAPABILITY_PERSISTENT_SESSION)
    write_invalid(
        "persistent-session-missing-key",
        "browser_session",
        pb.ConformanceCase(browser_plan=plan),
    )

    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=999,
        success=pb.BrowserSuccess(final_url="https://careers.example.invalid/jobs"),
    )
    write_invalid("unknown-browser-backend", "enum", pb.ConformanceCase(browser_result=result))

    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        unsupported=pb.BrowserUnsupported(capabilities=[999]),
    )
    write_invalid(
        "unknown-browser-unsupported-capability",
        "enum",
        pb.ConformanceCase(browser_result=result),
    )

    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        unsupported=pb.BrowserUnsupported(
            capabilities=[pb.BROWSER_CAPABILITY_EVALUATE, pb.BROWSER_CAPABILITY_EVALUATE]
        ),
    )
    write_invalid(
        "duplicate-browser-unsupported-capability",
        "duplicate",
        pb.ConformanceCase(browser_result=result),
    )

    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        error=pb.BrowserFailure(
            error=pb.RuntimeError(
                code=pb.ERROR_CODE_TIMEOUT,
                disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                message="retry hint exceeds scheduling ceiling",
                retry_after_ms=HARD_LIMITS.max_retry_after_ms + 1,
            )
        ),
    )
    write_invalid(
        "retry-after-limit",
        "limit",
        pb.ConformanceCase(browser_result=result),
    )

    browser_mutations: dict[str, tuple[str, callable]] = {
        "browser-result-missing-action": (
            "browser_result",
            lambda c: c.transcript.events[
                5
            ].server.frame.browser_result.success.action_outcomes.pop(),
        ),
        "browser-result-duplicate-action": (
            "browser_result",
            lambda c: (
                c.transcript.events[5]
                .server.frame.browser_result.success.action_outcomes.add()
                .CopyFrom(
                    c.transcript.events[5].server.frame.browser_result.success.action_outcomes[0]
                )
            ),
        ),
        "browser-result-unknown-action": (
            "browser_result",
            lambda c: setattr(
                c.transcript.events[5].server.frame.browser_result.success.action_outcomes[0],
                "action_id",
                "unknown-action",
            ),
        ),
        "browser-result-incomplete-action": (
            "browser_result",
            lambda c: setattr(
                c.transcript.events[5].server.frame.browser_result.success.action_outcomes[0],
                "completed",
                False,
            ),
        ),
        "browser-result-missing-capture": (
            "browser_result",
            lambda c: c.transcript.events[5].server.frame.browser_result.success.captures.pop(),
        ),
        "browser-result-missing-evaluation": (
            "browser_result",
            lambda c: c.transcript.events[5].server.frame.browser_result.success.evaluations.pop(),
        ),
    }
    for name, (code, mutate) in browser_mutations.items():
        value = base_browser_case(name)
        mutate(value)
        write_invalid(name, code, value)

    value = base_browser_case("browser-capture-planned-byte-limit")
    value.transcript.events[2].client.start.browser.plan.captures[0].max_bytes = 1
    write_invalid("browser-capture-planned-byte-limit", "transfer_limit", value)

    value = base_browser_case("browser-capture-artifact-only-inline")
    value.transcript.events[2].client.start.browser.plan.captures[0].artifact_only = True
    write_invalid("browser-capture-artifact-only-inline", "browser_result", value)

    value = base_browser_case("browser-evaluation-planned-byte-limit")
    value.transcript.events[2].client.start.browser.plan.evaluations[0].max_result_bytes = 1
    write_invalid("browser-evaluation-planned-byte-limit", "transfer_limit", value)

    value = base_browser_case("browser-action-duration-limit")
    value.transcript.events[5].server.frame.browser_result.success.action_outcomes[
        0
    ].duration_ms = HARD_LIMITS.max_active_duration_ms + 1
    write_invalid("browser-action-duration-limit", "limit", value)

    for name, outcome in {
        "commit-eligible-browser-error": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            error=pb.BrowserFailure(
                error=pb.RuntimeError(
                    code=pb.ERROR_CODE_TARGET_LOST,
                    disposition=pb.ERROR_DISPOSITION_RETRY_POLICY,
                    message="target closed",
                )
            ),
        ),
        "commit-eligible-browser-unsupported": pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            unsupported=pb.BrowserUnsupported(capabilities=[pb.BROWSER_CAPABILITY_EVALUATE]),
        ),
    }.items():
        value = base_browser_case(name)
        value.transcript.events[5].server.frame.browser_result.CopyFrom(outcome)
        value.transcript.events[-1].server.frame.terminal.output_items = 0
        write_invalid(name, "terminal", value)

    for name, mutate, code in (
        (
            "chunk-sequence-gap",
            lambda r: setattr(r.success.html.chunks[1], "sequence", 3),
            "chunk_sequence",
        ),
        (
            "chunk-incomplete",
            lambda r: setattr(r.success.html, "complete", False),
            "chunk",
        ),
        (
            "chunk-total-size-mismatch",
            lambda r: setattr(r.success.html, "total_size_bytes", 1),
            "transfer_limit",
        ),
        (
            "chunk-artifact-metadata-mismatch",
            lambda r: setattr(r.success.html.chunks[0], "size_bytes", 1),
            "chunk",
        ),
    ):
        result = pb.BrowserResult(
            contract_version=VERSION,
            backend=pb.BROWSER_BACKEND_LIGHTPANDA,
            success=pb.BrowserSuccess(
                final_url="https://careers.example.invalid/jobs",
                html=artifact_chunk_manifest(),
            ),
        )
        mutate(result)
        write_invalid(name, code, pb.ConformanceCase(browser_result=result))

    artifacts = []
    for index in range(65):
        body = f"artifact-{index}".encode()
        artifacts.append(
            pb.ArtifactHandle(
                handle=f"artifact:count:{index:02d}",
                media_type="application/octet-stream",
                size_bytes=1,
                sha256=hashlib.sha256(body).hexdigest(),
                redacted=True,
            )
        )
    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        success=pb.BrowserSuccess(
            final_url="https://careers.example.invalid/jobs", artifacts=artifacts
        ),
    )
    write_invalid(
        "browser-artifact-count-limit",
        "artifact_limit",
        pb.ConformanceCase(browser_result=result),
    )

    artifacts = []
    for index in range(9):
        digest = hashlib.sha256(f"large-artifact-{index}".encode()).hexdigest()
        artifacts.append(
            pb.ArtifactHandle(
                handle=f"artifact:large:{index:02d}",
                media_type="application/octet-stream",
                size_bytes=HARD_LIMITS.max_artifact_chunk_bytes,
                sha256=digest,
                redacted=True,
            )
        )
    result = pb.BrowserResult(
        contract_version=VERSION,
        backend=pb.BROWSER_BACKEND_LIGHTPANDA,
        success=pb.BrowserSuccess(
            final_url="https://careers.example.invalid/jobs", artifacts=artifacts
        ),
    )
    write_invalid(
        "browser-artifact-bytes-limit",
        "transfer_limit",
        pb.ConformanceCase(browser_result=result),
    )

    value = base_browser_case("terminal-nested-artifact-count")
    digest = hashlib.sha256(b"nested-browser-artifact").hexdigest()
    value.transcript.events[5].server.frame.browser_result.success.artifacts.append(
        pb.ArtifactHandle(
            handle="artifact:nested:01",
            media_type="application/octet-stream",
            size_bytes=23,
            sha256=digest,
            redacted=True,
        )
    )
    write_invalid("terminal-nested-artifact-count", "count", value)

    replay_path = FIXTURES / "replay/representative-paginated-monitor.json"
    if replay_path.exists():

        def replay_case(name: str) -> pb.ConformanceCase:
            replay = pb.ReplayCase()
            json_format.Parse(replay_path.read_text(), replay, ignore_unknown_fields=False)
            return pb.ConformanceCase(replay=replay)

        value = replay_case("unknown-replay-adapter")
        value.replay.adapter = 999
        write_invalid("unknown-replay-adapter", "enum", value)

        value = replay_case("replay-operation-mismatch")
        value.replay.exchanges[0].operation.role = "changed-role"
        write_invalid("replay-operation-mismatch", "origin", value)

        value = replay_case("replay-frame-sequence-gap")
        value.replay.expected_frames[1].sequence = 9
        write_invalid("replay-frame-sequence-gap", "sequence", value)

        value = replay_case("replay-missing-terminal")
        value.replay.expected_frames.pop()
        write_invalid("replay-missing-terminal", "terminal", value)

        value = replay_case("replay-incomplete-operation-coverage")
        value.replay.exchanges.pop()
        write_invalid("replay-incomplete-operation-coverage", "replay", value)

        value = replay_case("replay-duplicate-result-mapping")
        value.replay.exchanges[1].normalized_result_frame_sequence = value.replay.exchanges[
            0
        ].normalized_result_frame_sequence
        write_invalid("replay-duplicate-result-mapping", "replay", value)

        value = replay_case("replay-missing-result-mapping")
        value.replay.exchanges[1].ClearField("normalized_result_frame_sequence")
        write_invalid("replay-missing-result-mapping", "replay", value)

        value = replay_case("replay-lowercase-http-method")
        value.replay.exchanges[0].request.method = "get"
        write_invalid("replay-lowercase-http-method", "replay", value)

        value = replay_case("replay-invalid-http-status")
        value.replay.exchanges[0].response.status = 99
        write_invalid("replay-invalid-http-status", "http_status", value)

        value = replay_case("replay-invalid-semantic-hash")
        value.replay.expected_semantic_sha256 = "not-a-sha256"
        write_invalid("replay-invalid-semantic-hash", "hash", value)

        value = replay_case("replay-request-fingerprint-mismatch")
        value.replay.expected_frames[0].origin_contact.request_fingerprint = ZERO_HASH
        write_invalid("replay-request-fingerprint-mismatch", "fingerprint", value)

        value = replay_case("replay-request-target-semantic-mutation")
        value.replay.execution_request.board_manifest.board_url = (
            "https://careers.example.invalid/other-board"
        )
        value.replay.expected_projection.CopyFrom(
            project_frames(list(value.replay.expected_frames), value.replay.execution_request)
        )
        write_invalid("replay-request-target-semantic-mutation", "hash", value)

        value = replay_case("replay-plaintext-api-key-body")
        value.replay.exchanges[0].request.body.CopyFrom(chunk_manifest(b"api_key=fixture-secret"))
        write_invalid("replay-plaintext-api-key-body", "redaction", value)

        value = replay_case("replay-plaintext-api-hyphen-key-body")
        value.replay.exchanges[0].request.body.CopyFrom(chunk_manifest(b"api-key=fixture-secret"))
        write_invalid("replay-plaintext-api-hyphen-key-body", "redaction", value)

        value = replay_case("replay-json-escaped-api-key-body")
        value.replay.exchanges[0].request.body.CopyFrom(
            chunk_manifest(rb'{"api\u005fkey":"fixture-secret"}')
        )
        write_invalid("replay-json-escaped-api-key-body", "redaction", value)

        value = replay_case("replay-percent-encoded-api-key-body")
        value.replay.exchanges[0].request.headers[2].value = "application/x-www-form-urlencoded"
        value.replay.exchanges[0].request.body.CopyFrom(chunk_manifest(b"api_key%3Dfixture-secret"))
        write_invalid("replay-percent-encoded-api-key-body", "redaction", value)

        value = replay_case("replay-plaintext-email-split-across-body-chunks")
        value.replay.exchanges[0].response.body.CopyFrom(
            chunk_manifest(b'{"contact":"person@', b'example.test"}')
        )
        write_invalid("replay-plaintext-email-split-across-body-chunks", "redaction", value)

        value = replay_case("replay-plaintext-secret-query")
        value.replay.exchanges[0].request.url += "&api_key=fixture-secret"
        write_invalid("replay-plaintext-secret-query", "redaction", value)

        value = replay_case("replay-plaintext-secret-header")
        value.replay.exchanges[0].request.headers[0].value = "api_key=fixture-secret"
        write_invalid("replay-plaintext-secret-header", "redaction", value)

        value = replay_case("replay-plaintext-client-secret-header")
        value.replay.exchanges[0].request.headers[0].name = "client-secret"
        value.replay.exchanges[0].request.headers[0].value = "fixture-secret"
        write_invalid("replay-plaintext-client-secret-header", "redaction", value)

        value = replay_case("replay-invalid-header-token")
        value.replay.exchanges[0].request.headers[0].name = "bad header"
        write_invalid("replay-invalid-header-token", "header", value)

    # This raw fixture deliberately violates the protobuf oneof. Both bindings
    # must reject it during strict protobuf-JSON decoding.
    raw = json.loads(
        json_format.MessageToJson(
            pb.ConformanceCase(
                name="browser-union-partial-output",
                expected_valid=False,
                expected_error="parse",
                browser_result=pb.BrowserResult(
                    contract_version=VERSION,
                    backend=pb.BROWSER_BACKEND_LIGHTPANDA,
                    unsupported=pb.BrowserUnsupported(capabilities=[pb.BROWSER_CAPABILITY_FRAMES]),
                ),
            )
        )
    )
    raw["browserResult"]["success"] = {
        "finalUrl": "https://careers.example.invalid/jobs",
        "html": "<html>partial</html>",
    }
    path = FIXTURES / "conformance/negative/browser-union-partial-output.json"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")


def captured_exchange(
    op: pb.OriginOperationRef,
    response_message=None,
    *,
    normalized_result_frame_sequence: int | None = None,
) -> pb.CapturedExchange:
    request_body = json.dumps(
        {
            "api_key": redact("body:request:/api_key", "fixture-token"),
            "email": redact_email("body:request:/email", "person@example.test"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    response_body = (
        json_format.MessageToJson(response_message, sort_keys=True).encode()
        if response_message is not None
        else b"{}"
    )
    result = pb.CapturedExchange(
        operation=op,
        request=pb.CapturedRequest(
            method="GET",
            url=(
                "https://careers.example.invalid/api/jobs"
                f"?page={op.operation_sequence + 1}"
                f"&api_key={redact('query:api_key', 'fixture-token')}"
                f"&email={redact_email('query:email', 'person@example.test')}"
            ),
            headers=[
                pb.Header(name="accept", value="application/json"),
                pb.Header(
                    name="authorization",
                    value=redact("header:authorization", "fixture-token"),
                    redacted=True,
                ),
                pb.Header(name="content-type", value="application/json"),
            ],
            body=chunk_manifest(request_body),
        ),
        response=pb.CapturedResponse(
            status=200,
            headers=[pb.Header(name="content-type", value="application/json")],
            body=chunk_manifest(
                response_body[: len(response_body) // 2],
                response_body[len(response_body) // 2 :],
            ),
        ),
        deterministically_redacted=True,
    )
    if normalized_result_frame_sequence is not None:
        result.normalized_result_frame_sequence = normalized_result_frame_sequence
    return result


def bind_replay_fingerprints(
    frames: list[pb.ExecutionFrame], exchanges: list[pb.CapturedExchange]
) -> None:
    contacts = [
        frame.origin_contact for frame in frames if frame.WhichOneof("payload") == "origin_contact"
    ]
    if len(contacts) != len(exchanges):
        raise AssertionError("replay contacts and captured exchanges differ")
    for contact, exchange in zip(contacts, exchanges, strict=True):
        contact.request_fingerprint = captured_request_fingerprint(exchange.request)


def build_replay() -> None:
    op0 = operation(0, "page-1")
    op1 = operation(1, "page-2", parent=op0.origin_request_id)
    req = request(operations=[op0, op1])
    frames = [
        origin_frame(req, op0, 0),
        monitor_frame(req, 1, 1, hybrid=True),
        origin_frame(req, op1, 2),
        monitor_frame(req, 3, 2, hybrid=True, truncated=True),
        terminal_frame(req, 4, output=2, batches=2, origins=2),
    ]
    projection = project_frames(frames, req)
    exchanges = [
        captured_exchange(op0, frames[1].monitor_batch, normalized_result_frame_sequence=1),
        captured_exchange(op1, frames[3].monitor_batch, normalized_result_frame_sequence=3),
    ]
    bind_replay_fingerprints(frames, exchanges)
    replay = pb.ReplayCase(
        contract_version=VERSION,
        name="representative-paginated-monitor",
        provider_family="representative-json",
        adapter=pb.REPLAY_ADAPTER_NORMALIZED_MONITOR_JSON,
        execution_request=req,
        exchanges=exchanges,
        expected_frames=frames,
        expected_projection=projection,
    )
    replay.expected_semantic_sha256 = semantic_hash(frames, projection)
    write_message(FIXTURES / "replay/representative-paginated-monitor.json", replay)

    scrape_req = request(scrape=True)
    scrape_op = scrape_req.origin_operations[0]
    scrape_frames = [
        origin_frame(scrape_req, scrape_op, 0),
        scrape_frame(scrape_req, 1),
        terminal_frame(scrape_req, 2, output=1, batches=0, origins=1),
    ]
    scrape_projection = project_frames(scrape_frames, scrape_req)
    scrape_exchanges = [
        captured_exchange(
            scrape_op,
            scrape_frames[1].scrape_result,
            normalized_result_frame_sequence=1,
        )
    ]
    bind_replay_fingerprints(scrape_frames, scrape_exchanges)
    scrape_replay = pb.ReplayCase(
        contract_version=VERSION,
        name="representative-scrape",
        provider_family="representative-json",
        adapter=pb.REPLAY_ADAPTER_NORMALIZED_SCRAPE_JSON,
        execution_request=scrape_req,
        exchanges=scrape_exchanges,
        expected_frames=scrape_frames,
        expected_projection=scrape_projection,
    )
    scrape_replay.expected_semantic_sha256 = semantic_hash(scrape_frames, scrape_projection)
    write_message(FIXTURES / "replay/representative-scrape.json", scrape_replay)

    declared0 = operation(0, "session-bootstrap")
    declared1 = operation(1, "search", parent=declared0.origin_request_id)
    dynamic2 = pb.OriginOperationRef(
        origin_request_id="origin:dynamic:detail",
        operation_sequence=2,
        role="detail",
        parent_origin_request_id=declared1.origin_request_id,
    )
    dynamic_req = request(scrape=True, operations=[declared0, declared1])
    dynamic_frames = [
        origin_frame(dynamic_req, declared0, 0),
        origin_frame(dynamic_req, declared1, 1),
        origin_declaration_frame(dynamic_req, dynamic2, 2),
        origin_frame(dynamic_req, dynamic2, 3),
        scrape_frame(dynamic_req, 4),
        terminal_frame(dynamic_req, 5, output=1, batches=0, origins=3),
    ]
    dynamic_projection = project_frames(dynamic_frames, dynamic_req)
    dynamic_exchanges = [
        captured_exchange(declared0),
        captured_exchange(declared1),
        captured_exchange(
            dynamic2,
            dynamic_frames[4].scrape_result,
            normalized_result_frame_sequence=4,
        ),
    ]
    bind_replay_fingerprints(dynamic_frames, dynamic_exchanges)
    dynamic_replay = pb.ReplayCase(
        contract_version=VERSION,
        name="representative-multi-origin-dynamic-scrape",
        provider_family="representative-json",
        adapter=pb.REPLAY_ADAPTER_NORMALIZED_SCRAPE_JSON,
        execution_request=dynamic_req,
        exchanges=dynamic_exchanges,
        expected_frames=dynamic_frames,
        expected_projection=dynamic_projection,
    )
    dynamic_replay.expected_semantic_sha256 = semantic_hash(dynamic_frames, dynamic_projection)
    write_message(
        FIXTURES / "replay/representative-multi-origin-dynamic-scrape.json",
        dynamic_replay,
    )


def main() -> None:
    for directory in (
        FIXTURES / "conformance/positive",
        FIXTURES / "conformance/negative",
        FIXTURES / "replay",
    ):
        if directory.exists():
            for path in directory.glob("*.json"):
                path.unlink()
    build_positive()
    build_replay()
    build_negative()


if __name__ == "__main__":
    main()
