#!/usr/bin/env python3
"""Generate frozen source-identity compatibility vectors."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
V1 = ROOT.parents[1]
OUTPUT = ROOT / "vectors.json"
DIGEST = ROOT / "vectors.sha256"


def _compatibility_module() -> Any:
    path = V1 / "tools" / "check_compatibility.py"
    spec = importlib.util.spec_from_file_location("source_identity_descriptor_tools", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _message_schemas() -> dict[str, object]:
    compatibility = _compatibility_module()
    frozen_bytes, _ = compatibility.load_frozen_baseline(V1)
    shapes = {
        "frozen": compatibility.parse_descriptor_set(frozen_bytes),
        "current": compatibility.parse_descriptor_set(
            compatibility.compile_descriptor(V1 / "runtime.proto")
        ),
    }
    result: dict[str, object] = {}
    prefix = "jobseek.crawler.runtime.v1."
    for version, shape in shapes.items():
        messages = compatibility._flatten_messages(shape)
        selected: dict[str, object] = {}
        for message_name in ("DiscoveredJob", "JobEffect"):
            fields = []
            for field in messages[prefix + message_name].fields:
                if field.type == 9:
                    value_type = "string"
                elif field.type == 11:
                    value_type = "message"
                else:
                    raise AssertionError(
                        f"unsupported source-identity fixture field type: {field.type}"
                    )
                fields.append(
                    {
                        "json_name": field.json_name,
                        "name": field.name,
                        "number": field.number,
                        "proto3_optional": field.proto3_optional,
                        "value_type": value_type,
                        "wire_type": 2,
                    }
                )
            selected[message_name] = {"fields": fields}
        encoded = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
        result[version] = {
            "messages": selected,
            "shape_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    return result


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("protobuf fixture varints must be non-negative")
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _bytes_field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _text_field(number: int, value: str) -> bytes:
    return _bytes_field(number, value.encode("utf-8"))


def _message_vector(
    *,
    message: str,
    url_field: str,
    url: str,
    second_field: bytes,
    source_identity: str,
) -> dict[str, object]:
    absent = _text_field(1, url) + second_field
    present = absent + _text_field(3, source_identity)
    future = present + _varint(99 << 3) + _varint(9_007_199_254_740_993)
    return {
        "message": message,
        "url_field": url_field,
        "source_identity": source_identity,
        "absent_wire_hex": absent.hex(),
        "present_wire_hex": present.hex(),
        "future_unknown_field": 99,
        "future_unknown_wire_hex": future.hex(),
    }


def _job(*, source_identity: object = ...) -> dict[str, object]:
    job: dict[str, object] = {
        "url": "https://jobs.example/42?lang=en",
        "title": "Engineer",
        "description": None,
        "locations": ["Zurich"],
        "employment_type": None,
        "job_location_type": None,
        "date_posted": None,
        "base_salary": None,
        "language": "en",
        "localizations": None,
        "extras": None,
        "metadata": None,
    }
    if source_identity is not ...:
        job["source_identity"] = source_identity
    return job


def _json_vector(identifier: str, job: dict[str, object]) -> dict[str, object]:
    canonical = json.dumps(job, sort_keys=True, separators=(",", ":")).encode("ascii")
    return {"id": identifier, "job": job, "canonical_json_hex": canonical.hex()}


def corpus() -> dict[str, object]:
    url = "https://jobs.example/42?lang=en"
    identity = "smartrecruiters:example:42"
    content = _bytes_field(2, _text_field(1, "Engineer"))
    content_hash = "0" * 64
    return {
        "format": 1,
        "schemas": _message_schemas(),
        "protobuf": [
            _message_vector(
                message="DiscoveredJob",
                url_field="url",
                url=url,
                second_field=content,
                source_identity=identity,
            ),
            _message_vector(
                message="JobEffect",
                url_field="source_url",
                url=url,
                second_field=_text_field(2, content_hash),
                source_identity=identity,
            ),
        ],
        "json_jobs": [
            _json_vector("absent", _job()),
            _json_vector("explicit-null", _job(source_identity=None)),
            _json_vector("present", _job(source_identity=identity)),
        ],
    }


def render() -> tuple[bytes, bytes]:
    raw = (json.dumps(corpus(), indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = f"{hashlib.sha256(raw).hexdigest()}  vectors.json\n".encode()
    return raw, digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    raw, digest = render()
    if arguments.check:
        return 0 if OUTPUT.read_bytes() == raw and DIGEST.read_bytes() == digest else 1
    OUTPUT.write_bytes(raw)
    DIGEST.write_bytes(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
