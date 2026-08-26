# Versioning and compatibility

Within v1, producers may add fields with new field numbers and consumers must
preserve unknown protobuf fields while forwarding/storing. Field numbers and
meanings are never reused. Required semantic behavior is not added through an
optional field. Enum zero remains `UNSPECIFIED`.

Unknown enum numerics survive protobuf decode for forwarding, but a v1 runtime
validator rejects them before policy or commit-eligible state changes. A newer
optional diagnostic enum may be ignored only where this document explicitly
allows it; error, outcome, capability, kind, terminal, and origin enums never
default into behavior.

The introduction descriptor is frozen at
`baseline/runtime-v1.descriptor.b64`. `generate.sh --check` runs the structural
compatibility gate against it. Removing a field or enum value requires
reserving both its name and number; renumbering, retyping, changing optional
presence, or changing oneof membership fails. The baseline file itself is
immutable after the initial v1 merge.

Any removed/renamed field, changed meaning, tightened previously-valid
behavior, changed hash/canonicalization rule, or new required semantic creates
`contracts/v2`. The same PR must add deterministic converters in both Python
and Go under `converters/v1_to_v2`, converter fixtures (including lossy/error
cases), and a deployment plan proving the pinned rollback artifact can still
read/write the live shape. `tools/check_contract.py` rejects a later version
directory without the adjacent converter directory.

There is no converter for the provisional JSON Schemas: they were temporary,
had no Go binding, and had no production consumer. The existing in-process
Python runtime seams remain internal adapters, not v1 wire messages.
