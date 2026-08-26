from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from conformance.python.contract import (
    HARD_LIMITS,
    ContractViolation,
    load_case,
    load_replay,
    validate_case,
    validate_replay,
)
from crawler_runtime_contracts.v1 import runtime_pb2 as pb
from google.protobuf import __version__ as protobuf_version
from redaction import redact, redact_email

ROOT = Path(__file__).parents[1]
CONTRACTS = ROOT.parent
FIXTURES = ROOT / "fixtures"

JWT_RE = re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
PRIVATE_KEY_RE = re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")
URL_CREDENTIAL_RE = re.compile(rb"https?://[^/@\s:]+:[^/@\s]+@")
EMAIL_RE = re.compile(rb"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def check_versions() -> None:
    if int(protobuf_version.split(".", 1)[0]) != 6:
        raise AssertionError(
            "generated Python binding is runtime-tested only on protobuf 6.x, "
            f"got {protobuf_version}"
        )
    generated = (ROOT / "gen/python/runtime_pb2.py").read_text()
    if "Protobuf Python Version: 6.31.1" not in generated:
        raise AssertionError("Python binding was not produced by pinned libprotoc 31.1")
    if list(ROOT.glob("*.schema.json")):
        raise AssertionError("runtime.proto must remain the sole v1 IDL")
    proto = (ROOT / "runtime.proto").read_text()
    if "google.protobuf.Struct" in proto or "google.protobuf.Value" in proto:
        raise AssertionError("free-form Struct/Value is forbidden in runtime v1")
    baseline = json.loads((ROOT / "compatibility_baseline.json").read_text())
    descriptor_sha256 = hashlib.sha256(pb.DESCRIPTOR.serialized_pb).hexdigest()
    if baseline != {
        "contract_version": "crawler.runtime/v1",
        "file_descriptor_sha256": descriptor_sha256,
    }:
        raise AssertionError(
            "runtime.proto changed after the frozen v1 descriptor baseline; "
            "create v2 and converters"
        )

    limits = json.loads((ROOT / "limits.json").read_text())
    fields = {field.name for field in pb.Limits.DESCRIPTOR.fields}
    if set(limits) != fields:
        raise AssertionError("limits.json must define every and only Limits field")
    if pb.Limits(**limits) != HARD_LIMITS:
        raise AssertionError("Python hard limits drifted from limits.json")
    for document in (ROOT / "protocol.md", ROOT / "metrics.md"):
        text = document.read_text()
        if "authoritative=true" in text or "valid authoritative terminal" in text:
            raise AssertionError(f"obsolete terminal authority wording in {document}")

    versions = sorted(
        int(path.name[1:])
        for path in CONTRACTS.glob("v[0-9]*")
        if path.is_dir() and path.name[1:].isdigit()
    )
    for previous, current in zip(versions, versions[1:], strict=False):
        converter = CONTRACTS / f"v{previous}" / "converters" / f"v{previous}_to_v{current}"
        required = {
            converter / "converter.json",
            converter / "python.py",
            converter / "converter.go",
            converter / "fixtures/roundtrip.json",
            converter / "fixtures/lossy.json",
        }
        missing = sorted(
            str(path) for path in required if not path.is_file() or path.stat().st_size == 0
        )
        if missing:
            raise AssertionError(
                f"v{current} requires nonempty Python/Go converters and fixtures: {missing}"
            )
        manifest = json.loads((converter / "converter.json").read_text())
        if manifest != {
            "source_contract": f"crawler.runtime/v{previous}",
            "target_contract": f"crawler.runtime/v{current}",
            "supports": ["upgrade", "downgrade"],
        }:
            raise AssertionError(
                f"v{current} converter manifest is not the required bidirectional shape"
            )
        for name in ("roundtrip.json", "lossy.json"):
            vectors = json.loads((converter / "fixtures" / name).read_text())
            if not isinstance(vectors, list) or not vectors:
                raise AssertionError(f"v{current} converter fixture {name} must be a nonempty list")
        compile(
            (converter / "python.py").read_text(),
            str(converter / "python.py"),
            "exec",
        )
        if not re.search(
            r"(?m)^package [a-z][a-z0-9_]*$", (converter / "converter.go").read_text()
        ):
            raise AssertionError(f"v{current} converter.go must declare a Go package")


def check_fixtures() -> None:
    files = sorted(FIXTURES.rglob("*.json"))
    if not files:
        raise AssertionError("fixture corpus is empty")
    if sum(path.stat().st_size for path in files) > 64 * 1024 * 1024:
        raise AssertionError("fixture corpus exceeds 64 MiB")
    for path in files:
        if path.stat().st_size > 12 * 1024 * 1024:
            raise AssertionError(f"fixture exceeds bounded JSON envelope: {path}")
        raw = path.read_bytes()
        if JWT_RE.search(raw) or PRIVATE_KEY_RE.search(raw) or URL_CREDENTIAL_RE.search(raw):
            raise AssertionError(f"fixture contains secret-shaped material: {path}")
        for email in EMAIL_RE.findall(raw):
            if not email.lower().endswith(b"@redacted.invalid"):
                raise AssertionError(f"fixture contains a non-redacted email: {path}")

    for path in sorted((FIXTURES / "conformance/positive").glob("*.json")):
        case = load_case(path)
        if not case.expected_valid:
            raise AssertionError(f"positive fixture is marked invalid: {path}")
        validate_case(case)
    for path in sorted((FIXTURES / "conformance/negative").glob("*.json")):
        if path.name == "browser-union-partial-output.json":
            try:
                load_case(path)
            except Exception:
                continue
            raise AssertionError("protobuf oneof accepted partial unsupported browser output")
        case = load_case(path)
        try:
            validate_case(case)
        except ContractViolation as exc:
            if exc.code != case.expected_error:
                raise AssertionError(
                    f"{path}: got {exc.code}, expected {case.expected_error}"
                ) from exc
        else:
            raise AssertionError(f"negative fixture unexpectedly passed: {path}")
    for path in sorted((FIXTURES / "replay").glob("*.json")):
        validate_replay(load_replay(path))


def check_redaction_vectors() -> None:
    expected = "redacted-sha256:3742f7ab8e2513ce9d6da7e6e16d9f1b9797765fd79e11723769b2026fa70e2d"
    actual = redact("header:authorization", "fixture-token")
    if actual != expected:
        raise AssertionError(f"redaction algorithm drifted: {actual}")
    email = redact_email("person:email", "person@example.test")
    if not email.endswith("@redacted.invalid"):
        raise AssertionError("email redaction escaped the reserved invalid domain")


def main() -> None:
    check_versions()
    check_fixtures()
    check_redaction_vectors()


if __name__ == "__main__":
    main()
