# Resume Scoring — Design Spec

**Status:** Awaiting user review
**Date:** 2026-04-16
**Author:** Claude (planner) with @Milky-Mac (user)
**Depends on:** `docs/superpowers/specs/2026-04-16-local-job-alert-filter-design.md` (Phase 1 must be implemented first)

---

## 0. Context

### Repo state at design time

Phase 1 (local job alert filter) is designed but not yet implemented. This spec assumes Phase 1 is complete and the following are available:

- `job_posting` rows enriched with `technologies`, `keywords`, `seniority`, `experience`, `work_permit_support` via Gemini
- Three Phase 1 commands working: `crawler mark-candidates`, `crawler enrich-local`, `crawler alert`
- `ai/filters.yaml` with Phase 1 fields
- `GeminiSyncProvider` Protocol in `apps/crawler/src/core/enrich/providers/`

### User goals (this phase)

Phase 2 adds **resume scoring**: rank the Phase 1 filtered job pool against the user's own resume, so the best-fit jobs surface at the top of `crawler alert` output.

### Decisions locked in (from brainstorm dialogue)

| Decision | Rationale |
|---|---|
| Two new commands (`parse-resume` + `score`) | Mirrors Phase 1's idempotent step pattern. Clear, testable, composable. |
| Resume format: LaTeX or PDF | User's resume is in LaTeX/PDF. LaTeX read directly as text; PDF extracted via `pdfminer.six`. |
| Parse-once, cache as `ai/resume-parsed.yaml` | Resume rarely changes; re-parsing every run wastes API quota. YAML is version-controllable and hand-editable. |
| Score storage: `resume_score` Postgres table | Fits existing pattern; keeps `job_posting` clean; forward-compatible. |
| Hybrid scoring: structured overlap + LLM top-N | Structured overlap ranks cheaply (no API), LLM explains only top matches. |
| `explain_top_n` configurable in `ai/filters.yaml` | Default 20; easy to tune. Consistent with Phase 1's `output.limit` pattern. |
| `alert` extended, not a new command | `crawler alert` LEFT JOINs `resume_score`, sorts by `overlap_score DESC NULLS LAST`. No new command. |

---

## 1. Architecture

### Five commands total (Phase 1 + Phase 2)

```
# Phase 1 (unchanged)
crawler mark-candidates --filters ai/filters.yaml
crawler enrich-local
crawler alert --filters ai/filters.yaml

# Phase 2 (new)
crawler parse-resume --resume ai/resume.tex   # or .pdf
crawler score [--filters ai/filters.yaml]
```

`alert` is extended in place — no new `alert` command. The full daily chain is:

```
crawler mark-candidates && crawler enrich-local && crawler score && crawler alert
```

### What each Phase 2 command does

**`parse-resume`** — one-shot, re-run only when resume changes:

1. Read LaTeX source directly (plain text), or extract text from PDF via `pdfminer.six`
2. One Gemini call: "Extract structured skills profile from this resume"
3. Write `ai/resume-parsed.yaml` — gitignored, hand-editable
4. Print summary: `"Resume parsed: 12 technologies, 8 keywords"`

**`score`** — idempotent, run after each `enrich-local`:

1. Load `ai/resume-parsed.yaml`, compute `resume_hash` (sha256 of YAML)
2. Query jobs: enriched + pass cheap filters + not yet scored (or scored with a different hash)
3. For each: `compute_overlap()` — pure Python, no API calls
4. Sort by `overlap_score DESC`, take top `score.explain_top_n`
5. For top-N only: one Gemini call each for a 2–3 sentence fit explanation (rate-limited 15 RPM)
6. Write all scores + explanations to `resume_score` table
7. Print summary: `"Scored 847 jobs. Top match: Software Engineer @ ByteDance (score 73.4)"`

### Overlap scoring formula (pure Python)

```python
tech_overlap  = |resume.technologies ∩ job.technologies| / max(|job.technologies|, 1)
kw_overlap    = |resume.keywords ∩ job.keywords| / max(|job.keywords|, 1)
seniority_ok  = 1.0 if job.seniority in {"entry", "mid", None} else 0.5
overlap_score = (tech_overlap * 0.5 + kw_overlap * 0.3 + seniority_ok * 0.2) * 100
```

Weights (0.5 / 0.3 / 0.2) are constants in `overlap.py` — not config. Simple enough that YAGNI applies. Result is 0–100 `numeric(5,2)`.

Set comparison is case-insensitive (both sides lowercased before comparison).

### `resume_score` table (new)

```sql
CREATE TABLE resume_score (
    posting_id    uuid PRIMARY KEY REFERENCES job_posting(id),
    resume_hash   text NOT NULL,        -- sha256 of resume-parsed.yaml content
    overlap_score numeric(5,2) NOT NULL,
    explanation   text,                 -- null until explained
    scored_at     timestamptz DEFAULT now(),
    explained_at  timestamptz
);
```

