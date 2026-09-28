# Go JSON-LD details

`jsonld-detail-live` owns the shared JSON-LD parser and direct verified HTTP
transport. `--parse` consumes `{url, config, html}` on stdin, emits the ten
JobContent fields, and constructs no HTTP client. Lightpanda's one-shot adapter
uses this mode only after validating the retained HTML manifest and assignment.
Without `--parse`, the binary owns HTTP, cookies, redirects, provider status
retries, content retry, iCIMS iframe recovery, encoding, publisher checks and
optional CSS description extraction. Generic defaults, fallback scheduling,
normalization and persistence still run in the existing pipeline.

The native parser covers entity/control-character/CDATA/Talemetry repair,
ordered Python string representations, salary, metadata, locations, employment,
ignore flags and exact URL defaults. Freeze its canonical oracle from the
crawler directory with `PYTHONPATH=. python go/jsonld-detail/testdata/generate_python.py`.
The installed oracle uses only frozen inputs and `--parse`; it never fetches.
Native tests also compare required CSS inner HTML against Lexbor serialization.

`JSONLD_GO_DETAIL_PERCENT` defaults to 100. Selection admits eligible HTTP(S)
URLs/configs; render, proxy, skip-SSL, Workday recovery and unknown transports
remain explicit migration work. Selected Go failures never fall back to Python
fetching. The September 28 registry snapshot admits 700 of 802 primary JSON-LD
configs; 79 rendered (13 also proxied), 20 other proxied, two skip-SSL and one
Workday recovery config remain outside this direct transport.

`JSONLD_CAPTURE_HOSTS` passively retains four policy-checked parser inputs per
exact selected host in `/tmp/jobseek-jsonld-go-detail-<host SHA prefix>-<slot>.json`.
Exclusive mode 0600 snapshots contain URL, UTF-8 parser input and SHA-256;
no extra publisher request is made. Fixture parity does not prove production
output, database parity or whole-lane resource improvement.
