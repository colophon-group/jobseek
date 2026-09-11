"""Offline validation for the immutable Lightpanda claimant mTLS bundle."""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, SignatureAlgorithmOID

CLAIMANT_UID: Final = 10001
CLAIMANT_GID: Final = 10001
CLAIMANT_CA_PATH: Final = Path("/run/credentials/lightpanda-b0/ca.pem")
CLAIMANT_CLIENT_CERTIFICATE_PATH: Final = Path("/run/credentials/lightpanda-b0/client.pem")
CLAIMANT_CLIENT_PRIVATE_KEY_PATH: Final = Path("/run/credentials/lightpanda-b0/client-key.pem")
CLAIMANT_CA_PIN_PATH: Final = Path("/run/credentials/lightpanda-b0/ca.sha256")
CLAIMANT_SERVER_LEAF_PIN_PATH: Final = Path("/run/credentials/lightpanda-b0/server-leaf.sha256")
CLAIMANT_SERVER_SPKI_PIN_PATH: Final = Path("/run/credentials/lightpanda-b0/server-spki.sha256")
CLAIMANT_CLIENT_IDENTITY: Final = "spiffe://jobseek/crawler/lightpanda-b0"
_MAX_PEM_BYTES: Final = 32 * 1024
_PIN_BYTES: Final = 65


@dataclass(frozen=True, slots=True)
class ClaimantCredentialPaths:
    ca_certificate: Path
    client_certificate: Path
    client_private_key: Path
    ca_sha256: Path
    server_leaf_sha256: Path
    server_spki_sha256: Path


@dataclass(frozen=True, slots=True)
class ClaimantPins:
    ca_sha256: str
    server_leaf_sha256: str
    server_spki_sha256: str


def validate_installed_claimant_credentials(
    paths: ClaimantCredentialPaths,
) -> ClaimantPins:
    """Validate exact mounted paths, ownership, modes, signatures, and pins."""

    expected = ClaimantCredentialPaths(
        ca_certificate=CLAIMANT_CA_PATH,
        client_certificate=CLAIMANT_CLIENT_CERTIFICATE_PATH,
        client_private_key=CLAIMANT_CLIENT_PRIVATE_KEY_PATH,
        ca_sha256=CLAIMANT_CA_PIN_PATH,
        server_leaf_sha256=CLAIMANT_SERVER_LEAF_PIN_PATH,
        server_spki_sha256=CLAIMANT_SERVER_SPKI_PIN_PATH,
    )
    if paths != expected:
        raise ValueError("Lightpanda claimant credential paths must be exact")

    for path, mode, uid, gid, label in (
        (paths.ca_certificate, 0o444, None, None, "CA certificate"),
        (paths.client_certificate, 0o444, None, None, "client certificate"),
        (
            paths.client_private_key,
            0o400,
            CLAIMANT_UID,
            CLAIMANT_GID,
            "client private key",
        ),
        (paths.ca_sha256, 0o444, None, None, "CA pin"),
        (paths.server_leaf_sha256, 0o444, None, None, "server leaf pin"),
        (paths.server_spki_sha256, 0o444, None, None, "server SPKI pin"),
    ):
        _validate_installed_file(path, mode=mode, uid=uid, gid=gid, label=label)

    pins = ClaimantPins(
        ca_sha256=_read_pin(paths.ca_sha256, "CA pin"),
        server_leaf_sha256=_read_pin(paths.server_leaf_sha256, "server leaf pin"),
        server_spki_sha256=_read_pin(paths.server_spki_sha256, "server SPKI pin"),
    )
    ca, client, private_key = validate_claimant_credential_material(
        ca_path=paths.ca_certificate,
        client_path=paths.client_certificate,
        client_key_path=paths.client_private_key,
    )
    del client, private_key
    ca_digest = hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest()
    if ca_digest != pins.ca_sha256:
        raise ValueError("Lightpanda claimant CA pin does not match its certificate")
    return pins


