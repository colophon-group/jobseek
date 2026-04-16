# Local Job Alert Filter — Design Spec

**Status:** Awaiting user review of full spec. Sections 1-2 user-approved during brainstorm; sections 3-5 written by planner with practical defaults to close out the design quickly. Self-review passed.
**Date:** 2026-04-16
**Author:** Claude (planner) with @Milky-Mac (user)
**Related handoff:** `ai/status.md`, `ai/decisions.md`, `ai/questions.md`

> ⚠️ This is a live draft, persisted early to survive a possible 5-hour
> session limit. If the conversation ended before sections 3-5 were
> finished, see "Open design decisions still in flight" at the bottom and
> resume from there. The architecture (Section 1) and component layout
> (Section 2) are user-approved and stable.

---

## 0. Context

### Repo state at design time

Codex completed phase-zero (local stack stand-up, see `ai/status.md`):

- Local Postgres (`jobseek_crawler_local`) has **5,275 real job postings** from
  3 boards: `google-careers` (~4,051), `bytedance-careers` (~818),
  `0x-ashby` (6).
- Same 5,275 postings have HTML descriptions in the local `descriptions`
  table (full HTML, not in R2 — local pipeline never uploads to R2).
- **0 of 5,275 are enriched** (`enrichment` JSONB is null on all rows).
- `to_be_enriched = true` on all 5,275 (set by ingest, but no enrichment
  worker has run locally).
- Local `apps/web` Next.js dev server boots and serves the existing
  search/job-detail APIs. Browser stack works (Playwright Chromium installed).
- Typesense container exists locally but **is not used by phase 1** — the
  personal alert system queries Postgres directly.

### User goals (priority order)

1. **Phase 1 (this spec)**: Filter local jobs by visa/sponsorship support
   AND entry-level (max 2 yrs experience) AND title-keyword exclusions
   ("Senior", "Principal", "Staff", "Lead", "Director", "Manager", "VP",
   "Head of"). Output to terminal. Run locally.
2. **Phase 2**: Resume scoring/matching against same job pool.
3. **Phase 3**: Hourly cron + email alerts via Resend.
4. **Phase 4**: Cloud deployment on user's Oracle Cloud instances (24/7).
5. **Phase 5**: MCP exposure for Claude/Codex orchestration.

### Constraints

- **Personal use, single user** — no multi-tenant concerns.
- **Cost-sensitive** — prefer free tiers, no paid hosted services for the
  alert pipeline (production crawler/Typesense are separate).
- **Forward-compat with cloud** — phase-1 architecture must transplant to
  cloud with config changes only, no rewrite.
- **Documentation-first** — Codex agent will execute this plan later
  without conversation context. Spec must be self-contained.

### Decisions already locked in (from brainstorm dialogue)

| Decision | Rationale |
|---|---|
| Skip Typesense for personal alerts | Postgres has all needed fields; Typesense exists for the public website with thousands of users, not for personal use. |
| Use Gemini free tier (`gemini-2.0-flash`) for enrichment | Already integrated in `apps/crawler/src/core/enrich/providers/gemini.py`. Only major provider with ongoing free API tier (1,500 req/day, 15 RPM). No fallback chain (single provider; YAGNI). |
| Approach D — sync calls reusing batch.py scaffolding | See "Approach evaluation" below. |
| CLI output only in phase 1 | Web UI deferred to later. JSON output to stdout sets up future email/cron pipeline. |
| Three commands (mark / enrich / alert) instead of one mega-command | Idempotent steps map cleanly to cron later; matches production crawler architecture style. |

---

## 1. Architecture

### Approach evaluation (why D)

Four approaches were considered. See conversation transcript at
`/Users/weitingli/.claude/projects/-Users-weitingli-myWorkSpace-jobseek/`
(jsonl session log) for full reasoning.

| Approach | Sync? | Reuses batch.py? | Verdict |
|---|---|---|---|
| A — One command, sync, on-demand enrich | sync | no | Simple, but rebuilds validation/taxonomy logic that already works. |
| B — Two commands, sync enrich worker + filter | sync | no | Same drawback as A, plus extra long-running process. |
| C — Reuse batch.py with Gemini batch API | batch (async) | yes | Bad first-time UX (wait minutes for batch). R2 dependency in `_CLAIM_PENDING` blocks local use. |
| **D — Reuse batch.py persist/validate/taxonomy, swap batch call for sync rate-limited Gemini call** | sync | yes | Selected. Best of both: tested code + interactive UX. ~270 LOC new + ~20 LOC modified. |

