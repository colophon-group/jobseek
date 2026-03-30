# Neon → Supabase Migration Plan

Both are PostgreSQL. Schema, queries, and migrations stay unchanged. Only the connection layer needs updating.

## What Changes

### 3 files with Neon-specific code

| File | Change |
|------|--------|
| `apps/web/src/db/index.ts` | Replace `neon()` HTTP driver → `postgres` TCP driver |
| `apps/web/src/db/seed.ts` | Same — replace `neon()` → `postgres` |
| `apps/web/package.json` | Remove `@neondatabase/serverless`, already has `postgres` |

### Connection driver swap

Before (Neon HTTP):
```ts
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
const db = drizzle(neon(process.env.DATABASE_URL), { schema });
```

After (standard postgres):
```ts
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
const db = drizzle(postgres(process.env.DATABASE_URL), { schema });
```

### Environment variables

| Neon | Supabase equivalent |
|------|---------------------|
| `DATABASE_URL` (pooled, pgbouncer) | `DATABASE_URL` (pooled via Supavisor, port 6543) |
| `DATABASE_URL_UNPOOLED` (direct) | `DATABASE_URL_UNPOOLED` (direct, port 5432) |

Supabase uses the same pooled/unpooled pattern. Update the connection strings in `.env.local` and Vercel env vars.

## What Doesn't Change

- **Schema** (`src/db/schema.ts`) — standard `pgTable`, all types supported
- **Migrations** (`drizzle/`) — all SQL is standard Postgres, runs as-is
- **Migration runner** (`src/db/migrate.ts`) — already uses `postgres` package
- **Search queries** (`src/lib/search/postgres.ts`) — standard `tsvector`, `tsquery`
- **Drizzle config** (`drizzle.config.ts`) — already standard, uses `DATABASE_URL_UNPOOLED`
- **Crawler** (`apps/crawler/src/db.py`) — uses `asyncpg`, database-agnostic
- **Better Auth** — uses Drizzle adapter, no driver dependency

## Data Migration

### Option A: pg_dump / pg_restore (recommended)

```bash
# 1. Dump from Neon
pg_dump "$NEON_DATABASE_URL_UNPOOLED" -Fc -f jobseek.dump

# 2. Create Supabase project, get connection string

# 3. Restore to Supabase
pg_restore -d "$SUPABASE_DATABASE_URL" --no-owner --no-acl jobseek.dump
```

### Option B: Supabase migration tool

Supabase has a built-in migration tool that can pull from an existing Postgres instance directly.

## Steps

1. **Create Supabase project** (eu-central-1 to match current Neon region)
2. **Dump & restore** data from Neon
3. **Update connection strings** in `.env.local` and Vercel
4. **Swap driver** in `index.ts` and `seed.ts` (3 lines each)
5. **Remove `@neondatabase/serverless`** from `package.json`
6. **Update crawler `.env.local`** with new `DATABASE_URL`
7. **Verify** — run `pnpm build`, `pnpm db:migrate`, crawler smoke test
8. **Cut over** — update Vercel env vars, redeploy

## Supabase Free Tier

| Resource | Limit |
|----------|-------|
| Database size | 500 MB |
| Bandwidth | 5 GB |
| Edge functions | 500K invocations |
| Auth | 50K MAU |
| Storage | 1 GB |

After R2 description migration: ~90 MB DB — well within 500 MB limit.

## Risks

| Risk | Mitigation |
|------|------------|
| Supabase pauses inactive free-tier projects after 1 week | Pro plan ($25/mo) or keep crawler running |
| Connection pooler difference (Supavisor vs pgBouncer) | Both are transparent to the app; test `statement_cache_size=0` on crawler |
| Downtime during cutover | Do it during low traffic; keep Neon active until verified |
| Vercel <> Supabase latency | Choose same AWS region (eu-central-1) |
