# Adjacent contract converters

v1 is the first authoritative runtime contract, so it has no inbound
converter. The first `contracts/v2/runtime.proto` must be accompanied by this
exact layout:

```text
v1/converters/v1_to_v2/
├── converter.json
├── converter.py
├── converter.go
└── fixtures/
    ├── roundtrip.json
    └── lossy.json
```

`converter.json` has the exact value below:

```json
{
  "schema_version": 1,
  "from": "crawler.runtime/v1",
  "to": "crawler.runtime/v2",
  "directions": ["v1_to_v2", "v2_to_v1"],
  "python": "converter.py",
  "go": "converter.go",
  "roundtrip": "fixtures/roundtrip.json",
  "lossy": "fixtures/lossy.json"
}
```

Both programs read one JSON value from standard input, accept one direction as
their sole argument, and write exactly one JSON value to standard output.
Production converters must use their generated v1/v2 types internally.

`roundtrip.json` contains `{"schema_version":1,"cases":[...]}` with nonempty
cases shaped as `{"name":...,"v1":...,"v2":...}`. Each v1 and v2 value must
differ, and both conversion directions must reproduce the opposite value.
`lossy.json` uses nonempty cases shaped as
`{"name":...,"direction":...,"input":...,"expected":...,"reason":...}`; input
and expected must differ and the loss must be explained.

The gate compiles Python, compiles Go through `go run`, and executes all vectors
in both languages. Echo programs, empty fixtures, one-way manifests, invalid
JSON, divergent outputs, and declared-but-unexecuted implementations fail.
