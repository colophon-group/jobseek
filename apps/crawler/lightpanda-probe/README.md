# Candidate-only Lightpanda probe

This directory is a zero-production-egress feasibility harness for issue #8169,
a bounded child of #7959. It exercises only runtime-v1 browser navigation/DOM
rendering and side-effect-free JavaScript evaluation against deterministic
synthetic fixtures.

It is not a crawler backend, cannot receive production origins, and supplies no
Python/Go equivalence, compatibility, capacity, savings, or ROI claim. Results
are discarded on every unsupported capability or policy/error outcome.

The hosted integration creates an internal Docker network with no published
ports. The fixture server, pinned Lightpanda image, and probe runner are the only
members. The probe allowlists exactly `http://fixture:8080`; redirects and every
dynamically inserted subrequest are checked before dispatch against the same
origin and the fixture's robots rules. Every response is checked for the W3C
`TDM-Reservation` header, and the final main document is checked for the HTML
meta equivalent when the header is absent.

Response bytes are reserved from each fixture's mandatory `Content-Length`
header as soon as response headers arrive, then reconciled to CDP's encoded
byte count when loading completes. That keeps fail-closed cancellation results
deterministic without understating a response whose body was interrupted.

Immutable dependency and image pins live in `pins.json`. The candidate uses
Lightpanda 0.3.6 by OCI index digest, its exact linux/amd64 manifest, Go 1.24.0,
and chromedp v0.14.2.

## Local unit gates

```bash
go mod verify
go test -race ./...
go vet ./...
```

Docker-backed execution intentionally runs only in the focused hosted workflow;
it requires Linux, Docker, and the pinned amd64 image. No live origin or proxy
input is accepted.
