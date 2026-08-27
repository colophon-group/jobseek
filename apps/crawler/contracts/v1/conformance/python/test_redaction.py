from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Never
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).parents[2]
REGISTRY_PATH = ROOT / "privacy_registry.json"
MANIFEST_PATH = ROOT / "fixtures" / "redaction" / "manifest.json"
GENERATOR_PATH = ROOT / "tools" / "generate_privacy.py"
DOMAIN = b"jobseek.runtime.v1.redaction.result\0"

REGISTRY: dict[str, Any] = json.loads(REGISTRY_PATH.read_text())
MANIFEST: dict[str, Any] = json.loads(MANIFEST_PATH.read_text())

MANDATORY_CASE_IDS = {
    "redact_api_key",
    "redact_authentication",
    "redact_basic",
    "redact_basic_scalar",
    "redact_bearer",
    "redact_bearer_scalar",
    "redact_camel_api_key",
    "redact_case_variant",
    "redact_email_json",
    "redact_envelope_inline",
    "redact_envelope_metadata",
    "redact_form_secret",
    "redact_json_secret_key",
    "redact_secret_access_key",
    "redact_secret_query",
    "redact_separator_dot",
    "redact_separator_ff",
    "redact_separator_space",
    "redact_separator_underscore",
    "redact_separator_vt",
    "redact_set_cookie",
    "redact_whole_base64_wrapper",
    "redact_whole_percent_wrapper",
    "redact_x_api_token",
    "redact_x_secret",
    "redact_url_userinfo",
    "redact_unicode_escaped_email",
    "chunk_count_limit",
    "chunk_count_limit_minus_1",
    "chunk_count_limit_plus_1",
    "encoded_input_limit",
    "encoded_input_limit_minus_1",
    "encoded_input_limit_plus_1",
    "json_depth_limit",
    "json_depth_limit_minus_1",
    "json_depth_limit_plus_1",
    "reject_chunk_artifact",
    "reject_chunks_incomplete",
    "reject_chunks_misordered",
    "reject_chunks_wrong_digest",
    "reject_chunks_wrong_size",
    "reject_chunks_wrong_total",
    "reject_malformed_base64",
    "reject_malformed_form",
    "reject_malformed_headers",
    "reject_malformed_json",
    "reject_malformed_percent",
    "reject_malformed_url",
    "reject_envelope_inner_extreme_depth",
    "reject_envelope_inner_lone_surrogate",
    "reject_envelope_outer_duplicate_key",
    "reject_json_duplicate_key",
    "reject_json_extreme_depth",
    "reject_json_lone_surrogate",
    "reject_unavailable_envelope_artifact",
    "reject_unknown_context",
    "reject_unknown_envelope_encoding",
    "reject_unknown_envelope_schema",
    "reject_unknown_envelope_version",
    "reject_unknown_wrapper",
    "safe_base64_wrapper",
    "safe_extension_envelope",
    "safe_form",
    "safe_header_colon_value",
    "safe_headers",
    "safe_json",
    "safe_json_u2028",
    "safe_envelope_inline_u2029",
    "safe_leading_separator_key",
    "safe_percent_wrapper",
    "safe_url",
    "safe_trailing_separator_key",
    "split_base64_wrapper",
    "split_form_key",
    "split_header_key",
    "split_header_value",
    "split_json_email",
    "split_json_key",
    "split_percent_wrapper",
    "split_url_query",
    "structured_items_limit",
    "structured_items_limit_minus_1",
    "structured_items_limit_plus_1",
}

