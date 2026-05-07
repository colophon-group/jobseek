# AGENTS.md — Jobseek

Instructions for developer agents working on this repository.

## Project Overview

Jobseek monitors company career pages for new job postings. Companies are configured via CSV files in `apps/crawler/data/`. A Python crawler monitors boards and extracts job details. A Next.js frontend serves the data.

## Repository Structure

```
/
├── apps/
│   ├── web/                 # Next.js 15 frontend (TypeScript, Drizzle ORM, Lingui i18n)
│   └── crawler/             # Python crawler (asyncpg, httpx, structlog, redis)
│       ├── data/
│       │   ├── companies.csv    # Company registry (slug, name, website, logos)
│       │   ├── boards.csv      # Board configs (monitor + scraper per board)
│       │   └── images/          # Logo/icon staging area, uploaded to R2 by CI
│       └── src/
│           ├── core/        # Pure business logic (monitors + scrapers)
│           ├── workers/     # Worker pipeline (claim from Redis, dispatch)
│           ├── processing/  # Board/scrape processing, CPU work, R2 staging
│           ├── queries/     # SQL queries for local Postgres
│           ├── redis_queue.py # Lua-backed claim/enqueue/reschedule
│           ├── lua/         # Redis Lua scripts
│           ├── exporter.py  # CDC: local Postgres -> Supabase + Typesense
│           ├── typesense_client.py # Shared Typesense client (lazy, feature-flagged)
│           ├── sync.py      # CSV -> DB + Redis + Typesense taxonomy sync
│           ├── cli.py       # Entry point (crawler run/export/drain/sync/board/...)
│           ├── config.py    # Settings
│           └── labeller/    # Daily labelled-postings routine (Claude Code driven)
│               ├── cli.py           # labeller = src.labeller.cli:main
│               ├── normalize.py     # deterministic HTML normalizer
│               ├── blocks.py        # HTML -> block list
│               ├── validate.py      # JSON Schema + custom validators
│               ├── render.py        # Jinja task-input renderer
│               ├── sampling.py      # diverse per-company posting sample
│               ├── prepare.py       # load posting + normalize + blocks
│               ├── merge.py         # assemble subagent outputs into posting.json
│               ├── upload.py        # HuggingFace dataset push
│               ├── prompts/tasks/*.md.j2  # per-task Jinja templates
│               └── schemas/         # per-task + posting JSON Schemas
├── scripts/
│   ├── typesense-setup.py       # Create/recreate Typesense collections + aliases
│   └── typesense-backfill-local.py  # One-shot backfill from Postgres to Typesense
├── docs/                    # Architecture documentation
│   ├── 11-typesense.md      # Typesense deployment + architecture reference
│   ├── 12-typesense-benchmarks.md  # Performance benchmarks
│   ├── 14-error-review-routine.md  # Daily crawler error-review routine spec
│   └── 15-data-sampling-routine.md # Daily labelled-postings routine spec
└── .github/workflows/       # CI + agent automation
```

## Commands

Crawler (from `apps/crawler/` — see [apps/crawler/AGENTS.md](apps/crawler/AGENTS.md) for full reference):

```bash
uv sync                           # Install dependencies
uv run pytest tests/              # Run tests
uv run crawler run                # Run HTTP worker (claims from Redis simple queues)
uv run crawler run-browser        # Run browser worker (claims from Redis browser queues)
uv run crawler export             # Run CDC exporter (local Postgres -> Supabase + Typesense)
uv run crawler drain              # Run R2 description uploader
uv run crawler sync               # Sync CSVs to local Postgres + Supabase + Redis + Typesense taxonomies
uv run crawler board <slug>       # Process single board (debug)
uv run crawler backfill-typesense # Full re-index of job_posting to Typesense
uv run crawler refresh-typesense  # Refresh Typesense counts + reconcile watchlists
uv run crawler notify-indexnow    # Push changed company URLs to IndexNow (RETIRED in #2821 — companies left the index; module preserved, not scheduled)

# Labeller subsystem (daily gold-dataset routine — spec in docs/15-data-sampling-routine.md)
uv run labeller sample --date today --count 10 --out <path>
uv run labeller prepare <posting_id> --date today
uv run labeller render-task --task <task> --input <path> --out <path>
uv run labeller validate --kind <kind> --file <path>
uv run labeller merge --posting <id> --date <date> --out <path>
uv run labeller upload --date <date>
```

