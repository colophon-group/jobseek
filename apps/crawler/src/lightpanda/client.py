"""One-shot mTLS client for the fixed Lightpanda B0 rendering service.

This module owns no queue, retry, fallback, parser, or persistence behavior.
Every call opens one raw TLS connection to one literal private IP, verifies the
closed service identity, submits one immutable ``LightpandaB0Task``, and closes.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NoReturn, cast

if TYPE_CHECKING:
    from cryptography import x509
    from google.protobuf.message import Message

    from src.lightpanda_queue import LightpandaB0Task

SERVICE_PROTOCOL: Final = "jobseek.lightpanda.service/v1"
RUNTIME_CONTRACT: Final = "crawler.runtime/v1"
SERVICE_ALPN: Final = "jobseek-lightpanda-b0/1"
SERVICE_PORT: Final = 9443
SERVICE_CAPACITY: Final = 4
SERVICE_MEMORY_MAX_BYTES: Final = 1_073_741_824
SERVICE_MEMORY_SWAP_MAX_BYTES: Final = 0
HELLO_FRAME_LIMIT: Final = 512
INPUT_FRAME_LIMIT: Final = 128 * 1024 + 3
RESULT_FRAME_LIMIT: Final = 2 * 1024 * 1024
CLOSE_TIMEOUT_SECONDS: Final = 1.0
_MAX_PEM_BYTES: Final = 128 * 1024
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_NETWORKS: Final = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)
_CANONICAL_HELLO: Final = (
    b'{"protocol":"jobseek.lightpanda.service/v1","runtime_contract":"crawler.runtime/v1",'
    b'"mode":"b0","capacity":4,"memory_max_bytes":1073741824,'
    b'"memory_swap_max_bytes":0}'
)


class LightpandaServiceError(RuntimeError):
    """A closed transport, identity, framing, or service-contract failure."""


@dataclass(frozen=True, slots=True)
class LightpandaServiceConfig:
    host: str
    ca_certificate: Path
    client_certificate: Path
    client_private_key: Path
    ca_sha256: str
    server_leaf_sha256: str
    server_spki_sha256: str
    port: int = SERVICE_PORT


class LightpandaB0Client:
    """Open one authenticated connection for each B0 render invocation."""

    def __init__(self, config: LightpandaServiceConfig) -> None:
        self._config = _validate_config(config)
        self._ssl_context = _build_ssl_context(self._config)

    @asynccontextmanager
    async def reserve(self) -> AsyncIterator[LightpandaB0Reservation]:
        """Reserve one verified service connection before claiming Redis work."""

        writer: asyncio.StreamWriter | None = None
        try:
            try:
                async with asyncio.timeout(15):
                    reader, writer = await asyncio.open_connection(
                        host=self._config.host,
                        port=self._config.port,
                        ssl=self._ssl_context,
                        server_hostname=self._config.host,
                        ssl_handshake_timeout=10,
                    )
                    _verify_negotiated_tls(writer, self._config)
                    hello = await _read_record(reader, HELLO_FRAME_LIMIT)
                    _verify_hello(hello)
            except LightpandaServiceError:
                raise
            except (TimeoutError, OSError, ssl.SSLError, asyncio.IncompleteReadError) as exc:
                raise LightpandaServiceError("Lightpanda service transport failed closed") from exc
            yield LightpandaB0Reservation(reader, writer)
        finally:
            if writer is not None:
                await _close_writer(writer)

    async def execute(self, task: LightpandaB0Task) -> Message:
        """Compatibility entry point for one reserved, one-shot execution."""

        _require_canonical_task(task)
        async with self.reserve() as reservation:
            return await reservation.execute(task)


class LightpandaB0Reservation:
    """A held, hello-verified service slot that accepts exactly one task."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._used = False
        self._cancelled = False

    def cancel(self) -> None:
        """Synchronously close the TLS transport for lease loss or cancellation."""

        self._cancelled = True
        self._writer.close()

    async def execute(self, task: LightpandaB0Task) -> Message:
        """Execute one task once, without opening, retrying, or falling back."""

        if self._used:
            raise LightpandaServiceError("Lightpanda reservation is one-shot")
        if self._cancelled:
            raise LightpandaServiceError("Lightpanda reservation is closed")
        self._used = True

        canonical_task = _require_canonical_task(task)
        request = _execution_input(canonical_task)
        payload = request.SerializeToString(deterministic=True)
        request_record = _encode_record(payload, INPUT_FRAME_LIMIT)
        # The canonical empty frame is application-level write closure. TLS
        # streams cannot half-close before receiving the server response.
        outbound = request_record + b"\x00"
        timeout_seconds = canonical_task.assignment.timeout_ms / 1000 + 15

        try:
            async with asyncio.timeout(timeout_seconds):
                self._writer.write(outbound)
                await self._writer.drain()
                result_payload = await _read_record(self._reader, RESULT_FRAME_LIMIT)
                if await self._reader.read(1) != b"":
                    _fail("service sent bytes after its one result")
                return _decode_result(result_payload)
        except LightpandaServiceError:
            raise
        except (TimeoutError, OSError, ssl.SSLError, asyncio.IncompleteReadError) as exc:
            raise LightpandaServiceError("Lightpanda service transport failed closed") from exc


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close immediately and give TLS shutdown a small, bounded drain window."""

    writer.close()
    with suppress(TimeoutError, OSError, ssl.SSLError):
        async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
            await writer.wait_closed()


def _validate_config(config: LightpandaServiceConfig) -> LightpandaServiceConfig:
    if not isinstance(config, LightpandaServiceConfig):
        raise TypeError("config must be a LightpandaServiceConfig")
    try:
        address = ipaddress.ip_address(config.host)
    except ValueError as exc:
        raise ValueError("Lightpanda service host must be a literal private IP") from exc
    if not any(address in network for network in _PRIVATE_NETWORKS) or str(address) != config.host:
        raise ValueError("Lightpanda service host must be a canonical literal private IP")
    if type(config.port) is not int or config.port != SERVICE_PORT:
        raise ValueError(f"Lightpanda service port must be {SERVICE_PORT}")
    for value, name in (
        (config.ca_sha256, "ca_sha256"),
        (config.server_leaf_sha256, "server_leaf_sha256"),
        (config.server_spki_sha256, "server_spki_sha256"),
    ):
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    for path, name in (
        (config.ca_certificate, "ca_certificate"),
        (config.client_certificate, "client_certificate"),
        (config.client_private_key, "client_private_key"),
    ):
        if (
            not isinstance(path, Path)
            or not path.is_file()
            or not 0 < path.stat().st_size <= _MAX_PEM_BYTES
        ):
            raise ValueError(f"{name} must be a bounded regular file")
    _load_pinned_ca(config.ca_certificate, config.ca_sha256)
    return config


def _load_pinned_ca(path: Path, expected_sha256: str) -> x509.Certificate:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    try:
        contents = path.read_bytes()
        certificates = x509.load_pem_x509_certificates(contents)
    except (OSError, ValueError) as exc:
        raise ValueError("Lightpanda service CA must contain one certificate") from exc
    if len(certificates) != 1:
        raise ValueError("Lightpanda service CA must contain one certificate")
    certificate = certificates[0]
    if contents.strip() != certificate.public_bytes(serialization.Encoding.PEM).strip():
        raise ValueError("Lightpanda service CA must contain only one certificate")
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("Lightpanda service CA must be a CA certificate") from exc
    if (
        not constraints.ca
        or hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
        != expected_sha256
    ):
        raise ValueError("Lightpanda service CA fingerprint is invalid")
    return certificate


def _build_ssl_context(config: LightpandaServiceConfig) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.options |= ssl.OP_NO_TICKET
    context.set_alpn_protocols([SERVICE_ALPN])
    try:
        context.load_verify_locations(cafile=str(config.ca_certificate))
        context.load_cert_chain(
            certfile=str(config.client_certificate),
            keyfile=str(config.client_private_key),
        )
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("Lightpanda service TLS credentials are invalid") from exc
    return context


def _verify_negotiated_tls(
    writer: asyncio.StreamWriter,
    config: LightpandaServiceConfig,
) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID

    ssl_object = writer.get_extra_info("ssl_object")
    if not isinstance(ssl_object, ssl.SSLObject) or ssl_object.version() != "TLSv1.3":
        _fail("service did not negotiate TLS 1.3")
    if ssl_object.selected_alpn_protocol() != SERVICE_ALPN:
        _fail("service did not negotiate the exact ALPN")
    peer_der = ssl_object.getpeercert(binary_form=True)
    if not isinstance(peer_der, bytes):
        _fail("service did not provide a leaf certificate")
    try:
        certificate = x509.load_der_x509_certificate(peer_der)
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        usages = certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
    except (ValueError, x509.ExtensionNotFound):
        _fail("service leaf certificate identity is invalid")
    names = list(san)
    if (
        len(names) != 1
        or not isinstance(names[0], x509.IPAddress)
        or str(names[0].value) != config.host
        or list(usages) != [ExtendedKeyUsageOID.SERVER_AUTH]
        or constraints.ca
    ):
        _fail("service leaf certificate identity is invalid")
    leaf_digest = hashlib.sha256(peer_der).hexdigest()
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if (
        leaf_digest != config.server_leaf_sha256
        or hashlib.sha256(spki).hexdigest() != config.server_spki_sha256
    ):
        _fail("service leaf certificate pin is invalid")


def _require_canonical_task(task: LightpandaB0Task) -> LightpandaB0Task:
    from src.lightpanda_queue import LightpandaB0Task

    if type(task) is not LightpandaB0Task:
        raise TypeError("task must be a LightpandaB0Task")
    try:
        rebuilt = LightpandaB0Task.create(
            task_id=task.task_id,
            board_id=task.board_id,
            source_url=task.source_url,
            policy_key=task.policy_key,
            domain=task.domain,
            route=task.route,
            config_revision=task.config_revision,
            initial_ready_at_ms=task.initial_ready_at_ms,
            assignment=task.assignment,
        )
    except ValueError as exc:
        raise ValueError("task is not a canonical LightpandaB0Task") from exc
    if rebuilt != task:
        raise ValueError("task identity does not match its canonical envelope")
    return rebuilt


def _execution_input(task: LightpandaB0Task) -> Message:
    runtime = _runtime_module()
    origin_request_id = f"lightpanda-b0:{task.payload_sha256}"
    request_fingerprint = hashlib.sha256(
        b'{"body":"","headers":[],"method":"GET","url":'
        + json.dumps(task.source_url, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        + b"}"
    ).hexdigest()
    return runtime.BrowserExecutionInput(
        assignment=runtime.BrowserAssignment(
            backend=runtime.BROWSER_BACKEND_LIGHTPANDA,
            capability_class=runtime.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
            service_lane=runtime.BROWSER_SERVICE_LANE_LIGHTPANDA,
            routing_revision=task.assignment.routing_revision,
        ),
        plan=runtime.BrowserPlan(
            contract_version=RUNTIME_CONTRACT,
            target_url=task.source_url,
            required_capabilities=[runtime.BROWSER_CAPABILITY_RENDER],
            navigation=runtime.NavigationPlan(
                wait_until=runtime.WAIT_CONDITION_LOAD,
                timeout_ms=task.assignment.timeout_ms,
                origin_request_id=origin_request_id,
            ),
            origin_operations=[
                runtime.OriginOperationRef(
                    origin_request_id=origin_request_id,
                    operation_sequence=1,
                    role="navigation",
                    request_fingerprint=request_fingerprint,
                )
            ],
        ),
    )


def _encode_record(payload: bytes, maximum: int) -> bytes:
    length = len(payload)
    prefix = bytearray()
    remaining = length
    while remaining >= 0x80:
        prefix.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    prefix.append(remaining)
    if len(prefix) > maximum or length > maximum - len(prefix):
        _fail("outgoing request exceeds its frame limit")
    return bytes(prefix) + payload


async def _read_record(reader: asyncio.StreamReader, maximum: int) -> bytes:
    prefix = bytearray()
    value = 0
    for index in range(10):
        byte = (await reader.readexactly(1))[0]
        prefix.append(byte)
        if index == 9 and (byte > 1 or byte & 0x80):
            _fail("service frame prefix overflow")
        value |= (byte & 0x7F) << (7 * index)
        if byte < 0x80:
            canonical_size = max(1, (value.bit_length() + 6) // 7)
            if canonical_size != len(prefix):
                _fail("service frame prefix is noncanonical")
            if len(prefix) > maximum or value > maximum - len(prefix):
                _fail("service frame exceeds its limit")
            return await reader.readexactly(value)
    _fail("service frame prefix overflow")


def _verify_hello(payload: bytes) -> None:
    if payload != _CANONICAL_HELLO:
        _fail("service hello bytes do not match the canonical contract")


def _decode_result(payload: bytes) -> Message:
    from google.protobuf.message import DecodeError

    runtime = _runtime_module()
    result = runtime.BrowserResult()
    try:
        result.ParseFromString(payload)
    except DecodeError as exc:
        raise LightpandaServiceError("service result is not a BrowserResult") from exc
    without_unknown = runtime.BrowserResult()
    without_unknown.CopyFrom(result)
    without_unknown.DiscardUnknownFields()
    if without_unknown.SerializeToString(deterministic=True) != result.SerializeToString(
        deterministic=True
    ):
        _fail("service result contains unknown fields")
    if (
        result.contract_version != RUNTIME_CONTRACT
        or result.backend != runtime.BROWSER_BACKEND_LIGHTPANDA
        or result.WhichOneof("outcome") not in {"success", "error", "unsupported"}
    ):
        _fail("service result does not match the Lightpanda runtime contract")
    return result


def _runtime_module() -> Any:
    """Load protobuf only for an actual service operation, not utility imports."""

    from jobseek_runtime_v1 import runtime_pb2

    # protoc's checked-in output has no companion .pyi declarations. Keep the
    # dynamic surface behind one explicit Any boundary.
    return cast(Any, runtime_pb2)


def _fail(message: str) -> NoReturn:
    raise LightpandaServiceError(message)


__all__ = [
    "LightpandaB0Client",
    "LightpandaB0Reservation",
    "LightpandaServiceConfig",
    "LightpandaServiceError",
]
