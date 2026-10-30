# AGENTS.md — Crawler

Read the root [AGENTS.md](../../AGENTS.md). Detailed commands, transport,
registries, and operational references live in
[docs/reference/crawler.md](../../docs/reference/crawler.md); load relevant
sections on demand.

## Setup and verification

Production processing uses the Dockerfile's `native` target and Go modules under
`go/`. Run `go test ./...` and `go vet ./...` from each changed module; use focused
race/integration checks for changed ownership, queue and persistence contracts.
Preserve real PostgreSQL/Redis and installed-image verification where required.
Read the [current migration checkpoint](../../docs/27-go-lightpanda-continuation-plan.md)
before changing production transports or retained legacy paths.

For Python offline tooling, run from `apps/crawler`:

```bash
uv sync --frozen --group dev
uv run --frozen pytest tests/                    # or focused affected tests
uv run --frozen ruff check src/ tests/
uv run --frozen ruff format --check src/ tests/
```

Use `uv run ws --help` / `uv run ws help` for command syntax. For company setup,
start `uv run ws task --issue <N>`. Use `WS_LOCAL=1` only when intentionally
avoiding Git/GitHub effects; it still writes CSVs and runs network probes.
Experiment in a disposable worktree; preserve unrelated data edits.

## Architecture and invariants

- `go/ordinary-worker/`: production monitor/detail execution through Go HTTP/API
  or self-hosted Lightpanda. `go/ordinary-queue/`: claims, fencing, scheduling,
  canonical persistence and route admission. `src/lua/`: atomic Redis operations.
- `go/job-enrichment/`: shared enrichment; `go/pdf-text/`: document extraction.
- Local Postgres is authoritative. `go/typesense-exporter/` owns Typesense
  writer-floor/CDC export, canonical registry sync, migrations, maintenance and
  lease recovery. `go/r2-drain/` uploads descriptions. No direct production
  mutation outside the documented maintenance/deployment workflows.
- `src/core/`, `src/workers/`, `processing/` and Python export/sync/drain paths
  support offline parity/tooling and the retained cold-reversal contract. They
  are not the default production processors; do not add new production-only
  behavior there or remove required rollback compatibility speculatively.
- `src/workspace/`: agent setup CLI, state, runtime templates, gates and KB.
  `src/labeller/`: sampling, task rendering, validation, merging and uploading.
- Inspect [architecture](../../docs/03-crawler-architecture.md),
  [data fields](../../docs/08-job-data-fields.md), and
  [Typesense](../../docs/11-typesense.md) before changing those contracts.

## Code conventions

- Go: retain bounded contexts, response sizes, retries, publisher policy and
  SQL/Redis authority checks. Use the existing queue/profile/enrichment contracts
  and focused Go regression tests. Production routes remain Go HTTP/API or
  Lightpanda; do not introduce Chromium or per-task Python fallbacks.
- Python: `from __future__ import annotations` in every module; async I/O via asyncpg
  and httpx. Use `asyncio.TaskGroup` for parallel work and Redis Lua for claims.
- Structured logging: `structlog.get_logger()` and `log.info("event.name", key=value)`.
- Raw SQL with `$1` parameters for asyncpg; no ORM or string-interpolated SQL.
- Preserve bounded retries/backoff and circuit-breaker semantics. Do not
  silently swallow failures or turn missing evidence into success.
- Bump `VERSION` for crawler runtime changes. Add regression tests for bugs and
  meaningful tests for changed contracts; run focused checks before broad CI.

## Monitor/scraper work

Exhaust configuration options before adding a type. Prefer extending a matching
existing type; document the failing input and verification evidence. Qualify its
production Go profile, actual URLs, worker lane and publisher behavior as well
as the offline workspace configuration. Batch related profile changes when
possible. The Python interfaces below support offline setup and parity:

- Monitor: implement async `discover(board, client)` returning `DiscoveredJob`
  objects or URLs; optionally `can_handle`. Register at module bottom and import
  it in `src/core/monitors/__init__.py`. Use `rich=True` only with full job data;
  use streaming discovery for large result sets.
- Scraper: implement async `scrape(url, config, http) -> JobContent`, register and
  import in `src/core/scrapers/__init__.py`. Add `parse_html` for HTML-only reuse
  and `needs_browser=True` only when required.
- Verify actual title/description/location samples and field formats, not just
  counts. Compare static HTML, rendered DOM, embedded JSON and API provenance.
  Inspect DOM step order in `flat.json`; prefer complete upstream data.
- `ws` suggestions are hypotheses. Explain conflicting evidence and obtain a
  discriminating observation before choosing a configuration.
- Parallel tests must name their board and config: `select monitor --as NAME`,
  `select scraper --as NAME`, `run monitor/scraper --config NAME`, all with
  `--board ALIAS`. Reload/retry if the state layer rejects a concurrent edit.

## Operations

The Hetzner Codex runner schedules recurring production routines.

Use [ADR 006](../../docs/adr/006-crawler-deploy-quiescence-and-rollback.md),
[maintenance](../../docs/16-hetzner-maintenance.md), and
[runner deployment](../../docs/18-codex-automation-deployment.md). Preserve
exact revision/digest identity, mutation locks, writer quiescence and complete
rollback. Do not deploy via live rsync, `:latest`, or individual writer restarts.

Production paging is disabled by operator decision. Do not add notification
routes, contacts, tests, schedules or activation commands without a new explicit
operator decision. Grafana rule state remains visible; daily Codex error review
owns deduplicated issue delivery. Keep exporter metric attribution and collector
cardinality limits intact. Credentials stay scoped to their consumers; the
Typesense bootstrap key is root-only and Codex must not have Docker access.
