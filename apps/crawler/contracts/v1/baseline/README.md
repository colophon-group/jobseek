# Immutable runtime v1 introduction descriptor

`runtime-v1.descriptor.b64` is the canonical base64 encoding of the candidate
v1 `FileDescriptorSet`. `manifest.json` authenticates the descriptor, source,
and exact pre-introduction `main` commit.

There is intentionally no baseline-writing command. Normal checks compile only
into temporary directories. After introduction, the checker reads the baseline
and `runtime.proto` from authenticated prior main before comparing the current
descriptor, so rewriting these committed files together cannot conceal a
breaking change.
