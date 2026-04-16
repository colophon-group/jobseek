# Local Development Quickstart

This guide brings up the minimum local stack needed to run the crawler and public APIs end-to-end:

- `crawler -> local Postgres -> Typesense -> web /api/v1/*`

## 1) Start local infra

From repo root:

```bash
docker compose up -d postgres redis typesense
```

Health checks:

```bash
docker compose ps
curl -s http://localhost:8108/health
```

## 2) Configure crawler

```bash
cd apps/crawler
cp env.local.example .env.local
```

The local profile points both `DATABASE_URL` and `LOCAL_DATABASE_URL` to `postgresql://postgres:postgres@localhost:5432/jobseek`.

Install deps and migrate schema:

```bash
uv sync
uv run alembic -c src/migrations/alembic.ini upgrade head
```

Sync CSV configuration into DB/Redis/Typesense taxonomies:

```bash
uv run crawler sync
```

## 3) Verify crawler on a single board

Dry-run first (no writes):

```bash
uv run crawler board 0x-ashby --dry-run --verbose
```

Then run with writes:

```bash
uv run crawler board 0x-ashby
```

If a board is temporarily unavailable, pick another `board_slug` from `apps/crawler/data/boards.csv`.

## 4) Initialize and backfill Typesense

Create collections/aliases:

```bash
uv run python ../../scripts/typesense-setup.py
```

Backfill posting docs from local Postgres:

```bash
uv run python ../../scripts/typesense-backfill-local.py --limit 5000
```

Optional continuous sync loop:

```bash
uv run crawler export
```

## 5) Configure and start web app

```bash
cd ../web
cp env.local.example .env.local
pnpm install
pnpm dev
```

The web profile supports local startup without Upstash/OAuth. Rate limits become no-op and Redis cache falls back to in-memory storage.

## 6) Validate public APIs

Search:

```bash
curl -s "http://localhost:3000/api/v1/search?q=engineer&locale=en" | jq
```

Take a posting id from `topPostings[].id`, then fetch detail:

```bash
curl -s "http://localhost:3000/api/v1/job?id=<POSTING_ID>&locale=en" | jq
```

## Common issues

- `crawler sync` fails with DB connection errors:
  - confirm `docker compose ps` shows postgres as healthy
  - verify `.env.local` points to `localhost:5432`
- `search` returns empty data:
  - ensure Typesense setup/backfill commands completed
  - verify `TYPESENSE_*` vars in `apps/web/.env.local`
- `board <slug>` fails on a specific provider:
  - retry with another board slug to validate pipeline wiring first
