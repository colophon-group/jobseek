# Immutable runtime v1 baseline

`runtime-v1.descriptor.b64` is the protobuf `FileDescriptorProto` frozen when
runtime v1 was introduced. It is never regenerated after v1 lands. CI compares
the current generated descriptor against it: existing messages, enum values,
field names/numbers/wire types, optional presence, and oneof membership cannot
change. A removed field or enum value must reserve both its name and number.

Additive wire-compatible fields remain possible only when they do not create
new required semantics. Any change to the baseline itself or to a required
semantic belongs in `v2` with the converters and deployment proof described in
`compatibility.md`.
