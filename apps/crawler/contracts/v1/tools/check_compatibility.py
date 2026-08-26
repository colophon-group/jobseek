from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class CompatibilityError(RuntimeError):
    """The checked candidate is not compatible with the frozen descriptor."""


@dataclass(frozen=True)
class FieldShape:
    name: str
    number: int
    label: int
    type: int
    type_name: str
    default_value: str | None
    oneof_index: int | None
    json_name: str
    proto3_optional: bool
    extendee: str
    options: bytes


@dataclass(frozen=True)
class MessageShape:
    name: str
    fields: tuple[FieldShape, ...]
    oneofs: tuple[str, ...]
    nested: tuple[MessageShape, ...]
    enums: tuple[EnumShape, ...]
    reserved_ranges: tuple[tuple[int, int], ...]
    reserved_names: frozenset[str]
    options: bytes


@dataclass(frozen=True)
class EnumValueShape:
    name: str
    number: int
    options: bytes


@dataclass(frozen=True)
class EnumShape:
    name: str
    values: tuple[EnumValueShape, ...]
    reserved_ranges: tuple[tuple[int, int], ...]
    reserved_names: frozenset[str]
    options: bytes


@dataclass(frozen=True)
class FileShape:
    name: str
    package: str
    syntax: str
    dependencies: tuple[str, ...]
    public_dependencies: tuple[int, ...]
    weak_dependencies: tuple[int, ...]
    messages: tuple[MessageShape, ...]
    enums: tuple[EnumShape, ...]
    options: bytes


@dataclass(frozen=True)
class GitBaselineState:
    introduction: bool
    prior_proto: bytes | None


_BASELINE_RELATIVE = Path("baseline/runtime-v1.descriptor.b64")
_MANIFEST_RELATIVE = Path("baseline/manifest.json")
_PROTO_RELATIVE = Path("runtime.proto")
_ADJACENT_POLICY_RELATIVE = Path("fixtures/compatibility/adjacent_version_policy")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TEST_PACKAGE = re.compile(r"jobseek\.crawler\.runtime\.policytest\.v([1-9][0-9]*)\Z")
_CONVERTER_DIRECTIONS = ("old_to_new", "new_to_old")


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise CompatibilityError("malformed protobuf varint in descriptor")


def _wire_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    result: list[tuple[int, int, int | bytes]] = []
    offset = 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire_type = tag >> 3, tag & 7
        if number == 0:
            raise CompatibilityError("descriptor contains protobuf field zero")
        if wire_type == 0:
            value, offset = _varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise CompatibilityError("truncated fixed64 descriptor field")
            value, offset = data[offset:end], end
        elif wire_type == 2:
            size, offset = _varint(data, offset)
            end = offset + size
            if end > len(data):
                raise CompatibilityError("truncated length-delimited descriptor field")
            value, offset = data[offset:end], end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise CompatibilityError("truncated fixed32 descriptor field")
            value, offset = data[offset:end], end
        else:
            raise CompatibilityError(f"unsupported descriptor wire type: {wire_type}")
        result.append((number, wire_type, value))
    return result


def _bytes_values(fields: list[tuple[int, int, int | bytes]], number: int) -> list[bytes]:
    values = [value for field, wire, value in fields if field == number and wire == 2]
    if not all(isinstance(value, bytes) for value in values):
        raise CompatibilityError("internal descriptor decoder type mismatch")
    return values  # type: ignore[return-value]


def _int_values(fields: list[tuple[int, int, int | bytes]], number: int) -> list[int]:
    values = [value for field, wire, value in fields if field == number and wire == 0]
    if not all(isinstance(value, int) for value in values):
        raise CompatibilityError("internal descriptor decoder type mismatch")
    return values  # type: ignore[return-value]


def _text(fields: list[tuple[int, int, int | bytes]], number: int, default: str = "") -> str:
    values = _bytes_values(fields, number)
    if not values:
        return default
    try:
        return values[-1].decode("utf-8")
    except UnicodeDecodeError as error:
        raise CompatibilityError("descriptor contains invalid UTF-8") from error


def _optional_text(fields: list[tuple[int, int, int | bytes]], number: int) -> str | None:
    values = _bytes_values(fields, number)
    return _text(fields, number) if values else None


def _integer(fields: list[tuple[int, int, int | bytes]], number: int, default: int = 0) -> int:
    values = _int_values(fields, number)
    return values[-1] if values else default


def _optional_integer(fields: list[tuple[int, int, int | bytes]], number: int) -> int | None:
    values = _int_values(fields, number)
    return values[-1] if values else None


def _signed_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & (1 << 31) else value


def _parse_range(data: bytes) -> tuple[int, int]:
    fields = _wire_fields(data)
    return _integer(fields, 1), _integer(fields, 2)


def _parse_field(data: bytes) -> FieldShape:
    fields = _wire_fields(data)
    return FieldShape(
        name=_text(fields, 1),
        extendee=_text(fields, 2),
        number=_signed_int32(_integer(fields, 3)),
        label=_integer(fields, 4),
        type=_integer(fields, 5),
        type_name=_text(fields, 6),
        default_value=_optional_text(fields, 7),
        options=(_bytes_values(fields, 8) or [b""])[-1],
        oneof_index=_optional_integer(fields, 9),
        json_name=_text(fields, 10),
        proto3_optional=bool(_integer(fields, 17)),
    )


def _parse_enum_value(data: bytes) -> EnumValueShape:
    fields = _wire_fields(data)
    return EnumValueShape(
        name=_text(fields, 1),
        number=_signed_int32(_integer(fields, 2)),
        options=(_bytes_values(fields, 3) or [b""])[-1],
    )


