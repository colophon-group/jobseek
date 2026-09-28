"""Native fetch integration preserves DOM response and extraction semantics."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from src.core.monitors.dom import BotChallengeError
from src.core.scrapers import dom
from src.runtime import dom_go_http, dom_go_parse
from src.shared.tdm import TDMReservedError


@pytest.fixture(scope="module")
def native_binaries(tmp_path_factory):
    root = tmp_path_factory.mktemp("dom-http")
    module = Path(__file__).resolve().parents[1] / "go/dom-detail"
    for name in ("parse", "fetch"):
        subprocess.run(
            ["go", "build", "-o", str(root / name), f"./cmd/{name}"],
            cwd=module,
            check=True,
        )
    return root


def envelope(body: bytes, *, status=200, url="https://example.com/job", content_type="text/html"):
    return {
        "requests": 1,
        "responses": 1,
        "bytes": len(body),
        "status": status,
        "final_url": url,
        "content_type": content_type,
        "body_base64": base64.b64encode(body).decode(),
    }


@pytest.mark.asyncio
async def test_native_fetch_reaches_native_parser_once_with_canonical_identity(
    monkeypatch, native_binaries, tmp_path
):
    monkeypatch.setenv("DOM_GO_HTTP_ENABLED", "1")
    monkeypatch.setenv("DOM_GO_PARSE_ENABLED", "1")
    monkeypatch.setattr(dom_go_parse, "_BINARY", str(native_binaries / "parse"))
    monkeypatch.setattr(dom_go_parse, "_CAPTURE_PREFIX", str(tmp_path / "capture"))
    source = "https://example.com/job#selected"
    config = {
        "fetch_url_transform": {"find": r"/job", "replace": "/document"},
        "same_origin_redirects": True,
        "encoding": "iso-8859-1",
        "steps": [
            {"tag": "h1", "field": "title"},
            {"tag": "p", "field": "description", "html": True, "to_end": True},
        ],
        "defaults_by_url": {source: {"locations": ["Zurich"]}},
    }
    raw = "<h1>Noise</h1><h1 id='selected'>Ingénieur</h1><p>Construire.</p>".encode("latin-1")
    calls = []

    async def child(binary, request):
        calls.append(request)
        return envelope(
            raw, url="https://example.com/document", content_type="text/html; charset=utf-8"
        ), 0

    def unexpected(request):
        raise AssertionError("Python attempted origin traffic")

    monkeypatch.setattr(dom_go_http, "run_child", child)
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        monkeypatch.setattr(client, "_jobseek_verified_direct_http", True, raising=False)
        content = await dom.scrape(source, config, client)
    assert len(calls) == 1
    assert calls[0]["url"] == "https://example.com/document#selected"
    assert calls[0]["options"]["same_origin"]
    assert content.title == "Ingénieur"
    assert content.description == "<p>Construire.</p>"
    assert content.locations == ["Zurich"]
    capture = json.loads((tmp_path / "capture-0.json").read_text())
    assert capture["html"] == raw.decode("latin-1")
    assert capture["url"] == source


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["gone", "challenge", "status"])
async def test_native_response_preserves_typed_failure(monkeypatch, kind):
    monkeypatch.setenv("DOM_GO_HTTP_ENABLED", "1")
    config = {"steps": [{"tag": "h1", "field": "title"}], "gone_url_pattern": "/removed"}
    body = b"<title>Just a moment</title>" if kind == "challenge" else b"<h1>Unavailable</h1>"
    final = "https://example.com/removed" if kind == "gone" else "https://example.com/job"

    async def child(binary, request):
        return envelope(body, url=final, status=404 if kind == "status" else 200), 0

    monkeypatch.setattr(dom_go_http, "run_child", child)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("Python origin I/O"))
    ) as client:
        monkeypatch.setattr(client, "_jobseek_verified_direct_http", True, raising=False)
        with pytest.raises(
            BotChallengeError if kind == "challenge" else httpx.HTTPStatusError
        ) as failure:
            await dom.scrape("https://example.com/job", config, client)
    if kind != "challenge":
        assert isinstance(failure.value, httpx.HTTPStatusError)
        assert failure.value.response.status_code == (410 if kind == "gone" else 404)


@pytest.mark.asyncio
async def test_native_tdm_denial_is_not_content(monkeypatch):
    async def child(binary, request):
        payload = envelope(b"")
        payload.update(error_kind="tdm", tdm_source="header", tdm_policy="license", error="denied")
        return payload, 1

    monkeypatch.setattr(dom_go_http, "run_child", child)
    with pytest.raises(TDMReservedError):
        await dom_go_http.fetch_response(
            "https://example.com/job",
            headers={},
            retry_limits={429: 3},
            same_origin_redirects=False,
        )


@pytest.mark.asyncio
async def test_raw_document_bytes_survive_protocol(monkeypatch):
    body = b"%PDF-1.7\n\xff\x00raw document"

    async def child(binary, request):
        return envelope(body, content_type="application/pdf"), 0

    monkeypatch.setattr(dom_go_http, "run_child", child)
    response = await dom_go_http.fetch_response(
        "https://example.com/job", headers={}, retry_limits={}, same_origin_redirects=False
    )
    assert response.content == body
    assert response.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_native_binary_rejects_private_targets_without_origin_response(native_binaries):
    with pytest.raises(RuntimeError, match="HTTP fetch failed"):
        await dom_go_http.fetch_response(
            "http://127.0.0.1/job",
            headers={},
            retry_limits={},
            same_origin_redirects=False,
            binary=str(native_binaries / "fetch"),
        )


def test_rollout_reversal_and_transport_boundaries(monkeypatch):
    monkeypatch.delenv("DOM_GO_HTTP_ENABLED", raising=False)
    assert dom_go_http.eligible("https://example.com/job", {})
    for config in ({"render": True}, {"proxy": True}, {"skip_ssl": True}, {"actions": [{}]}):
        assert not dom_go_http.eligible("https://example.com/job", config)
    for url in (
        "https://user:secret@example.com/job",
        "https://example.com:8080/job",
        "file:///job",
    ):
        assert not dom_go_http.eligible(url, {})
    for value in ("0", "true", "invalid", ""):
        monkeypatch.setenv("DOM_GO_HTTP_ENABLED", value)
        assert not dom_go_http.eligible("https://example.com/job", {})


@pytest.mark.asyncio
async def test_effective_caller_transport_controls_admission(monkeypatch):
    from src.shared.http import create_http_client, create_logging_http_client

    monkeypatch.setenv("DOM_GO_HTTP_ENABLED", "1")
    async with create_http_client() as direct:
        assert dom_go_http.eligible("https://example.com/job", {}, direct)
    async with create_http_client(verify=False) as insecure:
        assert not dom_go_http.eligible("https://example.com/job", {}, insecure)
    logging_client, _ = create_logging_http_client()
    async with logging_client:
        assert not dom_go_http.eligible("https://example.com/job", {}, logging_client)
    async with httpx.AsyncClient() as custom:
        assert not dom_go_http.eligible("https://example.com/job", {}, custom)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"requests": True},
        {"responses": 2},
        {"bytes": -1},
        {"status": 999},
        {"final_url": "http://user:secret@example.com"},
        {"body_base64": "invalid!"},
        {"content_type": None},
    ],
)
async def test_invalid_native_accounting_rejected(monkeypatch, change):
    async def child(binary, request):
        payload = envelope(b"body")
        payload.update(change)
        return payload, 0

    monkeypatch.setattr(dom_go_http, "run_child", child)
    with pytest.raises((ValueError, TypeError)):
        await dom_go_http.fetch_response(
            "https://example.com/job", headers={}, retry_limits={}, same_origin_redirects=False
        )
