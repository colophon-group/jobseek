# `apps/crawler/murmur` — pipeline defs, validator, registrar

Authored as part of jobseek#2760 (P1, demo-minimum add-company pipeline) and
jobseek#2761 (P2, registration script).

## Layout

```
pipelines/
  add-company.yaml             # the demo pipeline def (jobseek-add-company)
scripts/
  validate-pipeline.ts         # Ajv validator + route existence checker (P1)
  validate-pipeline.test.ts    # vitest tests for the validator
  pipeline-def.schema.json     # vendored copy of the M0 JSON Schema
  register.ts                  # POST /pipelines registrar (P2)
  register.test.ts             # vitest tests for the registrar
  fixtures/
    broken.yaml                # deliberately invalid fixture
```

## Division of labour

- **`validate-pipeline.ts`** does *local* schema + route existence checking
  using the vendored M0 JSON Schema. Run it on every pre-commit /
  pre-deploy.
- **`register.ts`** does *no* validation locally; it only reads the YAML
  raw and POSTs it to a running Murmur, which performs its own schema
  validation. The two scripts deliberately split labour so a stale local
  schema vendoring can never silently bypass the server's checks.

## Schema source

`scripts/pipeline-def.schema.json` is a verbatim copy of
[`docs/contracts/pipeline-def.schema.json`](https://github.com/colophon-group/murmur/blob/adfb73e/docs/contracts/pipeline-def.schema.json)
from `colophon-group/murmur` at commit `adfb73e6`. Re-vendor when the upstream
schema changes; CI should keep these in sync (see jobseek#2761).

## Commands

```bash
# Validate the committed YAML (skips route existence — flag is temporary; see below)
pnpm --filter @jobseek/murmur-pipelines validate-pipeline

# Full check with routes (will fail until jobseek#2759 lands the /api/murmur/* routes)
pnpm --filter @jobseek/murmur-pipelines validate-pipeline:full

# Register the committed YAML against a running Murmur publisher.
# Requires MURMUR_URL and MURMUR_TOKEN env vars (see contracts.md §2).
# Idempotent: M4 upserts on `id` (last-write-wins).
MURMUR_URL=https://murmur.colophon-group.org \
  MURMUR_TOKEN=<bearer> \
  pnpm --filter @jobseek/murmur-pipelines register-pipeline

# Tests
pnpm --filter @jobseek/murmur-pipelines test
```

There are also root-level proxies: `pnpm validate-pipeline` and
`pnpm register-pipeline`.

The `--no-routes-check` flag in the default `validate-pipeline` script is a
**temporary** escape hatch while jobseek#2759 (J5) has not yet shipped the
`/api/murmur/*` Next.js routes. **Remove the flag once #2759 merges** and rely
on the `validate-pipeline:full` shape.

## `register.ts` — wire format

The script POSTs the body shape M4 expects (see
`docs/contracts.md` §1 and Murmur's `src/api/publisher/pipelines.ts`):

```json
{ "id": "jobseek-add-company", "def_yaml": "<raw YAML string>" }
```

`def_yaml` is the file's bytes verbatim — no JSON conversion locally;
Murmur parses YAML server-side. The `id` is read from the YAML's
top-level `id` field once, locally, just to satisfy the M4 body shape.

Exit codes:

- `0` — pipeline registered (HTTP 2xx + `{ ok: true }` envelope)
- `1` — registration failed (non-2xx, transport error, or `{ ok: false }`)
- `2` — usage / I/O / config error (missing argv, missing env, unreadable file)

The script **never logs `MURMUR_TOKEN`**. Missing-env messages reference
the variable *name* only.

## Provider enum

The `provider` enum in `list-boards`'s output_schema is restricted to the
demo-relevant subset of `monitor_type` values from
`apps/crawler/data/boards.csv`. To extend it, audit that CSV first.
