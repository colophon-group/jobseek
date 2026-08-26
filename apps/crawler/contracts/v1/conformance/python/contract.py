from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote_plus, unquote_to_bytes, urlsplit

from conformance.python.extension_rules import EXTENSION_RULES
from conformance.python.privacy_gen import (
    CREDENTIAL_SUFFIXES,
    EMAIL_NAMES,
    SECRET_NAMES,
    SENSITIVE_HEADERS,
)
from gen.python import runtime_pb2 as pb
from google.protobuf import json_format
from google.protobuf.message import Message

CONTRACT_VERSION = "crawler.runtime/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REDACTED_RE = re.compile(r"^redacted-sha256:[0-9a-f]{64}$")
TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-"
    r"(?P<parent>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})"
    r"(?P<extra>(?:-[0-9a-f]+)*)$"
)
DEADLINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?$")
LOCALE_ALIASES = frozenset({"iw", "in", "ji", "jw", "mo", "sh", "en-UK"})
SENTINEL_TEXT = frozenset({"-", "n/a", "na", "none", "null", "tbd", "unknown"})
EMAIL_RE = re.compile(r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])")
REDACTED_EMAIL_RE = re.compile(r"^person-[0-9a-f]{64}@redacted\.invalid$")
PHONE_RE = re.compile(r"(?<![0-9])\+?[0-9][0-9() .-]{7,}[0-9](?![0-9])")
REDACTED_PHONE_RE = re.compile(r"^\+999-[0-9a-f]{24}$")
TRACESTATE_KEY_RE = re.compile(
    r"(?:[a-z0-9][_0-9a-z*/-]{0,255}|"
    r"[a-z0-9][_0-9a-z*/-]{0,240}@[a-z0-9][a-z0-9*/_-]{0,13})"
)
TRACESTATE_VALUE_RE = re.compile(r"[\x21-\x2B\x2D-\x3C\x3E-\x7E]+")
MAX_DEADLINE = timedelta(minutes=15)
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9a-z]+$")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:"
    + "|".join(re.escape(name) for name in sorted(SECRET_NAMES, key=len, reverse=True))
    + r")[\"']?[ \t]*[:=][ \t]*[\"']?([^\"'&;,\s}]+)"
)
UNRESERVED_URL_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)

HARD_LIMITS = pb.Limits(**json.loads((Path(__file__).parents[2] / "limits.json").read_text()))
MAX_TRANSFER_CHUNKS = 64
MAX_EXTENSION_BYTES = 65_536
SCRAPER_EXTENSION_FIELDS = frozenset(
    {
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "date_posted",
        "base_salary",
        "language",
        "localizations",
        "skills",
    }
)


