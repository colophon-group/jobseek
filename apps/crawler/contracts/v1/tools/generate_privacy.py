#!/usr/bin/env python3
"""Generate the closed candidate privacy registry and synthetic corpus."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_from_bytes

ROOT = Path(__file__).parents[1]
REGISTRY_PATH = ROOT / "privacy_registry.json"
MANIFEST_PATH = ROOT / "fixtures" / "redaction" / "manifest.json"
DOMAIN = b"jobseek.runtime.v1.redaction.result\0"

LIMITS = {
    "max_chunks": 256,
    "max_decoded_working_set_bytes": 2_097_152,
    "max_encoded_input_bytes": 1_048_576,
    "max_json_depth": 32,
    "max_output_bytes": 2_097_152,
    "max_structured_items": 10_000,
}

REGISTRY: dict[str, Any] = {
    "contexts": ["headers", "url", "json", "form", "extension_envelope"],
    "extension_envelopes": [
        {
            "encoding": "canonical_json",
            "payload_contexts": ["headers", "url", "json", "form"],
            "schema_id": "jobseek.synthetic.capture",
            "schema_version": 1,
        }
    ],
    "format": "jobseek.runtime.privacy-registry/v1",
    "key_normalization": {
        "ascii_lowercase": True,
        "separator_bytes": [" ", "\t", "\n", "\u000b", "\f", "\r", "-", ".", "_"],
        "separator_replacement": "-",
        "word_splitting": False,
    },
    "limits": LIMITS,
    "rejected_codes": [
        "unknown_context",
        "malformed_encoding",
        "unsupported_envelope",
        "artifact_unavailable",
        "limit_exceeded",
        "invalid_chunks",
    ],
    "rules": [
        {
            "contexts": ["headers"],
            "id": "credential_header",
            "keys": ["authentication", "authorization", "proxy-authorization"],
            "kind": "key",
        },
        {
            "contexts": ["headers", "url", "json", "form", "extension_envelope"],
            "id": "cookie",
            "keys": ["cookie", "set-cookie"],
            "kind": "key",
        },
        {
            "contexts": ["headers", "url", "json", "form", "extension_envelope"],
            "id": "secret_key",
            "keys": [
                "access-token",
                "accesstoken",
                "api-key",
                "apikey",
                "auth",
                "authentication",
                "authorization",
                "client-secret",
                "clientsecret",
                "credential",
                "password",
                "passwd",
                "private-key",
                "privatekey",
                "refresh-token",
                "refreshtoken",
                "secret",
                "secret-key",
                "secretaccesskey",
                "secretkey",
                "session-token",
                "sessiontoken",
                "token",
                "x-api-token",
                "x-secret",
                "xapitoken",
                "xsecret",
            ],
            "kind": "key",
        },
        {
            "contexts": ["headers", "url", "json", "form", "extension_envelope"],
            "id": "credential_scheme",
            "kind": "ascii_case_insensitive_prefix",
            "prefixes": ["basic ", "bearer "],
        },
        {
            "contexts": ["headers", "url", "json", "form", "extension_envelope"],
            "id": "email",
            "kind": "ascii_email_scalar",
        },
        {"contexts": ["url"], "id": "url_userinfo", "kind": "url_userinfo"},
    ],
    "statuses": ["unchanged", "transformed", "rejected"],
    "wrappers": ["percent", "base64"],
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def replacement(rule_id: str) -> bytes:
    return f"[REDACTED:{rule_id}]".encode()


def finding(rule_id: str, context: str) -> dict[str, str]:
    return {"context": context, "rule_id": rule_id}


def result(
    case_id: str, output: bytes, findings: list[dict[str, str]], original: bytes
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "findings": findings,
        "output_b64": b64(output),
        "status": "transformed"
        if findings
        else ("unchanged" if output == original else "transformed"),
    }


def rejected(case_id: str, code: str) -> dict[str, str]:
    return {"case_id": case_id, "error_code": code, "status": "rejected"}


def inline(value: bytes) -> dict[str, str]:
    return {"inline_b64": b64(value)}


def chunks(parts: list[bytes], *, complete: bool = True) -> dict[str, Any]:
    return {
        "chunk_manifest": {
            "chunks": [
                {
                    "data_b64": b64(part),
                    "sequence": index,
                    "sha256": hashlib.sha256(part).hexdigest(),
                    "size": len(part),
                }
                for index, part in enumerate(parts)
            ],
            "complete": complete,
            "total_size": sum(map(len, parts)),
        }
    }


def case(
    case_id: str,
    context: str,
    raw: bytes,
    output: bytes,
    findings: list[dict[str, str]],
    *,
    wrapper: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "case_id": case_id,
        "context": context,
        "expected": result(case_id, output, findings, raw),
        "input": source or inline(raw),
    }
    if wrapper is not None:
        item["wrapper"] = wrapper
    return item


def reject_case(
    case_id: str,
    context: str,
    code: str,
    raw: bytes = b"SYNTHETIC_REJECTED_CANARY",
    *,
    wrapper: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "case_id": case_id,
        "context": context,
        "expected": rejected(case_id, code),
        "input": source or inline(raw),
    }
    if wrapper is not None:
        item["wrapper"] = wrapper
    return item


def envelope(
    inner: dict[str, Any],
    *,
    schema: str = "jobseek.synthetic.capture",
    version: int = 1,
    encoding: str = "canonical_json",
    payload_sha256: str = "SYNTHETIC-PREEXISTING-NOT-VERIFIED",
) -> bytes:
    return envelope_payload(
        canonical_json(inner),
        schema=schema,
        version=version,
        encoding=encoding,
        payload_sha256=payload_sha256,
    )


def envelope_payload(
    payload: bytes,
    *,
    schema: str = "jobseek.synthetic.capture",
    version: int = 1,
    encoding: str = "canonical_json",
    payload_sha256: str = "SYNTHETIC-PREEXISTING-NOT-VERIFIED",
) -> bytes:
    return canonical_json(
        {
            "encoding": encoding,
            "payload_b64": b64(payload),
            "payload_sha256": payload_sha256,
            "schema_id": schema,
            "schema_version": version,
        }
    )


def safe_envelope_output(inner: dict[str, Any]) -> bytes:
    return envelope(inner, payload_sha256="")


def wrapped(value: bytes, wrapper: str) -> bytes:
    if wrapper == "base64":
        return base64.b64encode(value)
    return quote_from_bytes(value, safe="").encode()


def make_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    safe_values = {
        "safe_headers": ("headers", b"content-type: application/json\nx-label: public\n"),
        "safe_header_colon_value": ("headers", b"x-safe: alpha: beta\n"),
        "safe_url": ("url", b"https://jobs.invalid/list?page=2&team=search"),
        "safe_json": ("json", b'{"name":"Ada","roles":["engineer"]}'),
        "safe_form": ("form", b"name=Ada&team=search"),
    }
    for case_id, (context, raw) in safe_values.items():
        cases.append(case(case_id, context, raw, raw, []))

    wrapped_raw = b'{"name":"Ada"}'
    for wrapper in ("percent", "base64"):
        encoded = wrapped(wrapped_raw, wrapper)
        cases.append(case(f"safe_{wrapper}_wrapper", "json", encoded, encoded, [], wrapper=wrapper))

    safe_inner = {
        "inline": {"context": "json", "data_b64": b64(b'{"name":"Ada"}')},
        "metadata": [{"name": "x-label", "value": "public"}],
    }
    safe_outer = envelope(safe_inner, payload_sha256="")
    cases.append(
        case(
            "safe_extension_envelope",
            "extension_envelope",
            safe_outer,
            safe_envelope_output(safe_inner),
            [],
        )
    )

    for case_id, key in (
        ("redact_separator_vt", "api\vkey"),
        ("redact_separator_ff", "api\fkey"),
    ):
        raw = canonical_json({key: "SYNTHETIC_SEPARATOR"})
        output = canonical_json({key: "[REDACTED:secret_key]"})
        cases.append(case(case_id, "json", raw, output, [finding("secret_key", "json")]))
    for case_id, key in (
        ("safe_leading_separator_key", " token"),
        ("safe_trailing_separator_key", "token_"),
    ):
        raw = canonical_json({key: "public"})
        cases.append(case(case_id, "json", raw, raw, []))

    duplicate_json = b'{"name":"first","name":"second"}'
    cases.append(
        reject_case("reject_json_duplicate_key", "json", "malformed_encoding", duplicate_json)
    )
    extreme_json = b"[" * 2_048 + b"0" + b"]" * 2_048
    cases.append(reject_case("reject_json_extreme_depth", "json", "limit_exceeded", extreme_json))
    cases.append(
        reject_case(
            "reject_json_lone_surrogate",
            "json",
            "malformed_encoding",
            b'{"value":"\\uD800"}',
        )
    )
    u2028_json = canonical_json({"value": "\u2028"})
    cases.append(case("safe_json_u2028", "json", u2028_json, u2028_json, []))

    safe_payload = canonical_json(safe_inner)
    duplicate_outer = (
        b'{"encoding":"canonical_json","payload_b64":"'
        + b64(safe_payload).encode()
        + b'","payload_sha256":"","schema_id":"jobseek.synthetic.capture",'
        + b'"schema_id":"jobseek.synthetic.capture","schema_version":1}'
    )
    cases.append(
        reject_case(
            "reject_envelope_outer_duplicate_key",
            "extension_envelope",
            "malformed_encoding",
            duplicate_outer,
        )
    )
    cases.append(
        reject_case(
            "reject_envelope_inner_extreme_depth",
            "extension_envelope",
            "limit_exceeded",
            envelope_payload(extreme_json),
        )
    )
    cases.append(
        reject_case(
            "reject_envelope_inner_lone_surrogate",
            "extension_envelope",
            "malformed_encoding",
            envelope_payload(b'{"value":"\\uDFFF"}'),
        )
    )
    u2029_inner = {
        "inline": {"context": "json", "data_b64": b64(canonical_json({"value": "\u2029"}))},
        "metadata": [],
    }
    u2029_outer = envelope(u2029_inner, payload_sha256="")
    cases.append(
        case(
            "safe_envelope_inline_u2029",
            "extension_envelope",
            u2029_outer,
            u2029_outer,
            [],
        )
    )

    header_matrix = [
        ("redact_set_cookie", b"set-cookie: sid=SYNTHETIC_COOKIE\n", "cookie"),
        ("redact_api_key", b"api-key: SYNTHETIC_API_KEY\n", "secret_key"),
        ("redact_x_api_token", b"x-api-token: SYNTHETIC_TOKEN\n", "secret_key"),
        ("redact_authentication", b"authentication: SYNTHETIC_AUTH\n", "credential_header"),
        ("redact_x_secret", b"x-secret: SYNTHETIC_SECRET\n", "secret_key"),
        ("redact_bearer", b"authorization: Bearer SYNTHETIC_BEARER\n", "credential_header"),
        ("redact_basic", b"authorization: Basic U1lOVEhFVElDOlNFQ1JFVA==\n", "credential_header"),
        ("redact_case_variant", b"X-API-TOKEN: SYNTHETIC_CASE\n", "secret_key"),
        ("redact_separator_underscore", b"api_key: SYNTHETIC_UNDERSCORE\n", "secret_key"),
        ("redact_separator_dot", b"api.key: SYNTHETIC_DOT\n", "secret_key"),
        ("redact_separator_space", b"api key: SYNTHETIC_SPACE\n", "secret_key"),
    ]
    for case_id, raw, rule_id in header_matrix:
        name = raw.split(b": ", 1)[0]
        output = name + b": " + replacement(rule_id) + b"\n"
        cases.append(case(case_id, "headers", raw, output, [finding(rule_id, "headers")]))

    structured = [
        (
            "redact_secret_access_key",
            "json",
            b'{"secretAccessKey":"SYNTHETIC_ACCESS","safe":"yes"}',
            canonical_json({"safe": "yes", "secretAccessKey": "[REDACTED:secret_key]"}),
            "secret_key",
        ),
        (
            "redact_camel_api_key",
            "json",
            b'{"apiKey":"SYNTHETIC_CAMEL"}',
            canonical_json({"apiKey": "[REDACTED:secret_key]"}),
            "secret_key",
        ),
        (
            "redact_email_json",
            "json",
            b'{"contact":"synthetic.person@example.invalid"}',
            canonical_json({"contact": "[REDACTED:email]"}),
            "email",
        ),
        (
            "redact_unicode_escaped_email",
            "json",
            b'{"contact":"synthetic\\u0040example.invalid"}',
            canonical_json({"contact": "[REDACTED:email]"}),
            "email",
        ),
        (
            "redact_json_secret_key",
            "json",
            b'{"name":"safe","token":"SYNTHETIC_JSON_TOKEN"}',
            canonical_json({"name": "safe", "token": "[REDACTED:secret_key]"}),
            "secret_key",
        ),
        (
            "redact_form_secret",
            "form",
            b"name=Ada&client_secret=SYNTHETIC_FORM_SECRET",
            b"name=Ada&client_secret=%5BREDACTED%3Asecret_key%5D",
            "secret_key",
        ),
    ]
    for case_id, context, raw, output, rule_id in structured:
        cases.append(case(case_id, context, raw, output, [finding(rule_id, context)]))

    url_userinfo = b"https://synthetic-user:SYNTHETIC_PASSWORD@jobs.invalid/list"
    cases.append(
        case(
            "redact_url_userinfo",
            "url",
            url_userinfo,
            b"https://%5BREDACTED%3Aurl_userinfo%5D@jobs.invalid/list",
            [finding("url_userinfo", "url")],
        )
    )
    for case_id, value, rule_id in (
        ("redact_bearer_scalar", "Bearer SYNTHETIC_SCHEME", "credential_scheme"),
        ("redact_basic_scalar", "Basic U1lOVEhFVElDOlNFQ1JFVA==", "credential_scheme"),
    ):
        raw = f"https://jobs.invalid/list?note={quote_from_bytes(value.encode(), safe='')}".encode()
        output = b"https://jobs.invalid/list?note=%5BREDACTED%3Acredential_scheme%5D"
        cases.append(case(case_id, "url", raw, output, [finding(rule_id, "url")]))
    secret_query = b"https://jobs.invalid/list?team=search&access_token=SYNTHETIC_QUERY"
    cases.append(
        case(
            "redact_secret_query",
            "url",
            secret_query,
            b"https://jobs.invalid/list?team=search&access_token=%5BREDACTED%3Asecret_key%5D",
            [finding("secret_key", "url")],
        )
    )

    for wrapper in ("percent", "base64"):
        logical = b'{"authorization":"Bearer SYNTHETIC_WHOLE_BODY"}'
        expected_logical = canonical_json({"authorization": "[REDACTED:secret_key]"})
        raw = wrapped(logical, wrapper)
        output = wrapped(expected_logical, wrapper)
        cases.append(
            case(
                f"redact_whole_{wrapper}_wrapper",
                "json",
                raw,
                output,
                [finding("secret_key", "json")],
                wrapper=wrapper,
            )
        )

    envelope_inner = {
        "inline": {"context": "json", "data_b64": b64(b'{"name":"Ada"}')},
        "metadata": [{"name": "x-api-token", "value": "SYNTHETIC_ENVELOPE_TOKEN"}],
    }
    envelope_safe_inner = {
        "inline": {"context": "json", "data_b64": b64(b'{"name":"Ada"}')},
        "metadata": [{"name": "x-api-token", "value": "[REDACTED:secret_key]"}],
    }
    outer = envelope(envelope_inner)
    cases.append(
        case(
            "redact_envelope_metadata",
            "extension_envelope",
            outer,
            safe_envelope_output(envelope_safe_inner),
            [finding("secret_key", "extension_envelope")],
        )
    )
    envelope_inline = {
        "inline": {
            "context": "json",
            "data_b64": b64(b'{"email":"synthetic.envelope@example.invalid"}'),
        },
        "metadata": [],
    }
    envelope_inline_safe = {
        "inline": {
            "context": "json",
            "data_b64": b64(canonical_json({"email": "[REDACTED:email]"})),
        },
        "metadata": [],
    }
    outer = envelope(envelope_inline)
    cases.append(
        case(
            "redact_envelope_inline",
            "extension_envelope",
            outer,
            safe_envelope_output(envelope_inline_safe),
            [finding("email", "extension_envelope")],
        )
    )

    split_matrix = [
        (
            "split_header_key",
            "headers",
            [b"x-api-", b"token: SYNTHETIC_SPLIT\n"],
            b"x-api-token: [REDACTED:secret_key]\n",
            "secret_key",
        ),
        (
            "split_header_value",
            "headers",
            [b"authorization: Bea", b"rer SYNTHETIC_SPLIT\n"],
            b"authorization: [REDACTED:credential_header]\n",
            "credential_header",
        ),
        (
            "split_url_query",
            "url",
            [b"https://jobs.invalid/?access_", b"token=SYNTHETIC_SPLIT"],
            b"https://jobs.invalid/?access_token=%5BREDACTED%3Asecret_key%5D",
            "secret_key",
        ),
        (
            "split_json_key",
            "json",
            [b'{"secretAcc', b'essKey":"SYNTHETIC_SPLIT"}'],
            canonical_json({"secretAccessKey": "[REDACTED:secret_key]"}),
            "secret_key",
        ),
        (
            "split_json_email",
            "json",
            [b'{"contact":"synthetic@exa', b'mple.invalid"}'],
            canonical_json({"contact": "[REDACTED:email]"}),
            "email",
        ),
        (
            "split_form_key",
            "form",
            [b"client_", b"secret=SYNTHETIC_SPLIT"],
            b"client_secret=%5BREDACTED%3Asecret_key%5D",
            "secret_key",
        ),
    ]
    for case_id, context, parts, output, rule_id in split_matrix:
        raw = b"".join(parts)
        cases.append(
            case(case_id, context, raw, output, [finding(rule_id, context)], source=chunks(parts))
        )

    for wrapper, split_at in (("percent", 7), ("base64", 9)):
        logical = b'{"token":"SYNTHETIC_SPLIT_WRAPPER"}'
        expected_logical = canonical_json({"token": "[REDACTED:secret_key]"})
        raw = wrapped(logical, wrapper)
        parts = [raw[:split_at], raw[split_at:]]
        cases.append(
            case(
                f"split_{wrapper}_wrapper",
                "json",
                raw,
                wrapped(expected_logical, wrapper),
                [finding("secret_key", "json")],
                wrapper=wrapper,
                source=chunks(parts),
            )
        )

    cases.extend(
        [
            reject_case("reject_unknown_context", "xml", "unknown_context"),
            reject_case("reject_unknown_wrapper", "json", "unsupported_envelope", wrapper="gzip"),
            reject_case(
                "reject_malformed_percent",
                "json",
                "malformed_encoding",
                b"%7B%2",
                wrapper="percent",
            ),
            reject_case(
                "reject_malformed_base64", "json", "malformed_encoding", b"@@@=", wrapper="base64"
            ),
            reject_case(
                "reject_malformed_headers",
                "headers",
                "malformed_encoding",
                b"authorization Bearer SYNTHETIC",
            ),
            reject_case(
                "reject_malformed_url", "url", "malformed_encoding", b"not-a-url?token=SYNTHETIC"
            ),
            reject_case(
                "reject_malformed_json", "json", "malformed_encoding", b'{"token":"SYNTHETIC"'
            ),
            reject_case("reject_malformed_form", "form", "malformed_encoding", b"token=%ZZ"),
            reject_case(
                "reject_unknown_envelope_schema",
                "extension_envelope",
                "unsupported_envelope",
                envelope(safe_inner, schema="unknown.synthetic"),
            ),
            reject_case(
                "reject_unknown_envelope_version",
                "extension_envelope",
                "unsupported_envelope",
                envelope(safe_inner, version=2),
            ),
            reject_case(
                "reject_unknown_envelope_encoding",
                "extension_envelope",
                "unsupported_envelope",
                envelope(safe_inner, encoding="protobuf"),
            ),
            reject_case(
                "reject_unavailable_envelope_artifact",
                "extension_envelope",
                "artifact_unavailable",
                envelope({"artifact": {"handle": "synthetic-unavailable"}, "metadata": []}),
            ),
        ]
    )

    good_parts = [b'{"token":"', b"SYNTHETIC_CHUNK", b'"}']
    invalid_sources: list[tuple[str, dict[str, Any]]] = []
    incomplete = chunks(good_parts, complete=False)
    invalid_sources.append(("reject_chunks_incomplete", incomplete))
    misordered = chunks(good_parts)
    misordered["chunk_manifest"]["chunks"][1]["sequence"] = 2
    invalid_sources.append(("reject_chunks_misordered", misordered))
    wrong_size = chunks(good_parts)
    wrong_size["chunk_manifest"]["chunks"][1]["size"] += 1
    invalid_sources.append(("reject_chunks_wrong_size", wrong_size))
    wrong_digest = chunks(good_parts)
    wrong_digest["chunk_manifest"]["chunks"][1]["sha256"] = "0" * 64
    invalid_sources.append(("reject_chunks_wrong_digest", wrong_digest))
    wrong_total = chunks(good_parts)
    wrong_total["chunk_manifest"]["total_size"] += 1
    invalid_sources.append(("reject_chunks_wrong_total", wrong_total))
    artifact = chunks(good_parts)
    artifact["chunk_manifest"]["chunks"][1].pop("data_b64")
    artifact["chunk_manifest"]["chunks"][1]["artifact"] = "synthetic-unavailable"
    cases.append(
        reject_case("reject_chunk_artifact", "json", "artifact_unavailable", source=artifact)
    )
    cases.extend(
        reject_case(case_id, "json", "invalid_chunks", source=source)
        for case_id, source in invalid_sources
    )

    encoded_limit = LIMITS["max_encoded_input_bytes"]
    header_prefix = b"authorization: "
    header_suffix = b"\n"
    redacted_header = b"authorization: [REDACTED:credential_header]\n"
    for suffix, total_size in (
        ("limit_minus_1", encoded_limit - 1),
        ("limit", encoded_limit),
    ):
        repeat_count = total_size - len(header_prefix) - len(header_suffix)
        raw = header_prefix + b"A" * repeat_count + header_suffix
        source = {
            "repeat_ascii": {
                "count": repeat_count,
                "prefix_b64": b64(header_prefix),
                "repeat_byte_b64": b64(b"A"),
                "suffix_b64": b64(header_suffix),
            }
        }
        cases.append(
            case(
                f"encoded_input_{suffix}",
                "headers",
                raw,
                redacted_header,
                [finding("credential_header", "headers")],
                source=source,
            )
        )
    repeat_count = encoded_limit + 1 - len(header_prefix) - len(header_suffix)
    cases.append(
        reject_case(
            "encoded_input_limit_plus_1",
            "headers",
            "limit_exceeded",
            source={
                "repeat_ascii": {
                    "count": repeat_count,
                    "prefix_b64": b64(header_prefix),
                    "repeat_byte_b64": b64(b"A"),
                    "suffix_b64": b64(header_suffix),
                }
            },
        )
    )

    for suffix, chunk_count, accepted in (
        ("limit_minus_1", LIMITS["max_chunks"] - 1, True),
        ("limit", LIMITS["max_chunks"], True),
        ("limit_plus_1", LIMITS["max_chunks"] + 1, False),
    ):
        parts = [header_prefix, b"SYNTHETIC_CHUNK_LIMIT" + header_suffix]
        parts.extend([b""] * (chunk_count - len(parts)))
        source = chunks(parts)
        case_id = f"chunk_count_{suffix}"
        if accepted:
            raw = b"".join(parts)
            cases.append(
                case(
                    case_id,
                    "headers",
                    raw,
                    redacted_header,
                    [finding("credential_header", "headers")],
                    source=source,
                )
            )
        else:
            cases.append(reject_case(case_id, "headers", "limit_exceeded", source=source))

    for suffix, depth, accepted in (
        ("limit_minus_1", LIMITS["max_json_depth"] - 1, True),
        ("limit", LIMITS["max_json_depth"], True),
        ("limit_plus_1", LIMITS["max_json_depth"] + 1, False),
    ):
        raw = b"[" * (depth - 1) + b'"safe"' + b"]" * (depth - 1)
        case_id = f"json_depth_{suffix}"
        if accepted:
            cases.append(case(case_id, "json", raw, raw, []))
        else:
            cases.append(reject_case(case_id, "json", "limit_exceeded", raw))

    for suffix, nodes, accepted in (
        ("limit_minus_1", LIMITS["max_structured_items"] - 1, True),
        ("limit", LIMITS["max_structured_items"], True),
        ("limit_plus_1", LIMITS["max_structured_items"] + 1, False),
    ):
        item_count = nodes - 3
        value = {"items": [0] * item_count, "token": "SYNTHETIC_COUNT_TOKEN"}
        raw = canonical_json(value)
        case_id = f"structured_items_{suffix}"
        if accepted:
            value["token"] = "[REDACTED:secret_key]"
            cases.append(
                case(
                    case_id,
                    "json",
                    raw,
                    canonical_json(value),
                    [finding("secret_key", "json")],
                )
            )
        else:
            cases.append(reject_case(case_id, "json", "limit_exceeded", raw))

    return cases


def digest_expected(expected: dict[str, Any]) -> str:
    return hashlib.sha256(DOMAIN + canonical_json(expected)).hexdigest()


def generated_documents() -> dict[Path, bytes]:
    cases = make_cases()
    for item in cases:
        item["result_digest"] = digest_expected(item["expected"])
    helper_vectors = []
    for name in ("decoded_working_set", "output"):
        maximum = LIMITS[f"max_{name}_bytes"]
        for suffix, observed, accepted in (
            ("limit_minus_1", maximum - 1, True),
            ("limit", maximum, True),
            ("limit_plus_1", maximum + 1, False),
        ):
            helper_vectors.append(
                {
                    "accepted": accepted,
                    "id": f"{name}_{suffix}",
                    "limit": maximum,
                    "observed": observed,
                }
            )
    manifest = {
        "cases": cases,
        "format": "jobseek.runtime.redaction-corpus/v1",
        "helper_limit_vectors": helper_vectors,
        "required_case_ids": [item["case_id"] for item in cases],
    }
    return {
        REGISTRY_PATH: canonical_json(REGISTRY) + b"\n",
        MANIFEST_PATH: canonical_json(manifest) + b"\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift: list[str] = []
    for path, expected in generated_documents().items():
        if args.check:
            if not path.is_file() or path.read_bytes() != expected:
                drift.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if drift:
        parser.error("generated privacy files differ: " + ", ".join(drift))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