def _parse_enum(data: bytes) -> EnumShape:
    fields = _wire_fields(data)
    return EnumShape(
        name=_text(fields, 1),
        values=tuple(_parse_enum_value(value) for value in _bytes_values(fields, 2)),
        options=(_bytes_values(fields, 3) or [b""])[-1],
        reserved_ranges=tuple(_parse_range(value) for value in _bytes_values(fields, 4)),
        reserved_names=frozenset(value.decode("utf-8") for value in _bytes_values(fields, 5)),
    )


def _parse_message(data: bytes) -> MessageShape:
    fields = _wire_fields(data)
    if _bytes_values(fields, 5) or _bytes_values(fields, 6):
        raise CompatibilityError("protobuf message extensions are unsupported in runtime v1")
    return MessageShape(
        name=_text(fields, 1),
        fields=tuple(_parse_field(value) for value in _bytes_values(fields, 2)),
        nested=tuple(_parse_message(value) for value in _bytes_values(fields, 3)),
        enums=tuple(_parse_enum(value) for value in _bytes_values(fields, 4)),
        oneofs=tuple(_text(_wire_fields(value), 1) for value in _bytes_values(fields, 8)),
        options=(_bytes_values(fields, 7) or [b""])[-1],
        reserved_ranges=tuple(_parse_range(value) for value in _bytes_values(fields, 9)),
        reserved_names=frozenset(value.decode("utf-8") for value in _bytes_values(fields, 10)),
    )


def parse_descriptor_set(data: bytes) -> FileShape:
    set_fields = _wire_fields(data)
    files = _bytes_values(set_fields, 1)
    if len(files) != 1:
        raise CompatibilityError(
            f"runtime descriptor set must contain exactly one file, found {len(files)}"
        )
    fields = _wire_fields(files[0])
    if _bytes_values(fields, 6):
        raise CompatibilityError("protobuf service declarations are unsupported in runtime v1")
    if _bytes_values(fields, 7):
        raise CompatibilityError("protobuf extension declarations are unsupported in runtime v1")
    if _int_values(fields, 14):
        raise CompatibilityError("protobuf editions are unsupported in runtime v1")
    return FileShape(
        name=_text(fields, 1),
        package=_text(fields, 2),
        dependencies=tuple(value.decode("utf-8") for value in _bytes_values(fields, 3)),
        messages=tuple(_parse_message(value) for value in _bytes_values(fields, 4)),
        enums=tuple(_parse_enum(value) for value in _bytes_values(fields, 5)),
        options=(_bytes_values(fields, 8) or [b""])[-1],
        public_dependencies=tuple(_int_values(fields, 10)),
        weak_dependencies=tuple(_int_values(fields, 11)),
        syntax=_text(fields, 12),
    )


def _flatten_messages(file: FileShape) -> dict[str, MessageShape]:
    result: dict[str, MessageShape] = {}

    def visit(prefix: str, messages: tuple[MessageShape, ...]) -> None:
        for message in messages:
            qualified = f"{prefix}.{message.name}"
            result[qualified] = message
            visit(qualified, message.nested)

    visit(file.package, file.messages)
    return result


def _flatten_enums(file: FileShape) -> dict[str, EnumShape]:
    result = {f"{file.package}.{enum.name}": enum for enum in file.enums}

    def visit(prefix: str, messages: tuple[MessageShape, ...]) -> None:
        for message in messages:
            qualified = f"{prefix}.{message.name}"
            result.update({f"{qualified}.{enum.name}": enum for enum in message.enums})
            visit(qualified, message.nested)

    visit(file.package, file.messages)
    return result


def _number_reserved(ranges: tuple[tuple[int, int], ...], number: int) -> bool:
    return any(start <= number < end for start, end in ranges)


def _enum_number_reserved(ranges: tuple[tuple[int, int], ...], number: int) -> bool:
    # EnumDescriptorProto.EnumReservedRange.end is inclusive. Message field
    # reserved ranges use the usual exclusive end.
    return any(start <= number <= end for start, end in ranges)


def _reserved_range_preserved(
    old_range: tuple[int, int],
    current_ranges: tuple[tuple[int, int], ...],
    *,
    inclusive_end: bool,
) -> bool:
    old_start, old_end = old_range
    target_end = old_end + 1 if inclusive_end else old_end
    cursor = old_start
    normalized = sorted((start, end + 1 if inclusive_end else end) for start, end in current_ranges)
    for start, end in normalized:
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= target_end:
            return True
    return cursor >= target_end


