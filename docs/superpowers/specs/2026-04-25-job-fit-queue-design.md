# Job Fit Queue — Design Spec

**Date:** 2026-04-25  
**Status:** Approved, ready for implementation

---

## Overview

A dedicated job shortlisting and fit-analysis feature for the jobseek web app. Users add jobs to a queue from the explore view, upload their resume once, then run AI-powered fit analysis against all queued jobs. Results show a match score, keyword overlap, and a short fit explanation per job.

This is distinct from the existing "Save" / application tracker — saving tracks the application pipeline (applied → interviewing → offer), while the queue is a pre-application evaluation tool.

---

## Features

### 1. Queue Button

A `+ Queue` button appears in two places:

- **Job detail panel header** — next to the existing Save button. Two states: `+ queue` (unqueued, indigo outline) and `✓ queued` (queued, indigo filled).
- **Job card in explore list** — small icon button on hover, same row as the Save icon.

State is managed by a `QueueProvider` context (mirrors `SavedJobsProvider`) that preloads the user's queued posting IDs on app boot. No per-card fetches needed.

### 2. Resume Upload (Settings page)

A new "Resume" section in the existing Settings page. Accepts PDF or plain text upload.

**Upload pipeline:**
1. Extract plain text from the uploaded file (strip formatting).
2. Server-side stop-word pre-processing (zero LLM cost):
   - Remove: prepositions, articles, adverbs, generic filler verbs (`managed`, `worked on`, `responsible for`), conjunctions, pronouns.
   - Keep: tech tool names, frameworks, languages, concept nouns (`distributed systems`, `microservices`, `system design`, `scalability`), domain terms, compound noun phrases — **filter by part-of-speech, not by semantics**.
3. One small LLM call on the cleaned token list: normalize abbreviations (`JS → JavaScript`), deduplicate, return as `string[]`.
4. Store `keywords[]` in `user_resume` (upsert — one row per user).

Stop-word list lives in `src/lib/resume/stop-words.ts`.

Settings card shows: filename, updated date, keyword count, and a "Replace" button.

### 3. Queue Page (`/queue`)

Dedicated page in the app nav. Layout:

- **Header:** `job_fit_queue` title + resume status indicator (green dot, filename, updated date) + "Upload resume" button + "Analyze all (N)" button.
- **Stats bar:** queued count, analyzed count, avg fit score.
- **Analyzed section:** job cards with fit score badge, matched/missing keyword pills, AI fit explanation.
- **Pending section:** job cards without scores yet.

**Job card anatomy:**
- Company logo avatar (gradient letter, matching existing app pattern)
- Job title + company + location/salary
- Fit score badge: green (≥80%), amber (60–79%), red (<60%)
- `matched` keyword pills (green) + `gap` keyword pills (red)
- 2–3 sentence AI fit explanation (covers skill match, seniority fit, notable gaps)
- Left accent bar color-coded by score tier
- Remove (✕) button

### 4. Analysis Flow

Triggered by "Analyze all" button on the Queue page.

Server action `analyzeQueue()`:
1. Fetch `user_resume.keywords[]` for the current user.
2. Fetch all unanalyzed rows from `job_queue` for the user.
3. For each job: fetch JD HTML from R2 (same mechanism as job detail panel), send to LLM.
4. Batched 3 jobs at a time (matches existing `enrich-jobs` cron pattern).
5. Upsert results into `job_queue` as each batch completes.
6. Queue page polls `getQueueStatus()` every 2s while analysis is in progress, re-rendering cards as results arrive. Polling stops when all jobs are analyzed.

**LLM prompt (per job):**

```
System:
"You are a job fit analyzer. Given a candidate's keyword list and a job
description, return JSON with exactly these fields:
{
  overlap_score: number,        // 0–100
  matched_keywords: string[],   // JD requirements present in resume keywords
  missing_keywords: string[],   // important JD requirements absent from resume
  fit_explanation: string       // 2–3 sentences: skill match, seniority fit, notable gap
}"

User:
"Resume keywords: [Go, PostgreSQL, distributed systems, ...]

Job: {title} at {company}

{jd_text}"
```

---

## Data Model

### `job_queue` table (new)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK | → auth.users |
| `posting_id` | text FK | → job_posting |
| `added_at` | timestamp | |
| `overlap_score` | float4 | null until analyzed |
| `matched_keywords` | text[] | null until analyzed |
| `missing_keywords` | text[] | null until analyzed |
| `fit_explanation` | text | null until analyzed |
| `analyzed_at` | timestamp | null until analyzed |

`(user_id, posting_id)` unique constraint — one queue entry per job per user.

### `user_resume` table (new)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK | unique — one resume per user |
| `filename` | varchar | original upload filename |
| `keywords` | text[] | extracted after stop-word filter + LLM normalization |
| `updated_at` | timestamp | |

---

## Architecture

### New files

| Path | Purpose |
|---|---|
| `src/db/schema.ts` | Add `jobQueue` + `userResume` Drizzle table definitions |
| `src/db/migrations/0006_add_job_queue_resume.ts` | Drizzle migration |
| `src/lib/resume/stop-words.ts` | Stop-word list constant |
| `src/lib/resume/extract-keywords.ts` | Text pre-processing + LLM normalization |
| `src/lib/actions/queue.ts` | `addToQueue`, `removeFromQueue`, `getQueuedIds`, `analyzeQueue` server actions |
| `src/lib/actions/resume.ts` | `uploadResume`, `getResume` server actions |
| `src/components/QueueProvider.tsx` | Context — preloads queued posting IDs, exposes `isQueued`, `toggleQueue` |
| `src/components/search/queue-button.tsx` | `+ Queue` / `✓ queued` toggle button |
| `src/components/queue/queue-job-card.tsx` | Job card with score, keyword pills, explanation |
| `app/[lang]/(app)/queue/page.tsx` | Queue page route |

### Modified files

| Path | Change |
|---|---|
| `src/components/search/job-detail-dialog.tsx` | Add `QueueButton` to header action row |
| `src/components/search/job-card.tsx` | Add small queue icon button on hover |
| `app/[lang]/(app)/settings/page.tsx` | Add Resume upload section |
| `src/components/AppBootstrapProvider.tsx` | Wrap with `QueueProvider` |
| App nav | Add "Queue" link |

---

## Out of scope

- Automatic re-analysis when resume is updated (manual "Analyze all" only)
- Per-job individual "Analyze" button (batch only)
- Sharing or exporting queue results
- Multi-resume support
