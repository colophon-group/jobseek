# Resume Customization — Design Spec

**Date:** 2026-04-25  
**Status:** Approved, ready for implementation

---

## Overview

Per-job resume tailoring triggered from the Queue page. After job fit analysis runs, the user can generate a customized `.tex` resume for that specific job. The AI makes targeted, contextually valid substitutions to bullet points — replacing keywords with compatible alternatives from the same tech ecosystem — to maximize keyword overlap without fabricating experience.

This is Phase 2-2, extending the Job Fit Queue (Phase 2-1).

---

## Features

### 1. LaTeX Resume Upload

The existing Settings resume upload is extended to accept `.tex` files. When a `.tex` is uploaded:

1. Extract plain text from LaTeX (strip `\commands`, braces, environments) for keyword extraction — same stop-word pipeline as before.
2. Store `keywords[]` as before.
3. Store the raw `.tex` source in `user_resume.latex_source` (text column).

Settings card shows: filename, updated date, keyword count. No change to card layout — `.tex` is just another accepted file type.

### 2. Queue Page — Per-Job Buttons

Each analyzed Queue job card gets two new action buttons (only visible when `user_resume.latex_source` is not null):

- **"Generate resume"** — triggers `customizeResume(jobQueueId)`. Disabled while generating. Shows spinner during generation.
- **"Remove"** (shown only when a customized resume exists) — calls `removeCustomizedResume(jobQueueId)`, deletes the file from R2 and clears the DB reference.

If no `.tex` source is uploaded, a tooltip on the button reads: "Upload your .tex in Settings to enable this."

### 3. Customization Flow

`customizeResume(jobQueueId)` server action:

1. Fetch `user_resume.latex_source` for the current user.
2. Fetch `missing_keywords[]`, `matched_keywords[]`, job title, company from `job_queue`.
3. Fetch JD text from R2.
4. Send to **Claude claude-opus-4-7** (`claude-opus-4-7`) — the most capable model, chosen for its superior reasoning about tech ecosystem compatibility.
5. Receive structured JSON response (see LLM Prompt section).
6. Upload customized `.tex` to R2 at `resumes/{user_id}/{posting_id}.tex`.
7. Upsert `job_queue.customized_r2_key`, `customized_at`.

### 4. Download

After generation, "Generate resume" button becomes a **"Download .tex"** link pointing to a signed R2 URL. No server-side PDF compilation — user compiles with their own LaTeX toolchain.

---

## LLM Prompt

**Model:** `claude-opus-4-7`

```
System:
"You are a resume editor. Given a LaTeX resume source, a list of missing keywords from a job
description, and the job description itself, make targeted edits to the resume to naturally
incorporate missing keywords.

Rules:
1. Only edit work experience bullet points — never touch contact info, education, skills section
   structure, LaTeX preamble, or column/spacing definitions.
2. Focus edits on the most recent experience entry first, then earlier entries if needed.
3. Never fabricate experience. Only substitute within compatible technology ecosystems:
   - JVM: Java ↔ Kotlin ↔ Scala (context-dependent)
   - Scripting/backend: Python ↔ TypeScript (only for scripting/tooling contexts, not web frameworks)
   - Container orchestration: Docker ↔ Kubernetes (only if candidate already has containerization)
   - Message queues: mention Kafka if the candidate has any event-driven or async experience
   Do NOT pair incompatible stacks (e.g., Python + Spring Boot, PHP + Go microservices).
4. Preserve all LaTeX formatting exactly: alignment environments, column widths, spacing commands,
   custom macros. The document must remain compilable and one page.
5. Make the minimum changes needed. Do not rewrite bullets that don't need changing.
6. Return a JSON object with exactly two fields:
   - 'changes': array of {original, replacement, keyword_added, rationale}
   - 'customized_latex': the full modified .tex source as a string"

User:
"Missing keywords: [{missing_keywords joined by comma}]
Matched keywords: [{matched_keywords joined by comma}]

Job: {title} at {company}

{jd_text}

Resume LaTeX:
{latex_source}"
```

---

## Data Model

### `user_resume` (modified)

| Column | Type | Notes |
|---|---|---|
| `latex_source` | text | Raw `.tex` file content. Null if user uploaded PDF/text. |

### `job_queue` (modified)

| Column | Type | Notes |
|---|---|---|
| `customized_r2_key` | varchar | R2 object key for customized `.tex`. Null until generated. |
| `customized_at` | timestamp | Null until generated. |

### R2 storage layout

```
resumes/
  {user_id}/
    {posting_id}.tex    # customized resume per job
```

---

## Architecture

### New files

| Path | Purpose |
|---|---|
| `src/lib/actions/customize-resume.ts` | `customizeResume(jobQueueId)`, `removeCustomizedResume(jobQueueId)`, `getCustomizedResumeUrl(jobQueueId)` server actions |

### Modified files

| Path | Change |
|---|---|
| `src/db/schema.ts` | Add `latex_source` to `userResume`; add `customized_r2_key`, `customized_at` to `jobQueue` |
| `src/db/migrations/0007_add_resume_customization.ts` | New Drizzle migration |
| `src/lib/actions/resume.ts` | Store `latex_source` when `.tex` file is uploaded |
| `src/components/queue/queue-job-card.tsx` | Add "Generate resume" + "Remove" buttons with loading/state logic |
| `app/[lang]/(app)/settings/page.tsx` | Accept `.tex` in file input `accept` attribute |

---

## Out of scope

- Server-side LaTeX → PDF compilation
- Inline diff view in the browser
- Multiple customized resumes per job (one per job, regenerate to replace)
- Automatic re-customization when resume source is updated