def validate_claimant_credential_material(
    *,
    ca_path: Path,
    client_path: Path,
    client_key_path: Path,
) -> tuple[x509.Certificate, x509.Certificate, ec.EllipticCurvePrivateKey]:
    """Validate claimant PKI material without inspecting deployment metadata."""

    ca = _load_single_certificate(ca_path, "Lightpanda claimant CA")
    client = _load_single_certificate(client_path, "Lightpanda claimant client certificate")
    private_key = _load_private_key(client_key_path)
    _validate_ca(ca)
    _validate_leaf(
        client,
        ca,
        extended_usage=ExtendedKeyUsageOID.CLIENT_AUTH,
        expected_san=x509.UniformResourceIdentifier(CLAIMANT_CLIENT_IDENTITY),
        label="Lightpanda claimant client certificate",
    )
    certificate_spki = client.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_spki = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if certificate_spki != key_spki:
        raise ValueError("Lightpanda claimant client certificate and key do not match")
    return ca, client, private_key


def validate_server_certificate(
    *,
    server_path: Path,
    ca: x509.Certificate,
    service_host: str,
) -> tuple[str, str]:
    """Validate the fixed server leaf and return its DER and SPKI pins."""

    try:
        address = ipaddress.ip_address(service_host)
    except ValueError as exc:
        raise ValueError("Lightpanda service host must be a literal IP") from exc
    server = _load_single_certificate(server_path, "Lightpanda renderer server certificate")
    _validate_leaf(
        server,
        ca,
        extended_usage=ExtendedKeyUsageOID.SERVER_AUTH,
        expected_san=x509.IPAddress(address),
        label="Lightpanda renderer server certificate",
    )
    leaf = server.public_bytes(serialization.Encoding.DER)
    spki = server.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(leaf).hexdigest(), hashlib.sha256(spki).hexdigest()


