# Murmur HTTP shim routes

This directory hosts the seven Next.js app-router routes that serve the
demo-path subcommands listed in Murmur DESIGN.md §4.2:

| Route | Subcommand | Lib function |
|---|---|---|
| `POST /api/murmur/probes/monitor` | `probe monitor` | `probe_monitor` |
| `POST /api/murmur/run/monitor` | `run monitor` | `run_monitor` |
| `POST /api/murmur/probes/scraper` | `probe scraper` | `probe_scraper` |
| `POST /api/murmur/run/scraper` | `run scraper` | `run_scraper` |
| `POST /api/murmur/select/monitor` | `select monitor` | `select_monitor` |
| `POST /api/murmur/select/scraper` | `select scraper` | `select_scraper` |
| `POST /api/murmur/feedback` | `feedback` | `feedback` |

Tracking issue: [colophon-group/jobseek#2759](https://github.com/colophon-group/jobseek/issues/2759).

## IPC pattern (the language boundary)

The route handlers are TypeScript; the lib functions live in
`apps/crawler/src/workspace/lib/*.py`. We chose **pattern (a):
subprocess invocation** for the demo path:

- Each route call spawns a fresh `python3 -m src.workspace.lib.cli_shim`
  child, pipes a JSON payload on stdin, and reads a JSON envelope on
  stdout.
- The shim builds a `BoardConfigState` (probe/run) or a
  `PostgresClaimKV` (select/feedback/run/probe) and dispatches to the
  corresponding lib function.
- Typed lib exceptions are mapped to stable `errors[]` tokens before
  the envelope is written; full tracebacks go to stderr only.

### Why pattern (a) and not (b) (long-lived sidecar)

- Probe and run already need Playwright + the entire crawler module
  tree. The 200–500 ms Python startup is in the noise compared to the
  multi-second Playwright work the lib does — these are the dominant
  call shapes by latency.
- `select` and `feedback` are sub-second otherwise; spawning doubles
  their wall-clock latency, but the result still lands inside the M0
  15 s subcommand budget (DESIGN.md §3.6, §7.1) by an order of
  magnitude.
- Pattern (b) would add a second container to the Hetzner deploy
  (DESIGN.md §6.2 today: `murmur` + `cloudflared` only), an extra
  health-check, and a second process-manager surface — out of scope
  for the demo's deploy footprint.
- If rehearsal shows the per-call subprocess startup is the bottleneck,
  swap the implementation behind `_lib/invoke-lib.ts`'s `defaultInvoker`
  to a long-lived Unix-socket worker without changing route shape.

## Request handling order (M0 contract)

Every route enforces, in order:

1. **Bearer auth** — `Authorization: Bearer <MURMUR_TOKEN>` checked with
   `crypto.timingSafeEqual`. Wrong / missing → HTTP 401 with body
   `{ ok: false, errors: ["unauthorized"] }`.
2. **Murmur headers** — `X-Murmur-Claim-Token` and
   `X-Murmur-Subcommand`. Empty string is treated as missing. Missing
   → HTTP 400 with `{ ok: false, errors: ["missing_header:..."] }`.
3. **Body parse + schema validation** — body must be a JSON object that
   matches the vendored input schema in `_lib/schemas.ts`.
   Validation failure → HTTP 400 with per-field
   `errors: ["schema:/<path>:<token>"]` entries.
4. **SSRF allowlist** — for routes that carry URL fields (declared in
   the route's `urlFields`), each is run through `validateUrl()` from
   `@/lib/murmur/ssrf`. Non-allowlisted host → HTTP 200 with
   `{ ok: false, errors: ["url_not_allowed"] }` (or one of the
   `validateUrl` error codes).
5. **Lib invocation** — `invokeLib()` returns an envelope verbatim.
6. **Envelope return** — never a 5xx for a typed lib failure; never a
   raw stack trace.

## Error mapping (Python → envelope)

| Typed exception (`apps/crawler/src/workspace/lib/exceptions.py`) | Envelope token |
|---|---|
| `WsBoardNotFound` | `board_not_found` |
| `WsConfigMissing` | `config_missing` |
| `WsConfigInvalid` | `config_invalid` |
| `WsProbeFailed` | `probe_failed` |
| `WsMonitorRunFailed` | `monitor_run_failed` |
| `WsScraperRunFailed` | `scraper_run_failed` |
| `WsFeedbackIncomplete` | `feedback_incomplete` |
| Any other `Exception` | `internal_error` (trace logged, never returned) |

## Required environment

| Variable | Purpose |
|---|---|
| `MURMUR_TOKEN` | The shared bearer the agent sends. Routes fail closed if unset. |
| `MURMUR_DB_DSN` | Postgres DSN for the `PostgresClaimKV` (the same DB jobseek's TS app uses; J3 added the `murmur_claim_kv` table). |
| `MURMUR_PY` (optional) | Python interpreter path; defaults to `python3`. |
| `MURMUR_CRAWLER_ROOT` (optional) | Path to the crawler app root; defaults to `<cwd>/../crawler`. |
| `MURMUR_INVOKE_TIMEOUT_MS` (optional) | Wallclock cap per call; defaults to 30 000 ms. |