### Code that already exists and we reuse unchanged

`apps/crawler/src/core/enrich/batch.py`:

- `_persist_results(pool, results, batch_id)` — validates Pydantic schema,
  resolves taxonomy FKs (occupation/seniority/technology), writes
  `enrichment` JSONB + `enrich_version` + `last_enriched_at`,
  `to_be_enriched=false`, populates taxonomy_miss table on unknown values.
  **We call this from the sync loop with a synthetic `batch_id`.**
- `_handle_batch_failure` — re-queues postings on whole-batch failure.
- `check_daily_budget` — useful even with free tier (sanity cap).

`apps/crawler/src/core/enrich/job.py`:

- `EnrichmentResult` Pydantic model — already has `work_permit_support: Literal["yes", "no"] | None`.
- `SYSTEM_PROMPT` — already includes detailed visa-sponsorship extraction rules.
- `build_user_message(html, *, title, locations, employment_type)` — used as-is.

`apps/crawler/src/core/enrich/taxonomy.py`:
- `resolve_taxonomy(pool, parsed)` — used as-is by `_persist_results`.

### Three CLI commands

```
crawler mark-candidates --filters ai/filters.yaml
   ↓ Sets to_be_enriched flag based on cheap filters (title regex,
     experience cap). No LLM calls. Pure SQL UPDATE. Idempotent.
crawler enrich-local
   ↓ Claims pending postings, fetches HTML from local descriptions table,
     calls Gemini sync (rate-limited to 15 RPM), persists results via
     existing _persist_results.
crawler alert --filters ai/filters.yaml
   ↓ Pure-read SQL: cheap filters + work_permit_support='yes'. JSON to
     stdout (or table format).
```

### Forward-compatibility with cloud (24/7 future)

| Concern | Local (phase 1) | Cloud (later) | How design accommodates |
|---|---|---|---|
| HTML source | local `descriptions` table | R2 | `_fetch_html` branches on `USE_LOCAL_DESCRIPTIONS` env. Same call site, two backends. |
| Provider mode | sync (interactive) | batch (cheaper at scale) | Two providers behind a `Protocol`. Choose via `ENRICH_MODE=sync\|batch`. Persist logic identical. |
| Orchestration | manual chain or local cron | k8s/systemd/GH Actions cron | 3 idempotent commands, no shared in-process state. Same commands work both places. |
| Output sink | stdout JSON | email / Slack / webhook (phase 3) | `crawler alert` already emits structured JSON. Future phase pipes it to mail/curl. No `alert` rewrite needed. |
| Credentials | `.env.local` | secret manager / GH secrets | Existing `pydantic-settings` pattern. No code changes. |
| Schema/migrations | local Postgres | Hetzner / Oracle Postgres | Same Alembic migration runs both. `enrich_batch` table is portable. |

**Headline**: phase 1 ships the same code that will run in cloud later.
Cloud migration = config-only (env vars + cron schedule), no rewrite.

### Explicitly NOT in phase 1

- Email delivery (phase 3)
- Resume scoring / matching (phase 2)
- Hourly scheduling automation (phase 3 — manual cron OK for now)
- Web UI for filtered results (later)
- MCP exposure (much later)
- Cloud deployment (later)
- Typesense integration for personal alerts (skipped permanently)

---

## 2. Components — file-by-file changes

### New files (5)

#### `apps/crawler/src/migrations/versions/0004_add_enrich_batch_table.py` (~30 LOC)

Alembic migration creating the `enrich_batch` table referenced by
`batch.py` but not present in local migrations.

```sql
CREATE TABLE enrich_batch (
    id            text PRIMARY KEY,
    provider      text NOT NULL,
    model         text NOT NULL,
    status        text NOT NULL,        -- submitted | completed | failed
    item_count    integer NOT NULL,
    posting_ids   uuid[] NOT NULL,
    estimated_cost_usd numeric(10,4),
    input_tokens  integer DEFAULT 0,
    output_tokens integer DEFAULT 0,
    submitted_at  timestamptz DEFAULT now(),
    completed_at  timestamptz
);
```

#### `apps/crawler/src/core/enrich/providers/gemini_sync.py` (~50 LOC)

