from __future__ import annotations

import base64
from pathlib import Path

from gen.python import runtime_pb2 as pb
from google.protobuf import descriptor_pb2

ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "baseline/runtime-v1.descriptor.b64"


def messages(file: descriptor_pb2.FileDescriptorProto) -> dict[str, descriptor_pb2.DescriptorProto]:
    result: dict[str, descriptor_pb2.DescriptorProto] = {}

    def visit(prefix: str, values) -> None:
        for value in values:
            name = f"{prefix}.{value.name}" if prefix else value.name
            result[name] = value
            visit(name, value.nested_type)

    visit(file.package, file.message_type)
    return result


def enums(
    file: descriptor_pb2.FileDescriptorProto,
) -> dict[str, descriptor_pb2.EnumDescriptorProto]:
    result = {f"{file.package}.{value.name}": value for value in file.enum_type}

    def visit(prefix: str, values) -> None:
        for message in values:
            name = f"{prefix}.{message.name}"
            for value in message.enum_type:
                result[f"{name}.{value.name}"] = value
            visit(name, message.nested_type)

    visit(file.package, file.message_type)
    return result


def number_reserved(message: descriptor_pb2.DescriptorProto, number: int) -> bool:
    return any(value.start <= number < value.end for value in message.reserved_range)


def enum_number_reserved(value: descriptor_pb2.EnumDescriptorProto, number: int) -> bool:
    return any(item.start <= number < item.end for item in value.reserved_range)


def check_compatibility(
    baseline: descriptor_pb2.FileDescriptorProto,
    current: descriptor_pb2.FileDescriptorProto,
) -> None:
    if baseline.package != current.package or baseline.syntax != current.syntax:
        raise AssertionError("runtime v1 package/syntax changed")

    current_messages = messages(current)
    for name, old_message in messages(baseline).items():
        new_message = current_messages.get(name)
        if new_message is None:
            raise AssertionError(f"baseline message was removed: {name}")
        new_by_number = {field.number: field for field in new_message.field}
        new_by_name = {field.name: field for field in new_message.field}
        for old_field in old_message.field:
            new_field = new_by_number.get(old_field.number)
            if new_field is None:
                if not number_reserved(new_message, old_field.number) or (
                    old_field.name not in new_message.reserved_name
                ):
                    raise AssertionError(
                        f"removed field must reserve both name and number: {name}.{old_field.name}"
                    )
                continue
            if new_field.name != old_field.name:
                raise AssertionError(f"field number was reused: {name} #{old_field.number}")
            for attribute in ("type", "type_name", "label", "proto3_optional"):
                if getattr(new_field, attribute) != getattr(old_field, attribute):
                    raise AssertionError(
                        f"wire shape changed: {name}.{old_field.name} ({attribute})"
                    )
            if new_field.HasField("oneof_index") != old_field.HasField("oneof_index") or (
                new_field.HasField("oneof_index") and new_field.oneof_index != old_field.oneof_index
            ):
                raise AssertionError(f"oneof membership changed: {name}.{old_field.name}")
            moved = new_by_name.get(old_field.name)
            if moved is None or moved.number != old_field.number:
                raise AssertionError(f"field name moved to a new number: {name}.{old_field.name}")

    current_enums = enums(current)
    for name, old_enum in enums(baseline).items():
        new_enum = current_enums.get(name)
        if new_enum is None:
            raise AssertionError(f"baseline enum was removed: {name}")
        by_number = {value.number: value for value in new_enum.value}
        by_name = {value.name: value for value in new_enum.value}
        for old_value in old_enum.value:
            new_value = by_number.get(old_value.number)
            if new_value is None:
                if not enum_number_reserved(new_enum, old_value.number) or (
                    old_value.name not in new_enum.reserved_name
                ):
                    raise AssertionError(
                        f"removed enum value must reserve name and number: {name}.{old_value.name}"
                    )
                continue
            if new_value.name != old_value.name or by_name.get(old_value.name) != new_value:
                raise AssertionError(f"enum number/name was reused: {name}.{old_value.name}")


def check() -> None:
    baseline = descriptor_pb2.FileDescriptorProto.FromString(
        base64.b64decode(BASELINE.read_text().strip(), validate=True)
    )
    current = descriptor_pb2.FileDescriptorProto.FromString(pb.DESCRIPTOR.serialized_pb)
    check_compatibility(baseline, current)


if __name__ == "__main__":
    check()