def compare_descriptors(baseline: FileShape, current: FileShape) -> None:
    if baseline.name != current.name:
        raise CompatibilityError(
            f"descriptor file name changed: {baseline.name!r} -> {current.name!r}"
        )
    if baseline.package != current.package:
        raise CompatibilityError(
            f"protobuf package changed: {baseline.package!r} -> {current.package!r}"
        )
    if baseline.syntax != current.syntax:
        raise CompatibilityError(
            f"protobuf syntax changed: {baseline.syntax!r} -> {current.syntax!r}"
        )
    if (
        baseline.dependencies != current.dependencies
        or baseline.public_dependencies != current.public_dependencies
        or baseline.weak_dependencies != current.weak_dependencies
    ):
        raise CompatibilityError("protobuf dependencies changed")
    if baseline.options != current.options:
        raise CompatibilityError("protobuf file options changed")

    current_messages = _flatten_messages(current)
    for qualified, old_message in _flatten_messages(baseline).items():
        new_message = current_messages.get(qualified)
        if new_message is None:
            raise CompatibilityError(f"baseline message was removed or renamed: {qualified}")
        if old_message.options != new_message.options:
            raise CompatibilityError(f"message options changed: {qualified}")
        new_by_number = {field.number: field for field in new_message.fields}
        new_by_name = {field.name: field for field in new_message.fields}
        for new_field in new_message.fields:
            if new_field.name in old_message.reserved_names:
                raise CompatibilityError(
                    f"reserved field name reused: {qualified}.{new_field.name}"
                )
            if _number_reserved(old_message.reserved_ranges, new_field.number):
                raise CompatibilityError(
                    f"reserved field number reused: {qualified} #{new_field.number}"
                )
        removed_reserved_names = old_message.reserved_names - new_message.reserved_names
        if removed_reserved_names:
            removed = sorted(removed_reserved_names)[0]
            raise CompatibilityError(f"reserved field name was removed: {qualified}.{removed}")
        for old_range in old_message.reserved_ranges:
            if not _reserved_range_preserved(
                old_range,
                new_message.reserved_ranges,
                inclusive_end=False,
            ):
                raise CompatibilityError(
                    f"reserved field range was removed: {qualified} {old_range}"
                )
        for old_field in old_message.fields:
            new_field = new_by_number.get(old_field.number)
            field_name = f"{qualified}.{old_field.name}"
            if new_field is None:
                moved = new_by_name.get(old_field.name)
                if moved is not None and moved.number != old_field.number:
                    raise CompatibilityError(f"field number changed: {field_name}")
                reserved = (
                    _number_reserved(new_message.reserved_ranges, old_field.number)
                    and old_field.name in new_message.reserved_names
                )
                if not reserved:
                    raise CompatibilityError(
                        f"removed field must reserve both name and number: {field_name}"
                    )
                raise CompatibilityError(
                    f"field removal requires v2 and executable converters: {field_name}"
                )
            if new_field.name != old_field.name:
                raise CompatibilityError(
                    f"field name changed or number reused: {qualified} #{old_field.number}"
                )
            moved = new_by_name.get(old_field.name)
            if moved is None or moved.number != old_field.number:
                raise CompatibilityError(f"field number changed: {field_name}")
            if new_field.type != old_field.type or new_field.type_name != old_field.type_name:
                raise CompatibilityError(f"field type changed: {field_name}")
            if new_field.label != old_field.label:
                raise CompatibilityError(f"field cardinality changed: {field_name}")
            if new_field.proto3_optional != old_field.proto3_optional:
                raise CompatibilityError(f"field presence changed: {field_name}")
            if new_field.json_name != old_field.json_name:
                raise CompatibilityError(f"field json_name changed: {field_name}")
            if new_field.oneof_index != old_field.oneof_index:
                raise CompatibilityError(f"field oneof membership changed: {field_name}")
            if new_field.default_value != old_field.default_value:
                raise CompatibilityError(f"field default changed: {field_name}")
            if new_field.extendee != old_field.extendee:
                raise CompatibilityError(f"field extendee changed: {field_name}")
            if new_field.options != old_field.options:
                raise CompatibilityError(f"field options changed: {field_name}")

        if len(new_message.oneofs) < len(old_message.oneofs):
            raise CompatibilityError(f"oneof declaration was removed: {qualified}")
        for index, old_name in enumerate(old_message.oneofs):
            if new_message.oneofs[index] != old_name:
                raise CompatibilityError(f"oneof declaration changed: {qualified}.{old_name}")

    current_enums = _flatten_enums(current)
    for qualified, old_enum in _flatten_enums(baseline).items():
        new_enum = current_enums.get(qualified)
        if new_enum is None:
            raise CompatibilityError(f"baseline enum was removed or renamed: {qualified}")
        if old_enum.options != new_enum.options:
            raise CompatibilityError(f"enum options changed: {qualified}")
        new_by_number = {value.number: value for value in new_enum.values}
        new_by_name = {value.name: value for value in new_enum.values}
        for new_value in new_enum.values:
            if new_value.name in old_enum.reserved_names:
                raise CompatibilityError(f"reserved enum name reused: {qualified}.{new_value.name}")
            if _enum_number_reserved(old_enum.reserved_ranges, new_value.number):
                raise CompatibilityError(
                    f"reserved enum number reused: {qualified} #{new_value.number}"
                )
        removed_reserved_names = old_enum.reserved_names - new_enum.reserved_names
        if removed_reserved_names:
            removed = sorted(removed_reserved_names)[0]
            raise CompatibilityError(f"reserved enum name was removed: {qualified}.{removed}")
        for old_range in old_enum.reserved_ranges:
            if not _reserved_range_preserved(
                old_range,
                new_enum.reserved_ranges,
                inclusive_end=True,
            ):
                raise CompatibilityError(
                    f"reserved enum range was removed: {qualified} {old_range}"
                )
        for old_value in old_enum.values:
            new_value = new_by_number.get(old_value.number)
            value_name = f"{qualified}.{old_value.name}"
            if new_value is None:
                moved = new_by_name.get(old_value.name)
                if moved is not None and moved.number != old_value.number:
                    raise CompatibilityError(f"enum number changed: {value_name}")
                reserved = (
                    _enum_number_reserved(new_enum.reserved_ranges, old_value.number)
                    and old_value.name in new_enum.reserved_names
                )
                if not reserved:
                    raise CompatibilityError(
                        f"removed enum value must reserve both name and number: {value_name}"
                    )
                raise CompatibilityError(
                    f"enum removal requires v2 and executable converters: {value_name}"
                )
            if new_value.name != old_value.name:
                raise CompatibilityError(
                    f"enum name changed or number reused: {qualified} #{old_value.number}"
                )
            moved = new_by_name.get(old_value.name)
            if moved is None or moved.number != old_value.number:
                raise CompatibilityError(f"enum number changed: {value_name}")
            if new_value.options != old_value.options:
                raise CompatibilityError(f"enum value options changed: {value_name}")


