# Contract converters

v1 is the first authoritative runtime contract, so no inbound version
converter exists. A future `v2` must be accompanied by
`v1/converters/v1_to_v2` with generated-type Python and Go converters plus
nonempty `fixtures/roundtrip.json` and `fixtures/lossy.json`, and the exact
bidirectional `converter.json` manifest enforced by `tools/check_contract.py`.
Both language suites must execute the vectors when v2 is introduced. See
`../compatibility.md`.