`resume_hash` enables automatic re-scoring when the resume changes: `score` detects hash mismatch on existing rows and re-scores those jobs.

### `ai/resume-parsed.yaml` schema

```yaml
# Written by: crawler parse-resume
# Edit manually to adjust what gets scored against
technologies:
  - Python
  - PostgreSQL
  - React
  # ... (proper casing, matches job.technologies format)
keywords:
  - backend
  - data engineering
  # ... (lowercase, matches job.keywords format)
experience_years: 2       # total years of relevant experience
education: bachelor       # uses Education enum from job.py
occupation: Software Engineer
```

---

## 2. Components — file-by-file changes

### New files (5 code + 1 generated artifact)

#### `apps/crawler/src/core/score/resume.py` (~60 LOC)

```
ResumeParsed             Pydantic model (fields above)
parse_resume_with_llm()  reads .tex or .pdf, calls Gemini, returns ResumeParsed
load_resume()            loads ai/resume-parsed.yaml → ResumeParsed
save_resume()            writes ResumeParsed → YAML
resume_hash()            sha256 of YAML serialization
```

PDF extraction: `pdfminer.six` — already a common Python dependency, lightweight, no subprocess needed.

LaTeX extraction: read file directly as text; the LLM prompt instructs Gemini to ignore LaTeX markup and extract skills/experience only.

#### `apps/crawler/src/core/score/overlap.py` (~40 LOC)

```
compute_overlap(resume: ResumeParsed, job: dict) → float
```

Pure Python. No imports from outside `score/`. Testable in isolation.

#### `apps/crawler/src/core/score/explain.py` (~50 LOC)

```
explain_match(resume: ResumeParsed, job: dict, provider: SyncProvider) → str
```

Prompt: system = "You are a career advisor. Given a resume profile and job posting, write a 2–3 sentence explanation of fit."; user = resume profile fields + job title + technologies + keywords + first 500 chars of description.

Rate-limited caller reuses `asyncio.sleep(60/rpm)` pattern from Phase 1's `local.py`.

#### `apps/crawler/src/queries/score.py` (~50 LOC)

```
fetch_unscored_jobs(pool, resume_hash, filters) → list[dict]
    -- enriched + pass cheap filters + (not in resume_score OR resume_hash mismatch)
upsert_score(pool, posting_id, resume_hash, overlap_score)
upsert_explanation(pool, posting_id, explanation)
```

#### `apps/crawler/src/migrations/versions/0005_add_resume_score_table.py` (~25 LOC)

Alembic migration creating `resume_score` table (DDL in Section 1).

#### `ai/resume-parsed.yaml` (generated, gitignored)

Written by `parse-resume`. Added to `.gitignore` (personal data). User may hand-edit after generation.

### Modified files (3)

- **`apps/crawler/src/cli.py`** — add `parse-resume` and `score` Click subcommands
- **`apps/crawler/src/queries/alert.py`** — extend alert query:
  ```sql
  LEFT JOIN resume_score rs ON rs.posting_id = jp.id
  ORDER BY rs.overlap_score DESC NULLS LAST, jp.first_seen_at DESC
  ```
  Output rows gain `overlap_score` and `explanation` fields (null when not scored).
- **`ai/filters.yaml`** — add `score` section:
  ```yaml
  score:
    explain_top_n: 20
  ```

### New tests (2)

- **`apps/crawler/tests/test_overlap.py`** — `compute_overlap`: full match, zero match, partial overlap, null `technologies`/`keywords`, seniority penalty (senior job gets 0.5 weight)
- **`apps/crawler/tests/test_resume_parse.py`** — YAML load/save roundtrip, hash stability across saves, Pydantic validation (missing fields, wrong types)

### Total scope

- ~225 LOC new code (5 new files)
- ~15 LOC modifications (cli.py + alert query + filters.yaml)
- ~60 LOC tests
- 1 new Alembic migration
- 1 new `.gitignore` entry

---

## 3. Data flow & sequencing

### Happy path (first-time setup)

```
1. crawler parse-resume --resume ai/resume.tex
       → read LaTeX as plain text
       → one Gemini call: extract ResumeParsed fields
       → write ai/resume-parsed.yaml
       → print summary

2. crawler mark-candidates --filters ai/filters.yaml   [Phase 1]
       → sets to_be_enriched flags

3. crawler enrich-local                                 [Phase 1]
       → enriches jobs with technologies, keywords, seniority, etc.

4. crawler score --filters ai/filters.yaml
       → load resume-parsed.yaml, compute hash
       → fetch_unscored_jobs: all enriched + filter-passing jobs not yet scored
       → compute_overlap() for each (pure Python, fast)
       → sort DESC, explain top 20 via Gemini (rate-limited)
       → upsert_score + upsert_explanation
       → print summary

5. crawler alert --filters ai/filters.yaml
       → existing query + LEFT JOIN resume_score
       → results ordered by overlap_score DESC NULLS LAST
       → JSON includes overlap_score + explanation per job
```

