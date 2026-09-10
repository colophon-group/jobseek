from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import ssl
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from jobseek_runtime_v1 import runtime_pb2

from src.lightpanda.client import (
    HELLO_FRAME_LIMIT,
    RESULT_FRAME_LIMIT,
    SERVICE_ALPN,
    LightpandaB0Client,
    LightpandaServiceConfig,
    LightpandaServiceError,
    _encode_record,
    _execution_input,
    _read_record,
    _verify_hello,
)
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import LightpandaB0Task, RouteIdentity

_SERVICE_IP = "10.0.0.5"
_HELLO = {
    "protocol": "jobseek.lightpanda.service/v1",
    "runtime_contract": "crawler.runtime/v1",
    "mode": "b0",
    "capacity": 4,
    "memory_max_bytes": 1_073_741_824,
    "memory_swap_max_bytes": 0,
}


def test_client_module_import_does_not_require_runtime_only_dependencies() -> None:
    script = """
import builtins

real_import = builtins.__import__

def import_without_runtime_dependencies(name, globals=None, locals=None, fromlist=(), level=0):
    blocked = ("cryptography", "google", "jobseek_runtime_v1", "src.lightpanda_queue")
    if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked):
        raise ModuleNotFoundError(f"{name} deliberately unavailable")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = import_without_runtime_dependencies
import src.lightpanda.client
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def _task() -> LightpandaB0Task:
    assignment = resolve_render_assignment(
        "json-ld",
        {
            "browser_backend": "lightpanda",
            "render": True,
            "routing_revision": "route-test-1",
            "timeout": 1_250,
            "wait": "load",
            "wait_fallback": None,
        },
    )
    assert assignment is not None
    return LightpandaB0Task.create(
        task_id="task-1",
        board_id="board-1",
        source_url="https://example.test/jobs/1",
        policy_key="lightpanda-b0-v1",
        domain="example.test",
        route=RouteIdentity(shard_id="shard-1", routing_epoch=1),
        config_revision=1,
        initial_ready_at_ms=1,
        assignment=assignment,
    )


def _certificate(
    *,
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: x509.Certificate,
    issuer_key: ec.EllipticCurvePrivateKey,
    san: x509.GeneralName,
    usage: x509.ObjectIdentifier,
) -> x509.Certificate:
    now = datetime.now(UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(issuer.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .sign(issuer_key, hashes.SHA256())
    )


@pytest.fixture
def tls_files(tmp_path: Path) -> tuple[LightpandaServiceConfig, ssl.SSLContext]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lightpanda-test-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = _certificate(
        subject="lightpanda-service",
        key=server_key,
        issuer=ca,
        issuer_key=ca_key,
        san=x509.IPAddress(ipaddress.ip_address(_SERVICE_IP)),
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
    )
    client_key = ec.generate_private_key(ec.SECP256R1())
    client = _certificate(
        subject="crawler-lightpanda-b0",
        key=client_key,
        issuer=ca,
        issuer_key=ca_key,
        san=x509.UniformResourceIdentifier("spiffe://jobseek/crawler/lightpanda-b0"),
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    )

    ca_path = tmp_path / "ca.pem"
    server_path = tmp_path / "server.pem"
    server_key_path = tmp_path / "server-key.pem"
    client_path = tmp_path / "client.pem"
    client_key_path = tmp_path / "client-key.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    server_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    server_key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    client_path.write_bytes(client.public_bytes(serialization.Encoding.PEM))
    client_key_path.write_bytes(
        client_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    server_spki = server.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    config = LightpandaServiceConfig(
        host=_SERVICE_IP,
        ca_certificate=ca_path,
        client_certificate=client_path,
        client_private_key=client_key_path,
        ca_sha256=hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest(),
        server_leaf_sha256=hashlib.sha256(
            server.public_bytes(serialization.Encoding.DER)
        ).hexdigest(),
        server_spki_sha256=hashlib.sha256(server_spki).hexdigest(),
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_3
    server_context.maximum_version = ssl.TLSVersion.TLSv1_3
    server_context.options |= ssl.OP_NO_TICKET
    server_context.set_alpn_protocols([SERVICE_ALPN])
    server_context.load_cert_chain(server_path, server_key_path)
    server_context.load_verify_locations(ca_path)
    server_context.verify_mode = ssl.CERT_REQUIRED
    return config, server_context


def _result() -> runtime_pb2.BrowserResult:
    return runtime_pb2.BrowserResult(
        contract_version="crawler.runtime/v1",
        backend=runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
        error=runtime_pb2.BrowserFailure(
            error=runtime_pb2.RuntimeError(
                code=runtime_pb2.ERROR_CODE_INTERNAL,
                disposition=runtime_pb2.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
            )
        ),
    )


@asynccontextmanager
async def _service(
    monkeypatch: pytest.MonkeyPatch,
    context: ssl.SSLContext,
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], object],
) -> AsyncIterator[None]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=context)
    socket = server.sockets[0]
    port = int(socket.getsockname()[1])
    real_open_connection = asyncio.open_connection

    async def local_open_connection(
        *args: object, **kwargs: object
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert kwargs["host"] == _SERVICE_IP
        assert kwargs["port"] == 9443
        assert kwargs["server_hostname"] == _SERVICE_IP
        kwargs["host"] = "127.0.0.1"
        kwargs["port"] = port
        return await real_open_connection(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "open_connection", local_open_connection)
    try:
        yield
    finally:
        server.close()
        await server.wait_closed()


async def _good_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        hello = json.dumps(_HELLO, separators=(",", ":")).encode("ascii")
        writer.write(_encode_record(hello, HELLO_FRAME_LIMIT))
        await writer.drain()
        request = await _read_record(reader, 128 * 1024 + 3)
        terminator = await _read_record(reader, 1)
        assert request and terminator == b""
        parsed = runtime_pb2.BrowserExecutionInput.FromString(request)
        assert parsed.plan.evaluations == []
        writer.write(
            _encode_record(_result().SerializeToString(deterministic=True), RESULT_FRAME_LIMIT)
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _hello_then_wait_for_close(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        hello = json.dumps(_HELLO, separators=(",", ":")).encode("ascii")
        writer.write(_encode_record(hello, HELLO_FRAME_LIMIT))
        await writer.drain()
        await reader.read()
    finally:
        writer.close()
        await writer.wait_closed()


async def test_client_uses_raw_literal_ip_tls_and_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
) -> None:
    config, server_context = tls_files
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.invalid:1080")

    async with _service(monkeypatch, server_context, _good_handler):
        result = await LightpandaB0Client(config).execute(_task())

    assert result.WhichOneof("outcome") == "error"
    assert os.environ["HTTPS_PROXY"].startswith("http://user:secret")


def test_client_builds_only_the_closed_b0_runtime_input() -> None:
    task = _task()
    request = _execution_input(task)

    assert request.assignment.backend == runtime_pb2.BROWSER_BACKEND_LIGHTPANDA
    assert request.assignment.routing_revision == task.assignment.routing_revision
    assert request.plan.target_url == task.source_url
    assert request.plan.required_capabilities == [runtime_pb2.BROWSER_CAPABILITY_RENDER]
    assert request.plan.navigation.wait_until == runtime_pb2.WAIT_CONDITION_LOAD
    assert request.plan.navigation.timeout_ms == task.assignment.timeout_ms
    assert request.plan.evaluations == []
    assert len(request.plan.origin_operations) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        json.dumps({**_HELLO, "extra": True}).encode(),
        json.dumps({**_HELLO, "capacity": True}).encode(),
        json.dumps({**_HELLO, "capacity": 5}).encode(),
        json.dumps(dict(reversed(list(_HELLO.items()))), separators=(",", ":")).encode("ascii"),
        json.dumps(_HELLO).encode("ascii"),
        json.dumps(_HELLO, separators=(",", ":")).encode("ascii").replace(b"/", b"\\/", 1),
        json.dumps(_HELLO, separators=(",", ":")).encode("ascii") + b"\n",
        b'{"protocol":"jobseek.lightpanda.service/v1","protocol":"jobseek.lightpanda.service/v1"}',
        b'{"protocol":NaN}',
        b"\xff",
    ],
)
def test_hello_rejection_matrix(payload: bytes) -> None:
    with pytest.raises(LightpandaServiceError):
        _verify_hello(payload)


def test_hello_accepts_only_the_exact_contract() -> None:
    _verify_hello(json.dumps(_HELLO, separators=(",", ":")).encode("ascii"))


@pytest.mark.parametrize("pin_field", ["server_leaf_sha256", "server_spki_sha256"])
async def test_client_rejects_wrong_well_formed_server_pins_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
    pin_field: str,
) -> None:
    config, server_context = tls_files
    bad_config = replace(config, **{pin_field: "0" * 64})

    async with _service(monkeypatch, server_context, _hello_then_wait_for_close):
        with pytest.raises(LightpandaServiceError, match="leaf certificate pin"):
            await LightpandaB0Client(bad_config).execute(_task())


async def test_client_rejects_alpn_mismatch_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
) -> None:
    config, server_context = tls_files
    server_context.set_alpn_protocols(["not-jobseek-lightpanda"])

    async with _service(monkeypatch, server_context, _hello_then_wait_for_close):
        with pytest.raises(LightpandaServiceError, match="exact ALPN"):
            await LightpandaB0Client(config).execute(_task())


@pytest.mark.parametrize(
    "patch",
    [
        {"host": "murmur.invalid"},
        {"host": "127.0.0.1"},
        {"host": "0.0.0.0"},
        {"host": "169.254.1.1"},
        {"host": "10.0.0.05"},
        {"port": 9444},
        {"ca_sha256": "A" * 64},
        {"server_leaf_sha256": ""},
        {"server_spki_sha256": "0" * 63},
    ],
)
def test_config_rejects_wrong_endpoint_and_pins(
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
    patch: dict[str, object],
) -> None:
    config, _ = tls_files
    values = {
        "host": config.host,
        "ca_certificate": config.ca_certificate,
        "client_certificate": config.client_certificate,
        "client_private_key": config.client_private_key,
        "ca_sha256": config.ca_sha256,
        "server_leaf_sha256": config.server_leaf_sha256,
        "server_spki_sha256": config.server_spki_sha256,
        "port": config.port,
        **patch,
    }
    with pytest.raises((TypeError, ValueError)):
        LightpandaB0Client(LightpandaServiceConfig(**values))  # type: ignore[arg-type]


def test_config_rejects_multiple_or_wrong_ca_certificates(
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
    tmp_path: Path,
) -> None:
    config, _ = tls_files
    duplicate = tmp_path / "duplicate-ca.pem"
    duplicate.write_bytes(config.ca_certificate.read_bytes() * 2)
    with pytest.raises(ValueError, match="one certificate"):
        LightpandaB0Client(
            LightpandaServiceConfig(
                host=config.host,
                ca_certificate=duplicate,
                client_certificate=config.client_certificate,
                client_private_key=config.client_private_key,
                ca_sha256=config.ca_sha256,
                server_leaf_sha256=config.server_leaf_sha256,
                server_spki_sha256=config.server_spki_sha256,
            )
        )

    with pytest.raises(ValueError, match="fingerprint"):
        LightpandaB0Client(
            LightpandaServiceConfig(
                host=config.host,
                ca_certificate=config.ca_certificate,
                client_certificate=config.client_certificate,
                client_private_key=config.client_private_key,
                ca_sha256="0" * 64,
                server_leaf_sha256=config.server_leaf_sha256,
                server_spki_sha256=config.server_spki_sha256,
            )
        )


def test_client_accepts_only_exact_canonical_task(
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
) -> None:
    config, _ = tls_files
    client = LightpandaB0Client(config)
    with pytest.raises(TypeError, match="LightpandaB0Task"):
        asyncio.run(client.execute(object()))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(client.execute(replace(_task(), payload="tampered")))


@pytest.mark.parametrize(
    "wire",
    [
        b"\x80\x00",
        b"\x81",
        b"\xff" * 10,
        b"\x05x",
    ],
)
async def test_client_rejects_malformed_or_truncated_result_frames(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
    wire: bytes,
) -> None:
    config, server_context = tls_files

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.dumps(_HELLO, separators=(",", ":")).encode("ascii")
            writer.write(_encode_record(hello, HELLO_FRAME_LIMIT))
            await writer.drain()
            await _read_record(reader, 128 * 1024 + 3)
            await _read_record(reader, 1)
            writer.write(wire)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _service(monkeypatch, server_context, handler):
        with pytest.raises(LightpandaServiceError):
            await LightpandaB0Client(config).execute(_task())


async def test_client_rejects_oversize_result_before_body(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
) -> None:
    config, server_context = tls_files

    async def handler(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.dumps(_HELLO, separators=(",", ":")).encode("ascii")
            writer.write(_encode_record(hello, HELLO_FRAME_LIMIT))
            writer.write(b"\x81\x80\x80\x01")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _service(monkeypatch, server_context, handler):
        with pytest.raises(LightpandaServiceError, match="exceeds"):
            await LightpandaB0Client(config).execute(_task())


async def test_client_rejects_trailing_server_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tls_files: tuple[LightpandaServiceConfig, ssl.SSLContext],
) -> None:
    config, server_context = tls_files

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.dumps(_HELLO, separators=(",", ":")).encode("ascii")
            writer.write(_encode_record(hello, HELLO_FRAME_LIMIT))
            await writer.drain()
            await _read_record(reader, 128 * 1024 + 3)
            await _read_record(reader, 1)
            payload = _result().SerializeToString(deterministic=True)
            writer.write(_encode_record(payload, RESULT_FRAME_LIMIT) + b"trailing")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with _service(monkeypatch, server_context, handler):
        with pytest.raises(LightpandaServiceError, match="after"):
            await LightpandaB0Client(config).execute(_task())
