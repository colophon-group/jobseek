# Deployment

## Environment variables & turbo.json

Turborepo only forwards environment variables to tasks that explicitly declare them. If a server-side module reads `process.env.SOME_KEY` and `SOME_KEY` is not listed in `turbo.json`, the build will either fail (if the code throws on missing values) or silently use `undefined`.

### How it works

In `turbo.json`, each task has an `env` array:

```jsonc
{
  "tasks": {
    "build": {
      "env": [
        "BETTER_AUTH_SECRET",
        "RESEND_API_KEY",
        // ...every env var the build touches
      ]
    }
  }
}
```

Vercel sets these variables in the project settings, but **turbo will not pass them to the build unless they are listed here**. Vercel even prints a warning at the end of failed builds listing the missing variables — check for it.

### When adding a new env var

1. Add it to `.env.local` for local dev
2. Add it to Vercel project settings (Settings > Environment Variables)
3. **Add it to the `env` array in `turbo.json`** — this is the step that gets forgotten

### Narrow-results runtime

The Jev filter fails closed unless its route, cache-key secret, and provider
credential are present. Configure these in the intended Vercel
environment and keep the two switches disabled until the database migrations,
Typesense stable-order receipt, and Workflow deployment are verified:

```text
AI_FILTER_ENABLED=true
AI_FILTER_JEV_1_13_0_ENABLED=true
AI_FILTER_CACHE_HMAC_SECRET=<at least 32 bytes>
TYPESAFE_AI_TOKEN=<secret>
```

`AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS` and
`AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS` are optional. Set either to a
positive integer nanodollar amount to enforce that monthly ceiling, or leave
both unset to keep Jev spend uncapped. Both scopes still have durable spend
ledgers and each call has a bounded reservation. Execution also requires
both switches, a valid credential, and Pro entitlement.

Optional concurrency controls are `AI_FILTER_MAX_SEGMENTS_PER_USER` (default 2)
and `AI_FILTER_MAX_SEGMENTS_PROJECT` (default 20). Runtime names are forwarded
by both web build tasks in the root `turbo.json`. The full execution and rollback
contract is in `docs/24-ai-filter.md`.

### Defensive coding

Even with `turbo.json` configured correctly, prefer lazy initialization for SDK clients that throw on missing keys:

```ts
// bad — throws at module load during build
const client = new SomeSDK(process.env.API_KEY);

// good — only throws when actually called at runtime
let _client: SomeSDK | null = null;
function getClient() {
  if (!_client) _client = new SomeSDK(process.env.API_KEY);
  return _client;
}
```

Next.js evaluates server modules during `Collecting page data` at build time. A top-level constructor that requires an API key will crash the build even if the key is set in Vercel — because turbo didn't forward it.

### Continuous narrowed watchlist refresh

Apply `0103_ai_filter_freshness` with the protected routine migration workflow
before deploying this revision. A dedicated `AI_FILTER_REFRESH_SECRET` protects the
`/api/internal/ai-filter-refresh` scheduler endpoint; the existing AI switches, credential,
entitlement, budgets and concurrency limits also gate background execution.
Enabled saved narrowing requests authorize ongoing matching while owners are away.
Verify a new eligible job receives a persisted decision without visiting its
watchlist. Large existing backlogs clear in bounded newest-first batches rather
than delaying new arrivals behind historical pagination.

The protected `deploy-ai-filter-refresh.yml` workflow installs a persistent,
resource-limited HTTP driver on the existing Hetzner host. It continues active
work two seconds after each bounded response, backs off idle reads to 30 seconds,
and uses the same database singleton as immediate owner wakes. Migration 0105
adds expiring first-page priority and accepted-result targets. Apply it through
the routine workflow before promoting this web revision; the production verifier
blocks promotion while the database is behind. Activation disables the old
minute timer and enables the service. Pause disables both units. The runner has
only its endpoint bearer and does not restart crawler writers.
See [rollout and rollback](../../../docs/24-ai-filter.md#interactive-worker-rollout).

Before the web deployment, provision one random `AI_FILTER_REFRESH_SECRET` in
both the production Vercel project and the production GitHub environment. Turbo
forwards it in both web build tasks. The existing sensitive `CRON_SECRET` cannot
be read back and is retained for the other internal endpoints. The dedicated
refresh bearer grants access only to refresh dispatch.

### Maintenance and subscription pauses

Source controls are in `src/config/service-availability.json`. Narrowed
maintenance blocks Workflow creation before `start()` and suspends foreground
evaluation. Purchases are also independently pausable. Existing billing is a
separate provider operation via `pnpm billing:control audit|pause|resume`; editing
source controls alone does not stop renewals. The protected timer workflow
defaults to `action=pause`; `action=activate` refuses active maintenance.
See [the maintenance runbook](../../../docs/runbooks/narrowed-maintenance.md) for
repeated pause/resume operations, existing provider sessions and old Workflow
runs, and verification requirements.