## Ops routines (Claude Code driven)

Two scheduled routines live as slash-commands + specs — orchestrated by a
Claude Code session (Opus), with specialized Sonnet subagents invoked via
the `Agent` tool. **No direct Anthropic API calls** — the data collected by
this tool feeds a future production model trained on the dataset.

- `.claude/commands/jobseek-label-daily.md` + `docs/15-data-sampling-routine.md` —
  samples diverse postings from the last 24h, labels via
  `.claude/agents/jobseek-labeller-*` subagents with tasks rendered from
  Jinja templates at `apps/crawler/src/labeller/prompts/tasks/*.md.j2`,
  validates (including a concrete QA rule gatekeeper), uploads accepted
  gold to `viktoroo/jobseek-postings-labelled` on HuggingFace.
- `docs/14-error-review-routine.md` — daily review of crawler errors on the
  Hetzner box.

Web app (from `apps/web/`):

```bash
pnpm dev          # Dev server
pnpm build        # Build (compiles i18n catalogs first)
pnpm db:migrate   # Run Drizzle migrations
pnpm db:seed      # Seed test data
pnpm extract      # Extract i18n strings to .po
pnpm compile      # Compile .po to .js catalogs
```

Web-app conventions live under `apps/web/docs/`. The ones contributors hit most:
- [`apps/web/docs/i18n.md`](apps/web/docs/i18n.md) — Lingui translation rules; every macro needs `id` + `comment`.
- [`apps/web/docs/cache-components.md`](apps/web/docs/cache-components.md) — Cache Components / PPR rendering rules. **Read before writing or modifying any server component, layout, or server action** — the build enforces them once `cacheComponents: true` is enabled (#2835).
- [`apps/web/docs/data-fetching.md`](apps/web/docs/data-fetching.md), [`apps/web/docs/styling.md`](apps/web/docs/styling.md), [`apps/web/docs/llm-content-mirror.md`](apps/web/docs/llm-content-mirror.md) — narrower, by topic.

## Crawler Setup Workflow (`ws` tool)

The `ws` CLI is an **agent utility** — it is run exclusively by Claude Code
agents, not by humans directly. It guides the agent through the company
setup workflow by rendering instructions, managing state, and enforcing
quality gates.

**Entry point:** `ws task --issue <N>` — fetches the issue, renders
pre-verification instructions, then (after `ws new`) renders the parallel
orchestrator which tells the agent to spawn subagents for independent work.

**Instruction sources** (modify these to change agent behavior):
- Orchestrator + subagent prompts: `apps/crawler/src/workspace/steps/parallel/`
- `ws help` reference docs: `apps/crawler/src/workspace/commands/help.py`
- Troubleshooting KB: `apps/crawler/src/workspace/kb/*.md`
- Workflow gates: `apps/crawler/src/workspace/workflow.yaml`

Developer guidance for agent reasoning style lives in [docs/agents.md](docs/agents.md).

## Typesense (Search Engine)

All search, typeahead, browse-all modals, watchlist search, and the **company detail page** are served by Typesense. Supabase Postgres still handles posting detail (full description blob), user/auth data, watchlist mutations, and acts as a graceful fallback when Typesense is unreachable.

See [docs/11-typesense.md](docs/11-typesense.md) for full deployment details, including the read-paths summary.

### Infrastructure

- **Typesense 27.1** on a dedicated Hetzner CX22 (4 GB RAM, 2 vCPU), Docker container with `--network host`, data at `/mnt/typesense-data`
- **Private network** (10.0.0.0/16) connects Typesense, Postgres, and Crawler machines. Crawler talks to Typesense over the private network (HTTP, no TLS needed)
- **Cloudflare tunnel** (`typesense.colophon-group.org`) exposes Typesense to the Vercel web app (Vercel has no stable IPs to firewall). Cache bypass rule configured in Cloudflare
- Port 8108 is firewalled: SSH from anywhere, 8108 from private network only

### API Keys

Three scoped keys (stored in `apps/crawler/.env.local`, GitHub secrets, and Vercel env vars):

| Key | Scope | Used by |
|-----|-------|---------|
| `TYPESENSE_ADMIN_KEY` | Full access | Exporter, sync, backfill (crawler machine) |
| `TYPESENSE_SEARCH_KEY` | `documents:search` on all collections | Web app (via Cloudflare tunnel) |
| `TYPESENSE_WRITE_KEY` | `documents:upsert/delete/update` on `watchlist` only | Web app watchlist mutations |

### Collections

7 collections, all with versioned names + aliases (e.g., `job_posting_v1` <- `job_posting` alias):

`job_posting`, `location`, `occupation`, `seniority`, `technology`, `company`, `watchlist`

Key design choices:
- `job_posting` stores **ancestor** `location_ids` and `occupation_ids` (self + all parents + macro regions), enabling hierarchy-free filtering without joins
- Sentinel values: `experience_min = -1` for NULL, `locales = ["_none"]` for empty arrays
- Taxonomy names are denormalized onto each posting for search/facet without joins

### Collection Management

Schema source of truth: `apps/crawler/src/typesense_schema.py`. Setup is idempotent — it creates missing collections + aliases AND patches existing collections in-place to add any new fields (no rebuild required). Runs automatically on every crawler deploy via `deploy.sh` before `crawler sync`.

```bash
# Idempotent create + patch (from inside the crawler image)
uv run crawler setup-typesense [--force]

# Operator-facing wrapper (dev workflows)
cd apps/crawler && uv run python ../../scripts/typesense-setup.py [--force]

# Full re-index from Postgres
uv run crawler backfill-typesense

# One-shot local backfill (dev/testing only)
cd apps/crawler && uv run python ../../scripts/typesense-backfill-local.py [--limit N]
```

### Indexing Pipeline

- **Exporter** (CDC): two-cursor design — Supabase and Typesense cursors advance independently. Concurrent upserts via `asyncio.gather`
- **Sync**: taxonomy collections (location, occupation, seniority, technology) and the `company` collection populated after CSV sync. Company docs include extended fields (logo, website, employee_count_range, founded_year) and per-locale variants (`description_{de,fr,it}`, `industry_name_{de,fr,it}`) for the company detail page reader. Handles taxonomy rename detection
- **Reconciliation**: daily count check + sample comparison
- **refresh-typesense**: periodic count refresh for taxonomy/company collections + watchlist reconciliation. Runs inline at every deploy/CSV sync (via `crawler sync`) and every 4h via `.github/workflows/crawler-scheduled-maintenance.yml` out-of-band

### Web App Integration

`TypesenseSearchProvider` replaces `PostgresSearchProvider` (one-shot cutover). The company detail page (`getCompanyBySlug`) reads from the `company` collection, falling back to Supabase on Typesense error or 0 hits. Graceful degradation: all errors return empty results, Postgres fallback for watchlist write functions. No Redis cache on main search (Typesense is fast enough); cached for unfiltered homepage (60s), popular watchlists (120s), and company detail (`ttl: 600`, skip-null to avoid poisoning brand-new slugs).

## SEO and IndexNow

`/{locale}/company/{slug}` is now `noindex,follow` (#2821) — pages stay as the in-app product surface but are excluded from the sitemap. The crawler-side IndexNow notifier (which only ever covered company URLs) was retired in the same PR. Watchlist-side IndexNow notification from web server actions via `after()` remains active for the qualifying-watchlist surface (#2823); full IndexNow phase-out tracked in #2843.

See [docs/13-seo-and-indexnow.md](docs/13-seo-and-indexnow.md).

## Git Workflow

- Branch naming: `add-company/<slug>` for company additions, `fix-crawler/<description>` for code changes
- Commit messages: imperative mood, concise (`Add Stripe`, `Fix sitemap parser timeout`)
- Never push directly to main — always create a PR
