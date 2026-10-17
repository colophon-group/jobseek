# Native ATS inventory

This command replaces the crawler's `ats-inventory` Python entrypoint in images
marked `org.jobseek.crawler.native-ats-inventory=v1`. The host runner preserves its
existing two phases, immutable image and registry snapshot, write gate, credentials,
resource limits, timeout, and completion event. Only retained older images use the
old entrypoint during the supported rollback window.

The command reads published data from the existing trusted Jobhive artifact
namespace. It does not import or execute upstream scraper code. It preserves the
47-family compatibility registry, checksum-addressed inventory cache schema 1,
impact cache schema 1 / algorithm 3, and SQLite candidate ledger schema and WAL
behavior. Existing Python-created cache objects and ledgers remain readable.

`--impact`, support issue planning/creation, candidate plans, queue reports, dry
runs, and capped refill retain their existing flags and defaults. Installed
registry defaults are `/app/data/companies.csv` and `/app/data/boards.csv`; checkout
callers should pass `--companies-file` and `--boards-file` explicitly. GitHub tokens
remain injected through the existing environment/file options. Unknown create
outcomes are reconciled through durable markers before another create is allowed.

Validation uses synthetic fixtures produced by the original Python implementation
at `40a1f6cbc5ad0c133a55703b3d67f5109a5faf41`: inventory refresh and recovery,
Parquet summaries and byte-identical impact cache digests, tenant and candidate
identity, local/GitHub/ledger deduplication, issue text, claims and queue caps.
The Python-created SQLite fixture tests compatibility with retained state. Full
CLI tests use a fake HTTP transport and never create real GitHub issues.

Run from this directory:

```sh
go test -race ./...
go vet ./...
go mod tidy -diff
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build .
```

The required crawler CI exercises this module and the installed image entrypoint.
The same immutable binary supplies the host runner's status, bounded logging,
GitHub App token, registry snapshot and network probe commands. The host installer
preflights its capability marker before changing services, includes the copied
binary and helper launchers in the existing rollback manifest, and accepts the
report-only run before disarming rollback. Native images never fall back to
Python after a helper failure. The exact old network probe remains available only
when an explicitly selected retained image lacks the native host-tools marker.
The initial pre-native deployment lock bootstrap is also retained during that
recovery window. Offline Python helper files remain as compatibility oracles.

Broader crawler coverage, freshness, resource comparisons, recovery, and final
Python/browser retirement remain governed by the repository migration plan.