ERROR_CODES = frozenset(REGISTRY["rejected_codes"])
CONTEXTS = frozenset(REGISTRY["contexts"])
WRAPPERS = frozenset(REGISTRY["wrappers"])
LIMITS: dict[str, int] = REGISTRY["limits"]
EMAIL_PATTERN = re.compile(
    r"(?a)(?:^|[^A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+(?:$|[^A-Za-z0-9-])"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PERCENT_ESCAPE = re.compile(rb"%(?![0-9A-Fa-f]{2})")


class RedactionFailure(Exception):
    def __init__(self, code: str) -> None:
        if code not in ERROR_CODES:
            raise AssertionError("unregistered redaction error")
        super().__init__(code)
        self.code = code


def _fail(code: str) -> Never:
    raise RedactionFailure(code)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _decode_b64(value: object, code: str = "malformed_encoding") -> bytes:
    if not isinstance(value, str) or not value.isascii():
        _fail(code)
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _fail(code)


def _bounded(observed: int, maximum: int) -> None:
    if observed > maximum:
        _fail("limit_exceeded")


def _load_input(source: object) -> bytes:
    if not isinstance(source, dict) or len(source) != 1:
        _fail("malformed_encoding")
    if "inline_b64" in source:
        raw = _decode_b64(source["inline_b64"])
        _bounded(len(raw), LIMITS["max_encoded_input_bytes"])
        return raw
    if "repeat_ascii" in source:
        descriptor = source["repeat_ascii"]
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "count",
            "prefix_b64",
            "repeat_byte_b64",
            "suffix_b64",
        }:
            _fail("malformed_encoding")
        count = descriptor["count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            _fail("malformed_encoding")
        prefix = _decode_b64(descriptor["prefix_b64"])
        repeated = _decode_b64(descriptor["repeat_byte_b64"])
        suffix = _decode_b64(descriptor["suffix_b64"])
        if len(repeated) != 1 or repeated[0] > 0x7F:
            _fail("malformed_encoding")
        _bounded(len(prefix) + count + len(suffix), LIMITS["max_encoded_input_bytes"])
        return prefix + repeated * count + suffix
    manifest = source.get("chunk_manifest")
    if not isinstance(manifest, dict):
        _fail("malformed_encoding")
    chunks = manifest.get("chunks")
    total_size = manifest.get("total_size")
    if (
        not isinstance(chunks, list)
        or not isinstance(total_size, int)
        or isinstance(total_size, bool)
    ):
        _fail("invalid_chunks")
    _bounded(len(chunks), LIMITS["max_chunks"])
    _bounded(total_size, LIMITS["max_encoded_input_bytes"])
    if manifest.get("complete") is not True:
        _fail("invalid_chunks")
    decoded: list[bytes] = []
    actual_total = 0
    for sequence, chunk in enumerate(chunks):
        if not isinstance(chunk, dict) or chunk.get("sequence") != sequence:
            _fail("invalid_chunks")
        size = chunk.get("size")
        digest = chunk.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail("invalid_chunks")
        if not isinstance(digest, str) or HEX64.fullmatch(digest) is None:
            _fail("invalid_chunks")
        if "artifact" in chunk:
            _fail("artifact_unavailable")
        part = _decode_b64(chunk.get("data_b64"), "invalid_chunks")
        if len(part) != size or hashlib.sha256(part).hexdigest() != digest:
            _fail("invalid_chunks")
        actual_total += size
        if actual_total > total_size:
            _fail("invalid_chunks")
        decoded.append(part)
    if actual_total != total_size:
        _fail("invalid_chunks")
    return b"".join(decoded)


def _decode_percent(raw: bytes) -> bytes:
    if PERCENT_ESCAPE.search(raw):
        _fail("malformed_encoding")
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        _fail("malformed_encoding")
    return unquote_to_bytes(raw)


def _decode_wrapper(raw: bytes, wrapper: object) -> bytes:
    if wrapper is None:
        return raw
    if wrapper not in WRAPPERS:
        _fail("unsupported_envelope")
    if wrapper == "percent":
        value = _decode_percent(raw)
    else:
        try:
            value = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            _fail("malformed_encoding")
    _bounded(len(value), LIMITS["max_decoded_working_set_bytes"])
    return value


def _encode_wrapper(raw: bytes, wrapper: object) -> bytes:
    if wrapper is None:
        return raw
    if wrapper == "percent":
        return quote_from_bytes(raw, safe="").encode()
    return base64.b64encode(raw)


def _normalize_key(value: str) -> str:
    lowered = "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)
    output: list[str] = []
    separator = False
    for char in lowered:
        if char in "-_. \t\n\v\f\r":
            if not separator:
                output.append("-")
            separator = True
        else:
            output.append(char)
            separator = False
    return "".join(output)


def _key_sets() -> dict[str, set[str]]:
    return {
        rule["id"]: set(rule.get("keys", [])) for rule in REGISTRY["rules"] if rule["kind"] == "key"
    }


KEYS = _key_sets()


def _rule_for_key(key: str, context: str) -> str | None:
    normalized = _normalize_key(key)
    if context == "headers" and normalized in KEYS["credential_header"]:
        return "credential_header"
    if normalized in KEYS["cookie"]:
        return "cookie"
    if normalized in KEYS["secret_key"]:
        return "secret_key"
    return None


def _rule_for_scalar(value: str) -> str | None:
    lowered = value.lower()
    if any(lowered.startswith(prefix) for prefix in ("basic ", "bearer ")):
        return "credential_scheme"
    if EMAIL_PATTERN.search(value):
        return "email"
    return None


def _replacement(rule_id: str) -> str:
    return f"[REDACTED:{rule_id}]"


def _finding(rule_id: str, context: str) -> dict[str, str]:
    return {"context": context, "rule_id": rule_id}


def _utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("malformed_encoding")


def _headers(
    raw: bytes, finding_context: str = "headers"
) -> tuple[bytes, list[dict[str, str]], int]:
    text = _utf8(raw)
    if not text.endswith("\n") or "\r" in text or "\x00" in text:
        _fail("malformed_encoding")
    lines = text[:-1].split("\n")
    _bounded(len(lines), LIMITS["max_structured_items"])
    output: list[str] = []
    findings: list[dict[str, str]] = []
    for line in lines:
        if ": " not in line:
            _fail("malformed_encoding")
        name, value = line.split(": ", 1)
        if not name or any(ord(char) < 0x20 or ord(char) > 0x7E or char == ":" for char in name):
            _fail("malformed_encoding")
        rule_id = _rule_for_key(name, "headers") or _rule_for_scalar(value)
        if rule_id is not None:
            value = _replacement(rule_id)
            findings.append(_finding(rule_id, finding_context))
        output.append(f"{name}: {value}\n")
    return "".join(output).encode(), findings, len(lines)


def _strict_unquote(value: str) -> str:
    raw = value.encode("ascii", errors="strict")
    return _utf8(_decode_percent(raw))


def _url(raw: bytes, finding_context: str = "url") -> tuple[bytes, list[dict[str, str]], int]:
    text = _utf8(raw)
    if any(ord(char) < 0x20 for char in text):
        _fail("malformed_encoding")
    try:
        split = urlsplit(text)
    except ValueError:
        _fail("malformed_encoding")
    if split.scheme not in {"http", "https"} or not split.hostname or split.fragment:
        _fail("malformed_encoding")
    findings: list[dict[str, str]] = []
    netloc = split.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        _strict_unquote(userinfo)
        netloc = quote_from_bytes(_replacement("url_userinfo").encode(), safe="") + "@" + host
        findings.append(_finding("url_userinfo", finding_context))
    pairs: list[tuple[str, str]] = []
    if split.query:
        for field in split.query.split("&"):
            if field.count("=") != 1:
                _fail("malformed_encoding")
            key, value = field.split("=", 1)
            try:
                decoded_key = _strict_unquote(key)
                decoded_value = _strict_unquote(value)
            except (UnicodeEncodeError, UnicodeDecodeError):
                _fail("malformed_encoding")
            rule_id = _rule_for_key(decoded_key, "url") or _rule_for_scalar(decoded_value)
            if rule_id is not None:
                decoded_value = _replacement(rule_id)
                findings.append(_finding(rule_id, finding_context))
            pairs.append((decoded_key, decoded_value))
    _bounded(len(pairs), LIMITS["max_structured_items"])
    query = "&".join(
        quote_from_bytes(key.encode(), safe="") + "=" + quote_from_bytes(value.encode(), safe="")
        for key, value in pairs
    )
    return urlunsplit((split.scheme, netloc, split.path, query, "")).encode(), findings, len(pairs)


def _scan_json_string(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == '"':
            return index + 1
        if ord(char) < 0x20 or 0xD800 <= ord(char) <= 0xDFFF:
            _fail("malformed_encoding")
        if char != "\\":
            index += 1
            continue
        if index + 1 >= len(text):
            _fail("malformed_encoding")
        escaped = text[index + 1]
        if escaped in '"\\/bfnrt':
            index += 2
            continue
        if escaped != "u" or index + 6 > len(text):
            _fail("malformed_encoding")
        digits = text[index + 2 : index + 6]
        if any(char not in "0123456789abcdefABCDEF" for char in digits):
            _fail("malformed_encoding")
        codepoint = int(digits, 16)
        index += 6
        if 0xD800 <= codepoint <= 0xDBFF:
            if index + 6 > len(text) or text[index : index + 2] != "\\u":
                _fail("malformed_encoding")
            low_digits = text[index + 2 : index + 6]
            if any(char not in "0123456789abcdefABCDEF" for char in low_digits):
                _fail("malformed_encoding")
            low = int(low_digits, 16)
            if not 0xDC00 <= low <= 0xDFFF:
                _fail("malformed_encoding")
            index += 6
        elif 0xDC00 <= codepoint <= 0xDFFF:
            _fail("malformed_encoding")
    _fail("malformed_encoding")


def _preflight_json(raw: bytes) -> str:
    text = _utf8(raw)
    stack: list[tuple[str, set[str] | None]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "{[":
            stack.append((char, set() if char == "{" else None))
            _bounded(len(stack), LIMITS["max_json_depth"])
            index += 1
            continue
        if char in "}]":
            expected = "{" if char == "}" else "["
            if not stack or stack[-1][0] != expected:
                _fail("malformed_encoding")
            stack.pop()
            index += 1
            continue
        if char != '"':
            index += 1
            continue
        end = _scan_json_string(text, index)
        lookahead = end
        while lookahead < len(text) and text[lookahead] in " \t\r\n":
            lookahead += 1
        if lookahead < len(text) and text[lookahead] == ":":
            if not stack or stack[-1][0] != "{":
                _fail("malformed_encoding")
            try:
                key = json.loads(text[index:end])
            except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
                _fail("malformed_encoding")
            keys = stack[-1][1]
            assert keys is not None
            if key in keys:
                _fail("malformed_encoding")
            keys.add(key)
        index = end
    if stack:
        _fail("malformed_encoding")
    return text


def _json_loads(raw: bytes) -> Any:
    def reject_constant(_: str) -> None:
        raise ValueError

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        return json.loads(
            _preflight_json(raw),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        _fail("malformed_encoding")


def _json_metrics(value: Any, depth: int = 1) -> tuple[int, int]:
    if isinstance(value, dict):
        child = [_json_metrics(item, depth + 1) for item in value.values()]
    elif isinstance(value, list):
        child = [_json_metrics(item, depth + 1) for item in value]
    else:
        child = []
    return max([depth, *(item[0] for item in child)]), 1 + sum(item[1] for item in child)


def _redact_json_value(
    value: Any,
    context: str,
    forced_rule: str | None = None,
) -> tuple[Any, list[dict[str, str]]]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        findings: list[dict[str, str]] = []
        for key in sorted(value):
            rule_id = forced_rule or _rule_for_key(key, "json")
            output[key], child_findings = _redact_json_value(value[key], context, rule_id)
            findings.extend(child_findings)
        return output, findings
    if isinstance(value, list):
        output_items: list[Any] = []
        findings = []
        for item in value:
            transformed, child_findings = _redact_json_value(item, context, forced_rule)
            output_items.append(transformed)
            findings.extend(child_findings)
        return output_items, findings
    rule_id = forced_rule or (_rule_for_scalar(value) if isinstance(value, str) else None)
    if rule_id is None:
        return value, []
    return _replacement(rule_id), [_finding(rule_id, context)]


def _json(raw: bytes, finding_context: str = "json") -> tuple[bytes, list[dict[str, str]], int]:
    value = _json_loads(raw)
    depth, nodes = _json_metrics(value)
    _bounded(depth, LIMITS["max_json_depth"])
    _bounded(nodes, LIMITS["max_structured_items"])
    output, findings = _redact_json_value(value, finding_context)
    return _canonical_json(output), findings, nodes


def _form(raw: bytes, finding_context: str = "form") -> tuple[bytes, list[dict[str, str]], int]:
    text = _utf8(raw)
    fields = text.split("&") if text else []
    _bounded(len(fields), LIMITS["max_structured_items"])
    output: list[str] = []
    findings: list[dict[str, str]] = []
    for field in fields:
        if field.count("=") != 1:
            _fail("malformed_encoding")
        key, value = field.split("=", 1)
        try:
            decoded_key = _strict_unquote(key)
            decoded_value = _strict_unquote(value)
        except (UnicodeEncodeError, UnicodeDecodeError):
            _fail("malformed_encoding")
        rule_id = _rule_for_key(decoded_key, "form") or _rule_for_scalar(decoded_value)
        if rule_id is not None:
            decoded_value = _replacement(rule_id)
            findings.append(_finding(rule_id, finding_context))
        output.append(
            quote_from_bytes(decoded_key.encode(), safe="")
            + "="
            + quote_from_bytes(decoded_value.encode(), safe="")
        )
    return "&".join(output).encode(), findings, len(fields)


def _context(
    raw: bytes, context: str, finding_context: str | None = None
) -> tuple[bytes, list[dict[str, str]], int]:
    actual_context = finding_context or context
    if context == "headers":
        return _headers(raw, actual_context)
    if context == "url":
        return _url(raw, actual_context)
    if context == "json":
        return _json(raw, actual_context)
    if context == "form":
        return _form(raw, actual_context)
    _fail("unknown_context")


def _extension_envelope(raw: bytes) -> tuple[bytes, list[dict[str, str]], int]:
    outer = _json_loads(raw)
    if not isinstance(outer, dict) or set(outer) != {
        "encoding",
        "payload_b64",
        "payload_sha256",
        "schema_id",
        "schema_version",
    }:
        _fail("malformed_encoding")
    if (
        outer["schema_id"] != "jobseek.synthetic.capture"
        or outer["schema_version"] != 1
        or outer["encoding"] != "canonical_json"
    ):
        _fail("unsupported_envelope")
    if not isinstance(outer["payload_sha256"], str):
        _fail("malformed_encoding")
    inner = _json_loads(_decode_b64(outer["payload_b64"]))
    if not isinstance(inner, dict) or not isinstance(inner.get("metadata"), list):
        _fail("malformed_encoding")
    if "artifact" in inner:
        _fail("artifact_unavailable")
    if set(inner) != {"inline", "metadata"} or not isinstance(inner["inline"], dict):
        _fail("malformed_encoding")
    metadata: list[dict[str, str]] = []
    findings: list[dict[str, str]] = []
    for header in inner["metadata"]:
        if not isinstance(header, dict) or set(header) != {"name", "value"}:
            _fail("malformed_encoding")
        name, value = header["name"], header["value"]
        if not isinstance(name, str) or not isinstance(value, str):
            _fail("malformed_encoding")
        rule_id = _rule_for_key(name, "extension_envelope") or _rule_for_scalar(value)
        if rule_id is not None:
            value = _replacement(rule_id)
            findings.append(_finding(rule_id, "extension_envelope"))
        metadata.append({"name": name, "value": value})
    inline = inner["inline"]
    if set(inline) != {"context", "data_b64"} or not isinstance(inline["context"], str):
        _fail("malformed_encoding")
    if inline["context"] not in {"headers", "url", "json", "form"}:
        _fail("unsupported_envelope")
    inline_output, inline_findings, item_count = _context(
        _decode_b64(inline["data_b64"]), inline["context"], "extension_envelope"
    )
    findings.extend(inline_findings)
    _bounded(len(metadata) + item_count, LIMITS["max_structured_items"])
    safe_inner = {
        "inline": {
            "context": inline["context"],
            "data_b64": base64.b64encode(inline_output).decode(),
        },
        "metadata": metadata,
    }
    safe_outer = {
        "encoding": "canonical_json",
        "payload_b64": base64.b64encode(_canonical_json(safe_inner)).decode(),
        "payload_sha256": "",
        "schema_id": "jobseek.synthetic.capture",
        "schema_version": 1,
    }
    return _canonical_json(safe_outer), findings, len(metadata) + item_count


def transform(case: dict[str, Any]) -> dict[str, Any]:
    try:
        context = case.get("context")
        if not isinstance(context, str) or context not in CONTEXTS:
            _fail("unknown_context")
        original = _load_input(case.get("input"))
        logical = _decode_wrapper(original, case.get("wrapper"))
        if context == "extension_envelope":
            output, findings, _ = _extension_envelope(logical)
        else:
            output, findings, _ = _context(logical, context)
        wrapped_output = _encode_wrapper(output, case.get("wrapper"))
        _bounded(len(wrapped_output), LIMITS["max_output_bytes"])
        if not findings and wrapped_output != original:
            _fail("malformed_encoding")
        return {
            "findings": findings,
            "output": wrapped_output,
            "status": "transformed" if findings else "unchanged",
        }
    except RedactionFailure as error:
        return {"error_code": error.code, "status": "rejected"}


def _safe_result(case_id: str, transformed: dict[str, Any]) -> dict[str, Any]:
    if transformed["status"] == "rejected":
        return {"case_id": case_id, "error_code": transformed["error_code"], "status": "rejected"}
    return {
        "case_id": case_id,
        "findings": transformed["findings"],
        "output_b64": base64.b64encode(transformed["output"]).decode(),
        "status": transformed["status"],
    }


def _digest(expected: dict[str, Any]) -> str:
    return hashlib.sha256(DOMAIN + _canonical_json(expected)).hexdigest()


def test_generated_registry_and_manifest_are_exact() -> None:
    subprocess.run([sys.executable, str(GENERATOR_PATH), "--check"], check=True)


def test_closed_registry_is_exact_and_non_discovering() -> None:
    assert REGISTRY["format"] == "jobseek.runtime.privacy-registry/v1"
    assert set(REGISTRY["contexts"]) == {"headers", "url", "json", "form", "extension_envelope"}
    assert set(REGISTRY["wrappers"]) == {"percent", "base64"}
    assert set(REGISTRY["rejected_codes"]) == {
        "unknown_context",
        "malformed_encoding",
        "unsupported_envelope",
        "artifact_unavailable",
        "limit_exceeded",
        "invalid_chunks",
    }
    source = GENERATOR_PATH.read_text()
    assert "urlopen(" not in source and "socket." not in source
    assert "requests." not in source and "httpx." not in source


def test_manifest_case_ids_are_independently_mandatory() -> None:
    case_ids = [item["case_id"] for item in MANIFEST["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert set(case_ids) == MANDATORY_CASE_IDS
    assert set(MANIFEST["required_case_ids"]) == MANDATORY_CASE_IDS


def test_shared_corpus_matches_every_safe_expected_result() -> None:
    for item in MANIFEST["cases"]:
        actual = _safe_result(item["case_id"], transform(item))
        assert actual == item["expected"], item["case_id"]
        assert _digest(item["expected"]) == item["result_digest"], item["case_id"]


def test_transform_is_deterministic_and_result_has_no_digest() -> None:
    for item in MANIFEST["cases"]:
        first = transform(item)
        second = transform(item)
        assert first == second, item["case_id"]
        assert "result_digest" not in first, item["case_id"]


def test_rejections_return_only_closed_code_and_never_canary_material() -> None:
    for item in MANIFEST["cases"]:
        if item["expected"]["status"] != "rejected":
            continue
        actual = transform(item)
        assert set(actual) == {"error_code", "status"}, item["case_id"]
        assert actual["error_code"] in ERROR_CODES, item["case_id"]
        rendered = json.dumps(actual, sort_keys=True)
        assert "SYNTHETIC_REJECTED_CANARY" not in rendered, item["case_id"]


def test_dominated_limit_comparators_have_inclusive_boundaries() -> None:
    expected_ids = {
        f"{name}_{suffix}"
        for name in ("decoded_working_set", "output")
        for suffix in ("limit_minus_1", "limit", "limit_plus_1")
    }
    vectors = MANIFEST["helper_limit_vectors"]
    assert {item["id"] for item in vectors} == expected_ids
    for item in vectors:
        accepted = item["observed"] <= item["limit"]
        assert accepted is item["accepted"], item["id"]


def test_no_non_test_python_redaction_module_exists() -> None:
    assert not (ROOT / "conformance" / "python" / "redaction.py").exists()


@pytest.mark.parametrize("case_id", sorted(MANDATORY_CASE_IDS))
def test_each_mandatory_case_is_named_and_executed(case_id: str) -> None:
    item = next(case for case in MANIFEST["cases"] if case["case_id"] == case_id)
    assert _safe_result(case_id, transform(item)) == item["expected"], case_id
