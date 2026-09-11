"""Strict result-boundary tests for the one-shot Lightpanda B0 runtime."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from jobseek_runtime_v1 import runtime_pb2

from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda.runtime import (
    HTML_CHUNK_LIMIT_BYTES,
    HTML_LIMIT_BYTES,
    LightpandaB0ScrapeRuntime,
    LightpandaResultError,
    _validated_rendered_html,
)
from src.lightpanda_queue import LightpandaB0Task, RouteIdentity
from src.processing.scrape import _is_budget_eligible_failure, _is_permanent_gone
from src.shared.browser import BrowserNavigationHTTPStatusError


def _task() -> tuple[LightpandaB0Task, dict]:
    url = "https://example.test/jobs/1"
    config = {
        "browser_backend": "lightpanda",
        "render": True,
        "routing_revision": "route-test-1",
        "timeout": 5_000,
        "wait": "load",
        "wait_fallback": None,
        "defaults_by_url": {url: {"locations": ["London"]}},
    }
    assignment = resolve_render_assignment("json-ld", config)
    assert assignment is not None
    return (
        LightpandaB0Task.create(
            task_id=str(uuid.uuid4()),
            board_id="board-1",
            source_url=url,
            policy_key="lightpanda-b0-v1",
            domain="example.test",
            route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
            config_revision=1,
            initial_ready_at_ms=1,
            assignment=assignment,
        ),
        config,
    )


def _manifest(body: bytes) -> runtime_pb2.ChunkManifest:
    pieces = [
        body[offset : offset + HTML_CHUNK_LIMIT_BYTES]
        for offset in range(0, len(body), HTML_CHUNK_LIMIT_BYTES)
    ]
    chunks = [
        runtime_pb2.DataChunk(
            sequence=index,
            size_bytes=len(piece),
            sha256=hashlib.sha256(piece).hexdigest(),
            inline_body=piece,
        )
        for index, piece in enumerate(pieces)
        if piece
    ]
    return runtime_pb2.ChunkManifest(
        chunks=chunks,
        total_size_bytes=len(body),
        total_sha256=hashlib.sha256(body).hexdigest(),
        complete=True,
    )


def _result(body: bytes) -> runtime_pb2.BrowserResult:
    return runtime_pb2.BrowserResult(
        contract_version="crawler.runtime/v1",
        backend=runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
        success=runtime_pb2.BrowserSuccess(
            final_url="https://example.test/jobs/1",
            status=200,
            html=_manifest(body),
        ),
    )


async def test_runtime_validates_and_parses_only_the_frozen_jsonld_invocation() -> None:
    task, config = _task()
    html = b"""<script type="application/ld+json">
    {"@type":"JobPosting","title":"Engineer"}
    </script>"""
    reservation = AsyncMock()
    reservation.execute.return_value = _result(html)
    runtime = LightpandaB0ScrapeRuntime(task, reservation)
    local_http = AsyncMock()

    content = await runtime.scrape(
        task.source_url,
        "json-ld",
        config,
        local_http,
    )

    assert runtime.implementation == "go"
    assert content.title == "Engineer"
    assert content.locations == ["London"]
    reservation.execute.assert_awaited_once_with(task)
    assert local_http.mock_calls == []
    with pytest.raises(LightpandaResultError, match="one-shot"):
        await runtime.scrape(task.source_url, "json-ld", config, AsyncMock())


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://example.test/jobs/2"},
        {"scraper_type": "dom"},
        {"scraper_config": None},
        {"pw": object()},
        {"artifact_dir": object()},
    ],
)
async def test_runtime_rejects_invocations_outside_the_frozen_assignment(
    change: dict[str, object],
) -> None:
    task, config = _task()
    reservation = AsyncMock()
    runtime = LightpandaB0ScrapeRuntime(task, reservation)
    arguments = {
        "url": task.source_url,
        "scraper_type": "json-ld",
        "scraper_config": config,
        "http": AsyncMock(),
        "pw": None,
        "artifact_dir": None,
        **change,
    }

    with pytest.raises(LightpandaResultError, match="frozen"):
        await runtime.scrape(**arguments)  # type: ignore[arg-type]

    reservation.execute.assert_not_awaited()


async def test_runtime_independently_rejects_changed_assignment_identity() -> None:
    task, config = _task()
    task = replace(task, assignment=replace(task.assignment, timeout_ms=4_999))
    reservation = AsyncMock()

    with pytest.raises(LightpandaResultError, match="identity"):
        await LightpandaB0ScrapeRuntime(task, reservation).scrape(
            task.source_url,
            "json-ld",
            config,
            AsyncMock(),
        )

    reservation.execute.assert_not_awaited()


def _invalid_result(case: str) -> runtime_pb2.BrowserResult:
    body = b"<html>valid</html>"
    result = _result(body)
    manifest = result.success.html
    if case == "outcome":
        return runtime_pb2.BrowserResult(
            contract_version="crawler.runtime/v1",
            backend=runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
            error=runtime_pb2.BrowserFailure(),
        )
    if case == "status":
        result.success.ClearField("status")
    elif case == "html":
        result.success.ClearField("html")
    elif case == "extra_output":
        result.success.action_outcomes.add(action_id="forbidden", completed=True)
    elif case == "incomplete":
        manifest.complete = False
    elif case == "sequence":
        manifest.chunks[0].sequence = 1
    elif case == "artifact":
        manifest.chunks[0].artifact.handle = "forbidden"
    elif case == "chunk_size":
        manifest.chunks[0].size_bytes += 1
    elif case == "chunk_hash":
        manifest.chunks[0].sha256 = "0" * 64
    elif case == "total_size":
        manifest.total_size_bytes += 1
    elif case == "total_hash":
        manifest.total_sha256 = "0" * 64
    elif case == "declared_oversize":
        manifest.total_size_bytes = HTML_LIMIT_BYTES + 1
    elif case == "utf8":
        result.success.html.CopyFrom(_manifest(b"\xff"))
    else:  # pragma: no cover - test table is closed
        raise AssertionError(case)
    return result


@pytest.mark.parametrize(
    "case",
    [
        "outcome",
        "status",
        "html",
        "extra_output",
        "incomplete",
        "sequence",
        "artifact",
        "chunk_size",
        "chunk_hash",
        "total_size",
        "total_hash",
        "declared_oversize",
        "utf8",
    ],
)
def test_result_manifest_fail_closed_matrix(case: str) -> None:
    with pytest.raises(LightpandaResultError):
        _validated_rendered_html(
            _invalid_result(case),
            requested_url="https://example.test/jobs/1",
        )


def test_empty_zero_chunk_html_is_canonical() -> None:
    result = _result(b"")

    assert (
        _validated_rendered_html(
            result,
            requested_url="https://example.test/jobs/1",
        )
        == ""
    )


def test_canonical_full_and_tail_chunk_partition_is_accepted() -> None:
    body = b"x" * (HTML_CHUNK_LIMIT_BYTES + 1)

    assert (
        _validated_rendered_html(
            _result(body),
            requested_url="https://example.test/jobs/1",
        )
        == body.decode()
    )


def test_noncanonical_chunk_partition_is_rejected() -> None:
    body = b"x" * (HTML_CHUNK_LIMIT_BYTES + 1)
    result = _result(body)
    manifest = result.success.html
    first = body[: HTML_CHUNK_LIMIT_BYTES // 2]
    second = body[HTML_CHUNK_LIMIT_BYTES // 2 :]
    manifest.ClearField("chunks")
    for sequence, piece in enumerate((first, second)):
        manifest.chunks.add(
            sequence=sequence,
            size_bytes=len(piece),
            sha256=hashlib.sha256(piece).hexdigest(),
            inline_body=piece,
        )

    with pytest.raises(LightpandaResultError, match="chunk is invalid"):
        _validated_rendered_html(result, requested_url="https://example.test/jobs/1")


@pytest.mark.parametrize(
    ("status", "permanent_gone", "budget_eligible"),
    [
        (403, False, False),
        (404, True, False),
        (410, True, False),
        (422, False, True),
        (429, False, False),
        (500, False, False),
    ],
)
def test_http_error_result_uses_existing_browser_status_classification(
    status: int,
    permanent_gone: bool,
    budget_eligible: bool,
) -> None:
    result = _result(b"<html>gone</html>")
    result.success.final_url = "https://example.test/jobs/archive/1"
    result.success.status = status

    with pytest.raises(BrowserNavigationHTTPStatusError) as raised:
        _validated_rendered_html(result, requested_url="https://example.test/jobs/1")

    assert raised.value.requested_url == "https://example.test/jobs/1"
    assert raised.value.response_url == "https://example.test/jobs/archive/1"
    assert raised.value.status == status
    assert raised.value.phase == "primary"
    assert _is_permanent_gone(raised.value) is permanent_gone
    assert _is_budget_eligible_failure(raised.value) is budget_eligible


def test_http_error_cannot_bypass_strict_manifest_validation() -> None:
    result = _result(b"<html>gone</html>")
    result.success.status = 410
    result.success.html.complete = False

    with pytest.raises(LightpandaResultError, match="header"):
        _validated_rendered_html(result, requested_url="https://example.test/jobs/1")
