# Go Ashby monitor

The native binary owns the single public posting API request and rich job
projection. It retains response accounting, 404/gone handling, TDM reservation
checks, truncation, the 64 MiB response limit and direct public-address egress.
The Python adapter retains the existing worker/writer integration.

## Configuration coverage

The adapter resolves `metadata.token` first, then the existing Python URL-token
rule for an exact `https://jobs.ashbyhq.com/<token>` board URL. Explicit tokens
also work on custom HTTPS career domains; the only fetched origin remains
`api.ashbyhq.com`. Tokens support ASCII letters, digits, underscores, hyphens,
dots and internal spaces, with the existing 128-character bound. Go encodes
spaces as `%20`; path/query/fragment delimiters and percent-encoded input are
rejected.

`org` and `board_token` are accepted only when they agree with the resolved
token. They do not change Python's token precedence. `blast_radius_floor` and
scraper settings remain owned by the existing processing path. A JSON-LD detail
configuration can coexist with a Go monitor; its configured detail behavior
is preserved. Monitor proxy, render, SSL overrides and unknown settings remain
outside this direct route.

`ASHBY_GO_PERCENT` keeps deterministic percentage routing (default zero);
`ASHBY_GO_BOARD_IDS` selects explicit boards. An explicit selection with an
unsupported configuration fails before fetching. Setting the percentage to
zero is the provider routing reversal; deployed exact selectors must still be
changed through the supported c1 cold rollback and mutation-lock procedure.

## Verification

`testdata/python_endpoints.json` freezes Python/httpx endpoints for all 57
production configurations excluded by the prior strict route, sampled on
2026-09-27. It retains request inputs and metadata key presence; unrelated
writer bookkeeping values are replaced with null. Tests also compare rich
output on identical bytes and reject changed transport/configuration options.
The read-only current registry preview covers all 935 enabled Ashby monitors.
This does not prove migration of Nord Security's separate JSON-LD detail path.

```sh
go test -race ./...
go vet ./...
go build -o /tmp/ashby-monitor-live ./cmd/live
python3 testdata/verify_installed.py /tmp/ashby-monitor-live
```

`--resolve-only --token '<token>'` prints the canonical request endpoint and
performs no network request. CI runs the frozen endpoint checks through the
installed binary with networking disabled. Natural production URL/content,
database and failure-rate evidence belongs in the migration checkpoint.