### Re-run / incremental flow

- **Resume unchanged**: `score` skips already-scored jobs (hash matches); only scores newly enriched jobs
- **Resume updated**: re-run `parse-resume` → new hash → `score` detects mismatch on all existing rows → re-scores all
- **New crawl adds jobs**: `enrich-local` enriches them; `score` picks them up on next run (hash matches, only new rows missing from `resume_score`)

### Edge cases

| Case | Decision | Why |
|---|---|---|
| `ai/resume-parsed.yaml` missing when `score` runs | Exit non-zero with clear message: "Run `crawler parse-resume --resume <path>` first" | Fail fast; no silent empty results |
| Job has no `technologies` or `keywords` in enrichment | `overlap_score` computed from available components; seniority component still applies. Score may be low but job is not excluded | Conservative: don't drop jobs on missing data |
| PDF extraction produces garbled/empty text | `parse-resume` logs warning and exits non-zero. User can hand-write `ai/resume-parsed.yaml` directly | PDF extraction is best-effort; LaTeX is preferred |
| Gemini explanation call fails (rate limit, transient) | Score still written; `explanation` stays null, `explained_at` stays null. Re-run `score` fills gaps (upsert_explanation only for null rows) | Partial failure doesn't block scoring |
| Resume hash changes mid-run (user edits YAML during run) | Hash read once at start of `score`; all in-flight rows get the same hash | No partial-hash state |
| `explain_top_n` > total scored jobs | Explain all scored jobs; no error | Graceful |
| Job has `seniority = "senior"` but passes title filter | Gets `seniority_ok = 0.5` (half weight) not excluded — still scored and shown | Title filter already excluded explicit "Senior" titles; enriched seniority is a softer signal |

### Concurrency

Single-process expected. `FOR UPDATE SKIP LOCKED` in `fetch_unscored_jobs` makes it safe to run two `score` processes in parallel if desired.

### Observability

`structlog` events: `score.resume_load`, `score.overlap_computed`, `score.explain_call`, `score.upsert`

---

## 4. Forward-compatibility & cloud

### How Phase 2 fits the existing forward-compat model

| Concern | Phase 2 local | Cloud (later) | How design accommodates |
|---|---|---|---|
| Resume source | `ai/resume.tex` / `.pdf` on laptop | Same file on Oracle VM | `--resume` flag; no hardcoded path |
| LLM provider | Gemini free tier | Same (or batch for bulk re-scores) | `explain.py` takes `SyncProvider` Protocol — same abstraction as Phase 1 |
| Score storage | Local Postgres | Oracle Cloud Postgres | Same Alembic migration runs both |
| Cron chain | manual / local cron | systemd timer | `mark-candidates && enrich-local && score && alert` — 5 idempotent commands |
| Personal data | `ai/resume-parsed.yaml` gitignored | Off-repo on cloud VM | `.gitignore` entry; cloud path from `--resume` flag |

### Cloud migration checklist (when ready)

1. Copy `ai/resume-parsed.yaml` to Oracle VM (or re-run `parse-resume` there)
2. Run Alembic migration 0005 on cloud DB
3. No code changes expected

### Explicitly NOT in Phase 2

- Multiple resume profiles (e.g., SWE vs. data roles) — YAGNI; single `ai/resume-parsed.yaml` only
- Score expiry / decay — scores don't expire; re-parse resume to reset
- Web UI for score display — deferred
- Email with scores — Phase 3 pipes `crawler alert` JSON (which now includes scores) to Resend; no Phase 2 changes needed
- Embedding-based similarity — structured overlap is sufficient at personal scale

---

## Self-review (passed)

- ✅ **Placeholder scan**: no TBD, TODO, or incomplete sections. All fields, weights, and SQL defined concretely.
- ✅ **Internal consistency**: Section 1 formula → Section 2 `overlap.py` → Section 3 flow all reference the same fields (`technologies`, `keywords`, `seniority`). `resume_hash` referenced consistently across table schema, `fetch_unscored_jobs`, and re-score logic.
- ✅ **Scope check**: single spec for a single pipeline addition. No embedded sub-systems. Depends on Phase 1 but does not redesign it.
- ✅ **Ambiguity check**: case-sensitivity in set comparison specified (lowercased both sides). Seniority weight (0.5 not 0.0) chosen and justified. Re-score-on-hash-mismatch behavior specified. PDF failure behavior specified.
- ✅ **Dependency on Phase 1**: clearly stated in Section 0. `GeminiSyncProvider` Protocol reuse identified. `_persist_results` not needed (scoring is simpler than enrichment).

---

## Next steps after spec approval

1. **User reviews this spec** and signals approval (or requests changes)
2. **Invoke `superpowers:writing-plans`** to convert into a step-by-step implementation plan for Codex
3. Codex implements; planner reviews PR
4. After Phase 2 ships, brainstorm Phase 3 (hourly cron + Resend email)
