# Contract converters

v1 is the first authoritative runtime contract, so no inbound version
converter exists. A future `v2` must be accompanied by
`v1/converters/v1_to_v2` with generated-type Python and Go converters plus
shared positive, lossy, and rejected fixtures. See `../compatibility.md`.
