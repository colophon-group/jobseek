# `apps/crawler/murmur` — pipeline defs + validator

Authored as part of jobseek#2760 (P1, demo-minimum add-company pipeline).

## Layout

```
pipelines/
  add-company.yaml             # the demo pipeline def (jobseek-add-company)
scripts/
  validate-pipeline.ts         # Ajv validator + route existence checker
  pipeline-def.schema.json     # vendored copy of the M0 JSON Schema
  validate-pipeline.test.ts    # vitest tests for the validator
  fixtures/
    broken.yaml                # deliberately invalid fixture
```

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

# Tests
pnpm --filter @jobseek/murmur-pipelines test
```

The `--no-routes-check` flag in the default `validate-pipeline` script is a
**temporary** escape hatch while jobseek#2759 (J5) has not yet shipped the
`/api/murmur/*` Next.js routes. **Remove the flag once #2759 merges** and rely
on the `validate-pipeline:full` shape.

## Provider enum

The `provider` enum in `list-boards`'s output_schema is restricted to the
demo-relevant subset of `monitor_type` values from
`apps/crawler/data/boards.csv`. To extend it, audit that CSV first.
