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
    return FileShape(
        name=_text(fields, 1),
        package=_text(fields, 2),
        messages=tuple(_parse_message(value) for value in _bytes_values(fields, 4)),
        enums=tuple(_parse_enum(value) for value in _bytes_values(fields, 5)),
        options=(_bytes_values(fields, 8) or [b""])[-1],
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
                "--include_imports",
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


def resolve_base_ref(root: Path, explicit: str | None = None) -> str:
    requested = explicit or os.environ.get("BASELINE_BASE_SHA", "").strip()
    repository = _repository_root(root)
    head_output = _git(repository, ["rev-parse", "HEAD^{commit}"])
    origin_output = _git(
        repository,
        ["rev-parse", "origin/main^{commit}"],
        allow_failure=True,
    )
    if origin_output is None:
        raise CompatibilityError(
            "cannot authenticate prior main: refs/remotes/origin/main is unavailable"
        )
    assert head_output is not None
    head = head_output.decode().strip()
    origin_main = origin_output.decode().strip()
    if head == origin_main:
        trusted_output = _git(
            repository,
            ["rev-parse", "HEAD^1^{commit}"],
            allow_failure=True,
        )
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


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompatibilityError(f"invalid JSON fixture {path}: {error}") from error


def _converter_output(
    command: list[str],
    *,
    cwd: Path,
    direction: str,
    value: Any,
) -> Any:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    completed = _run([*command, direction], cwd=cwd, input_bytes=serialized, timeout=45)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CompatibilityError(
            f"converter {' '.join(command)} emitted invalid JSON for {direction}"
        ) from error


def check_transition_reservations(v1: FileShape, v2: FileShape) -> None:
    if v2.name != v1.name:
        raise CompatibilityError("adjacent runtime descriptor file name changed")
    if v2.package != "jobseek.crawler.runtime.v2" or v2.syntax != "proto3":
        raise CompatibilityError(
            "adjacent runtime descriptor must use jobseek.crawler.runtime.v2/proto3"
        )

    v1_messages = {
        name.removeprefix(v1.package + "."): message
        for name, message in _flatten_messages(v1).items()
    }
    v2_messages = {
        name.removeprefix(v2.package + "."): message
        for name, message in _flatten_messages(v2).items()
    }
    for name, old_message in v1_messages.items():
        new_message = v2_messages.get(name)
        if new_message is None:
            raise CompatibilityError(f"v2 must retain message tombstone for reservations: {name}")
        new_by_name = {field.name: field for field in new_message.fields}
        for old_field in old_message.fields:
            retained = new_by_name.get(old_field.name)
            if retained is not None and retained.number == old_field.number:
                continue
            if not (
                old_field.name in new_message.reserved_names
                and _number_reserved(new_message.reserved_ranges, old_field.number)
            ):
                raise CompatibilityError(
                    "v2 removed/renamed field must reserve both name and number: "
                    f"{name}.{old_field.name}"
                )

    v1_enums = {
        name.removeprefix(v1.package + "."): enum for name, enum in _flatten_enums(v1).items()
    }
    v2_enums = {
        name.removeprefix(v2.package + "."): enum for name, enum in _flatten_enums(v2).items()
    }
    for name, old_enum in v1_enums.items():
        new_enum = v2_enums.get(name)
        if new_enum is None:
            raise CompatibilityError(f"v2 must retain enum tombstone for reservations: {name}")
        new_by_name = {value.name: value for value in new_enum.values}
        for old_value in old_enum.values:
            retained = new_by_name.get(old_value.name)
            if retained is not None and retained.number == old_value.number:
                continue
            if not (
                old_value.name in new_enum.reserved_names
                and _enum_number_reserved(new_enum.reserved_ranges, old_value.number)
            ):
                raise CompatibilityError(
                    "v2 removed/renamed enum value must reserve both name and number: "
                    f"{name}.{old_value.name}"
                )


def check_adjacent_converter(root: Path, *, protoc: str = "protoc", go: str = "go") -> None:
    future_proto = root.parent / "v2" / "runtime.proto"
    if not future_proto.exists():
        return
    current_descriptor = compile_descriptor(root / _PROTO_RELATIVE, protoc=protoc)
    future_descriptor = compile_descriptor(future_proto, protoc=protoc)
    repeated_future = compile_descriptor(future_proto, protoc=protoc)
    if future_descriptor != repeated_future:
        raise CompatibilityError("v2 descriptor generation is nondeterministic")
    check_transition_reservations(
        parse_descriptor_set(current_descriptor),
        parse_descriptor_set(future_descriptor),
    )

    converter = root / "converters" / "v1_to_v2"
    manifest_path = converter / "converter.json"
    manifest = _load_json(manifest_path)
    expected_manifest = {
        "schema_version": 1,
        "from": "crawler.runtime/v1",
        "to": "crawler.runtime/v2",
        "directions": ["v1_to_v2", "v2_to_v1"],
        "python": "converter.py",
        "go": "converter.go",
        "roundtrip": "fixtures/roundtrip.json",
        "lossy": "fixtures/lossy.json",
    }
    if manifest != expected_manifest:
        raise CompatibilityError(
            "adjacent converter manifest must exactly declare the executable "
            "bidirectional v1/v2 interface"
        )

    python_file = converter / manifest["python"]
    go_file = converter / manifest["go"]
    roundtrip_path = converter / manifest["roundtrip"]
    lossy_path = converter / manifest["lossy"]
    for required in (python_file, go_file, roundtrip_path, lossy_path):
        if not required.is_file():
            raise CompatibilityError(f"adjacent converter file is missing: {required}")

    go_executable = shutil.which(go)
    if go_executable is None:
        raise CompatibilityError(f"required Go compiler is unavailable: {go}")
    _run(
        [sys.executable, "-m", "py_compile", python_file.name],
        cwd=converter,
    )

    roundtrip = _load_json(roundtrip_path)
    lossy = _load_json(lossy_path)
    for fixture_name, fixture in (("roundtrip", roundtrip), ("lossy", lossy)):
        if (
            not isinstance(fixture, dict)
            or set(fixture) != {"schema_version", "cases"}
            or fixture["schema_version"] != 1
            or not isinstance(fixture["cases"], list)
            or not fixture["cases"]
        ):
            raise CompatibilityError(
                f"{fixture_name} converter vectors must contain nonempty version-1 cases"
            )

    python_command = [sys.executable, python_file.name]
    go_command = [go_executable, "run", go_file.name]
    seen_names: set[str] = set()
    for case in roundtrip["cases"]:
        if not isinstance(case, dict) or set(case) != {"name", "v1", "v2"}:
            raise CompatibilityError("roundtrip converter case has an invalid shape")
        name = case["name"]
        if not isinstance(name, str) or not name or name in seen_names:
            raise CompatibilityError("converter case names must be nonempty and unique")
        seen_names.add(name)
        if case["v1"] == case["v2"]:
            raise CompatibilityError(
                f"roundtrip converter case {name!r} does not exercise a shape change"
            )
        for command in (python_command, go_command):
            forward = _converter_output(
                command,
                cwd=converter,
                direction="v1_to_v2",
                value=case["v1"],
            )
            reverse = _converter_output(
                command,
                cwd=converter,
                direction="v2_to_v1",
                value=case["v2"],
            )
            if forward != case["v2"] or reverse != case["v1"]:
                raise CompatibilityError(
                    f"converter {' '.join(command)} failed roundtrip vector {name!r}"
                )

    for case in lossy["cases"]:
        if not isinstance(case, dict) or set(case) != {
            "name",
            "direction",
            "input",
            "expected",
            "reason",
        }:
            raise CompatibilityError("lossy converter case has an invalid shape")
        name = case["name"]
        if not isinstance(name, str) or not name or name in seen_names:
            raise CompatibilityError("converter case names must be nonempty and unique")
        seen_names.add(name)
        if case["direction"] not in expected_manifest["directions"]:
            raise CompatibilityError(f"lossy converter case {name!r} has invalid direction")
        if case["input"] == case["expected"]:
            raise CompatibilityError(
                f"lossy converter case {name!r} does not prove information loss"
            )
        if not isinstance(case["reason"], str) or not case["reason"].strip():
            raise CompatibilityError(f"lossy converter case {name!r} must explain the loss")
        outputs = [
            _converter_output(
                command,
                cwd=converter,
                direction=case["direction"],
                value=case["input"],
            )
            for command in (python_command, go_command)
        ]
        if outputs[0] != case["expected"] or outputs[1] != case["expected"]:
            raise CompatibilityError(f"Python/Go converters failed lossy vector {name!r}")


def check(
    root: Path,
    *,
    base_ref: str | None = None,
    protoc: str = "protoc",
    go: str = "go",
) -> None:
    root = root.resolve()
    baseline_bytes, manifest = load_frozen_baseline(root)
    current_bytes = compile_descriptor(root / _PROTO_RELATIVE, protoc=protoc)
    second_bytes = compile_descriptor(root / _PROTO_RELATIVE, protoc=protoc)
    if current_bytes != second_bytes:
        raise CompatibilityError("runtime descriptor generation is nondeterministic")

    baseline = parse_descriptor_set(baseline_bytes)
    current = parse_descriptor_set(current_bytes)
    compare_descriptors(baseline, current)
    git_state = enforce_git_baseline_immutable(root, base_ref=base_ref, manifest=manifest)
    if git_state.introduction:
        if _sha256((root / _PROTO_RELATIVE).read_bytes()) != manifest["introduction_proto_sha256"]:
            raise CompatibilityError("introduced runtime.proto does not match its frozen manifest")
        if current_bytes != baseline_bytes:
            raise CompatibilityError(
                "introduced runtime.proto does not match its frozen descriptor"
            )
    else:
        assert git_state.prior_proto is not None
        prior_main = parse_descriptor_set(
            compile_descriptor_source(git_state.prior_proto, protoc=protoc)
        )
        compare_descriptors(prior_main, current)
    check_adjacent_converter(root, protoc=protoc, go=go)


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check runtime v1 against its immutable introduction descriptor."
    )
    parser.add_argument("--root", type=Path, default=_default_root())
    parser.add_argument("--base-ref")
    parser.add_argument("--protoc", default="protoc")
    parser.add_argument("--go", default="go")
    arguments = parser.parse_args()
    try:
        check(
            arguments.root,
            base_ref=arguments.base_ref,
            protoc=arguments.protoc,
            go=arguments.go,
        )
    except CompatibilityError as error:
        print(f"runtime v1 compatibility failed: {error}", file=sys.stderr)
        return 1
    print("runtime v1 compatibility: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