New `GeminiSyncProvider` class implementing the new `SyncProvider`
Protocol. Uses `client.aio.models.generate_content()` with
`response_mime_type="application/json"` and `response_schema=...`.
Returns `(parsed_dict, LLMUsage)` per call.

#### `apps/crawler/src/core/enrich/local.py` (~120 LOC)

The local-mode glue:

- `mark_candidates_from_yaml(pool, yaml_path) → dict` — runs cheap-filter
  UPDATE, returns `{marked: N, cleared: M}`.
- `fetch_html_local(pool, posting_id, locale) → str | None` — reads from
  `descriptions` table.
- `run_sync_enrich(pool, provider, *, batch_size, rate_limit_rpm) → dict`
  — claim loop with rate limiting (`asyncio.sleep(60/rpm)` between calls).
  Builds a synthetic `enrich_batch` row per chunk
  (`id = f"local_sync_{uuid4()}"`) so `_persist_results` runs unchanged.

#### `apps/crawler/src/queries/alert.py` (~40 LOC)

Single SQL query joining `job_posting + company`, filtered by enrichment
+ cheap filters. Returns rows for JSON serialization.

#### `ai/filters.yaml` (committed stub)

```yaml
# Personal job filters - phase 1
exclude_title_patterns:   # case-insensitive regex alternation
  - senior
  - "sr\\."
  - principal
  - staff
  - lead
  - director
  - manager
  - "vp\\b"
  - "head of"

require:
  work_permit_support: "yes"   # only show jobs explicitly sponsoring
  experience_max: 2            # max years required (NULL is OK)

output:
  limit: 100                   # cap rows in alert output
```

### Modified files (5)

- **`apps/crawler/src/core/enrich/providers/__init__.py`** — add
  `SyncProvider` Protocol next to existing `BatchProvider`. Add
  `create_sync_provider(provider, model, api_key)` factory.
- **`apps/crawler/src/core/enrich/batch.py`** — verify `_persist_results`
  works with arbitrary string `batch_id` (it should — column is `text`).
  No signature change planned; revisit if unit tests show otherwise.
- **`apps/crawler/src/cli.py`** — add 3 new Click subcommands:
  - `crawler mark-candidates [--filters PATH]`
  - `crawler enrich-local [--batch-size N] [--rate-limit-rpm N]`
  - `crawler alert [--filters PATH] [--format json|table]`
- **`apps/crawler/src/config.py`** — add settings:
  ```python
  use_local_descriptions: bool = False
  enrich_mode: Literal["batch", "sync"] = "batch"
  enrich_rate_limit_rpm: int = 15
  alert_filters_path: str = "ai/filters.yaml"
  ```
- **`apps/crawler/env.local.example`** — document new env vars.

### New tests (2)

- **`apps/crawler/tests/test_filters.py`** — YAML loader, regex
  compilation, edge cases (empty patterns, missing fields).
- **`apps/crawler/tests/test_enrich_local.py`** — mark-candidates SQL
  behavior, alert SQL behavior, sync provider stubbed (Gemini integration
  covered manually).

### Total scope

- ~270 LOC new code (5 new files)
- ~20 LOC modifications (mostly cli.py)
- ~80 LOC tests
- 1 new Alembic migration

---

## 3. Data flow & sequencing

### Happy path (first-time setup)

```
1. Crawler ingests jobs (existing pipeline, unchanged)
       → job_posting rows with to_be_enriched=true (default)
       → descriptions rows with full HTML

2. crawler mark-candidates --filters ai/filters.yaml
       → SQL UPDATE: clears to_be_enriched=false on rows that FAIL
         cheap filters (title matches exclude regex OR experience_max > 2)
       → leaves to_be_enriched=true on rows that pass
       → reports {marked: N, cleared: M}

3. crawler enrich-local
       → loop:
           a. claim up to batch_size pending postings (FOR UPDATE SKIP LOCKED)
           b. for each: fetch HTML from descriptions table
           c. for each: call Gemini sync (sleep 60/RPM between calls)
           d. accumulate results, write synthetic enrich_batch row,
              call _persist_results(pool, results, batch_id) unchanged
       → exits when claim returns 0 rows
       → reports {enriched: N, failed: M, skipped: K}

4. crawler alert --filters ai/filters.yaml
       → read-only SELECT joining job_posting + company
       → WHERE is_active AND enrichment->>'work_permit_support' = 'yes'
              AND (experience_max IS NULL OR experience_max <= cap)
              AND NOT titles[1] ~* exclude_regex
       → prints JSON to stdout, ordered by first_seen_at DESC, LIMIT N
```

