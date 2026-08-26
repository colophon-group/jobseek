# Immutable runtime v1 descriptor

`runtime-v1.descriptor.b64` is the canonical base64 encoding of the
`google.protobuf.FileDescriptorSet` produced once when the candidate v1 IDL was
introduced. `manifest.json` authenticates the descriptor and its exact source.

These files are not generated during normal checks and are immutable after the
introduction merge. `tools/check_compatibility.py` compiles only into temporary
directories, authenticates prior main, compares its baseline files byte for
byte, and structurally compares both its latest `runtime.proto` and the frozen
introduction with the candidate. This protects additive declarations made
after introduction as well as the original surface.
