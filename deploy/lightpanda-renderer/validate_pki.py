#!/usr/bin/env python3
"""Validate the fixed dormant-renderer PKI profile and emit public pins only."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import re
import subprocess
import sys
from pathlib import Path


class PKIError(RuntimeError):
    pass


def openssl(*arguments: str, stdin: bytes | None = None) -> bytes:
    try:
        return subprocess.run(
            ["openssl", *arguments],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise PKIError("OpenSSL rejected the renderer PKI") from error


def require_single_pem(path: Path, label: str) -> bytes:
    payload = path.read_bytes()
    if not payload or len(payload) > 32768 or b"\x00" in payload:
        raise PKIError("renderer PKI object has an invalid size")
    begin = f"-----BEGIN {label}-----".encode()
    end = f"-----END {label}-----".encode()
    if payload.count(begin) != 1 or payload.count(end) != 1:
        raise PKIError("renderer PKI must contain exactly one PEM object")
    if payload.count(b"-----BEGIN ") != 1 or payload.count(b"-----END ") != 1:
        raise PKIError("renderer PKI must not contain a chain or extra object")
    return payload


def extension(text: str, name: str) -> tuple[bool, str] | None:
    pattern = re.compile(
        rf"^[ \t]*X509v3 {re.escape(name)}:[ \t]*(critical)?[ \t]*$\n"
        r"[ \t]+([^\n]+)$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if match is None:
        return None
    value = match.group(2).strip()
    return bool(match.group(1)), value


def sha256_der(cert: Path) -> str:
    return hashlib.sha256(openssl("x509", "-in", str(cert), "-outform", "DER")).hexdigest()


def sha256_spki(cert: Path) -> str:
    public_pem = openssl("x509", "-in", str(cert), "-pubkey", "-noout")
    public_der = openssl("pkey", "-pubin", "-outform", "DER", stdin=public_pem)
    return hashlib.sha256(public_der).hexdigest()


def certificate_spki(cert: Path) -> bytes:
    public_pem = openssl("x509", "-in", str(cert), "-pubkey", "-noout")
    return openssl("pkey", "-pubin", "-outform", "DER", stdin=public_pem)


def certificate_text(cert: Path) -> str:
    return openssl("x509", "-in", str(cert), "-noout", "-text").decode("utf-8")


def validate_ecdsa_p256_sha256(text: str) -> None:
    if text.count("Signature Algorithm: ecdsa-with-SHA256") < 2:
        raise PKIError("renderer certificate signature algorithm is not exact")
    if "Public Key Algorithm: id-ecPublicKey" not in text:
        raise PKIError("renderer certificate public key is not ECDSA")


def validate_p256_spki(cert: Path) -> None:
    spki = certificate_spki(cert)
    ec_public_key_oid = bytes.fromhex("06072a8648ce3d0201")
    prime256v1_oid = bytes.fromhex("06082a8648ce3d030107")
    if len(spki) != 91 or ec_public_key_oid not in spki or prime256v1_oid not in spki:
        raise PKIError("renderer certificate curve is not P-256")


def validate_ca(path: Path) -> None:
    require_single_pem(path, "CERTIFICATE")
    text = certificate_text(path)
    validate_ecdsa_p256_sha256(text)
    validate_p256_spki(path)
    basic = extension(text, "Basic Constraints")
    usage = extension(text, "Key Usage")
    if basic != (True, "CA:TRUE, pathlen:0"):
        raise PKIError("renderer CA constraints are not critical pathlen zero")
    if usage is None or usage[0] is not True:
        raise PKIError("renderer CA key usage is not critical")
    if usage[1] not in {"Certificate Sign", "Certificate Sign, CRL Sign"}:
        raise PKIError("renderer CA key usage is not exact")
    if extension(text, "Subject Alternative Name") is not None:
        raise PKIError("renderer CA must not contain SANs")
    if extension(text, "Extended Key Usage") is not None:
        raise PKIError("renderer CA must not contain extended usages")
    openssl("verify", "-check_ss_sig", "-CAfile", str(path), str(path))
    openssl("x509", "-in", str(path), "-noout", "-checkend", "2592000")
    dates = openssl("x509", "-in", str(path), "-noout", "-dates").decode().splitlines()
    if (
        len(dates) != 2
        or not dates[0].startswith("notBefore=")
        or not dates[1].startswith("notAfter=")
    ):
        raise PKIError("renderer CA validity is unreadable")
    start = dt.datetime.strptime(dates[0].split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.UTC
    )
    end = dt.datetime.strptime(dates[1].split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.UTC
    )
    lifetime_days = (end - start).total_seconds() / 86400
    if lifetime_days < 1000 or lifetime_days > 1120:
        raise PKIError("renderer CA validity is not the reviewed approximately three-year profile")


def validate_leaf(
    path: Path,
    ca: Path,
    *,
    san: str,
    extended_usage: str,
) -> None:
    require_single_pem(path, "CERTIFICATE")
    text = certificate_text(path)
    validate_ecdsa_p256_sha256(text)
    validate_p256_spki(path)
    if extension(text, "Basic Constraints") != (True, "CA:FALSE"):
        raise PKIError("renderer leaf constraints are not critical CA=false")
    if extension(text, "Key Usage") != (True, "Digital Signature"):
        raise PKIError("renderer leaf key usage is not critical digitalSignature")
    if extension(text, "Subject Alternative Name") != (False, san):
        raise PKIError("renderer leaf SAN is not exact")
    if extension(text, "Extended Key Usage") != (False, extended_usage):
        raise PKIError("renderer leaf extended usage is not exact")
    openssl("verify", "-purpose", "any", "-CAfile", str(ca), str(path))
    openssl("x509", "-in", str(path), "-noout", "-checkend", "2592000")
    dates = openssl("x509", "-in", str(path), "-noout", "-dates").decode().splitlines()
    if (
        len(dates) != 2
        or not dates[0].startswith("notBefore=")
        or not dates[1].startswith("notAfter=")
    ):
        raise PKIError("renderer leaf validity is unreadable")
    start = dt.datetime.strptime(dates[0].split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.UTC
    )
    end = dt.datetime.strptime(dates[1].split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=dt.UTC
    )
    lifetime_days = (end - start).total_seconds() / 86400
    if lifetime_days < 170 or lifetime_days > 190:
        raise PKIError("renderer leaf validity is not the reviewed 180-day profile")


def validate_key(path: Path, server_cert: Path) -> None:
    require_single_pem(path, "PRIVATE KEY")
    key_public = openssl("pkey", "-in", str(path), "-pubout", "-outform", "DER")
    cert_public_pem = openssl("x509", "-in", str(server_cert), "-pubkey", "-noout")
    cert_public = openssl("pkey", "-pubin", "-outform", "DER", stdin=cert_public_pem)
    if not hmac.compare_digest(
        hashlib.sha256(key_public).digest(), hashlib.sha256(cert_public).digest()
    ):
        raise PKIError("renderer server certificate and key do not match")


def validate(ca: Path, server: Path, server_key: Path, client: Path) -> dict[str, str]:
    validate_ca(ca)
    validate_leaf(
        server,
        ca,
        san="IP Address:10.0.0.5",
        extended_usage="TLS Web Server Authentication",
    )
    validate_leaf(
        client,
        ca,
        san="URI:spiffe://jobseek/crawler/lightpanda-b0",
        extended_usage="TLS Web Client Authentication",
    )
    validate_key(server_key, server)
    return {
        "CA_DER_SHA256": sha256_der(ca),
        "SERVER_LEAF_SHA256": sha256_der(server),
        "SERVER_SPKI_SHA256": sha256_spki(server),
        "CLIENT_LEAF_SHA256": sha256_der(client),
        "CLIENT_SPKI_SHA256": sha256_spki(client),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--server-key", required=True, type=Path)
    parser.add_argument("--client", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.ca, args.server, args.server_key, args.client)
        args.output.write_text(
            "".join(f"{key}={result[key]}\n" for key in sorted(result)),
            encoding="ascii",
        )
        return 0
    except PKIError as error:
        print(f"renderer PKI validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