### Re-run / incremental flow

After a new crawl adds more postings:

- `mark-candidates` is idempotent — re-run flips flags for new postings.
- `enrich-local` only sees postings still flagged `to_be_enriched=true` and
  not yet enriched (claim query checks `enrichment IS NULL`).
- `alert` re-prints all currently-matching jobs (no "since last alert"
  tracking — that's phase 3 with email).

### Edge case decisions

| Case | Decision | Why |
|---|---|---|
| Gemini returns `work_permit_support = null` (ambiguous) | **Exclude from alert** — only `= 'yes'` matches. | User explicitly requires sponsorship. False negatives (missed jobs) are acceptable; false positives (showing jobs that won't sponsor) waste user attention. User can override later by editing the alert SQL filter to `IN ('yes', null)`. |
| `enrich-local` interrupted mid-run | Postings stay `to_be_enriched=true` until `_persist_results` writes them. Rerun resumes naturally. | Existing batch.py semantics — no extra logic needed. |
| Gemini API error (rate limit, transient) | Existing `_persist_results` re-queues failed items by setting `to_be_enriched=true`. Sync loop catches per-call exceptions and continues. | Reuses `_handle_batch_failure` pattern. |
| Re-enrich on `enrich_version` bump | **Skip already-enriched in phase 1.** Manual re-run = `UPDATE job_posting SET enrichment=NULL, to_be_enriched=true WHERE enrich_version < 4`. | YAGNI for personal use. Easy to add a `--force` flag later. |
| Job has no HTML in descriptions table | Skip with warning log. Do not flip `to_be_enriched`. | Same shape as existing R2-miss handling. |
| User changes `ai/filters.yaml` | Re-run `mark-candidates` to re-flag postings, then `enrich-local` for any newly-included candidates. `alert` is pure-read; reflects current YAML on every run. | No state caching; YAML is the source of truth each invocation. |
| Title field empty / null | Treat as "title regex doesn't match" — i.e., included. Will get enriched and probably filtered out by sponsorship. | Conservative: don't drop jobs on missing data alone. |

### Concurrency

Single-process expected. `FOR UPDATE SKIP LOCKED` in claim query already
makes it safe to run two `enrich-local` processes in parallel if user wants
to (no locking corruption), but no need to design for that.

### Observability

- `structlog` events: `enrich.local.claim`, `enrich.local.gemini_call`,
  `enrich.local.persist`, `alert.query`, `alert.emit`.
- No Prometheus metrics in phase 1 (cloud phase adds them).

---

## 4. Filter config schema

### Phase 1 schema (frozen for this spec)

```yaml
# ai/filters.yaml — personal job filters (phase 1)

# Title-keyword exclusions, applied as a single case-insensitive regex.
# Joined with | (alternation). Each pattern is a regex fragment.
# Use \\b for word boundaries when needed.
exclude_title_patterns:
  - senior
  - "sr\\."
  - principal
  - staff
  - lead
  - director
  - manager
  - "vp\\b"
  - "head of"

require:
  # Must be present in enrichment. "yes" = explicitly sponsors visas.
  # null/missing values are excluded (see Section 3 edge case).
  work_permit_support: "yes"

  # Max years of experience required. NULL in DB = no requirement stated,
  # also OK. Values > this number are excluded.
  experience_max: 2

output:
  # Cap rows in alert output (mostly to avoid scrolling).
  limit: 100
```

### Schema validation

`apps/crawler/src/core/enrich/filters.py` (folded into `local.py`,
no separate file) loads via `yaml.safe_load`, validates with a Pydantic
model:

```python
class FilterConfig(BaseModel):
    exclude_title_patterns: list[str] = Field(default_factory=list)
    require: RequireConfig
    output: OutputConfig = Field(default_factory=OutputConfig)

class RequireConfig(BaseModel):
    work_permit_support: Literal["yes", "no"] | None = "yes"
    experience_max: int | None = 2

class OutputConfig(BaseModel):
    limit: int = 100
```

Invalid YAML or schema = exit with non-zero status and a clear error.
No silent fallback to defaults.

### Deferred to phase 2 (NOT in phase 1)

- Location filter (e.g., must include US locations)
- Occupation filter (e.g., software engineering only)
- Company allow / deny list
- Salary minimums
- Education-level filter
- "Posted within last N days" filter

These are easy to add as YAML keys + extra `WHERE` clauses later. The
phase-1 schema is intentionally thin so the user can ship and iterate.

---

## 5. Cloud-future considerations

Most content lives in Section 1's forward-compatibility table. This
section adds concrete cloud-target notes for when the user is ready to
promote phase 1 to Oracle Cloud (24/7).

### Target shape (assumption — user to confirm at cloud-phase brainstorm)

- 1× Oracle Cloud Always-Free VM (1 OCPU / 6GB RAM ARM, or equivalent)
- Postgres co-located on same VM (Docker, single-instance — personal scale,
  no need for managed Postgres)
- Existing Hetzner crawler is unchanged; the alert pipeline reads from a
  separate Postgres (NOT the production Hetzner one) populated by a
  smaller crawler instance OR a one-way sync from production.

### Open at cloud-phase brainstorm (NOT for this spec)

- Should the cloud alert pipeline ingest its own crawl, or pull from
  production Hetzner Postgres via read-replica / dump?
- Email transport: Resend (already integrated in `apps/web`) vs Gmail SMTP.
- Cron mechanism: systemd timer vs `cron` vs in-process scheduler.
- Multi-recipient (only the user, or shared with others later)?

### Migration checklist (when cloud phase begins)

1. Provision Oracle Cloud VM, install Docker, Postgres, Python 3.13.
2. Run Alembic migrations on cloud DB (same files as local).
3. Set env vars: `USE_LOCAL_DESCRIPTIONS=0`, `ENRICH_MODE=batch`,
   plus R2 credentials (if using HTML from R2 in cloud).
4. Set up cron / systemd timer for `mark-candidates && enrich-local && alert`.
5. Pipe `crawler alert --format json` to `mail` / Resend / webhook.
6. No code changes expected. If any are needed, that's a bug in this spec.

### What this spec is NOT designing

This spec is for **phase 1 only** (local). The cloud phase will get its
own brainstorm, spec, and plan when the user is ready. The forward-compat
work in phase 1 is just to ensure cloud migration is feasible without a
rewrite — not to fully design it now.

---

## Self-review (passed)

- ✅ **Placeholder scan**: no TBD, TODO, or "fill in later" markers
  remain in Sections 1-5. Open questions exist only for the deferred
  cloud phase, which is correctly marked out of scope.
- ✅ **Internal consistency**: Section 1 architecture ↔ Section 2 file
  list ↔ Section 3 flow all reference the same 3 commands and the same
  reused functions in `batch.py`. Section 4 fields all show up in Section
  3 SQL semantics.
- ✅ **Scope check**: phase 1 only, with explicit non-goals listed in
  Section 1. Cloud future is a paragraph, not a parallel design. Filter
  schema is intentionally thin.
- ✅ **Ambiguity check**: edge cases all decided in Section 3 table. No
  "depends on context" left unresolved.

Caveat: the SQL fragments shown in Section 3 are conceptual; the
implementation plan (writing-plans output) will pin the exact statements
and parameter binding.

---

## Next steps after spec approval

1. **User reviews this spec** and signals approval (or requests changes).
2. **Invoke `superpowers:writing-plans`** skill to convert the spec into
   a step-by-step implementation plan that Codex can execute end-to-end.
3. Codex executes the plan; planner (Claude) reviews resulting PR.
4. After phase 1 ships, brainstorm phase 2 (resume scoring) the same way.

## Implementation phasing reminder

This spec covers **Phase 1 only**. Future phases:

- Phase 2: resume scoring against same job pool
- Phase 3: hourly cron + Resend email
- Phase 4: Oracle Cloud deployment (24/7)
- Phase 5: MCP exposure

Each future phase will get its own spec via the same brainstorming flow.

## Implementation phasing reminder

This spec covers **Phase 1 only**. Future phases:

- Phase 2: resume scoring against same job pool
- Phase 3: hourly cron + Resend email
- Phase 4: Oracle Cloud deployment (24/7)
- Phase 5: MCP exposure

Each future phase will get its own spec via the same brainstorming flow.