def _validate_installed_file(
    path: Path,
    *,
    mode: int,
    uid: int | None,
    gid: int | None,
    label: str,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if stat.S_IMODE(metadata.st_mode) != mode:
        raise ValueError(f"{label} has an unsafe mode")
    if not 0 < metadata.st_size <= _MAX_PEM_BYTES:
        raise ValueError(f"{label} has an invalid size")
    if uid is not None and (metadata.st_uid != uid or metadata.st_gid != gid):
        raise ValueError(f"{label} has unsafe ownership")


def _read_pin(path: Path, label: str) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if len(payload) != _PIN_BYTES or payload[-1:] != b"\n":
        raise ValueError(f"{label} is not canonical")
    value = payload[:-1]
    if any(byte not in b"0123456789abcdef" for byte in value):
        raise ValueError(f"{label} is not canonical")
    return value.decode("ascii")


def _load_single_certificate(path: Path, label: str) -> x509.Certificate:
    try:
        payload = path.read_bytes()
        if not 0 < len(payload) <= _MAX_PEM_BYTES or b"\x00" in payload:
            raise ValueError
        certificates = x509.load_pem_x509_certificates(payload)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must contain one certificate") from exc
    if len(certificates) != 1:
        raise ValueError(f"{label} must contain one certificate")
    certificate = certificates[0]
    canonical = certificate.public_bytes(serialization.Encoding.PEM)
    if payload.strip() != canonical.strip():
        raise ValueError(f"{label} must contain only one certificate")
    return certificate


def _load_private_key(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        payload = path.read_bytes()
        if not 0 < len(payload) <= _MAX_PEM_BYTES or b"\x00" in payload:
            raise ValueError
        key = serialization.load_pem_private_key(payload, password=None)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("Lightpanda claimant private key is invalid") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Lightpanda claimant private key must be ECDSA P-256")
    canonical = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    if payload.strip() != canonical.strip():
        raise ValueError("Lightpanda claimant private key must be one canonical PKCS8 key")
    return key


def _validate_ca(certificate: x509.Certificate) -> None:
    _validate_signature_profile(certificate, "Lightpanda claimant CA")
    if certificate.subject != certificate.issuer:
        raise ValueError("Lightpanda claimant CA must be self-issued")
    try:
        certificate.verify_directly_issued_by(certificate)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Lightpanda claimant CA self-signature is invalid") from exc
    constraints = _required_extension(certificate, x509.BasicConstraints, "CA constraints")
    if not constraints.critical or constraints.value != x509.BasicConstraints(True, 0):
        raise ValueError("Lightpanda claimant CA constraints are invalid")
    usage = _required_extension(certificate, x509.KeyUsage, "CA key usage")
    expected_usage = x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=True,
        encipher_only=False,
        decipher_only=False,
    )
    if not usage.critical or usage.value != expected_usage:
        raise ValueError("Lightpanda claimant CA key usage is invalid")
    for extension_type in (x509.SubjectAlternativeName, x509.ExtendedKeyUsage):
        try:
            certificate.extensions.get_extension_for_class(extension_type)
        except x509.ExtensionNotFound:
            continue
        raise ValueError("Lightpanda claimant CA profile is invalid")
    _validate_validity(certificate, minimum_days=1000, maximum_days=1120, label="CA")


def _validate_leaf(
    certificate: x509.Certificate,
    ca: x509.Certificate,
    *,
    extended_usage: x509.ObjectIdentifier,
    expected_san: x509.GeneralName,
    label: str,
) -> None:
    _validate_signature_profile(certificate, label)
    if certificate.issuer != ca.subject:
        raise ValueError(f"{label} issuer is invalid")
    try:
        certificate.verify_directly_issued_by(ca)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(f"{label} signature is invalid") from exc
    constraints = _required_extension(certificate, x509.BasicConstraints, "leaf constraints")
    if not constraints.critical or constraints.value != x509.BasicConstraints(False, None):
        raise ValueError(f"{label} constraints are invalid")
    usage = _required_extension(certificate, x509.KeyUsage, "leaf key usage")
    expected_usage = x509.KeyUsage(
        digital_signature=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    if not usage.critical or usage.value != expected_usage:
        raise ValueError(f"{label} key usage is invalid")
    usages = _required_extension(certificate, x509.ExtendedKeyUsage, "extended key usage")
    if usages.critical or list(usages.value) != [extended_usage]:
        raise ValueError(f"{label} extended key usage is invalid")
    san = _required_extension(certificate, x509.SubjectAlternativeName, "subject alternative name")
    if san.critical or list(san.value) != [expected_san]:
        raise ValueError(f"{label} identity is invalid")
    _validate_validity(certificate, minimum_days=170, maximum_days=190, label=label)


def _validate_signature_profile(certificate: x509.Certificate, label: str) -> None:
    public_key = certificate.public_key()
    if (
        certificate.signature_algorithm_oid != SignatureAlgorithmOID.ECDSA_WITH_SHA256
        or certificate.signature_hash_algorithm is None
        or not isinstance(certificate.signature_hash_algorithm, hashes.SHA256)
        or not isinstance(public_key, ec.EllipticCurvePublicKey)
        or not isinstance(public_key.curve, ec.SECP256R1)
    ):
        raise ValueError(f"{label} signature profile is invalid")


def _required_extension[
    ExtensionType: x509.ExtensionType,
](
    certificate: x509.Certificate,
    extension_type: type[ExtensionType],
    label: str,
) -> x509.Extension[ExtensionType]:
    try:
        return certificate.extensions.get_extension_for_class(extension_type)
    except x509.ExtensionNotFound as exc:
        raise ValueError(f"Lightpanda claimant {label} is missing") from exc


def _validate_validity(
    certificate: x509.Certificate,
    *,
    minimum_days: int,
    maximum_days: int,
    label: str,
) -> None:
    lifetime = certificate.not_valid_after_utc - certificate.not_valid_before_utc
    if not dt.timedelta(days=minimum_days) <= lifetime <= dt.timedelta(days=maximum_days):
        raise ValueError(f"Lightpanda claimant {label} lifetime is invalid")
    now = dt.datetime.now(dt.UTC)
    if (
        certificate.not_valid_before_utc > now
        or certificate.not_valid_after_utc <= now + dt.timedelta(days=30)
    ):
        raise ValueError(f"Lightpanda claimant {label} is not currently valid")


__all__ = [
    "CLAIMANT_CA_PATH",
    "CLAIMANT_CA_PIN_PATH",
    "CLAIMANT_CLIENT_CERTIFICATE_PATH",
    "CLAIMANT_CLIENT_PRIVATE_KEY_PATH",
    "CLAIMANT_GID",
    "CLAIMANT_SERVER_LEAF_PIN_PATH",
    "CLAIMANT_SERVER_SPKI_PIN_PATH",
    "CLAIMANT_UID",
    "ClaimantCredentialPaths",
    "ClaimantPins",
    "validate_claimant_credential_material",
    "validate_installed_claimant_credentials",
    "validate_server_certificate",
]
