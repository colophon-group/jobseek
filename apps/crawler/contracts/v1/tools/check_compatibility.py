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
from dataclasses import dataclass
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
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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
    timeout: int = 30,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_bytes,
            capture_output=True,
            check=False,
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


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check runtime v1 against its immutable introduction descriptor."
    )
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--base-ref")
    parser.add_argument("--protoc", default="protoc")
    arguments = parser.parse_args()
    try:
        check(
            arguments.root,
            base_ref=arguments.base_ref,
            protoc=arguments.protoc,
        )
    except CompatibilityError as error:
        print(f"runtime v1 compatibility failed: {error}", file=sys.stderr)
        return 1
    print("runtime v1 compatibility: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
