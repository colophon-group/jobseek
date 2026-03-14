# Supabase as Infrastructure-as-Code

This repo tracks Supabase configuration in git under `supabase/`.

## Source of truth

- **Supabase config:** `supabase/config.toml`
- **Database SQL schema/migrations:** `apps/web/drizzle/*.sql`
  - wired through `db.migrations.schema_paths` in `supabase/config.toml`

This keeps local and hosted Supabase DB shape reproducible from the repository.

## Common workflows

From repository root:

```bash
# Link CLI to hosted project once
supabase link --project-ref rbjzdlsdovasviziflbp

# Inspect changes vs remote DB
supabase db diff

# Apply repo SQL state to linked project
supabase db push
```

For local development:

```bash
supabase start
supabase db reset
```

## Notes

- `supabase/.temp/*` is intentionally gitignored (machine-local state).
- `seed.sql` is intentionally minimal; runtime datasets are managed by app sync jobs.