def _run(
    command: list[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            capture_output=True,
            check=False,
            env=env,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CompatibilityError(f"failed to execute {' '.join(command)}: {error}") from error
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CompatibilityError(
            f"{' '.join(command)} failed with exit {completed.returncode}: {stderr}"
        )
    return completed


def compile_descriptor(proto: Path, *, protoc: str = "protoc") -> bytes:
    proto = proto.resolve()
    if not proto.is_file():
        raise CompatibilityError(f"protobuf source does not exist: {proto}")
    executable = shutil.which(protoc)
    if executable is None:
        raise CompatibilityError(f"required protobuf compiler is unavailable: {protoc}")
    with tempfile.TemporaryDirectory(prefix="runtime-v1-descriptor-") as temporary:
        output = Path(temporary) / "runtime.descriptor.pb"
        _run(
            [
                executable,
                f"--proto_path={proto.parent}",
                f"--descriptor_set_out={output}",
                proto.name,
            ],
            cwd=proto.parent,
        )
        return output.read_bytes()


def compile_descriptor_source(source: bytes, *, protoc: str = "protoc") -> bytes:
    with tempfile.TemporaryDirectory(prefix="runtime-v1-prior-main-") as temporary:
        proto = Path(temporary) / "runtime.proto"
        proto.write_bytes(source)
        return compile_descriptor(proto, protoc=protoc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_frozen_baseline(root: Path) -> tuple[bytes, dict[str, Any]]:
    baseline_path = root / _BASELINE_RELATIVE
    manifest_path = root / _MANIFEST_RELATIVE
    try:
        encoded = "".join(baseline_path.read_text(encoding="ascii").splitlines())
        descriptor = base64.b64decode(encoded, validate=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise CompatibilityError(f"invalid frozen descriptor baseline: {error}") from error
    expected_keys = {
        "format",
        "source",
        "descriptor_sha256",
        "introduction_proto_sha256",
        "introduction_base_sha",
        "generator",
    }
    if set(manifest) != expected_keys:
        raise CompatibilityError("baseline manifest has unexpected or missing keys")
    if manifest["format"] != "google.protobuf.FileDescriptorSet/base64":
        raise CompatibilityError("baseline manifest format is not the frozen v1 format")
    if manifest["source"] != "runtime.proto":
        raise CompatibilityError("baseline manifest source must be runtime.proto")
    digest = _sha256(descriptor)
    if not _HEX_SHA256.fullmatch(str(manifest["descriptor_sha256"])):
        raise CompatibilityError("baseline descriptor hash is malformed")
    if digest != manifest["descriptor_sha256"]:
        raise CompatibilityError("baseline descriptor hash does not match manifest")
    if base64.b64encode(descriptor).decode("ascii") != encoded:
        raise CompatibilityError("baseline descriptor base64 is not canonical")
    return descriptor, manifest


def _git(repository: Path, arguments: list[str], *, allow_failure: bool = False) -> bytes | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        if allow_failure:
            return None
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CompatibilityError(f"git {' '.join(arguments)} failed: {stderr}")
    return completed.stdout


def _repository_root(root: Path) -> Path:
    output = _git(root, ["rev-parse", "--show-toplevel"])
    assert output is not None
    return Path(output.decode().strip()).resolve()


def _git_object(repository: Path, ref: str, path: Path) -> bytes | None:
    relative = path.resolve().relative_to(repository).as_posix()
    return _git(repository, ["show", f"{ref}:{relative}"], allow_failure=True)


def _commit_sha(value: object) -> str | None:
    candidate = str(value or "").strip().lower()
    if _HEX_SHA256.fullmatch(candidate) and candidate != "0" * 64:
        return candidate
    # Git still uses 40-character object IDs in this repository. Keep the
    # 64-character pattern above for a future SHA-256 repository transition.
    if re.fullmatch(r"[0-9a-f]{40}", candidate) and candidate != "0" * 40:
        return candidate
    return None


def _ensure_commit(repository: Path, commit: str) -> str:
    resolved = _git(
        repository,
        ["rev-parse", f"{commit}^{{commit}}"],
        allow_failure=True,
    )
    if resolved is None and os.environ.get("GITHUB_ACTIONS") == "true":
        _run(
            ["git", "fetch", "--no-tags", "--depth=1", "origin", commit],
            cwd=repository,
            timeout=60,
        )
        resolved = _git(
            repository,
            ["rev-parse", f"{commit}^{{commit}}"],
            allow_failure=True,
        )
    if resolved is None:
        raise CompatibilityError(f"cannot authenticate prior-main commit: {commit}")
    return resolved.decode().strip()


def _github_event_base(repository: Path) -> str | None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    workspace = os.environ.get("GITHUB_WORKSPACE", "").strip()
    if not workspace or Path(workspace).resolve() != repository.resolve():
        # Regression fixtures create independent repositories. The outer
        # workflow event is not provenance for those repositories.
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompatibilityError(f"cannot read GitHub event provenance: {error}") from error
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        base = pull_request.get("base")
        if isinstance(base, dict) and (sha := _commit_sha(base.get("sha"))):
            return _ensure_commit(repository, sha)
        raise CompatibilityError("GitHub pull-request event has no valid base SHA")
    if sha := _commit_sha(event.get("before")):
        return _ensure_commit(repository, sha)
    return None


def _first_parent(repository: Path) -> str:
    output = _git(repository, ["show", "-s", "--format=%P", "HEAD"])
    assert output is not None
    parents = output.decode().strip().split()
    if not parents:
        raise CompatibilityError("cannot authenticate prior main: HEAD has no parent")
    return _ensure_commit(repository, parents[0])


def resolve_base_ref(root: Path, explicit: str | None = None) -> str:
    requested = explicit or os.environ.get("BASELINE_BASE_SHA", "").strip()
    repository = _repository_root(root)
    head_output = _git(repository, ["rev-parse", "HEAD^{commit}"])
    event_base = _github_event_base(repository)
    assert head_output is not None
    head = head_output.decode().strip()

    if event_base is not None:
        trusted = event_base
    else:
        origin_output = _git(
            repository,
            ["rev-parse", "origin/main^{commit}"],
            allow_failure=True,
        )
        if origin_output is None:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise CompatibilityError(
                    "cannot authenticate prior main: refs/remotes/origin/main is unavailable"
                )
            trusted = _first_parent(repository)
        else:
            origin_main = origin_output.decode().strip()
            if head == origin_main:
                trusted = _first_parent(repository)
            else:
                trusted_output = _git(
                    repository,
                    ["merge-base", "HEAD", "origin/main"],
                    allow_failure=True,
                )
                if trusted_output is None:
                    raise CompatibilityError("cannot resolve the trusted prior-main commit")
                trusted = trusted_output.decode().strip()
    if trusted == head:
        raise CompatibilityError("trusted prior-main commit cannot equal current HEAD")
    if requested:
        requested_output = _git(repository, ["rev-parse", f"{requested}^{{commit}}"])
        assert requested_output is not None
        resolved_requested = requested_output.decode().strip()
        if resolved_requested != trusted:
            raise CompatibilityError(
                "requested base ref does not match the trusted prior-main commit"
            )
    return trusted


def enforce_git_baseline_immutable(
    root: Path,
    *,
    base_ref: str | None = None,
    manifest: dict[str, Any],
) -> GitBaselineState:
    repository = _repository_root(root)
    resolved = resolve_base_ref(root, base_ref)
    baseline_path = root / _BASELINE_RELATIVE
    manifest_path = root / _MANIFEST_RELATIVE
    proto_path = root / _PROTO_RELATIVE
    prior_baseline = _git_object(repository, resolved, baseline_path)
    prior_manifest = _git_object(repository, resolved, manifest_path)
    prior_proto = _git_object(repository, resolved, proto_path)
    if prior_baseline is not None or prior_manifest is not None:
        if prior_baseline is None or prior_manifest is None:
            raise CompatibilityError("prior v1 baseline is incomplete")
        if prior_baseline != baseline_path.read_bytes():
            raise CompatibilityError(
                "runtime v1 introduction descriptor is immutable relative to prior main"
            )
        if prior_manifest != manifest_path.read_bytes():
            raise CompatibilityError(
                "runtime v1 baseline manifest is immutable relative to prior main"
            )
        if prior_proto is None:
            raise CompatibilityError("prior main has a baseline but no runtime.proto")
        return GitBaselineState(introduction=False, prior_proto=prior_proto)

    if prior_proto is not None:
        raise CompatibilityError(
            "cannot introduce a mutable baseline after runtime.proto already exists"
        )
    if manifest["introduction_base_sha"] != resolved:
        raise CompatibilityError("introduction_base_sha must equal the exact baseline commit")
    return GitBaselineState(introduction=True, prior_proto=None)


def check(
    root: Path,
    *,
    base_ref: str | None = None,
    protoc: str = "protoc",
) -> None:
    """Validate the candidate source against the frozen and prior-main descriptors."""

    root = root.resolve()
    baseline_bytes, manifest = load_frozen_baseline(root)
    current_bytes = compile_descriptor(root / _PROTO_RELATIVE, protoc=protoc)
    second_bytes = compile_descriptor(root / _PROTO_RELATIVE, protoc=protoc)
    if current_bytes != second_bytes:
        raise CompatibilityError("runtime descriptor generation is nondeterministic")

    baseline = parse_descriptor_set(baseline_bytes)
    current = parse_descriptor_set(current_bytes)
    compare_descriptors(baseline, current)
    git_state = enforce_git_baseline_immutable(
        root,
        base_ref=base_ref,
        manifest=manifest,
    )
    if git_state.introduction:
        source = (root / _PROTO_RELATIVE).read_bytes()
        if _sha256(source) != manifest["introduction_proto_sha256"]:
            raise CompatibilityError("introduced runtime.proto does not match its frozen manifest")
        # Descriptor encodings may differ between supported protoc releases.
        # Structural equality is authoritative; the committed baseline bytes
        # remain immutable and authenticated by their own digest.
        compare_descriptors(current, baseline)
    else:
        assert git_state.prior_proto is not None
        prior_main = parse_descriptor_set(
            compile_descriptor_source(git_state.prior_proto, protoc=protoc)
        )
        compare_descriptors(prior_main, current)


def _policy_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CompatibilityError(f"duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompatibilityError(
            f"invalid adjacent-version fixture {path.name}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise CompatibilityError(f"adjacent-version fixture must be an object: {path.name}")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise CompatibilityError(f"{label} has unexpected or missing keys")


def _fixture_file(directory: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise CompatibilityError(f"{label} must be a relative fixture path")
    path = (directory / value).resolve()
    if not path.is_relative_to(directory.resolve()) or path.is_symlink() or not path.is_file():
        raise CompatibilityError(f"{label} does not resolve to a regular fixture file")
    return path


def _relative_shapes(file: FileShape) -> tuple[dict[str, MessageShape], dict[str, EnumShape]]:
    prefix = f"{file.package}."
    return (
        {name.removeprefix(prefix): shape for name, shape in _flatten_messages(file).items()},
        {name.removeprefix(prefix): shape for name, shape in _flatten_enums(file).items()},
    )


def _field_oneof(message: MessageShape, field: FieldShape) -> str | None:
    return message.oneofs[field.oneof_index] if field.oneof_index is not None else None


def _normalized_policy_field(field: FieldShape, package: str) -> FieldShape:
    return replace(
        field,
        type_name=field.type_name.removeprefix(f".{package}"),
        oneof_index=None,
    )


def _compare_policy_descriptors(old: FileShape, new: FileShape) -> tuple[set[str], set[str]]:
    if old.syntax != "proto3" or new.syntax != "proto3":
        raise CompatibilityError("adjacent-version specimens must use proto3")
    if old.dependencies or new.dependencies:
        raise CompatibilityError("adjacent-version specimens cannot import dependencies")
    old_messages, old_enums = _relative_shapes(old)
    new_messages, new_enums = _relative_shapes(new)
    removed_fields: set[str] = set()
    added_fields: set[str] = set()
    for name, old_message in old_messages.items():
        new_message = new_messages.get(name)
        if new_message is None:
            raise CompatibilityError(f"test message removed without a field-level policy: {name}")
        if old_message.options != new_message.options:
            raise CompatibilityError(f"message or map-entry options changed: {name}")
        if not old_message.reserved_names <= new_message.reserved_names or any(
            not _reserved_range_preserved(
                old_range, new_message.reserved_ranges, inclusive_end=False
            )
            for old_range in old_message.reserved_ranges
        ):
            raise CompatibilityError(f"prior field tombstone was removed: {name}")
        new_by_name = {field.name: field for field in new_message.fields}
        new_by_number = {field.number: field for field in new_message.fields}
        for old_field in old_message.fields:
            field_name = old_field.name
            new_field = new_by_name.get(field_name)
            if new_field is not None:
                if _normalized_policy_field(old_field, old.package) != _normalized_policy_field(
                    new_field, new.package
                ):
                    raise CompatibilityError(f"field or map shape changed: {name}.{field_name}")
                if _field_oneof(old_message, old_field) != _field_oneof(new_message, new_field):
                    raise CompatibilityError(
                        f"oneof identity or membership changed: {name}.{field_name}"
                    )
                continue
            number = old_field.number
            if field_name not in new_message.reserved_names or not _number_reserved(
                new_message.reserved_ranges, number
            ):
                raise CompatibilityError(
                    f"removed field must reserve both name and number: {name}.{field_name}"
                )
            if number in new_by_number:
                raise CompatibilityError(f"removed field number was reused: {name} #{number}")
            removed_fields.add(field_name)
        added_fields.update(set(new_by_name) - {field.name for field in old_message.fields})

    for name, old_enum in old_enums.items():
        new_enum = new_enums.get(name)
        if new_enum is None:
            raise CompatibilityError(f"test enum was removed: {name}")
        if old_enum.options != new_enum.options:
            raise CompatibilityError(f"enum alias policy changed: {name}")
        if not old_enum.reserved_names <= new_enum.reserved_names or any(
            not _reserved_range_preserved(old_range, new_enum.reserved_ranges, inclusive_end=True)
            for old_range in old_enum.reserved_ranges
        ):
            raise CompatibilityError(f"prior enum tombstone was removed: {name}")
        new_by_name = {value.name: value for value in new_enum.values}
        new_numbers = {value.number for value in new_enum.values}
        for old_value in old_enum.values:
            value_name = old_value.name
            new_value = new_by_name.get(value_name)
            if new_value is not None:
                if new_value != old_value:
                    raise CompatibilityError(f"enum alias or value changed: {name}.{value_name}")
                continue
            number = old_value.number
            if value_name not in new_enum.reserved_names or not _enum_number_reserved(
                new_enum.reserved_ranges, number
            ):
                raise CompatibilityError(
                    f"removed enum value must reserve both name and number: {name}.{value_name}"
                )
            if number in new_numbers:
                raise CompatibilityError(f"removed enum number was reused: {name} #{number}")
    return removed_fields, added_fields


def _contains_large_integer(value: Any) -> bool:
    if type(value) is int:
        return abs(value) > 2**53
    if isinstance(value, dict):
        return any(_contains_large_integer(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_large_integer(item) for item in value)
    return False


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
    )


def _validate_vectors(
    vectors: dict[str, Any], removed_fields: set[str], added_fields: set[str]
) -> list[dict[str, Any]]:
    _exact_keys(vectors, {"format", "cases"}, "adjacent-version vectors")
    if type(vectors["format"]) is not int or vectors["format"] != 1:
        raise CompatibilityError("adjacent-version vector format must be 1")
    cases = vectors["cases"]
    if not isinstance(cases, list) or not cases:
        raise CompatibilityError("adjacent-version evidence corpus must be nonempty")
    ids: set[str] = set()
    directions: set[str] = set()
    lossy_directions: set[str] = set()
    reversible_directions: set[str] = set()
    saw_large = False
    saw_absent_defaults = False
    saw_explicit_defaults = False
    saw_unknown = False
    serialized_outputs: set[bytes] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise CompatibilityError("adjacent-version case must be an object")
        _exact_keys(
            case,
            {"id", "direction", "input", "expected", "reversible"},
            "adjacent-version case",
        )
        case_id = case["id"]
        direction = case["direction"]
        payload = case["input"]
        expected = case["expected"]
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise CompatibilityError("adjacent-version case id is invalid or duplicated")
        if direction not in _CONVERTER_DIRECTIONS:
            raise CompatibilityError(f"unsupported converter direction: {direction}")
        if not isinstance(payload, dict) or not isinstance(expected, dict):
            raise CompatibilityError(f"case {case_id} input and expected must be objects")
        _exact_keys(expected, {"payload", "losses"}, f"case {case_id} expected")
        if not isinstance(expected["payload"], dict) or not isinstance(expected["losses"], list):
            raise CompatibilityError(f"case {case_id} expected payload/losses are invalid")
        if type(case["reversible"]) is not bool:
            raise CompatibilityError(f"case {case_id} reversible must be a boolean")
        allowed_loss_fields = removed_fields if direction == "old_to_new" else added_fields
        loss_fields: set[str] = set()
        for loss in expected["losses"]:
            if not isinstance(loss, dict):
                raise CompatibilityError(f"case {case_id} loss must be an object")
            _exact_keys(loss, {"path", "reason"}, f"case {case_id} loss")
            path = loss["path"]
            reason = loss["reason"]
            if (
                not isinstance(path, str)
                or not path.startswith("$.")
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise CompatibilityError(f"case {case_id} loss path/reason is invalid")
            field = path[2:]
            if "." in field or field not in allowed_loss_fields:
                raise CompatibilityError(f"case {case_id} declares an unsupported loss: {path}")
            if field not in payload or field in expected["payload"] or field in loss_fields:
                raise CompatibilityError(f"case {case_id} loss is false or duplicated: {path}")
            loss_fields.add(field)
        preserved = {key: value for key, value in payload.items() if key not in loss_fields}
        if expected["payload"] != preserved:
            raise CompatibilityError(f"case {case_id} fabricates or changes preserved fields")
        if case["reversible"] != (not loss_fields):
            raise CompatibilityError(f"case {case_id} reversible/loss declaration disagrees")
        if loss_fields:
            lossy_directions.add(direction)
        else:
            reversible_directions.add(direction)
        saw_large = saw_large or _contains_large_integer(payload)
        saw_absent_defaults = saw_absent_defaults or (
            "explicit_count" not in payload and "explicit_label" not in payload
        )
        saw_explicit_defaults = saw_explicit_defaults or (
            payload.get("explicit_count") == 0
            and "explicit_count" in payload
            and payload.get("explicit_label") == ""
            and "explicit_label" in payload
        )
        saw_unknown = saw_unknown or any(key.startswith("unknown_") for key in payload)
        serialized_outputs.add(_canonical_json(expected))
        ids.add(case_id)
        directions.add(direction)
    if directions != set(_CONVERTER_DIRECTIONS):
        raise CompatibilityError("adjacent-version evidence must cover both directions")
    if lossy_directions != set(_CONVERTER_DIRECTIONS):
        raise CompatibilityError("adjacent-version evidence must be genuinely lossy both ways")
    if reversible_directions != set(_CONVERTER_DIRECTIONS):
        raise CompatibilityError(
            "adjacent-version evidence must include reversible cases both ways"
        )
    if not saw_large or not saw_absent_defaults or not saw_explicit_defaults or not saw_unknown:
        raise CompatibilityError(
            "adjacent-version evidence lacks >2^53, presence/default, or unknown-field coverage"
        )
    if len(serialized_outputs) < 2:
        raise CompatibilityError("constant-output converter evidence is forbidden")
    return cases


def _load_adjacent_policy(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    manifest = _policy_json(directory / "manifest.json")
    _exact_keys(
        manifest,
        {"format", "production", "fixture_only", "from", "to", "converters", "vectors"},
        "adjacent-version manifest",
    )
    if type(manifest["format"]) is not int or manifest["format"] != 1:
        raise CompatibilityError("adjacent-version manifest format must be 1")
    if type(manifest["production"]) is not bool or manifest["production"] is not False:
        raise CompatibilityError("adjacent-version specimen production must be exactly false")
    if type(manifest["fixture_only"]) is not bool or manifest["fixture_only"] is not True:
        raise CompatibilityError("adjacent-version specimen must be explicitly fixture_only")
    proto_paths: list[Path] = []
    for key in ("from", "to"):
        endpoint = manifest[key]
        if not isinstance(endpoint, dict):
            raise CompatibilityError(f"adjacent-version {key} endpoint must be an object")
        _exact_keys(endpoint, {"version", "package", "proto"}, f"{key} endpoint")
        version = endpoint["version"]
        package = endpoint["package"]
        if type(version) is not int or version <= 0 or not isinstance(package, str):
            raise CompatibilityError(f"adjacent-version {key} identity is invalid")
        match = _TEST_PACKAGE.fullmatch(package)
        if match is None or int(match.group(1)) != version:
            raise CompatibilityError(f"adjacent-version {key} package is not test-only")
        proto = _fixture_file(directory, endpoint["proto"], f"{key} protobuf")
        if proto.suffix != ".proto":
            raise CompatibilityError(f"adjacent-version {key} protobuf has the wrong file type")
        proto_paths.append(proto)
    if manifest["to"]["version"] != manifest["from"]["version"] + 1:
        raise CompatibilityError("adjacent-version specimen is not numerically adjacent")
    endpoints = [parse_descriptor_set(compile_descriptor(path)) for path in proto_paths]
    for key, descriptor in zip(("from", "to"), endpoints, strict=True):
        if descriptor.package != manifest[key]["package"]:
            raise CompatibilityError(f"adjacent-version {key} protobuf package disagrees")
    removed_fields, added_fields = _compare_policy_descriptors(*endpoints)
    if not removed_fields or not added_fields:
        raise CompatibilityError(
            "adjacent-version specimen must exercise real additions and removals"
        )

    converters = manifest["converters"]
    if not isinstance(converters, dict) or set(converters) != {"python", "go"}:
        raise CompatibilityError("adjacent-version policy requires Python and Go converters")
    runners: dict[str, Path] = {}
    for language, suffix in (("python", ".py"), ("go", ".go")):
        config = converters[language]
        if not isinstance(config, dict):
            raise CompatibilityError(f"{language} converter declaration must be an object")
        _exact_keys(config, {"path", "directions"}, f"{language} converter")
        if config["directions"] != list(_CONVERTER_DIRECTIONS):
            raise CompatibilityError(f"{language} converter must declare both directions")
        path = _fixture_file(directory, config["path"], f"{language} converter")
        if path.suffix != suffix:
            raise CompatibilityError(f"{language} converter has the wrong file type")
        runners[language] = path
    vectors_path = _fixture_file(directory, manifest["vectors"], "adjacent-version vectors")
    cases = _validate_vectors(_policy_json(vectors_path), removed_fields, added_fields)
    return {"manifest": manifest, "runners": runners, "cases": cases}


def validate_adjacent_version_policy(directory: Path) -> None:
    """Validate the deliberately test-only adjacent-version policy specimen."""

    _load_adjacent_policy(directory)


def _converter_batch(
    *,
    language: str,
    runner: Path,
    direction: str,
    cases: list[dict[str, Any]],
    go_cache: Path,
) -> tuple[bytes, dict[str, Any]]:
    batch = {"cases": [{"id": case["id"], "payload": case["input"]} for case in cases]}
    if language == "python":
        command = [sys.executable, str(runner), "--direction", direction]
        environment = os.environ.copy()
    else:
        go = shutil.which("go")
        if go is None:
            raise CompatibilityError("required Go toolchain is unavailable")
        command = [go, "run", str(runner), "--direction", direction]
        environment = os.environ.copy()
        environment.update(
            {
                "GO111MODULE": "off",
                "GOCACHE": str(go_cache / "build"),
                "GOENV": "off",
                "GOMODCACHE": str(go_cache / "modules"),
                "GOPATH": str(go_cache / "path"),
                "GOPROXY": "off",
                "GOSUMDB": "off",
                "GOTOOLCHAIN": "local",
            }
        )
    completed = _run(
        command,
        cwd=runner.parent,
        input_bytes=_canonical_json(batch),
        env=environment,
        timeout=60,
    )
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CompatibilityError(f"{language} converter emitted invalid JSON") from error
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"results"}
        or not isinstance(parsed["results"], list)
    ):
        raise CompatibilityError(f"{language} converter emitted an invalid result envelope")
    if completed.stdout != _canonical_json(parsed):
        raise CompatibilityError(f"{language} converter bytes are not canonical")
    return completed.stdout, parsed


def check_adjacent_version_policy(root: Path, *, policy_directory: Path | None = None) -> None:
    """Execute both test-only language paths, directions, and mixed compositions."""

    directory = (
        policy_directory.resolve()
        if policy_directory is not None
        else root.resolve() / _ADJACENT_POLICY_RELATIVE
    )
    policy = _load_adjacent_policy(directory)
    cases: list[dict[str, Any]] = policy["cases"]
    runners: dict[str, Path] = policy["runners"]
    by_direction = {
        direction: [case for case in cases if case["direction"] == direction]
        for direction in _CONVERTER_DIRECTIONS
    }
    observed: dict[tuple[str, str], dict[str, Any]] = {}
    raw: dict[tuple[str, str], bytes] = {}
    with tempfile.TemporaryDirectory(prefix="runtime-adjacent-policy-go-") as temporary:
        go_cache = Path(temporary)
        for language, runner in runners.items():
            for direction, direction_cases in by_direction.items():
                first_raw, first = _converter_batch(
                    language=language,
                    runner=runner,
                    direction=direction,
                    cases=direction_cases,
                    go_cache=go_cache,
                )
                second_raw, _ = _converter_batch(
                    language=language,
                    runner=runner,
                    direction=direction,
                    cases=direction_cases,
                    go_cache=go_cache,
                )
                if first_raw != second_raw:
                    raise CompatibilityError(
                        f"{language} {direction} converter is nondeterministic"
                    )
                expected = {
                    "results": [{"id": case["id"], **case["expected"]} for case in direction_cases]
                }
                if first != expected:
                    raise CompatibilityError(
                        f"{language} {direction} converter disagrees with shared vectors"
                    )
                observed[(language, direction)] = first
                raw[(language, direction)] = first_raw
        for direction in _CONVERTER_DIRECTIONS:
            if raw[("python", direction)] != raw[("go", direction)]:
                raise CompatibilityError(f"cross-language {direction} canonical bytes differ")

        opposite = {"old_to_new": "new_to_old", "new_to_old": "old_to_new"}
        for first_language, second_language in (("python", "go"), ("go", "python")):
            for direction in _CONVERTER_DIRECTIONS:
                direction_cases = by_direction[direction]
                first_results = {
                    item["id"]: item for item in observed[(first_language, direction)]["results"]
                }
                composed_cases = [
                    {
                        "id": case["id"],
                        "input": first_results[case["id"]]["payload"],
                    }
                    for case in direction_cases
                ]
                _, composed = _converter_batch(
                    language=second_language,
                    runner=runners[second_language],
                    direction=opposite[direction],
                    cases=composed_cases,
                    go_cache=go_cache,
                )
                final = {item["id"]: item for item in composed["results"]}
                for case in direction_cases:
                    item = final.get(case["id"])
                    if item is None or item["losses"]:
                        raise CompatibilityError(
                            f"mixed {first_language}/{second_language} round trip failed: "
                            f"{case['id']}"
                        )
                    expected_payload = (
                        case["input"] if case["reversible"] else case["expected"]["payload"]
                    )
                    if item["payload"] != expected_payload:
                        raise CompatibilityError(
                            f"mixed {first_language}/{second_language} round trip failed: "
                            f"{case['id']}"
                        )


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check runtime v1 against its immutable introduction descriptor."
    )
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--base-ref")
    parser.add_argument("--protoc", default="protoc")
    parser.add_argument("--adjacent-policy-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if not arguments.adjacent_policy_only:
            check(
                arguments.root,
                base_ref=arguments.base_ref,
                protoc=arguments.protoc,
            )
        check_adjacent_version_policy(arguments.root)
    except CompatibilityError as error:
        print(f"runtime v1 compatibility failed: {error}", file=sys.stderr)
        return 1
    if arguments.adjacent_policy_only:
        print("runtime v1 adjacent-version policy: ok")
    else:
        print("runtime v1 compatibility and adjacent-version policy: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
