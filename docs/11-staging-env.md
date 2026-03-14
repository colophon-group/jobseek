# Staging Environment Outline (Fly.io + Supabase)

> Planning-only guide. **Do not deploy from this document**.

This outlines a safe staging setup parallel to production for `jobseek`.

## 1) Topology

- **App runtime (Fly.io):** separate staging app, `jobseek-staging`
- **Database/Auth/Storage (Supabase):** separate staging project (same region where possible)
- **Code branch flow:** staging follows a non-main branch (e.g. `develop` or release candidate)

## 2) Fly.io staging shape

Use `apps/crawler/fly.staging.toml` as the staging config baseline.

Recommended staged differences vs prod:

- App name: `jobseek-staging`
- Lower scale and machine count
- Explicit staging secrets only (never reuse prod credentials)
- Optional fixed hostname for smoke tests

### Suggested staging secrets (Fly)

- `DATABASE_URL` → staging Supabase Postgres URL
- `SUPABASE_URL` → staging Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` → staging-only service key
- Any crawler/provider API keys as staging-scoped values

## 3) Supabase staging shape

Create a dedicated staging Supabase project and keep it linked via CLI when needed:

```bash
# Example only; do not run blindly
supabase link --project-ref <staging_project_ref>
```

Schema/state as code comes from:

- `supabase/config.toml`
- `apps/web/drizzle/*.sql` (via `db.migrations.schema_paths`)

### Staging DB policy

- Never point staging at prod DB.
- Run schema migrations against staging first.
- Use synthetic or scrubbed data in staging.

## 4) Promotion path (no-deploy checklist)

1. Merge feature PRs to staging branch.
2. Validate crawler behavior against staging Supabase.
3. Verify metrics + logs on Fly staging app.
4. Promote same commits to production branch.
5. Apply same migration set to production.

## 5) Guardrails

- Separate secrets per environment.
- Separate Supabase refs and service keys.
- Explicit app names (`jobseek` vs `jobseek-staging`).
- CI should fail if prod secrets/project refs appear in staging config.

## 6) Optional next step (still no deploy)

If wanted, add CI jobs that only **validate** staging config integrity:

- parse `fly.staging.toml`
- verify required env var names are documented
- verify Supabase schema path points to Drizzle SQL files
