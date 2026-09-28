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

## Configured JOIN detail extraction

`JOIN_GO_DETAIL_PERCENT` defaults to 100 and uses a stable board-ID bucket.
Selection requires a direct JOIN job URL, the exact Next.js job path and
implemented field expressions. All 268 enabled JOIN Next.js detail configs
are admitted: 265 mapped configurations across four forms and three with no
fields. The latter return an empty `JobContent` before HTTP. Eight JSON-LD
boards and any separately resolved fallback/browser steps retain their owners.
A provided B0 runtime takes precedence. Set the detail percentage to zero through
the supported cold mutation procedure to reverse this route.

`--detail` accepts a bounded JSON stdin request and owns one detail fetch with
redirects/cookies and verified public HTTPS transport. It adds no retry: a
non-200 response or absent/malformed Next.js payload returns empty content,
matching Python instead of producing a tombstone. The full HTML body is parsed,
within the 16 MiB response bound. The bridge preserves scalar/list conversion,
JMESPath OR truthiness, trailing-comma cleanup and last matching script behavior.
Forty frozen cases from the actual Python detail parser exercise the four config
forms and unusual values, attributes, duplicate scripts, malformed payloads and
HTML past the monitor's four-million-character prefix. Installed-image CI repeats
these cases offline with `--parse-detail`.

Publisher metadata now matches the shared checker's bounded 65,536-character
excerpt, last valid literal reservation, source, repeated attributes and companion
policy. Fourteen frozen policy cases cover metadata precedence and Unicode bounds;
immediate header reservation rejection remains the existing transport behavior.

`JOIN_CAPTURE_SLUGS` also selects native passive snapshots from already fetched,
policy-checked monitor/detail responses. Monitor pages 1/2 are retained once;
details retain at most four jobs per slug in mode 0600 envelopes containing URL,
base64 response bytes and SHA-256. Nothing is refetched or overwritten. Natural
production captures provide the same-byte output comparison; these fixtures
alone do not establish live parity or whole-lane resource improvement.