class ContractViolation(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


def fail(code: str, detail: str) -> None:
    raise ContractViolation(code, detail)


def load_case(path: Path) -> pb.ConformanceCase:
    case = pb.ConformanceCase()
    json_format.Parse(path.read_text(), case, ignore_unknown_fields=False)
    return case


def load_replay(path: Path) -> pb.ReplayCase:
    case = pb.ReplayCase()
    json_format.Parse(path.read_text(), case, ignore_unknown_fields=False)
    return case


def _present(message: Message, field: str) -> bool:
    return message.HasField(field)


def _enum(value: int, enum_wrapper, field: str) -> None:
    if value == 0 or value not in enum_wrapper.values():
        fail("enum", f"{field} is unspecified or unknown: {value}")


def _text(value: str, field: str, *, maximum: int = 4_096) -> None:
    size = len(value.encode())
    if not value or size > maximum:
        fail("text", f"{field} must contain 1..{maximum} UTF-8 bytes")


def _bounded_text(value: str, field: str, *, maximum: int) -> None:
    if len(value.encode()) > maximum:
        fail("domain_limit", f"{field} exceeds {maximum} UTF-8 bytes")


def _url(value: str, field: str) -> None:
    if any(ord(character) <= 0x20 or ord(character) >= 0x7F for character in value):
        fail("url", f"{field} must contain visible ASCII only")
    if "\\" in value:
        fail("url", f"{field} must not contain backslashes")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        fail("url", f"{field} contains an invalid percent escape")
    scheme_separator = value.find("://")
    raw_scheme = value[:scheme_separator] if scheme_separator >= 0 else ""
    if raw_scheme not in {"http", "https"}:
        fail("url", f"{field} must use a canonical lowercase HTTP(S) scheme")
    authority = (
        re.split(r"[/?#]", value[scheme_separator + 3 :], maxsplit=1)[0]
        if scheme_separator >= 0
        else ""
    )
    if "%" in authority:
        fail("url", f"{field} authority/host must not contain percent escapes")
    try:
        authority.encode("ascii")
    except UnicodeEncodeError:
        fail("url", f"{field} authority must use an ASCII IDNA A-label")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        fail("url", f"{field}: {exc}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail("url", f"{field} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None or "#" in value:
        fail("url", f"{field} must not contain credentials or a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        fail("url", f"{field}: {exc}")
    if port == 0:
        fail("url", f"{field} port must be within 1..65535")
    if (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443):
        fail("url", f"{field} must omit the default port")
    host_port = parsed.netloc
    if host_port.endswith(":"):
        fail("url", f"{field} must omit an explicit empty port")
    if host_port.startswith("["):
        raw_host = host_port[1 : host_port.index("]")]
    else:
        raw_host = host_port.rsplit(":", 1)[0] if ":" in host_port else host_port
    if raw_host != raw_host.lower():
        fail("url", f"{field} must use canonical lowercase scheme and host")
    if ":" in raw_host:
        if "." in raw_host:
            fail("url", f"{field} must not use an embedded-IPv4 IPv6 literal")
        try:
            canonical_ip = ipaddress.IPv6Address(raw_host).compressed
        except ipaddress.AddressValueError:
            fail("url", f"{field} contains an invalid IPv6 literal")
        if raw_host != canonical_ip:
            fail("url", f"{field} IPv6 literal must use canonical RFC5952 form")
    elif any(not DNS_LABEL_RE.fullmatch(label) for label in raw_host.split(".")):
        fail("url", f"{field} host must use canonical ASCII DNS labels")
    if not parsed.path:
        fail("url", f"{field} must use '/' instead of an empty path")
    if "//" in parsed.path:
        fail("url", f"{field} path must not contain an empty segment")
    if any(part in {".", ".."} for part in parsed.path.split("/")):
        fail("url", f"{field} path must not contain dot segments")
    for escape in re.finditer(r"%([0-9A-Fa-f]{2})", value):
        hexadecimal = escape.group(1)
        if hexadecimal != hexadecimal.upper():
            fail("url", f"{field} percent escapes must use uppercase hexadecimal")
        if int(hexadecimal, 16) in UNRESERVED_URL_BYTES:
            fail("url", f"{field} must not percent-encode an unreserved character")
    without_fragment = value.split("#", 1)[0]
    if "?" in without_fragment and not parsed.query:
        fail("url", f"{field} must omit an empty query delimiter")
    if ";" in parsed.query:
        fail("url", f"{field} query must use ampersand-only field separators")
    if parsed.query:
        pairs: list[str] = []
        component_re = re.compile(r"(?:[A-Za-z0-9._~:-]|%[0-9A-F]{2})*")
        for item in parsed.query.split("&"):
            if item.count("=") != 1:
                fail("url", f"{field} query entries require exactly one equals sign")
            raw_key, raw_value = item.split("=", 1)
            if (
                not raw_key
                or component_re.fullmatch(raw_key) is None
                or component_re.fullmatch(raw_value) is None
            ):
                fail("url", f"{field} query pair is not canonical")
            _validate_named_secret(
                unquote_plus(raw_key), unquote_plus(raw_value), "query parameter"
            )
            pairs.append(item)
        if len(pairs) != len(set(pairs)) or pairs != sorted(pairs):
            fail("url", f"{field} query pairs must be unique and sorted")


def _sensitive_name(value: str) -> bool:
    name = value.strip().lower()
    return name in SECRET_NAMES or any(name.endswith(suffix) for suffix in CREDENTIAL_SUFFIXES)


def _validate_trace_context(traceparent: str | None, tracestate: str | None) -> None:
    if traceparent is None:
        if tracestate is not None:
            fail("trace", "tracestate requires traceparent")
        return
    match = TRACEPARENT_RE.fullmatch(traceparent)
    if match is None or match["version"] == "ff":
        fail("trace", "traceparent is not valid W3C Trace Context syntax")
    if match["trace"] == "0" * 32 or match["parent"] == "0" * 16:
        fail("trace", "traceparent IDs must be nonzero")
    if match["version"] == "00" and match["extra"]:
        fail("trace", "traceparent version 00 cannot contain extension fields")
    if tracestate is None:
        return
    if not tracestate or len(tracestate) > 512 or " " in tracestate:
        fail("trace", "tracestate is empty, oversized, or contains optional whitespace")
    members = tracestate.split(",")
    if len(members) > 32:
        fail("trace", "tracestate has more than 32 list-members")
    keys: set[str] = set()
    for member in members:
        if member.count("=") != 1:
            fail("trace", "tracestate member must contain one equals sign")
        key, member_value = member.split("=", 1)
        if not TRACESTATE_KEY_RE.fullmatch(key):
            fail("trace", "tracestate key is not canonical")
        if key in keys:
            fail("trace", "tracestate keys must be unique")
        keys.add(key)
        if (
            not member_value
            or len(member_value) > 256
            or not TRACESTATE_VALUE_RE.fullmatch(member_value)
            or member_value.endswith(" ")
        ):
            fail("trace", "tracestate value is invalid")


def _canonical_locale(value: str) -> bool:
    return (
        bool(LOCALE_RE.fullmatch(value))
        and value not in LOCALE_ALIASES
        and (value.split("-", 1)[0] not in LOCALE_ALIASES)
    )


def _has_unpaired_json_surrogate(text: str) -> bool:
    index = 0
    while index < len(text):
        if text[index] != "\\":
            index += 1
            continue
        end = index
        while end < len(text) and text[end] == "\\":
            end += 1
        if (end - index) % 2 == 0 or end >= len(text) or text[end] != "u":
            index = end
            continue
        hexadecimal = text[end + 1 : end + 5]
        if len(hexadecimal) != 4 or re.fullmatch(r"[0-9A-Fa-f]{4}", hexadecimal) is None:
            index = end + 1
            continue
        codepoint = int(hexadecimal, 16)
        if 0xD800 <= codepoint <= 0xDBFF:
            pair = end + 5
            if (
                text[pair : pair + 2] != "\\u"
                or re.fullmatch(r"[0-9A-Fa-f]{4}", text[pair + 2 : pair + 6]) is None
                or not 0xDC00 <= int(text[pair + 2 : pair + 6], 16) <= 0xDFFF
            ):
                return True
            index = pair + 6
            continue
        if 0xDC00 <= codepoint <= 0xDFFF:
            return True
        index = end + 5
    return False


def _reject_json_constant(value: str) -> None:
    fail("redaction", f"JSON contains non-finite number {value}")


def _strict_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        fail("redaction", f"JSON contains non-finite number {value}")
    return parsed


def _strict_json_loads(body: bytes | str) -> object:
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    if _has_unpaired_json_surrogate(text):
        fail("redaction", "JSON contains an unpaired Unicode surrogate escape")
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        parse_float=_strict_json_float,
    )


def _parse_ampersand_fields(value: str, field: str) -> list[tuple[str, str]]:
    if ";" in value:
        fail("redaction", f"{field} must use ampersand-only field separators")
    result: list[tuple[str, str]] = []
    for item in value.split("&"):
        if not item:
            continue
        raw_name, separator, raw_value = item.partition("=")
        for component in (raw_name, raw_value if separator else ""):
            if re.search(r"%(?![0-9A-Fa-f]{2})", component):
                fail("redaction", f"{field} contains an invalid percent escape")
        try:
            name = unquote_plus(raw_name, errors="strict")
            decoded_value = unquote_plus(raw_value if separator else "", errors="strict")
        except UnicodeDecodeError:
            fail("redaction", f"{field} contains non-UTF-8 percent encoding")
        result.append((name, decoded_value))
    return result


def _validate_named_secret(name: str, value: object, field: str) -> None:
    normalized = name.lower()
    if _sensitive_name(normalized) and (
        not isinstance(value, str) or REDACTED_RE.fullmatch(value) is None
    ):
        fail("redaction", f"sensitive {field} {name} is not deterministically redacted")
    if normalized in EMAIL_NAMES and (
        not isinstance(value, str) or REDACTED_EMAIL_RE.fullmatch(value) is None
    ):
        fail("redaction", f"email {field} {name} is not deterministically redacted")


def _validate_redacted_body_once(
    body: bytes, field: str, *, depth: int, declared_json: bool
) -> None:
    try:
        text_value = body.decode("utf-8")
    except UnicodeDecodeError:
        if declared_json:
            fail("redaction", f"{field} declares non-UTF-8 JSON")
        return
    for match in EMAIL_RE.finditer(text_value):
        if not REDACTED_EMAIL_RE.fullmatch(match.group(0)):
            fail("redaction", f"{field} contains an unredacted email address")
    for match in PHONE_RE.finditer(text_value):
        if not REDACTED_PHONE_RE.fullmatch(match.group(0)):
            fail("redaction", f"{field} contains an unredacted phone number")
    if re.search(r"(?i)\bbearer\s+(?!redacted-sha256:)[A-Za-z0-9._~+/-]+", text_value):
        fail("redaction", f"{field} contains an unredacted bearer credential")
    if "-----BEGIN " in text_value and "PRIVATE KEY-----" in text_value:
        fail("redaction", f"{field} contains private key material")
    if any(
        REDACTED_RE.fullmatch(match.group(1)) is None
        for match in SECRET_ASSIGNMENT_RE.finditer(text_value)
    ):
        fail("redaction", f"{field} contains an unredacted secret assignment")
    looks_json = text_value.lstrip().startswith(("{", "["))
    try:
        parsed = _strict_json_loads(text_value)
    except (json.JSONDecodeError, UnicodeDecodeError):
        if declared_json or looks_json:
            fail("redaction", f"{field} contains malformed JSON")
        parsed = None

    def walk(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                _validate_named_secret(str(key), child, f"{path} key")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and len(value) >= 16 and len(value) % 4 == 0:
            try:
                decoded = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                return
            _validate_redacted_body(decoded, path, depth=depth + 1)

    if parsed is not None:
        walk(parsed, field)


def _content_type(headers: list[pb.Header]) -> str:
    for header in headers:
        if header.name == "content-type":
            return header.value.split(";", 1)[0].strip().lower()
    return ""


def _validate_redacted_body(
    body: bytes,
    field: str,
    *,
    headers: list[pb.Header] | None = None,
    depth: int = 0,
) -> None:
    if not body or depth > 2:
        return
    media_type = _content_type(headers or [])
    declared_json = media_type == "application/json" or media_type.endswith("+json")
    decoded = body
    for decode_pass in range(4):
        _validate_redacted_body_once(
            decoded,
            field if decoded == body else f"{field} percent-decoded",
            depth=depth,
            declared_json=declared_json,
        )
        next_decoded = unquote_to_bytes(decoded)
        if next_decoded == decoded:
            break
        if decode_pass == 3:
            fail("redaction", f"{field} exceeds the bounded percent-decoding depth")
        decoded = next_decoded
    if media_type == "application/x-www-form-urlencoded":
        try:
            form = body.decode("utf-8")
        except UnicodeDecodeError:
            fail("redaction", f"{field} form body is not UTF-8")
        for name, value in _parse_ampersand_fields(form, f"{field} form"):
            _validate_named_secret(name, value, "form field")


def _sha256(value: str, field: str) -> None:
    if not SHA256_RE.fullmatch(value):
        fail("hash", f"{field} must be lowercase sha256 hex")


def _contract(value: str) -> None:
    if value != CONTRACT_VERSION:
        fail("version", f"expected {CONTRACT_VERSION}, got {value!r}")


def fencing_digest(context: pb.FencingContext) -> bytes:
    values = (
        context.shard_id,
        str(context.routing_epoch),
        str(context.engine_owner),
        context.claim_token,
        context.lease_id,
        context.config_revision,
    )
    raw = b"crawler.runtime/v1/fence\0" + "\0".join(values).encode()
    return hashlib.sha256(raw).digest()


def validate_fencing(context: pb.FencingContext, *, config_revision: str) -> None:
    for field in ("shard_id", "claim_token", "lease_id", "config_revision"):
        if "\x00" in getattr(context, field):
            fail("fence", f"fencing.{field} must not contain NUL")
        _text(getattr(context, field), f"fencing.{field}", maximum=512)
    if context.routing_epoch == 0:
        fail("fence", "routing_epoch must be positive")
    _enum(context.engine_owner, pb.EngineOwner, "fencing.engine_owner")
    if context.config_revision != config_revision:
        fail("fence", "fencing config_revision differs from manifest")
    if len(context.fence_digest) != 32:
        fail("fence", "fencing digest must contain exactly 32 bytes")
    if context.fence_digest != fencing_digest(context):
        fail("fence", "fencing digest disagrees with its canonical typed context")


def _headers(headers: list[pb.Header], field: str) -> None:
    if len(headers) > 256:
        fail("limit", f"{field} exceeds 256 headers")
    seen: set[str] = set()
    for header in headers:
        name = header.name.strip().lower()
        _text(name, f"{field}.name", maximum=256)
        if name != header.name or HEADER_NAME_RE.fullmatch(name) is None:
            fail("header", f"{field}.name must be a canonical lowercase HTTP token")
        if name in seen:
            fail("duplicate", f"duplicate header {name}")
        seen.add(name)
        if len(header.value.encode()) > 8_192:
            fail("limit", f"{field}.{name} exceeds 8192 UTF-8 bytes")
        if "\n" in header.value or "\r" in header.value:
            fail("header", f"{name} contains a line break")
        if (name in SENSITIVE_HEADERS or _sensitive_name(name)) and (
            not header.redacted or not REDACTED_RE.fullmatch(header.value)
        ):
            fail("redaction", f"sensitive header {name} is not deterministically redacted")
        _validate_redacted_body(header.value.encode(), f"{field}.{name}")


def request_fingerprint(request: pb.CapturedRequest) -> str:
    canonical = pb.CapturedRequest()
    canonical.CopyFrom(request)
    ordered_headers = sorted(canonical.headers, key=lambda value: value.name)
    del canonical.headers[:]
    canonical.headers.extend(ordered_headers)
    raw = b"crawler.runtime/v1/request\0" + canonical.SerializeToString(deterministic=True)
    return hashlib.sha256(raw).hexdigest()


def validate_limits(limits: pb.Limits, *, requested: pb.Limits | None = None) -> None:
    for field in limits.DESCRIPTOR.fields:
        value = getattr(limits, field.name)
        ceiling = getattr(HARD_LIMITS, field.name)
        if value <= 0 or value > ceiling:
            fail("limit", f"{field.name} must be within 1..{ceiling}")
        if requested is not None and value > getattr(requested, field.name):
            fail("negotiation", f"accepted {field.name} exceeds the requested limit")


def validate_artifact(artifact: pb.ArtifactHandle, limits: pb.Limits) -> None:
    _text(artifact.handle, "artifact.handle", maximum=512)
    if "/" in artifact.handle or "\\" in artifact.handle or artifact.handle.startswith("."):
        fail("artifact", "artifact handle must be opaque, not a filesystem path")
    _text(artifact.media_type, "artifact.media_type", maximum=256)
    if artifact.size_bytes > limits.max_artifact_chunk_bytes:
        fail("artifact_limit", "artifact exceeds negotiated max_artifact_chunk_bytes")
    _sha256(artifact.sha256, "artifact.sha256")


def validate_chunk_manifest(
    manifest: pb.ChunkManifest,
    limits: pb.Limits,
    *,
    maximum_total: int,
    require_inline: bool = False,
) -> bytes | None:
    if not manifest.complete:
        fail("chunk", "authoritative transfer manifest must be complete")
    if len(manifest.chunks) > MAX_TRANSFER_CHUNKS:
        fail("chunk_limit", f"transfer exceeds {MAX_TRANSFER_CHUNKS} chunks")
    total = 0
    inline_parts: list[bytes] = []
    total_digest = hashlib.sha256()
    all_inline = True
    artifact_handles: set[str] = set()
    for sequence, chunk in enumerate(manifest.chunks):
        if chunk.sequence != sequence:
            fail("chunk_sequence", "chunk sequences must be unique and contiguous from zero")
        storage = chunk.WhichOneof("storage")
        if storage is None or chunk.size_bytes == 0:
            fail("chunk", "each chunk requires non-empty inline or artifact storage")
        if chunk.size_bytes > limits.max_artifact_chunk_bytes:
            fail("chunk_limit", "chunk exceeds max_artifact_chunk_bytes")
        _sha256(chunk.sha256, "chunk.sha256")
        if storage == "inline_body":
            if len(chunk.inline_body) != chunk.size_bytes:
                fail("chunk", "inline chunk size metadata disagrees")
            if len(chunk.inline_body) > limits.max_inline_body_bytes:
                fail("body_limit", "inline chunk exceeds max_inline_body_bytes")
            if hashlib.sha256(chunk.inline_body).hexdigest() != chunk.sha256:
                fail("hash", "inline chunk digest disagrees")
            total_digest.update(chunk.inline_body)
            if require_inline:
                inline_parts.append(chunk.inline_body)
        else:
            all_inline = False
            validate_artifact(chunk.artifact, limits)
            if chunk.artifact.handle in artifact_handles:
                fail("duplicate", "artifact chunk handles must be unique")
            artifact_handles.add(chunk.artifact.handle)
            if (
                chunk.artifact.size_bytes != chunk.size_bytes
                or chunk.artifact.sha256 != chunk.sha256
            ):
                fail("chunk", "artifact handle and chunk metadata disagree")
        total += chunk.size_bytes
    if total != manifest.total_size_bytes or total > maximum_total:
        fail("transfer_limit", "chunk total disagrees or exceeds transfer limit")
    _sha256(manifest.total_sha256, "chunk.total_sha256")
    if total == 0 and manifest.chunks:
        fail("chunk", "zero-byte transfer must not contain chunks")
    if total > 0 and not manifest.chunks:
        fail("chunk", "non-empty transfer requires chunks")
    if all_inline:
        if total_digest.hexdigest() != manifest.total_sha256:
            fail("hash", "inline transfer total digest disagrees")
        if require_inline:
            if total > limits.max_inline_body_bytes:
                fail("replay", "normalized replay body exceeds bounded inline decode size")
            return b"".join(inline_parts)
        return None
    if require_inline:
        fail("replay", "offline replay fixture requires inline chunk bytes")
    return None


def validate_extension(extension: pb.ExtensionEnvelope, *, context: str) -> None:
    _text(extension.schema_id, "extension.schema_id", maximum=256)
    _enum(extension.encoding, pb.ExtensionEncoding, "extension.encoding")
    rule = EXTENSION_RULES.get(extension.schema_id)
    if rule is None or (
        extension.schema_version != rule["version"] or extension.encoding != rule["encoding"]
    ):
        fail("extension", "extension schema/version/encoding is not registered for v1")
    if context not in rule["contexts"]:
        fail("extension_context", f"{extension.schema_id} is forbidden in {context}")
    if len(extension.payload) > MAX_EXTENSION_BYTES:
        fail("extension_limit", "extension payload exceeds 65536 bytes")
    _sha256(extension.payload_sha256, "extension.payload_sha256")
    if hashlib.sha256(extension.payload).hexdigest() != extension.payload_sha256:
        fail("hash", "extension payload digest disagrees")
    if extension.encoding == pb.EXTENSION_ENCODING_CANONICAL_JSON:
        try:
            value = _strict_json_loads(extension.payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractViolation):
            fail("extension", "canonical JSON extension payload is invalid")
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if canonical != extension.payload:
            fail("extension", "JSON extension payload is not canonical")
        validator = rule["validator"]
        if validator == "monitor_config":
            if (
                type(value) is not dict
                or set(value) != {"pages"}
                or type(value["pages"]) is not int
                or not 1 <= value["pages"] <= 1_000
            ):
                fail("extension_schema", "monitor-config requires pages integer 1..1000")
        elif validator == "scraper_config":
            if type(value) is not dict or set(value) != {"fields"}:
                fail("extension_schema", "scraper-config requires exactly fields")
            fields = value["fields"]
            if (
                type(fields) is not list
                or not fields
                or len(fields) > 64
                or any(
                    type(field) is not str or field not in SCRAPER_EXTENSION_FIELDS
                    for field in fields
                )
                or len(fields) != len(set(fields))
            ):
                fail("extension_schema", "scraper-config fields are invalid")
        elif validator == "runtime_metadata":
            if (
                type(value) is not dict
                or set(value) != {"source"}
                or value["source"] not in {"captured-provider", "offline-fixture"}
            ):
                fail("extension_schema", "runtime-metadata source is invalid")
        elif validator == "evaluation_json" and (
            type(value) is not dict
            or set(value) != {"value"}
            or type(value["value"]) is not int
            or not -(2**53 - 1) <= value["value"] <= 2**53 - 1
        ):
            fail("extension_schema", "evaluation-json requires one safe integer value")


def validate_extensions(extensions: list[pb.ExtensionEnvelope], *, context: str) -> None:
    seen: set[str] = set()
    for extension in extensions:
        if extension.schema_id in seen:
            fail("duplicate", "extension schema IDs must be unique in one envelope set")
        seen.add(extension.schema_id)
        validate_extension(extension, context=context)


ERROR_POLICY = {
    pb.ERROR_CODE_TDM_RESERVED: pb.ERROR_DISPOSITION_DEFER_POLICY,
    pb.ERROR_CODE_PROVIDER_GONE: pb.ERROR_DISPOSITION_PROVIDER_GONE_POLICY,
    pb.ERROR_CODE_PERMANENT_GONE: pb.ERROR_DISPOSITION_PERMANENT_GONE_POLICY,
    pb.ERROR_CODE_INVALID_CONFIG: pb.ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
    pb.ERROR_CODE_CANCELLED: pb.ERROR_DISPOSITION_CANCELLED_POLICY,
    pb.ERROR_CODE_AMBIGUOUS_ORIGIN: pb.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
    pb.ERROR_CODE_UNSUPPORTED_CAPABILITY: pb.ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
}


def validate_error(error: pb.RuntimeError, limits: pb.Limits = HARD_LIMITS) -> None:
    _enum(error.code, pb.ErrorCode, "error.code")
    _enum(error.disposition, pb.ErrorDisposition, "error.disposition")
    expected = ERROR_POLICY.get(error.code, pb.ERROR_DISPOSITION_RETRY_POLICY)
    if error.disposition != expected:
        fail(
            "error_policy",
            f"{pb.ErrorCode.Name(error.code)} requires {pb.ErrorDisposition.Name(expected)}",
        )
    if len(error.message.encode()) > 4_096:
        fail("error_limit", "error.message exceeds 4096 bytes")
    if error.code == pb.ERROR_CODE_HTTP_STATUS:
        if not _present(error, "http_status") or not 100 <= error.http_status <= 599:
            fail("http_status", "HTTP_STATUS requires a status in 100..599")
    elif _present(error, "http_status") and not 100 <= error.http_status <= 599:
        fail("http_status", "http_status must be in 100..599")
    if _present(error, "retry_after_ms") and error.retry_after_ms > limits.max_retry_after_ms:
        fail("limit", "retry_after_ms exceeds the independent scheduling-hint limit")
    detail_keys: set[str] = set()
    for detail in error.diagnostic_details:
        _text(detail.key, "error.diagnostic_details.key", maximum=128)
        if detail.key in detail_keys:
            fail("duplicate", "diagnostic detail keys must be unique")
        detail_keys.add(detail.key)
        if len(detail.value.encode()) > 2_048:
            fail("diagnostic_limit", "diagnostic detail exceeds 2048 bytes")


def validate_origin_operations(
    operations: list[pb.OriginOperationRef], primary: str | None = None
) -> dict[str, pb.OriginOperationRef]:
    if not operations:
        fail("origin", "at least one semantic origin operation is required")
    by_id: dict[str, pb.OriginOperationRef] = {}
    for expected, operation in enumerate(operations):
        _text(operation.origin_request_id, "origin_request_id", maximum=512)
        _text(operation.role, "origin.role", maximum=128)
        _sha256(operation.request_fingerprint, "origin.request_fingerprint")
        if operation.operation_sequence != expected:
            fail("origin_sequence", "origin operation sequences must be contiguous from zero")
        if operation.origin_request_id in by_id:
            fail("duplicate", f"duplicate origin_request_id {operation.origin_request_id}")
        if (
            _present(operation, "parent_origin_request_id")
            and operation.parent_origin_request_id not in by_id
        ):
            fail("origin_parent", "origin parent must reference an earlier operation")
        by_id[operation.origin_request_id] = operation
    if primary is not None and operations[0].origin_request_id != primary:
        fail("origin", "request origin_request_id must equal the first semantic operation ID")
    return by_id


def validate_manifest(manifest: pb.BoardManifest) -> None:
    _contract(manifest.contract_version)
    for field in (
        "manifest_id",
        "board_id",
        "company_id",
        "config_revision",
        "config_fingerprint",
        "provider_family",
        "monitor_type",
        "throttle_key",
    ):
        _text(getattr(manifest, field), f"manifest.{field}", maximum=512)
    _sha256(manifest.config_fingerprint, "manifest.config_fingerprint")
    _url(manifest.board_url, "manifest.board_url")
    if manifest.check_interval_ms == 0 or manifest.scrape_interval_ms == 0:
        fail("manifest", "manifest intervals must be positive")
    if _present(manifest, "scraper_type"):
        _text(manifest.scraper_type, "manifest.scraper_type", maximum=128)
    if _present(manifest, "egress_policy_ref"):
        _text(manifest.egress_policy_ref, "manifest.egress_policy_ref", maximum=512)
    validate_extensions(list(manifest.config_extensions), context="manifest")


def validate_request(request: pb.ExecutionRequest) -> None:
    _contract(request.contract_version)
    for field in ("request_id", "origin_request_id", "attempt_id"):
        _text(getattr(request, field), f"request.{field}", maximum=512)
    _enum(request.kind, pb.ExecutionKind, "request.kind")
    if not DEADLINE_RE.fullmatch(request.deadline_rfc3339):
        fail("deadline", "deadline_rfc3339 is not strict RFC3339")
    try:
        deadline = datetime.fromisoformat(request.deadline_rfc3339.replace("Z", "+00:00"))
    except ValueError:
        fail("deadline", "deadline_rfc3339 is not RFC3339")
    if deadline.tzinfo is None:
        fail("deadline", "deadline_rfc3339 must include an offset")
    _validate_trace_context(
        request.traceparent if _present(request, "traceparent") else None,
        request.tracestate if _present(request, "tracestate") else None,
    )
    validate_manifest(request.board_manifest)
    validate_fencing(
        request.fencing_context,
        config_revision=request.board_manifest.config_revision,
    )
    validate_origin_operations(list(request.origin_operations), request.origin_request_id)
    payload = request.WhichOneof("input")
    if request.kind == pb.EXECUTION_KIND_MONITOR:
        if (
            payload != "monitor"
            or request.monitor.monitor_type != request.board_manifest.monitor_type
        ):
            fail("kind", "monitor request/input/manifest types disagree")
    elif request.kind == pb.EXECUTION_KIND_SCRAPE:
        if payload != "scrape":
            fail("kind", "scrape execution requires scrape input")
        _url(request.scrape.source_url, "scrape.source_url")
        _text(request.scrape.scraper_type, "scrape.scraper_type", maximum=128)
        if not _present(request.board_manifest, "scraper_type") or (
            request.scrape.scraper_type != request.board_manifest.scraper_type
        ):
            fail("kind", "scrape input and manifest scraper_type disagree")
    elif request.kind == pb.EXECUTION_KIND_BROWSER:
        if payload != "browser":
            fail("kind", "browser execution requires a browser plan")
        validate_browser_plan(request.browser.plan)
        if len(request.browser.plan.origin_operations) != len(request.origin_operations) or any(
            left.SerializeToString(deterministic=True)
            != right.SerializeToString(deterministic=True)
            for left, right in zip(
                request.browser.plan.origin_operations,
                request.origin_operations,
                strict=True,
            )
        ):
            fail("origin", "browser plan operations must equal execution operations")


def validate_job_content(content: pb.JobContent, limits: pb.Limits) -> None:
    def meaningful(value: str | None, field: str) -> bool:
        if value is None:
            return False
        normalized = value.strip()
        visible = re.sub(r"<[^>]*>", "", normalized).strip()
        if not visible or visible.casefold() in SENTINEL_TEXT:
            fail("job_content", f"{field} must not be empty, whitespace, or a sentinel")
        return True

    if _present(content, "title"):
        _bounded_text(content.title, "job.title", maximum=32_768)
    if _present(content, "description_html"):
        _bounded_text(
            content.description_html,
            "job.description_html",
            maximum=HARD_LIMITS.max_inline_body_bytes,
        )
    if _present(content, "locations"):
        if len(content.locations.values) > 1_024:
            fail("domain_limit", "job.locations exceeds 1024 values")
        for location in content.locations.values:
            _bounded_text(location, "job.locations", maximum=4_096)
    for field, maximum in (
        ("employment_type", 128),
        ("job_location_type", 128),
        ("date_posted", 128),
        ("language", 35),
    ):
        if _present(content, field):
            _bounded_text(getattr(content, field), f"job.{field}", maximum=maximum)

    title_present = meaningful(content.title, "job.title") if _present(content, "title") else False
    description_present = (
        meaningful(content.description_html, "job.description_html")
        if _present(content, "description_html")
        else False
    )
    if _present(content, "language") and not _canonical_locale(content.language):
        fail("locale", "job.language must be a canonical, non-alias locale")
    if _present(content, "base_salary"):
        salary = content.base_salary
        _text(salary.currency, "salary.currency", maximum=3)
        _text(salary.period, "salary.period", maximum=32)
        if (
            _present(salary, "minimum_minor")
            and _present(salary, "maximum_minor")
            and salary.minimum_minor > salary.maximum_minor
        ):
            fail("salary", "salary minimum exceeds maximum")
    if len(content.localizations) > 128:
        fail("domain_limit", "job.localizations exceeds 128 values")
    locales: set[str] = set()
    for localized in content.localizations:
        _text(localized.locale, "localization.locale", maximum=35)
        if not _canonical_locale(localized.locale):
            fail("locale", "localization.locale must be canonical and non-alias")
        if localized.locale in locales:
            fail("duplicate", "localized content locales must be unique")
        locales.add(localized.locale)
        if _present(localized, "title"):
            _bounded_text(localized.title, "localization.title", maximum=32_768)
        if _present(localized, "description_html"):
            _bounded_text(
                localized.description_html,
                "localization.description_html",
                maximum=HARD_LIMITS.max_inline_body_bytes,
            )
        localized_title = (
            meaningful(localized.title, "localization.title")
            if _present(localized, "title")
            else False
        )
        localized_description = (
            meaningful(localized.description_html, "localization.description_html")
            if _present(localized, "description_html")
            else False
        )
        if not localized_title and not localized_description:
            fail("job_content", "localization must contain title or description")
        title_present |= localized_title
        description_present |= localized_description
    if not title_present or not description_present:
        fail("job_content", "commit-eligible rich job requires meaningful title and description")
    if len(content.skills) > 1_024:
        fail("domain_limit", "job.skills exceeds 1024 values")
    for skill in content.skills:
        _bounded_text(skill, "job.skills", maximum=512)
    if len(content.skills) != len(set(content.skills)) or any(
        not skill.strip() or skill != skill.strip() for skill in content.skills
    ):
        fail("job_content", "skills must be unique, nonempty, and trimmed")
    if _present(content, "locations") and (
        len(content.locations.values) != len(set(content.locations.values))
        or any(
            not location.strip() or location != location.strip()
            for location in content.locations.values
        )
    ):
        fail("job_content", "locations must be unique, nonempty, and trimmed")
    validate_extensions(list(content.extensions), context="job_content")
    total = len(content.SerializeToString())
    if total > limits.max_inline_body_bytes:
        fail("body_limit", "JobContent exceeds negotiated max_inline_body_bytes")


def validate_monitor_result(result: pb.MonitorResult, limits: pb.Limits) -> None:
    if len(result.urls) != len(set(result.urls)):
        fail("duplicate", "monitor URLs must be unique")
    url_set = set(result.urls)
    for url in result.urls:
        _url(url, "monitor.urls")
    job_urls: set[str] = set()
    for job in result.jobs:
        _url(job.url, "monitor.jobs.url")
        if job.url in job_urls:
            fail("duplicate", "job URLs must be unique")
        if job.url not in url_set:
            fail("url_job", "every job URL must be present in urls")
        job_urls.add(job.url)
        validate_job_content(job.content, limits)
    if job_urls and not result.hybrid and job_urls != url_set:
        fail("url_job", "non-hybrid rich results require a job for every URL")
    if _present(result, "new_sitemap_url"):
        _url(result.new_sitemap_url, "monitor.new_sitemap_url")
    if _present(result, "metadata_updates"):
        validate_extensions(list(result.metadata_updates.extensions), context="monitor_metadata")
    if len(result.SerializeToString()) > limits.max_inline_body_bytes:
        fail("body_limit", "monitor result exceeds negotiated max_inline_body_bytes")


def _validate_frame_size(frame: pb.ExecutionFrame, limits: pb.Limits) -> None:
    payload_size = len(frame.SerializeToString())
    if payload_size + uvarint_size(payload_size) > limits.max_frame_bytes:
        fail("frame_limit", "length-delimited frame exceeds max_frame_bytes")


def uvarint_size(value: int) -> int:
    if value < 0 or value > 2**64 - 1:
        fail("framing", "unsigned varint value is outside uint64")
    return max(1, (value.bit_length() + 6) // 7)


def encode_record(payload: bytes, maximum: int) -> bytes:
    size = len(payload)
    prefix = bytearray()
    remaining = size
    while remaining >= 0x80:
        prefix.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    prefix.append(remaining)
    if len(prefix) + size > maximum:
        fail("frame_limit", "length-delimited protocol record exceeds max_frame_bytes")
    return bytes(prefix) + payload


def decode_record(data: bytes, maximum: int) -> bytes:
    value = 0
    shift = 0
    prefix_size = 0
    for byte in data[:10]:
        prefix_size += 1
        if prefix_size == 10 and byte > 1:
            fail("framing", "unsigned varint overflows uint64")
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            break
        shift += 7
    else:
        fail("framing", "truncated or overlong unsigned varint")
    if prefix_size != uvarint_size(value):
        fail("framing", "unsigned varint uses a non-minimal encoding")
    if prefix_size + value > maximum:
        fail("frame_limit", "length-delimited protocol record exceeds max_frame_bytes")
    if len(data) < prefix_size + value:
        fail("framing", "truncated length-delimited payload")
    if len(data) != prefix_size + value:
        fail("framing", "trailing bytes after one length-delimited record")
    return data[prefix_size:]


def _semantic_frame_bytes(frame: pb.ExecutionFrame) -> bytes:
    value = pb.ExecutionFrame()
    value.CopyFrom(frame)
    value.attempt_id = ""
    kind = value.WhichOneof("payload")
    if kind == "monitor_batch":
        result = value.monitor_batch
        urls = sorted(result.urls)
        del result.urls[:]
        result.urls.extend(urls)
        jobs = []
        for job in result.jobs:
            canonical_job = pb.DiscoveredJob(url=job.url)
            canonical_job.content.CopyFrom(_canonical_job_content(job.content))
            jobs.append(canonical_job)
        jobs.sort(key=lambda job: (job.url, job.SerializeToString(deterministic=True)))
        del result.jobs[:]
        result.jobs.extend(jobs)
        if _present(result, "metadata_updates"):
            extensions = sorted(
                result.metadata_updates.extensions, key=lambda extension: extension.schema_id
            )
            del result.metadata_updates.extensions[:]
            result.metadata_updates.extensions.extend(extensions)
    elif kind == "scrape_result":
        value.scrape_result.content.CopyFrom(_canonical_job_content(value.scrape_result.content))
    return value.SerializeToString(deterministic=True)


def validate_browser_plan(plan: pb.BrowserPlan, limits: pb.Limits = HARD_LIMITS) -> None:
    uses_frames = False

    def validate_selector(selector: pb.Selector, field: str) -> None:
        nonlocal uses_frames
        _enum(selector.kind, pb.SelectorKind, f"{field}.kind")
        _text(selector.value, f"{field}.value", maximum=4_096)
        if _present(selector, "frame_name"):
            _text(selector.frame_name, f"{field}.frame_name", maximum=256)
            uses_frames = True

    def validate_timeout(value: int, field: str) -> None:
        if value == 0 or value > limits.max_active_duration_ms:
            fail("limit", f"{field} is out of bounds")

    _contract(plan.contract_version)
    _url(plan.target_url, "browser.target_url")
    capabilities = list(plan.required_capabilities)
    if len(capabilities) != len(set(capabilities)):
        fail("duplicate", "browser capabilities must be unique")
    for capability in capabilities:
        _enum(capability, pb.BrowserCapability, "browser.required_capabilities")
    if pb.BROWSER_CAPABILITY_RENDER not in capabilities:
        fail("capability", "browser navigation requires the render capability")
    operations = validate_origin_operations(list(plan.origin_operations))
    if plan.navigation.origin_request_id not in operations:
        fail("origin", "browser navigation must reference a declared origin operation")
    origin_owners = [plan.navigation.origin_request_id]
    _enum(plan.navigation.wait_until, pb.WaitCondition, "browser.navigation.wait_until")
    if (
        plan.navigation.timeout_ms == 0
        or plan.navigation.timeout_ms > limits.max_active_duration_ms
    ):
        fail("limit", "browser navigation timeout is out of bounds")
    _headers(list(plan.navigation.headers), "browser.navigation.headers")
    if len(plan.actions) > limits.max_browser_actions:
        fail("limit", "too many browser actions")
    if len(plan.captures) > limits.max_browser_captures:
        fail("limit", "too many browser captures")
    if len(plan.evaluations) > limits.max_browser_evaluations:
        fail("limit", "too many browser evaluations")
    action_ids: set[str] = set()
    for action in plan.actions:
        _text(action.action_id, "browser.action_id", maximum=128)
        if action.action_id in action_ids or action.WhichOneof("action") is None:
            fail("browser_action", "actions need unique IDs and a tagged action")
        action_ids.add(action.action_id)
        _enum(
            action.network_effect,
            pb.BrowserNetworkEffect,
            "browser.action.network_effect",
        )
        has_origin = _present(action, "origin_request_id")
        if has_origin and action.origin_request_id not in operations:
            fail("origin", "browser action references an undeclared origin operation")
        if action.network_effect == pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT:
            if not has_origin:
                fail(
                    "origin",
                    "origin-contact browser action requires a stable origin_request_id",
                )
            origin_owners.append(action.origin_request_id)
        elif has_origin:
            fail("origin", "no-network browser action must omit origin_request_id")
        action_kind = action.WhichOneof("action")
        if action_kind == "click":
            validate_selector(action.click.selector, "browser.action.click.selector")
            validate_timeout(action.click.timeout_ms, "browser.action.click.timeout_ms")
        elif action_kind == "fill":
            validate_selector(action.fill.selector, "browser.action.fill.selector")
            if len(action.fill.value.encode()) > 65_536:
                fail("limit", "browser fill value exceeds 65536 bytes")
            validate_timeout(action.fill.timeout_ms, "browser.action.fill.timeout_ms")
        elif action_kind == "wait":
            if _present(action.wait, "selector"):
                validate_selector(action.wait.selector, "browser.action.wait.selector")
            if action.wait.duration_ms > limits.max_active_duration_ms:
                fail("limit", "browser wait duration is out of bounds")
            validate_timeout(action.wait.timeout_ms, "browser.action.wait.timeout_ms")
        elif action_kind == "scroll":
            _enum(
                action.scroll.direction,
                pb.ScrollDirection,
                "browser.action.scroll.direction",
            )
            if not 1 <= action.scroll.pixels <= 1_000_000:
                fail("limit", "browser scroll pixels are out of bounds")
        elif action_kind == "paginate":
            if action.network_effect != pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT:
                fail("origin", "pagination is origin-contact work and requires a bound operation")
            validate_selector(
                action.paginate.next_selector, "browser.action.paginate.next_selector"
            )
            if not 1 <= action.paginate.max_pages <= 1_000:
                fail("limit", "browser pagination page count is out of bounds")
            page_origins = list(action.paginate.additional_page_origin_request_ids)
            if action.paginate.dynamic_origin_per_additional_page:
                if page_origins:
                    fail("origin", "dynamic pagination must omit predeclared page origins")
            elif len(page_origins) != action.paginate.max_pages - 1:
                fail("origin", "pagination requires exactly max_pages-1 predeclared page origins")
            if len(page_origins) != len(set(page_origins)):
                fail("duplicate", "pagination page origin IDs must be unique")
            for origin_id in page_origins:
                if origin_id not in operations:
                    fail("origin", "pagination page references an undeclared origin operation")
                origin_owners.append(origin_id)
            validate_timeout(
                action.paginate.page_timeout_ms,
                "browser.action.paginate.page_timeout_ms",
            )
            if pb.BROWSER_CAPABILITY_PAGINATION not in capabilities:
                fail("capability", "pagination action requires the pagination capability")
        else:
            _text(action.evaluate.expression, "browser.action.evaluate.expression", maximum=262_144)
            validate_timeout(action.evaluate.timeout_ms, "browser.action.evaluate.timeout_ms")
            if not 1 <= action.evaluate.max_result_bytes <= limits.max_browser_transfer_bytes:
                fail("limit", "browser evaluate result bound is invalid")
            if _present(action.evaluate, "frame_name"):
                _text(action.evaluate.frame_name, "browser.action.evaluate.frame_name", maximum=256)
                uses_frames = True
            if pb.BROWSER_CAPABILITY_EVALUATE not in capabilities:
                fail("capability", "evaluate action requires the evaluate capability")
    if plan.actions and pb.BROWSER_CAPABILITY_ACTIONS not in capabilities:
        fail("capability", "browser actions require the actions capability")
    capture_ids: set[str] = set()
    for capture in plan.captures:
        _text(capture.capture_id, "browser.capture_id", maximum=128)
        if capture.capture_id in capture_ids:
            fail("duplicate", "browser capture IDs must be unique")
        capture_ids.add(capture.capture_id)
        _enum(capture.kind, pb.CaptureKind, "browser.capture.kind")
        if _present(capture, "url_pattern"):
            _text(capture.url_pattern, "browser.capture.url_pattern", maximum=4_096)
        if not 1 <= capture.max_bytes <= limits.max_browser_transfer_bytes:
            fail("limit", "browser capture max_bytes is invalid")
    evaluation_ids: set[str] = set()
    for evaluation in plan.evaluations:
        _text(evaluation.evaluation_id, "browser.evaluation_id", maximum=128)
        if evaluation.evaluation_id in evaluation_ids:
            fail("duplicate", "browser evaluation IDs must be unique")
        evaluation_ids.add(evaluation.evaluation_id)
        _text(evaluation.expression, "browser.evaluation.expression", maximum=262_144)
        if not 1 <= evaluation.max_result_bytes <= limits.max_browser_transfer_bytes:
            fail("limit", "browser evaluation max_result_bytes is invalid")
        if _present(evaluation, "frame_name"):
            _text(evaluation.frame_name, "browser.evaluation.frame_name", maximum=256)
            uses_frames = True
        _enum(
            evaluation.network_effect,
            pb.BrowserNetworkEffect,
            "browser.evaluation.network_effect",
        )
        evaluation_has_origin = _present(evaluation, "origin_request_id")
        if evaluation.network_effect == pb.BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT:
            if not evaluation_has_origin:
                fail("origin", "origin-contact evaluation requires origin_request_id")
            if evaluation.origin_request_id not in operations:
                fail("origin", "evaluation references undeclared origin operation")
            origin_owners.append(evaluation.origin_request_id)
        elif evaluation_has_origin:
            fail("origin", "no-network evaluation must omit origin_request_id")
    if len(origin_owners) != len(set(origin_owners)):
        fail("origin", "browser origin operation IDs must have exactly one plan owner")
    if set(origin_owners) != set(operations):
        fail(
            "origin",
            "browser origin operations must be exhausted by navigation/actions/evaluations",
        )
    for interception in plan.interceptions:
        _text(interception.url_pattern, "browser.interception.url_pattern", maximum=4_096)
        _headers(list(interception.replace_headers), "browser.interception.headers")
    if plan.evaluations and pb.BROWSER_CAPABILITY_EVALUATE not in capabilities:
        fail("capability", "browser evaluations require the evaluate capability")
    if plan.captures and pb.BROWSER_CAPABILITY_RESPONSE_CAPTURE not in capabilities:
        fail("capability", "browser captures require response-capture capability")
    if plan.interceptions and (pb.BROWSER_CAPABILITY_REQUEST_INTERCEPTION not in capabilities):
        fail("capability", "interception rules require request-interception capability")
    if uses_frames and pb.BROWSER_CAPABILITY_FRAMES not in capabilities:
        fail("capability", "frame_name requires frames capability")
    if (
        plan.navigation.headers or plan.navigation.ignore_tls_errors
    ) and pb.BROWSER_CAPABILITY_TRANSPORT_OVERRIDES not in capabilities:
        fail("capability", "navigation transport overrides require capability")
    if _present(plan.session, "session_key"):
        _text(plan.session.session_key, "browser.session.session_key", maximum=512)
    if _present(plan.session, "proxy_policy_ref"):
        _text(
            plan.session.proxy_policy_ref,
            "browser.session.proxy_policy_ref",
            maximum=512,
        )
    if plan.session.persistent and not _present(plan.session, "session_key"):
        fail("browser_session", "persistent browser sessions require a stable session_key")
    if plan.session.headful_identity and (
        pb.BROWSER_CAPABILITY_HEADFUL_IDENTITY not in capabilities
    ):
        fail("capability", "headful identity requires capability")
    if _present(plan.session, "proxy_policy_ref") and (
        pb.BROWSER_CAPABILITY_PROXY not in capabilities
    ):
        fail("capability", "proxy policy requires proxy capability")
    if plan.session.persistent and pb.BROWSER_CAPABILITY_PERSISTENT_SESSION not in capabilities:
        fail("capability", "persistent session was not declared required")


def validate_browser_result(
    result: pb.BrowserResult,
    limits: pb.Limits = HARD_LIMITS,
    plan: pb.BrowserPlan | None = None,
) -> None:
    _contract(result.contract_version)
    _enum(result.backend, pb.BrowserBackend, "browser.backend")
    outcome = result.WhichOneof("outcome")
    if outcome is None:
        fail("browser_union", "BrowserResult requires exactly one tagged outcome")
    artifact_handles: set[str] = set()
    artifact_bytes = 0

    def account_artifact(artifact: pb.ArtifactHandle) -> None:
        nonlocal artifact_bytes
        validate_artifact(artifact, limits)
        if artifact.handle in artifact_handles:
            fail("duplicate", "browser artifact handles must be unique")
        artifact_handles.add(artifact.handle)
        artifact_bytes += artifact.size_bytes

    def account_manifest(manifest: pb.ChunkManifest) -> None:
        for chunk in manifest.chunks:
            if chunk.WhichOneof("storage") == "artifact":
                account_artifact(chunk.artifact)

    if outcome == "success":
        _url(result.success.final_url, "browser.final_url")
        if _present(result.success, "status") and not 100 <= result.success.status <= 599:
            fail("http_status", "browser status must be in 100..599")
        total = 0
        action_ids = [item.action_id for item in result.success.action_outcomes]
        capture_ids = [item.capture_id for item in result.success.captures]
        evaluation_ids = [item.evaluation_id for item in result.success.evaluations]
        for action in result.success.action_outcomes:
            _text(action.action_id, "browser.result.action_id", maximum=128)
            if action.duration_ms > limits.max_active_duration_ms:
                fail("limit", "browser action duration exceeds the active-duration limit")
        for capture in result.success.captures:
            _text(capture.capture_id, "browser.result.capture_id", maximum=128)
        for evaluation in result.success.evaluations:
            _text(evaluation.evaluation_id, "browser.result.evaluation_id", maximum=128)
        if len(action_ids) != len(set(action_ids)):
            fail("browser_result", "browser action outcome IDs must be unique")
        if len(capture_ids) != len(set(capture_ids)):
            fail("browser_result", "browser capture outcome IDs must be unique")
        if len(evaluation_ids) != len(set(evaluation_ids)):
            fail("browser_result", "browser evaluation outcome IDs must be unique")
        if any(not item.completed for item in result.success.action_outcomes):
            fail("browser_result", "all required browser actions must complete")
        for item in result.success.evaluations:
            validate_extension(item.value, context="browser_evaluation")
        plan_captures: dict[str, pb.CapturePlan] = {}
        plan_evaluations: dict[str, pb.EvaluationPlan] = {}
        if plan is not None:
            if set(action_ids) != {item.action_id for item in plan.actions}:
                fail("browser_result", "browser action outcomes do not match the plan")
            if set(capture_ids) != {item.capture_id for item in plan.captures}:
                fail("browser_result", "browser capture outcomes do not match the plan")
            if set(evaluation_ids) != {item.evaluation_id for item in plan.evaluations}:
                fail("browser_result", "browser evaluation outcomes do not match the plan")
            plan_captures = {item.capture_id: item for item in plan.captures}
            plan_evaluations = {item.evaluation_id: item for item in plan.evaluations}
        if _present(result.success, "html"):
            validate_chunk_manifest(
                result.success.html,
                limits,
                maximum_total=limits.max_browser_transfer_bytes,
            )
            account_manifest(result.success.html)
            total += result.success.html.total_size_bytes
        for capture in result.success.captures:
            planned_capture = plan_captures.get(capture.capture_id)
            validate_chunk_manifest(
                capture.body,
                limits,
                maximum_total=(
                    planned_capture.max_bytes
                    if planned_capture is not None
                    else limits.max_browser_transfer_bytes
                ),
            )
            if (
                planned_capture is not None
                and planned_capture.artifact_only
                and any(
                    chunk.WhichOneof("storage") == "inline_body" for chunk in capture.body.chunks
                )
            ):
                fail("browser_result", "artifact-only capture returned inline bytes")
            account_manifest(capture.body)
            total += capture.body.total_size_bytes
        for evaluation in result.success.evaluations:
            planned_evaluation = plan_evaluations.get(evaluation.evaluation_id)
            if (
                planned_evaluation is not None
                and len(evaluation.value.payload) > planned_evaluation.max_result_bytes
            ):
                fail("transfer_limit", "browser evaluation exceeds its planned byte limit")
            total += len(evaluation.value.payload)
        for artifact in result.success.artifacts:
            account_artifact(artifact)
            total += artifact.size_bytes
        if total > limits.max_browser_transfer_bytes:
            fail("transfer_limit", "aggregate browser output exceeds browser transfer limit")
    elif outcome == "error":
        validate_error(result.error.error, limits)
        for artifact in result.error.diagnostic_artifacts:
            account_artifact(artifact)
    else:
        if not result.unsupported.capabilities:
            fail("browser_union", "unsupported outcome requires capabilities")
        if len(result.unsupported.capabilities) != len(set(result.unsupported.capabilities)):
            fail("duplicate", "unsupported browser capabilities must be unique")
        for capability in result.unsupported.capabilities:
            _enum(capability, pb.BrowserCapability, "browser.unsupported.capabilities")
            if plan is not None and capability not in plan.required_capabilities:
                fail("capability", "unsupported capability was not required by the plan")
        for artifact in result.unsupported.diagnostic_artifacts:
            account_artifact(artifact)
    if len(artifact_handles) > limits.max_artifact_count:
        fail("artifact_limit", "browser artifact count exceeds negotiated limit")
    if artifact_bytes > min(limits.max_artifact_total_bytes, limits.max_browser_transfer_bytes):
        fail("artifact_limit", "browser artifact bytes exceed negotiated aggregate limit")


def browser_artifact_stats(result: pb.BrowserResult) -> tuple[list[pb.ArtifactHandle], int]:
    artifacts: list[pb.ArtifactHandle] = []
    outcome = result.WhichOneof("outcome")
    if outcome == "success":
        manifests = [capture.body for capture in result.success.captures]
        if _present(result.success, "html"):
            manifests.append(result.success.html)
        for manifest in manifests:
            artifacts.extend(
                chunk.artifact
                for chunk in manifest.chunks
                if chunk.WhichOneof("storage") == "artifact"
            )
        artifacts.extend(result.success.artifacts)
    elif outcome == "error":
        artifacts.extend(result.error.diagnostic_artifacts)
    elif outcome == "unsupported":
        artifacts.extend(result.unsupported.diagnostic_artifacts)
    return artifacts, sum(artifact.size_bytes for artifact in artifacts)


def validate_transcript(
    transcript: pb.ProtocolTranscript,
    live_fencing_context: pb.FencingContext | None = None,
) -> None:
    _contract(transcript.contract_version)
    _text(transcript.name, "transcript.name", maximum=256)
    phase = "client_hello"
    requested: pb.Limits | None = None
    limits: pb.Limits | None = None
    accepted_at: datetime | None = None
    credits = 0
    request: pb.ExecutionRequest | None = None
    attempt_ids: set[str] = set()
    next_sequence = 0
    frames: dict[int, pb.ExecutionFrame] = {}
    operations: dict[str, pb.OriginOperationRef] = {}
    dynamic_quotas: dict[str, int] = {}
    dispatched: set[str] = set()
    dedupe_required: set[str] = set()
    terminal_seen = False
    resume_rejected = False
    needs_resume = False
    resume_pending = False
    replaying_unacknowledged = False
    acknowledged_sequence = -1
    current_attempt_id: str | None = None
    cancelled = False
    error_count = scrape_count = browser_count = browser_success_count = 0
    batch_count = artifact_count = 0
    artifact_bytes = 0
    artifact_handles: set[str] = set()
    output_items = 0
    monitor_urls_seen: set[str] = set()
    monitor_job_urls_seen: set[str] = set()

    for index, event in enumerate(transcript.events):
        _enum(event.direction, pb.EventDirection, f"events[{index}].direction")
        kind = event.WhichOneof("event")
        expected_kind = {
            pb.EVENT_DIRECTION_CLIENT: "client",
            pb.EVENT_DIRECTION_SERVER: "server",
            pb.EVENT_DIRECTION_FAULT: "fault",
        }[event.direction]
        if kind != expected_kind:
            fail("direction", f"events[{index}] direction and payload disagree")
        if kind in {"client", "server"}:
            message = event.client if kind == "client" else event.server
            frame_ceiling = (
                limits.max_frame_bytes if limits is not None else HARD_LIMITS.max_frame_bytes
            )
            message_size = len(message.SerializeToString())
            if message_size + uvarint_size(message_size) > frame_ceiling:
                fail("frame_limit", "length-delimited protocol record exceeds max_frame_bytes")
        if terminal_seen or resume_rejected:
            fail("terminal", "no events are legal after terminal/rejected resume")

        if kind == "fault":
            _enum(event.fault.point, pb.DisconnectPoint, "fault.point")
            if phase != "ready" or request is None:
                fail("disconnect", "disconnect fault requires an active connected execution")
            if not event.fault.origin_request_id:
                fail("origin", "disconnect must identify the affected semantic operation")
            if event.fault.origin_request_id not in operations:
                fail("origin", "disconnect references an undeclared origin operation")
            fault_operation = operations[event.fault.origin_request_id]
            if event.fault.request_fingerprint != fault_operation.request_fingerprint:
                fail("fingerprint", "disconnect changed the durable request fingerprint")
            if (
                event.fault.point == pb.DISCONNECT_POINT_AFTER_DISPATCH
                and not event.fault.origin_was_dispatched
            ):
                fail("disconnect", "AFTER_DISPATCH must record origin_was_dispatched=true")
            if event.fault.origin_was_dispatched:
                dispatched.add(event.fault.origin_request_id)
                if event.fault.point == pb.DISCONNECT_POINT_AFTER_DISPATCH:
                    dedupe_required.add(event.fault.origin_request_id)
            if event.fault.point in {
                pb.DISCONNECT_POINT_AFTER_FRAME,
                pb.DISCONNECT_POINT_RESULT_BEFORE_TERMINAL,
            } and (not _present(event.fault, "sequence") or event.fault.sequence not in frames):
                fail("disconnect", "after-frame fault must reference an observed sequence")
            phase = "client_hello"
            credits = 0
            needs_resume = True
            resume_pending = False
            continue

        if kind == "client":
            payload = event.client.WhichOneof("payload")
            if payload == "hello":
                if phase != "client_hello":
                    fail("handshake", "client hello is out of order")
                if CONTRACT_VERSION not in event.client.hello.supported_contract_versions:
                    fail("version", "client does not support crawler.runtime/v1")
                _enum(event.client.hello.implementation, pb.Implementation, "client.implementation")
                validate_limits(event.client.hello.requested_limits)
                requested = pb.Limits()
                requested.CopyFrom(event.client.hello.requested_limits)
                phase = "server_hello"
            elif payload == "start":
                if phase != "ready" or request is not None:
                    fail("start", "execution start is out of order or duplicated")
                validate_request(event.client.start)
                assert limits is not None
                assert accepted_at is not None
                deadline = datetime.fromisoformat(
                    event.client.start.deadline_rfc3339.replace("Z", "+00:00")
                ).astimezone(UTC)
                if deadline <= accepted_at or deadline - accepted_at > MAX_DEADLINE:
                    fail("deadline", "deadline must be after accepted_at and at most 15 minutes")
                if event.client.start.kind == pb.EXECUTION_KIND_BROWSER:
                    validate_browser_plan(event.client.start.browser.plan, limits)
                request = pb.ExecutionRequest()
                request.CopyFrom(event.client.start)
                if live_fencing_context is not None:
                    validate_fencing(
                        live_fencing_context,
                        config_revision=request.board_manifest.config_revision,
                    )
                    if live_fencing_context.SerializeToString(deterministic=True) != (
                        request.fencing_context.SerializeToString(deterministic=True)
                    ):
                        fail("fence", "request fencing context is stale against live caller")
                attempt_ids.add(request.attempt_id)
                current_attempt_id = request.attempt_id
                operations = validate_origin_operations(
                    list(request.origin_operations), request.origin_request_id
                )
                if request.kind == pb.EXECUTION_KIND_BROWSER:
                    for action in request.browser.plan.actions:
                        if (
                            action.WhichOneof("action") == "paginate"
                            and action.paginate.dynamic_origin_per_additional_page
                        ):
                            dynamic_quotas[action.origin_request_id] = action.paginate.max_pages - 1
                next_sequence = 0
            elif payload == "resume":
                if phase != "ready" or request is None or not needs_resume:
                    fail("resume", "resume requires a disconnected prior execution")
                resume = event.client.resume
                assert accepted_at is not None
                original_deadline = datetime.fromisoformat(
                    request.deadline_rfc3339.replace("Z", "+00:00")
                ).astimezone(UTC)
                if original_deadline <= accepted_at:
                    fail("deadline", "execution deadline expired before resume acceptance")
                _contract(resume.contract_version)
                if (
                    resume.request_id != request.request_id
                    or resume.origin_request_id != request.origin_request_id
                ):
                    fail("resume", "resume changed semantic execution identity")
                if resume.attempt_id in attempt_ids or not resume.attempt_id:
                    fail("resume", "resume requires a fresh transport attempt_id")
                attempt_ids.add(resume.attempt_id)
                if resume.fencing_context.SerializeToString(deterministic=True) != (
                    request.fencing_context.SerializeToString(deterministic=True)
                ):
                    fail("fence", "resume changed the active fencing context")
                current_attempt_id = resume.attempt_id
                needs_resume = False
                resume_pending = True
                replaying_unacknowledged = True
                if _present(resume, "after_sequence"):
                    if resume.after_sequence not in frames:
                        fail("resume", "after_sequence was not previously observed")
                    if resume.after_sequence < acknowledged_sequence:
                        fail("resume", "after_sequence regressed acknowledged progress")
                    acknowledged_sequence = max(acknowledged_sequence, resume.after_sequence)
                next_sequence = acknowledged_sequence + 1
            elif payload == "window_update":
                if phase != "ready" or limits is None or request is None:
                    fail("backpressure", "window update requires an active execution")
                update = event.client.window_update
                if update.request_id != request.request_id or update.additional_frames == 0:
                    fail("backpressure", "invalid window update")
                if (
                    update.attempt_id != current_attempt_id
                    or update.fence_digest != request.fencing_context.fence_digest
                ):
                    fail("fence", "window update used stale attempt/fencing identity")
                if update.additional_frames > limits.max_in_flight_frames - credits:
                    fail("backpressure", "window credits exceed negotiated maximum")
                credits += update.additional_frames
            elif payload == "cancel":
                if phase != "ready" or request is None:
                    fail("cancel", "cancel requires an active execution")
                if event.client.cancel.request_id != request.request_id:
                    fail("cancel", "cancel request_id mismatch")
                if event.client.cancel.attempt_id != current_attempt_id:
                    fail("cancel", "cancel attempt_id does not own the active transport attempt")
                if event.client.cancel.fencing_context.SerializeToString(
                    deterministic=True
                ) != request.fencing_context.SerializeToString(deterministic=True):
                    fail("fence", "cancel changed the active fencing context")
                cancelled = True
            else:
                fail("message", "client message is untagged")
            continue

        payload = event.server.WhichOneof("payload")
        if payload == "hello":
            if phase != "server_hello" or requested is None:
                fail("handshake", "server hello is out of order")
            hello = event.server.hello
            if (
                hello.selected_contract_version != CONTRACT_VERSION
                or not hello.resume_by_origin_request_id
            ):
                fail("handshake", "server must select v1 and support origin-ID resume")
            _enum(hello.implementation, pb.Implementation, "server.implementation")
            validate_limits(hello.accepted_limits, requested=requested)
            if not DEADLINE_RE.fullmatch(
                hello.accepted_at_rfc3339
            ) or not hello.accepted_at_rfc3339.endswith("Z"):
                fail("deadline", "server accepted_at is not strict RFC3339")
            try:
                accepted_at = datetime.fromisoformat(
                    hello.accepted_at_rfc3339.replace("Z", "+00:00")
                ).astimezone(UTC)
            except ValueError:
                fail("deadline", "server accepted_at is invalid")
            if not 1 <= hello.initial_window_frames <= hello.accepted_limits.max_in_flight_frames:
                fail("backpressure", "initial frame window is outside negotiated limits")
            limits = pb.Limits()
            limits.CopyFrom(hello.accepted_limits)
            credits = hello.initial_window_frames
            phase = "ready"
            continue
        if payload == "resume_rejected":
            if phase != "ready" or request is None or not resume_pending:
                fail("resume", "resume rejection is out of order")
            rejected = event.server.resume_rejected
            validate_error(rejected.error, limits)
            if (
                rejected.request_id != request.request_id
                or rejected.origin_request_id != request.origin_request_id
                or rejected.error.code != pb.ERROR_CODE_AMBIGUOUS_ORIGIN
            ):
                fail("resume", "unknown resume must fail closed as AMBIGUOUS_ORIGIN")
            if (
                rejected.attempt_id != current_attempt_id
                or rejected.fence_digest != request.fencing_context.fence_digest
            ):
                fail("fence", "resume rejection used stale attempt/fencing identity")
            resume_rejected = True
            continue
        if payload != "frame" or phase != "ready" or request is None or limits is None:
            fail("frame", "execution frame is out of order")
        if credits <= 0:
            fail("backpressure", "producer emitted without frame credit")
        credits -= 1
        frame = event.server.frame
        _contract(frame.contract_version)
        if frame.request_id != request.request_id or frame.sequence != next_sequence:
            fail("sequence", "frame request ID or contiguous sequence is invalid")
        if (
            frame.attempt_id != current_attempt_id
            or frame.fence_digest != request.fencing_context.fence_digest
        ):
            fail("fence", "frame echoed a stale/mismatched attempt or fencing digest")
        _validate_frame_size(frame, limits)
        prior = frames.get(frame.sequence)
        if prior is None and len(frames) >= limits.max_execution_frames:
            fail("limit", "execution frame count exceeds negotiated limit")
        if prior is not None:
            if (
                not replaying_unacknowledged
                or frame.sequence <= acknowledged_sequence
                or _semantic_frame_bytes(prior) != _semantic_frame_bytes(frame)
            ):
                fail("sequence", "acknowledged or changed frame was replayed")
            next_sequence += 1
            resume_pending = False
            continue
        replaying_unacknowledged = False
        resume_pending = False
        frames[frame.sequence] = frame
        next_sequence += 1
        frame_kind = frame.WhichOneof("payload")
        if frame_kind is None:
            fail("frame", "frame payload is untagged")
        if dedupe_required and (
            frame_kind != "origin_contact"
            or frame.origin_contact.operation.origin_request_id not in dedupe_required
            or frame.origin_contact.disposition != pb.ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED
        ):
            fail("dedupe", "AFTER_DISPATCH resume requires a DEDUPLICATED origin contact")
        if cancelled and frame_kind != "terminal":
            fail("cancel", "only a cancelled terminal may follow cancellation")
        if error_count and frame_kind != "terminal":
            fail("error_terminal", "only terminal may follow an error frame")
        if frame_kind == "origin_operation_declared":
            operation = frame.origin_operation_declared.operation
            if request.kind != pb.EXECUTION_KIND_BROWSER:
                fail("origin", "dynamic declarations require a browser pagination request")
            if not _present(operation, "parent_origin_request_id"):
                fail("origin", "dynamic pagination declaration requires its action parent")
            parent_id = operation.parent_origin_request_id
            if dynamic_quotas.get(parent_id, 0) <= 0:
                fail("origin", "dynamic pagination declaration exceeds its bounded quota")
            _text(operation.origin_request_id, "origin.origin_request_id", maximum=512)
            _text(operation.role, "origin.role", maximum=128)
            _sha256(operation.request_fingerprint, "origin.request_fingerprint")
            if (
                operation.origin_request_id in operations
                or operation.operation_sequence != len(operations)
                or parent_id not in operations
            ):
                fail("origin_sequence", "dynamic origin declaration is not contiguous and unique")
            durable = pb.OriginOperationRef()
            durable.CopyFrom(operation)
            operations[operation.origin_request_id] = durable
            dynamic_quotas[parent_id] -= 1
        elif frame_kind == "origin_contact":
            operation = frame.origin_contact.operation
            known = operations.get(operation.origin_request_id)
            if known is None:
                fail("origin", "origin contact requires a durable pre-dispatch declaration")
            if known.SerializeToString(deterministic=True) != operation.SerializeToString(
                deterministic=True
            ):
                fail("origin", "origin operation identity changed")
            if frame.origin_contact.request_fingerprint != known.request_fingerprint:
                fail("fingerprint", "origin contact changed its pre-dispatch request fingerprint")
            disposition = frame.origin_contact.disposition
            _enum(disposition, pb.OriginContactDisposition, "origin.disposition")
            if disposition == pb.ORIGIN_CONTACT_DISPOSITION_DISPATCHED:
                if operation.origin_request_id in dispatched:
                    fail("at_most_once", "origin operation was dispatched more than once")
                dispatched.add(operation.origin_request_id)
            elif operation.origin_request_id not in dedupe_required:
                fail("dedupe", "deduplication is legal exactly once after an ambiguous resume")
            else:
                dedupe_required.discard(operation.origin_request_id)
            if _present(frame.origin_contact, "exchange_artifact"):
                validate_artifact(frame.origin_contact.exchange_artifact, limits)
                artifact = frame.origin_contact.exchange_artifact
                if artifact.handle in artifact_handles:
                    fail("duplicate", "artifact handle was emitted more than once")
                artifact_handles.add(artifact.handle)
                artifact_count += 1
                artifact_bytes += artifact.size_bytes
        elif frame_kind == "monitor_batch":
            if request.kind != pb.EXECUTION_KIND_MONITOR:
                fail("kind", "monitor batch is illegal for scrape execution")
            validate_monitor_result(frame.monitor_batch, limits)
            batch_urls = set(frame.monitor_batch.urls)
            batch_job_urls = {job.url for job in frame.monitor_batch.jobs}
            if batch_urls & monitor_urls_seen or batch_job_urls & monitor_job_urls_seen:
                fail("duplicate", "monitor URLs/jobs must be unique across all batches")
            monitor_urls_seen.update(batch_urls)
            monitor_job_urls_seen.update(batch_job_urls)
            batch_count += 1
            output_items += len(frame.monitor_batch.urls)
            if batch_count > limits.max_monitor_batches:
                fail("limit", "monitor batch count exceeds negotiated limit")
        elif frame_kind == "scrape_result":
            if request.kind != pb.EXECUTION_KIND_SCRAPE or scrape_count:
                fail("kind", "scrape execution requires exactly one scrape result")
            validate_job_content(frame.scrape_result.content, limits)
            scrape_count += 1
            output_items += 1
        elif frame_kind == "browser_result":
            if request.kind != pb.EXECUTION_KIND_BROWSER or browser_count:
                fail("kind", "browser execution requires exactly one browser result")
            validate_browser_result(frame.browser_result, limits, request.browser.plan)
            nested_artifacts, nested_bytes = browser_artifact_stats(frame.browser_result)
            for artifact in nested_artifacts:
                if artifact.handle in artifact_handles:
                    fail("duplicate", "artifact handle was emitted more than once")
                artifact_handles.add(artifact.handle)
            artifact_count += len(nested_artifacts)
            artifact_bytes += nested_bytes
            browser_count += 1
            if frame.browser_result.WhichOneof("outcome") == "success":
                browser_success_count += 1
                output_items += 1
            else:
                error_count += 1
        elif frame_kind == "artifact":
            validate_artifact(frame.artifact.artifact, limits)
            if frame.artifact.artifact.handle in artifact_handles:
                fail("duplicate", "artifact handle was emitted more than once")
            artifact_handles.add(frame.artifact.artifact.handle)
            artifact_count += 1
            artifact_bytes += frame.artifact.artifact.size_bytes
        elif frame_kind == "error":
            validate_error(frame.error, limits)
            error_count += 1
            if (
                error_count > 1
                or (request.kind == pb.EXECUTION_KIND_SCRAPE and scrape_count)
                or (request.kind == pb.EXECUTION_KIND_BROWSER and browser_count)
            ):
                fail("error", "illegal result/error combination")
        else:
            terminal = frame.terminal
            _enum(terminal.status, pb.TerminalStatus, "terminal.status")
            if terminal.frame_count != len(frames) - 1:
                fail("count", "terminal frame_count disagrees with observed frames")
            if terminal.output_items != output_items:
                fail("count", "terminal output_items disagrees with observed output")
            if terminal.monitor_batches != batch_count or terminal.artifact_count != artifact_count:
                fail("count", "terminal batch/artifact counts disagree")
            if terminal.origin_operation_count != len(dispatched):
                fail("count", "terminal origin_operation_count disagrees with dispatch records")
            if (
                output_items > limits.max_output_items
                or terminal.active_duration_ms > limits.max_active_duration_ms
            ):
                fail("limit", "terminal exceeds negotiated output/duration limits")
            if (
                artifact_count > limits.max_artifact_count
                or artifact_bytes > limits.max_artifact_total_bytes
            ):
                fail("artifact_limit", "execution artifacts exceed aggregate limits")
            if cancelled and terminal.status != pb.TERMINAL_STATUS_CANCELLED:
                fail("cancel", "cancellation requires a cancelled terminal")
            if terminal.status == pb.TERMINAL_STATUS_SUCCESS:
                if error_count or not terminal.eligible_for_commit:
                    fail("terminal", "successful terminal must be error-free and commit-eligible")
                if request.kind == pb.EXECUTION_KIND_MONITOR and batch_count == 0:
                    fail("terminal", "monitor success requires at least one batch")
                if request.kind == pb.EXECUTION_KIND_SCRAPE and scrape_count != 1:
                    fail("terminal", "scrape success requires exactly one result")
                if request.kind == pb.EXECUTION_KIND_BROWSER and browser_success_count != 1:
                    fail("terminal", "browser success requires exactly one correlated result")
                if len(dispatched) != len(operations):
                    fail("terminal", "commit-eligible success requires every declared origin")
            elif terminal.status == pb.TERMINAL_STATUS_ERROR:
                if error_count != 1 or terminal.eligible_for_commit:
                    fail("terminal", "error terminal requires one error and is not commit-eligible")
            elif not cancelled or terminal.eligible_for_commit or error_count:
                fail(
                    "terminal",
                    "cancelled terminal requires cancellation and no commit-eligible output",
                )
            terminal_seen = True

    if request is None:
        fail("start", "transcript has no execution request")
    if not terminal_seen and not resume_rejected:
        fail("terminal", "result is incomplete without exactly one terminal frame")


def _canonical_json(message: Message) -> bytes:
    obj = json_format.MessageToDict(message, preserving_proto_field_name=True)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def semantic_hash(frames: list[pb.ExecutionFrame], projection: pb.ProjectedEffects) -> str:
    digest = hashlib.sha256(CONTRACT_VERSION.encode() + b"\0")
    semantic_values = [_semantic_frame_bytes(frame) for frame in frames]
    canonical_projection = pb.ProjectedEffects()
    canonical_projection.CopyFrom(projection)
    for field in (canonical_projection.urls_to_upsert, canonical_projection.content_hashes):
        ordered = sorted(field)
        del field[:]
        field.extend(ordered)
    job_effects = sorted(
        canonical_projection.job_effects,
        key=lambda effect: (effect.source_url, effect.content_sha256),
    )
    del canonical_projection.job_effects[:]
    canonical_projection.job_effects.extend(job_effects)
    targets = sorted(
        canonical_projection.targets,
        key=lambda target: (target.url, target.action, target.content_sha256),
    )
    del canonical_projection.targets[:]
    canonical_projection.targets.extend(targets)
    semantic_values.append(canonical_projection.SerializeToString(deterministic=True))
    for raw in semantic_values:
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _canonical_job_content(content: pb.JobContent) -> pb.JobContent:
    # Canonicalize semantically unordered repeated fields before deterministic
    # protobuf serialization. Optional presence remains intact.
    canonical = pb.JobContent()
    canonical.CopyFrom(content)
    ordered_localizations = sorted(
        canonical.localizations,
        key=lambda value: (value.locale, value.title, value.description_html),
    )
    del canonical.localizations[:]
    canonical.localizations.extend(ordered_localizations)
    ordered_skills = sorted(canonical.skills)
    del canonical.skills[:]
    canonical.skills.extend(ordered_skills)
    ordered_extensions = sorted(canonical.extensions, key=lambda value: value.schema_id)
    del canonical.extensions[:]
    canonical.extensions.extend(ordered_extensions)
    if _present(canonical, "locations"):
        locations = sorted(canonical.locations.values)
        del canonical.locations.values[:]
        canonical.locations.values.extend(locations)
    return canonical


def content_hash(content: pb.JobContent) -> str:
    return hashlib.sha256(
        _canonical_job_content(content).SerializeToString(deterministic=True)
    ).hexdigest()


def project_frames(
    frames: list[pb.ExecutionFrame], request: pb.ExecutionRequest | None = None
) -> pb.ProjectedEffects:
    if request is None:
        fail("projection", "projection requires its typed ExecutionRequest")
    target_url = {
        pb.EXECUTION_KIND_MONITOR: request.board_manifest.board_url,
        pb.EXECUTION_KIND_SCRAPE: request.scrape.source_url,
        pb.EXECUTION_KIND_BROWSER: request.browser.plan.target_url,
    }.get(request.kind)
    if target_url is None:
        fail("projection", "projection request has an unsupported execution kind")
    projection = pb.ProjectedEffects(
        gone_detection_allowed=True,
        request_id=request.request_id,
        origin_request_id=request.origin_request_id,
        execution_kind=request.kind,
        target_url=target_url,
    )
    urls: set[str] = set()
    hashes: list[str] = []
    metadata_digest = hashlib.sha256()
    metadata_seen = False
    job_effects: list[tuple[str, str]] = []
    targets: dict[str, tuple[int, str | None]] = {}
    for frame in frames:
        kind = frame.WhichOneof("payload")
        if kind == "monitor_batch":
            result = frame.monitor_batch
            urls.update(result.urls)
            for url in result.urls:
                targets[url] = (pb.PROJECTED_ACTION_UPSERT, None)
            for job in result.jobs:
                digest = content_hash(job.content)
                hashes.append(digest)
                job_effects.append((job.url, digest))
                targets[job.url] = (pb.PROJECTED_ACTION_UPSERT, digest)
            projection.gone_detection_allowed &= (
                not result.truncated and result.security_filtered_count == 0
            )
            projection.hybrid |= result.hybrid
            projection.truncated |= result.truncated
            projection.filtered_count += result.filtered_count
            projection.security_filtered_count += result.security_filtered_count
            if _present(result, "new_sitemap_url"):
                projection.new_sitemap_url = result.new_sitemap_url
            if _present(result, "metadata_updates"):
                raw = result.metadata_updates.SerializeToString(deterministic=True)
                metadata_digest.update(len(raw).to_bytes(8, "big"))
                metadata_digest.update(raw)
                metadata_seen = True
        elif kind == "scrape_result":
            digest = content_hash(frame.scrape_result.content)
            hashes.append(digest)
            if request.kind != pb.EXECUTION_KIND_SCRAPE:
                fail("projection", "scrape projection requires its typed ExecutionRequest")
            job_effects.append((request.scrape.source_url, digest))
            targets[request.scrape.source_url] = (pb.PROJECTED_ACTION_UPSERT, digest)
        elif kind == "browser_result" and frame.browser_result.WhichOneof("outcome") == "success":
            targets[request.browser.plan.target_url] = (
                pb.PROJECTED_ACTION_BROWSER_RESULT,
                None,
            )
    projection.urls_to_upsert.extend(sorted(urls))
    projection.content_hashes.extend(sorted(hashes))
    projection.job_effects.extend(
        pb.JobEffect(source_url=url, content_sha256=digest) for url, digest in sorted(job_effects)
    )
    for url in sorted(targets):
        action, digest = targets[url]
        target = projection.targets.add(url=url, action=action)
        if digest is not None:
            target.content_sha256 = digest
    if metadata_seen:
        projection.metadata_updates_sha256 = metadata_digest.hexdigest()
    return projection


def validate_replay(replay: pb.ReplayCase, limits: pb.Limits = HARD_LIMITS) -> None:
    validate_limits(limits)
    _contract(replay.contract_version)
    _text(replay.name, "replay.name", maximum=256)
    _text(replay.provider_family, "replay.provider_family", maximum=256)
    _sha256(replay.expected_semantic_sha256, "replay.expected_semantic_sha256")
    validate_request(replay.execution_request)
    _enum(replay.adapter, pb.ReplayAdapter, "replay.adapter")
    if not replay.exchanges:
        fail("replay", "replay requires captured origin exchanges")
    request_operations = list(replay.execution_request.origin_operations)
    if len(replay.exchanges) < len(request_operations):
        fail("replay", "captured exchanges must cover all initially declared operations")
    operation_ids: set[str] = set()
    exchange_operations: list[pb.OriginOperationRef] = []
    decoded: dict[int, Message] = {}
    for expected_sequence, exchange in enumerate(replay.exchanges):
        operation = exchange.operation
        if expected_sequence < len(request_operations) and (
            operation.SerializeToString(deterministic=True)
            != request_operations[expected_sequence].SerializeToString(deterministic=True)
        ):
            fail("origin", "captured exchange prefix differs from ExecutionRequest")
        if operation.operation_sequence != expected_sequence:
            fail("origin_sequence", "captured exchanges must follow operation sequence")
        if operation.origin_request_id in operation_ids:
            fail("duplicate", "replay origin operations must be unique")
        operation_ids.add(operation.origin_request_id)
        exchange_operations.append(operation)
        if not exchange.deterministically_redacted:
            fail("redaction", "captured exchange is not marked deterministically redacted")
        _text(exchange.request.method, "replay.request.method", maximum=16)
        if not re.fullmatch(r"[A-Z]+", exchange.request.method):
            fail("replay", "captured request method must be an uppercase HTTP token")
        _url(exchange.request.url, "replay.request.url")
        query = urlsplit(exchange.request.url).query
        for name, value in _parse_ampersand_fields(query, "replay.request.query"):
            _validate_named_secret(name, value, "query parameter")
        _validate_redacted_body(query.encode(), "replay.request.query")
        _headers(list(exchange.request.headers), "replay.request.headers")
        _headers(list(exchange.response.headers), "replay.response.headers")
        if not 100 <= exchange.response.status <= 599:
            fail("http_status", "captured response status must be in 100..599")
        request_body = validate_chunk_manifest(
            exchange.request.body,
            limits,
            maximum_total=limits.max_http_transfer_bytes,
            require_inline=True,
        )
        response_body = validate_chunk_manifest(
            exchange.response.body,
            limits,
            maximum_total=limits.max_http_transfer_bytes,
            require_inline=True,
        )
        assert request_body is not None
        assert response_body is not None
        _validate_redacted_body(
            request_body,
            "replay.request.body",
            headers=list(exchange.request.headers),
        )
        _validate_redacted_body(
            response_body,
            "replay.response.body",
            headers=list(exchange.response.headers),
        )
        if request_fingerprint(exchange.request) != operation.request_fingerprint:
            fail("fingerprint", "captured request differs from its durable operation fingerprint")
        if _present(exchange, "normalized_result_frame_sequence"):
            target_sequence = exchange.normalized_result_frame_sequence
            if target_sequence in decoded:
                fail("replay", "normalized result frame mappings must be unique")
            if replay.adapter == pb.REPLAY_ADAPTER_NORMALIZED_MONITOR_JSON:
                message: Message = pb.MonitorResult()
            else:
                message = pb.ScrapeResult()
            json_format.Parse(response_body.decode(), message, ignore_unknown_fields=False)
            decoded[target_sequence] = message
    validate_origin_operations(exchange_operations, replay.execution_request.origin_request_id)

    requested = pb.Limits()
    requested.CopyFrom(limits)
    accepted = pb.Limits()
    accepted.CopyFrom(limits)
    accepted.max_in_flight_frames = min(64, limits.max_in_flight_frames)
    events = [
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(
                hello=pb.ClientHello(
                    supported_contract_versions=[CONTRACT_VERSION],
                    implementation=pb.IMPLEMENTATION_PYTHON,
                    requested_limits=requested,
                )
            ),
        ),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_SERVER,
            server=pb.ServerMessage(
                hello=pb.ServerHello(
                    selected_contract_version=CONTRACT_VERSION,
                    implementation=pb.IMPLEMENTATION_GO,
                    accepted_limits=accepted,
                    initial_window_frames=accepted.max_in_flight_frames,
                    resume_by_origin_request_id=True,
                    accepted_at_rfc3339="2026-08-26T11:50:00Z",
                )
            ),
        ),
        pb.ProtocolEvent(
            direction=pb.EVENT_DIRECTION_CLIENT,
            client=pb.ClientMessage(start=replay.execution_request),
        ),
    ]
    credits = accepted.max_in_flight_frames
    for frame in replay.expected_frames:
        if credits == 0:
            events.append(
                pb.ProtocolEvent(
                    direction=pb.EVENT_DIRECTION_CLIENT,
                    client=pb.ClientMessage(
                        window_update=pb.WindowUpdate(
                            request_id=replay.execution_request.request_id,
                            additional_frames=accepted.max_in_flight_frames,
                            attempt_id=replay.execution_request.attempt_id,
                            fence_digest=replay.execution_request.fencing_context.fence_digest,
                        )
                    ),
                )
            )
            credits = accepted.max_in_flight_frames
        events.append(
            pb.ProtocolEvent(
                direction=pb.EVENT_DIRECTION_SERVER,
                server=pb.ServerMessage(frame=frame),
            )
        )
        credits -= 1
    validate_transcript(
        pb.ProtocolTranscript(
            contract_version=CONTRACT_VERSION,
            name=f"replay:{replay.name}",
            events=events,
        )
    )
    contact_operations = [
        frame.origin_contact.operation
        for frame in replay.expected_frames
        if frame.WhichOneof("payload") == "origin_contact"
    ]
    if len(contact_operations) != len(replay.exchanges) or any(
        left.SerializeToString(deterministic=True)
        != right.operation.SerializeToString(deterministic=True)
        for left, right in zip(contact_operations, replay.exchanges, strict=True)
    ):
        fail("origin", "expected origin-contact frames must exactly match captured exchanges")
    result_frames = {
        frame.sequence: frame
        for frame in replay.expected_frames
        if frame.WhichOneof("payload") in {"monitor_batch", "scrape_result"}
    }
    if set(result_frames) != set(decoded):
        fail("replay", "normalized response mappings must exactly cover result frames")
    for sequence, message in decoded.items():
        frame = result_frames[sequence]
        expected = (
            frame.monitor_batch if isinstance(message, pb.MonitorResult) else frame.scrape_result
        )
        if (
            isinstance(message, pb.MonitorResult) and frame.WhichOneof("payload") != "monitor_batch"
        ) or (
            isinstance(message, pb.ScrapeResult) and frame.WhichOneof("payload") != "scrape_result"
        ):
            fail("replay", "replay adapter result kind differs from mapped frame")
        if _canonical_json(message) != _canonical_json(expected):
            fail("replay", "offline decoded result differs from expected frame")
    projection = project_frames(list(replay.expected_frames), replay.execution_request)
    if _canonical_json(projection) != _canonical_json(replay.expected_projection):
        fail("projection", "projected DB effects differ from golden expectation")
    actual_hash = semantic_hash(list(replay.expected_frames), projection)
    if actual_hash != replay.expected_semantic_sha256:
        fail("hash", "semantic replay hash mismatch")


def validate_case(case: pb.ConformanceCase) -> None:
    subject = case.WhichOneof("subject")
    if subject == "transcript":
        validate_transcript(case.transcript)
    elif subject == "browser_plan":
        validate_browser_plan(case.browser_plan)
    elif subject == "browser_result":
        validate_browser_result(case.browser_result)
    elif subject == "replay":
        validate_replay(case.replay)
    else:
        fail("case", "conformance case has no tagged subject")
