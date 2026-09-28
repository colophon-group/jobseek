# Go JOIN monitor

The selected route owns the complete URL inventory of a canonical
`https://join.com/companies/<slug>` board. `JOIN_GO_PERCENT` defaults to 100;
zero restores Python routing. Exact board selectors remain available. Unsupported
monitor/transport configuration is rejected before a request; a selected Go
failure never triggers a second Python publisher fetch.

The binary preserves the configured board URL and slug, Python's HTML headers,
cookie session, redirect limit, three-attempt page retries, `__NEXT_DATA__` path,
four-million-character HTML prefix, `idParam` conversion, pagination and required
page failures. Pagination uses chunks of ten with at most five requests in
flight. A required-page failure publishes no inventory. First-page 404/410 and
TDM reservation retain their typed outcomes. Each redirect is counted, every
target uses the public-address transport, and body reads have a 30-second
inactivity timeout.

Response bodies are bounded to 16 MiB per page and discarded after parsing;
there is no aggregate response-byte ceiling across pages. Inventory boundaries
remain 10,000 pages and 50,000 unique URLs. The bridge bounds child output while
reading and terminates/reaps on cancellation or output overflow. Description
scraping, scheduling, enrichment and database writes are separate migration work.

`testdata/generate_python.py` freezes 52 cases using the actual Python monitor
helpers and mocked HTTP, including numeric/boolean IDs, empty and required
pages, malformed JSON, count coercion and Unicode character boundaries.
`go test -race ./...` compares the native parser with those frozen outputs and
tests redirects/cookies/headers, HTTP/2, retries, cancellation and an inventory
whose response bodies total more than 64 MiB. CI also runs
`testdata/verify_installed.py` against the installed binary in a network-disabled
container through `--parse-page`, which constructs no HTTP client.

This establishes parser and transport coverage. Natural production runs and
same-workload whole-lane resource measurements remain distinct evidence.
