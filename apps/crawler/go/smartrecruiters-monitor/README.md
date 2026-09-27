# SmartRecruiters Go extraction

The production binary owns ordinary publication monitoring, localized job
monitoring (H&M URL template, `job-v1`, `job-location-v1`), and scheduled
SmartRecruiters detail extraction. The existing writer still owns enrichment,
PostgreSQL persistence and scheduling; this module does not claim their retirement.

`cmd/live` accepts one JSON object on stdin, at most 65,536 bytes. Default mode
is `monitor` with `board_url` and `metadata`; `detail` takes a posting `url`.
`replay` accepts captured `responses` keyed by endpoint instead of HTTP, and
`parse-detail` accepts a `posting` object. Neither offline mode opens a socket.
All modes emit one JSON result and return nonzero on failure.

## Preserved behavior

- List pages of 100, complete inventory validation, one fresh snapshot retry
  for duplicate IDs or changing totals, and the 50,000-publication truncation flag.
- `job-location-v1` cap of 500 publications (other modes retain the 50,000
  limit), 12 concurrent detail requests, inactive
  publication snapshot retry, language ordering, location/provider-code identity,
  publication membership, all rich fields and canonical source identities.
- Three bounded monitor HTTP attempts for retryable statuses/transport/JSON
  errors. Scheduled detail makes exactly one request, preserving empty content
  on non-200 statuses; no hidden Python retry/fallback is introduced.
- Public DNS/IP allowlist, verified TLS, no redirect/proxy fallback; 2 MiB list,
  1 MiB detail and 512 MiB aggregate body limits. Scheduled detail now uses the
  same body and TDM header/meta protections as the monitor (the prior standalone
  Python scraper did not explicitly enforce those protections).
- Complete buffered output only after successful discovery. Existing confirmed
  drop and empty-board policies remain in the writer. Cancellation stops and
  joins Go requests and terminates/reaps the compatibility child process.

## Rollout

`SMARTRECRUITERS_GO_BOARD_IDS` explicitly selects monitor boards.
`SMARTRECRUITERS_GO_PERCENT` defaults to zero and admits only direct supported
configurations with three positive recent inventories (at most 500 each).
`SMARTRECRUITERS_GO_DETAIL_BOARD_IDS` separately selects scheduled detail boards.
Unsupported configurations fail closed in a selected runtime. No selector is
activated by installing the image.

Use the supported B0 cold rollback and exact selector cleanup before deploying
or changing selectors. Stage against the exact deployed revision and reactivate
c1. Observe natural monitor/detail work and database results; never force due
or run parallel live Python/Go requests to establish parity.

## Bounded verification

`go test -race ./...`, `go vet ./...`, and `go mod tidy -diff` cover 60 frozen
Python monitor cases, 26 detail cases, retries, policy, bounds and cancellation.
Generate the fixtures offline with `testdata/generate_python.py` using the
crawler Python environment. `testdata/verify_installed.py <binary>` verifies
the same 86 cases through the real binary; CI runs it inside the installed
crawler image with networking disabled. Fixtures are synthetic, not production
resource or output evidence. Runtime completion events include URL/field hashes
and request/byte accounting for subsequent natural production evidence.
