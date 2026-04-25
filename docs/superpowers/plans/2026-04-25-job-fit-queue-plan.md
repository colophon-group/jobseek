# Job Fit Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-application job evaluation tool that lets users queue jobs from the explore view, upload their resume once, and run AI-powered fit analysis that returns a match score, keyword overlap, and fit explanation per job.

**Architecture:** Two new Drizzle tables (`job_queue`, `user_resume`) back a `QueueProvider` context that mirrors the existing `SavedJobsProvider` pattern for O(1) per-card state. Resume processing is split: a pure stop-word filter in `src/lib/resume/stop-words.ts` + `extract-keywords.ts` runs server-side, then a single small MiniMax LLM call normalizes abbreviations and deduplicates. Queue fit analysis is a server action that fetches JD HTML from R2 and calls MiniMax in batches of 3, matching the existing `enrich-job.ts` pattern.

**Tech Stack:** Next.js 15 App Router, Drizzle ORM (PostgreSQL), MiniMax LLM API (`abab6.5s-chat`), Vitest, Lingui i18n, Radix UI Tooltip, Lucide icons, Tailwind CSS.

---

## File Map

### New files

| Path | Responsibility |
|---|---|
| `apps/web/drizzle/0076_add_job_queue_user_resume.sql` | Migration SQL for `job_queue` + `user_resume` tables |
| `apps/web/src/lib/resume/stop-words.ts` | Exported constant `STOP_WORDS: Set<string>` |
| `apps/web/src/lib/resume/extract-keywords.ts` | `extractKeywords(text: string): Promise<string[]>` — stop-word filter + MiniMax normalization |
| `apps/web/src/lib/actions/queue.ts` | Server actions: `addToQueue`, `removeFromQueue`, `getQueuedIds`, `getQueueItems`, `getQueueStatus`, `analyzeQueue` |
| `apps/web/src/lib/actions/resume.ts` | Server actions: `uploadResume`, `getResume` |
| `apps/web/src/components/QueueProvider.tsx` | `QueueProvider` context + `useQueue` hook |
| `apps/web/src/components/search/queue-button.tsx` | `QueueButton` component (+ Queue / ✓ queued toggle) |
| `apps/web/src/components/queue/queue-job-card.tsx` | Job card with fit score badge, keyword pills, AI explanation |
| `apps/web/src/components/settings/ResumeSettings.tsx` | Resume upload card for settings page |
| `apps/web/app/[lang]/(app)/queue/page.tsx` | Queue page route |
| `apps/web/app/[lang]/(app)/queue/queue-page.tsx` | Client component with polling + layout |
| `apps/web/src/lib/resume/__tests__/extract-keywords.test.ts` | Vitest unit tests for stop-word filtering |
| `apps/web/src/lib/actions/__tests__/queue.test.ts` | Vitest unit tests for queue server actions |

### Modified files

| Path | Change |
|---|---|
| `apps/web/src/db/schema.ts` | Add `jobQueue` + `userResume` table definitions |
| `apps/web/drizzle/relations.ts` | Add relations for new tables |
| `apps/web/src/lib/actions/bootstrap.ts` | Add `queuedIds: string[]` to `AppBootstrapData` |
| `apps/web/src/components/AppBootstrapProvider.tsx` | Wrap with `QueueProvider` |
| `apps/web/src/components/search/job-detail-dialog.tsx` | Add `QueueButton` next to `SaveButton` |
| `apps/web/src/components/search/company-card.tsx` | Add small queue icon button on hover row per posting |
| `apps/web/app/[lang]/(app)/settings/settings-loader.tsx` | Load resume data + render `ResumeSettings` |
| `apps/web/src/components/AppHeader.tsx` | Add Queue nav link (desktop `NavIcon` + mobile `BottomBarLink`) |

---

## Task 1: DB Schema — `job_queue` + `user_resume` tables

**Files:**
- Modify: `apps/web/src/db/schema.ts`
- Create: `apps/web/drizzle/0076_add_job_queue_user_resume.sql`

- [ ] **Step 1: Add Drizzle table definitions to schema**

Open `apps/web/src/db/schema.ts` and append at the end of the file:

```typescript
// ── Job Fit Queue ────────────────────────────────────────────────────

export const jobQueue = pgTable(
  "job_queue",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => user.id, { onDelete: "cascade" }),
    postingId: uuid("posting_id")
      .notNull()
      .references(() => jobPosting.id, { onDelete: "cascade" }),
    addedAt: timestamp("added_at", { withTimezone: true }).defaultNow().notNull(),
    overlapScore: real("overlap_score"),
    matchedKeywords: text("matched_keywords").array(),
    missingKeywords: text("missing_keywords").array(),
    fitExplanation: text("fit_explanation"),
    analyzedAt: timestamp("analyzed_at", { withTimezone: true }),
  },
  (table) => [
    uniqueIndex("idx_jq_user_posting").on(table.userId, table.postingId),
    index("idx_jq_user_added").on(table.userId, table.addedAt),
  ],
);

export const userResume = pgTable("user_resume", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: text("user_id")
    .notNull()
    .unique()
    .references(() => user.id, { onDelete: "cascade" }),
  filename: text("filename").notNull(),
  keywords: text("keywords").array().notNull().default([]),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
});
```

- [ ] **Step 2: Write the migration SQL**

Create `apps/web/drizzle/0076_add_job_queue_user_resume.sql`:

```sql
CREATE TABLE "job_queue" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "posting_id" uuid NOT NULL REFERENCES "job_posting"("id") ON DELETE CASCADE,
  "added_at" timestamp with time zone DEFAULT now() NOT NULL,
  "overlap_score" real,
  "matched_keywords" text[],
  "missing_keywords" text[],
  "fit_explanation" text,
  "analyzed_at" timestamp with time zone
);

CREATE UNIQUE INDEX "idx_jq_user_posting" ON "job_queue" ("user_id", "posting_id");
CREATE INDEX "idx_jq_user_added" ON "job_queue" ("user_id", "added_at");

CREATE TABLE "user_resume" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text UNIQUE NOT NULL REFERENCES "user"("id") ON DELETE CASCADE,
  "filename" text NOT NULL,
  "keywords" text[] NOT NULL DEFAULT '{}',
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
```

- [ ] **Step 3: Run the migration against the local DB**

```bash
cd apps/web && pnpm db:migrate
```

Expected: migration runs without error, tables `job_queue` and `user_resume` exist.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/db/schema.ts apps/web/drizzle/0076_add_job_queue_user_resume.sql
git commit -m "feat(db): add job_queue and user_resume tables"
```

---

## Task 2: Stop-word list + keyword extraction

**Files:**
- Create: `apps/web/src/lib/resume/stop-words.ts`
- Create: `apps/web/src/lib/resume/extract-keywords.ts`
- Create: `apps/web/src/lib/resume/__tests__/extract-keywords.test.ts`

- [ ] **Step 1: Write failing tests**

Create `apps/web/src/lib/resume/__tests__/extract-keywords.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { filterStopWords } from "@/lib/resume/extract-keywords";

describe("filterStopWords", () => {
  it("removes common prepositions", () => {
    const tokens = ["with", "Go", "and", "PostgreSQL"];
    expect(filterStopWords(tokens)).toEqual(["Go", "PostgreSQL"]);
  });

  it("removes generic filler verbs", () => {
    const tokens = ["managed", "Redis", "worked", "TypeScript"];
    expect(filterStopWords(tokens)).toEqual(["Redis", "TypeScript"]);
  });

  it("removes articles and pronouns", () => {
    const tokens = ["the", "a", "React", "I", "we", "Kubernetes"];
    expect(filterStopWords(tokens)).toEqual(["React", "Kubernetes"]);
  });

  it("keeps tech tool names and domain nouns", () => {
    const tokens = ["Go", "PostgreSQL", "microservices", "system", "design"];
    expect(filterStopWords(tokens)).toEqual(["Go", "PostgreSQL", "microservices", "system", "design"]);
  });

  it("case-insensitive removal", () => {
    const tokens = ["With", "Python", "AND", "Django"];
    expect(filterStopWords(tokens)).toEqual(["Python", "Django"]);
  });

  it("empty input returns empty array", () => {
    expect(filterStopWords([])).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd apps/web && pnpm test -- src/lib/resume/__tests__/extract-keywords.test.ts
```

Expected: FAIL with "Cannot find module '@/lib/resume/extract-keywords'"

- [ ] **Step 3: Create the stop-words list**

Create `apps/web/src/lib/resume/stop-words.ts`:

```typescript
/**
 * Stop-words filtered from resume text before keyword extraction.
 * Covers: prepositions, articles, conjunctions, pronouns, adverbs,
 * and generic filler verbs that carry no signal for job fit matching.
 * Filter by part-of-speech only — no semantic judgments.
 */
export const STOP_WORDS = new Set([
  // Articles
  "a", "an", "the",
  // Prepositions
  "in", "on", "at", "by", "for", "with", "about", "against", "between",
  "into", "through", "during", "before", "after", "above", "below",
  "to", "from", "up", "down", "of", "off", "over", "under", "again",
  "out", "per", "than", "as",
  // Conjunctions
  "and", "but", "or", "nor", "so", "yet", "both", "either", "neither",
  "not", "only", "whether", "while", "although", "because", "since",
  "unless", "until", "when", "where", "if", "that",
  // Pronouns
  "i", "me", "my", "myself", "we", "our", "ours", "ourselves",
  "you", "your", "yours", "yourself", "he", "him", "his", "she",
  "her", "hers", "it", "its", "they", "them", "their", "theirs",
  "this", "these", "those", "who", "which", "what",
  // Adverbs
  "very", "quite", "also", "just", "still", "already", "always",
  "often", "sometimes", "never", "here", "there", "now", "then",
  "how", "all", "each", "more", "most", "other", "some", "such",
  "no", "own", "same", "too", "s", "will", "can", "may",
  // Generic filler verbs
  "managed", "worked", "responsible", "helped", "assisted", "supported",
  "involved", "participated", "contributed", "utilized", "leveraged",
  "implemented", "developed", "designed", "built", "created", "made",
  "used", "wrote", "led", "drove", "delivered", "ensured", "provided",
  "maintained", "improved", "increased", "reduced", "performed",
  "collaborated", "coordinated", "communicated", "reported", "updated",
  "reviewed", "analyzed", "identified", "defined", "established",
  "executed", "operated", "monitored", "tested", "deployed", "migrated",
  "integrated", "configured", "managed", "handled", "processed",
  "achieved", "completed", "demonstrated", "applied", "followed",
  "including", "etc",
]);
```

- [ ] **Step 4: Create the extract-keywords module**

Create `apps/web/src/lib/resume/extract-keywords.ts`:

```typescript
import { STOP_WORDS } from "./stop-words";

/**
 * Splits raw text into tokens and removes stop-words.
 * Exported for unit testing.
 */
export function filterStopWords(tokens: string[]): string[] {
  return tokens.filter((t) => !STOP_WORDS.has(t.toLowerCase()));
}

/**
 * Tokenizes text, strips stop-words, then calls MiniMax to normalize
 * abbreviations (JS → JavaScript) and deduplicate. Returns string[].
 *
 * If MINIMAX_API_KEY is absent or the API call fails, returns the
 * stop-word-filtered tokens directly (graceful degradation).
 */
export async function extractKeywords(text: string): Promise<string[]> {
  // 1. Tokenize: split on whitespace and common punctuation
  const raw = text
    .replace(/[^\w\s+#.]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1);

  // 2. Stop-word filter
  const filtered = filterStopWords(raw);

  if (filtered.length === 0) return [];

  const apiKey = process.env.MINIMAX_API_KEY;
  if (!apiKey) return [...new Set(filtered)];

  try {
    const resp = await fetch("https://api.minimax.chat/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "abab6.5s-chat",
        messages: [
          {
            role: "system",
            content:
              'You are a resume keyword normalizer. Given a list of tokens, return a JSON array of strings with: abbreviations expanded (JS → JavaScript, K8s → Kubernetes, TS → TypeScript, etc.), exact duplicates removed, and very short noise tokens (1-2 chars that are not known acronyms) removed. Return ONLY the JSON array, no explanation.',
          },
          {
            role: "user",
            content: JSON.stringify(filtered),
          },
        ],
        max_tokens: 800,
        temperature: 0.1,
      }),
    });

    if (!resp.ok) return [...new Set(filtered)];

    const data = (await resp.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    const content = data.choices?.[0]?.message?.content?.trim() ?? "";

    // Extract JSON array from response (model may wrap in markdown)
    const match = content.match(/\[[\s\S]*\]/);
    if (!match) return [...new Set(filtered)];

    const parsed = JSON.parse(match[0]);
    if (!Array.isArray(parsed)) return [...new Set(filtered)];
    return parsed.filter((k): k is string => typeof k === "string" && k.length > 0);
  } catch {
    return [...new Set(filtered)];
  }
}
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
cd apps/web && pnpm test -- src/lib/resume/__tests__/extract-keywords.test.ts
```

Expected: 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/resume/
git commit -m "feat(resume): add stop-word filter and keyword extraction"
```

---

## Task 3: Queue server actions

**Files:**
- Create: `apps/web/src/lib/actions/queue.ts`
- Create: `apps/web/src/lib/actions/__tests__/queue.test.ts`

- [ ] **Step 1: Write failing tests**

Create `apps/web/src/lib/actions/__tests__/queue.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { scoreColor, formatScore } from "@/lib/actions/queue";

// These two pure helpers can be unit tested without DB
describe("scoreColor", () => {
  it("returns green for score >= 80", () => {
    expect(scoreColor(80)).toBe("green");
    expect(scoreColor(95)).toBe("green");
    expect(scoreColor(100)).toBe("green");
  });

  it("returns amber for score 60-79", () => {
    expect(scoreColor(60)).toBe("amber");
    expect(scoreColor(75)).toBe("amber");
    expect(scoreColor(79)).toBe("amber");
  });

  it("returns red for score < 60", () => {
    expect(scoreColor(0)).toBe("red");
    expect(scoreColor(59)).toBe("red");
  });
});

describe("formatScore", () => {
  it("formats score as integer percentage string", () => {
    expect(formatScore(75)).toBe("75%");
    expect(formatScore(0)).toBe("0%");
    expect(formatScore(100)).toBe("100%");
  });

  it("rounds fractional scores", () => {
    expect(formatScore(74.6)).toBe("75%");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd apps/web && pnpm test -- src/lib/actions/__tests__/queue.test.ts
```

Expected: FAIL with "Cannot find module '@/lib/actions/queue'"

- [ ] **Step 3: Write queue server actions**

Create `apps/web/src/lib/actions/queue.ts`:

```typescript
"use server";

import { eq, and, desc, count, avg, isNull, isNotNull } from "drizzle-orm";
import { db } from "@/db";
import { jobQueue, userResume, jobPosting, company } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

// ── Pure helpers (exported for tests) ───────────────────────────────

export function scoreColor(score: number): "green" | "amber" | "red" {
  if (score >= 80) return "green";
  if (score >= 60) return "amber";
  return "red";
}

export function formatScore(score: number): string {
  return `${Math.round(score)}%`;
}

// ── Types ────────────────────────────────────────────────────────────

export type QueueItemEntry = {
  id: string;           // job_queue row id
  postingId: string;
  addedAt: string;
  title: string | null;
  sourceUrl: string;
  companyId: string;
  companyName: string;
  companyIcon: string | null;
  companySlug: string;
  locations: string;    // first location name, or ""
  overlapScore: number | null;
  matchedKeywords: string[] | null;
  missingKeywords: string[] | null;
  fitExplanation: string | null;
  analyzedAt: string | null;
};

export type QueueStatus = {
  total: number;
  analyzed: number;
  avgScore: number | null;
};

// ── Toggle ───────────────────────────────────────────────────────────

export async function addToQueue(postingId: string): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  await db
    .insert(jobQueue)
    .values({ userId, postingId })
    .onConflictDoNothing();
}

export async function removeFromQueue(postingId: string): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  await db
    .delete(jobQueue)
    .where(and(eq(jobQueue.userId, userId), eq(jobQueue.postingId, postingId)));
}

export async function toggleQueue(
  postingId: string,
): Promise<{ queued: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [existing] = await db
    .select({ id: jobQueue.id })
    .from(jobQueue)
    .where(and(eq(jobQueue.userId, userId), eq(jobQueue.postingId, postingId)))
    .limit(1);

  if (existing) {
    await db.delete(jobQueue).where(eq(jobQueue.id, existing.id));
    return { queued: false };
  }

  await db.insert(jobQueue).values({ userId, postingId });
  return { queued: true };
}

// ── Bootstrap ────────────────────────────────────────────────────────

export async function getQueuedIds(): Promise<string[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  try {
    const rows = await db
      .select({ postingId: jobQueue.postingId })
      .from(jobQueue)
      .where(eq(jobQueue.userId, userId));
    return rows.map((r) => r.postingId);
  } catch {
    return [];
  }
}

// ── Queue page data ──────────────────────────────────────────────────

export async function getQueueItems(): Promise<QueueItemEntry[]> {
  const userId = await getSessionUserId();
  if (!userId) return [];

  const rows = await db
    .select({
      id: jobQueue.id,
      postingId: jobQueue.postingId,
      addedAt: jobQueue.addedAt,
      overlapScore: jobQueue.overlapScore,
      matchedKeywords: jobQueue.matchedKeywords,
      missingKeywords: jobQueue.missingKeywords,
      fitExplanation: jobQueue.fitExplanation,
      analyzedAt: jobQueue.analyzedAt,
      title: jobPosting.titles,
      sourceUrl: jobPosting.sourceUrl,
      companyId: company.id,
      companyName: company.name,
      companyIcon: company.icon,
      companySlug: company.slug,
    })
    .from(jobQueue)
    .innerJoin(jobPosting, eq(jobQueue.postingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(eq(jobQueue.userId, userId))
    .orderBy(desc(jobQueue.addedAt));

  return rows.map((r) => ({
    id: r.id,
    postingId: r.postingId,
    addedAt: r.addedAt.toISOString(),
    title: (r.title as string[] | null)?.[0] ?? null,
    sourceUrl: r.sourceUrl,
    companyId: r.companyId,
    companyName: r.companyName,
    companyIcon: r.companyIcon,
    companySlug: r.companySlug,
    locations: "",
    overlapScore: r.overlapScore ?? null,
    matchedKeywords: r.matchedKeywords ?? null,
    missingKeywords: r.missingKeywords ?? null,
    fitExplanation: r.fitExplanation ?? null,
    analyzedAt: r.analyzedAt ? r.analyzedAt.toISOString() : null,
  }));
}

export async function getQueueStatus(): Promise<QueueStatus> {
  const userId = await getSessionUserId();
  if (!userId) return { total: 0, analyzed: 0, avgScore: null };

  const [totalRow] = await db
    .select({ count: count() })
    .from(jobQueue)
    .where(eq(jobQueue.userId, userId));

  const [analyzedRow] = await db
    .select({ count: count(), avg: avg(jobQueue.overlapScore) })
    .from(jobQueue)
    .where(and(eq(jobQueue.userId, userId), isNotNull(jobQueue.analyzedAt)));

  return {
    total: totalRow?.count ?? 0,
    analyzed: analyzedRow?.count ?? 0,
    avgScore: analyzedRow?.avg ? Number(analyzedRow.avg) : null,
  };
}

// ── Analysis ─────────────────────────────────────────────────────────

type FitResult = {
  overlap_score: number;
  matched_keywords: string[];
  missing_keywords: string[];
  fit_explanation: string;
};

async function analyzeSingleJob(params: {
  queueId: string;
  title: string;
  companyName: string;
  jdText: string;
  resumeKeywords: string[];
}): Promise<void> {
  const apiKey = process.env.MINIMAX_API_KEY;
  if (!apiKey) return;

  try {
    const resp = await fetch("https://api.minimax.chat/v1/chat/completions", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "abab6.5s-chat",
        messages: [
          {
            role: "system",
            content:
              'You are a job fit analyzer. Given a candidate\'s keyword list and a job description, return JSON with exactly these fields:\n{\n  "overlap_score": number,\n  "matched_keywords": string[],\n  "missing_keywords": string[],\n  "fit_explanation": string\n}\noverlap_score is 0-100. matched_keywords are JD requirements present in resume keywords. missing_keywords are important JD requirements absent from resume. fit_explanation is 2-3 sentences covering skill match, seniority fit, notable gap. Return ONLY the JSON object.',
          },
          {
            role: "user",
            content: `Resume keywords: ${JSON.stringify(params.resumeKeywords)}\n\nJob: ${params.title} at ${params.companyName}\n\n${params.jdText}`,
          },
        ],
        max_tokens: 400,
        temperature: 0.2,
      }),
    });

    if (!resp.ok) return;

    const data = (await resp.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    const content = data.choices?.[0]?.message?.content?.trim() ?? "";
    const match = content.match(/\{[\s\S]*\}/);
    if (!match) return;

    const result = JSON.parse(match[0]) as FitResult;

    await db
      .update(jobQueue)
      .set({
        overlapScore: result.overlap_score,
        matchedKeywords: result.matched_keywords,
        missingKeywords: result.missing_keywords,
        fitExplanation: result.fit_explanation,
        analyzedAt: new Date(),
      })
      .where(eq(jobQueue.id, params.queueId));
  } catch {
    // Silently skip — job remains unanalyzed, user can retry
  }
}

export async function analyzeQueue(): Promise<{ started: boolean }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  // Fetch resume keywords
  const [resume] = await db
    .select({ keywords: userResume.keywords })
    .from(userResume)
    .where(eq(userResume.userId, userId))
    .limit(1);

  if (!resume || resume.keywords.length === 0) {
    return { started: false };
  }

  // Fetch unanalyzed queue items with JD URL
  const unanalyzed = await db
    .select({
      id: jobQueue.id,
      postingId: jobQueue.postingId,
      title: jobPosting.titles,
      companyName: company.name,
      descriptionR2Hash: jobPosting.descriptionR2Hash,
      sourceUrl: jobPosting.sourceUrl,
    })
    .from(jobQueue)
    .innerJoin(jobPosting, eq(jobQueue.postingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(and(eq(jobQueue.userId, userId), isNull(jobQueue.analyzedAt)));

  if (unanalyzed.length === 0) return { started: false };

  const r2Base = process.env.R2_PUBLIC_URL ?? "";

  // Process in batches of 3
  for (let i = 0; i < unanalyzed.length; i += 3) {
    const batch = unanalyzed.slice(i, i + 3);
    await Promise.all(
      batch.map(async (item) => {
        let jdText = "";
        if (item.descriptionR2Hash && r2Base) {
          try {
            const url = `${r2Base}/${item.descriptionR2Hash}.html`;
            const r = await fetch(url);
            if (r.ok) {
              const html = await r.text();
              jdText = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 4000);
            }
          } catch {
            // skip — proceed with empty JD text
          }
        }

        await analyzeSingleJob({
          queueId: item.id,
          title: (item.title as string[] | null)?.[0] ?? "Unknown role",
          companyName: item.companyName,
          jdText,
          resumeKeywords: resume.keywords,
        });
      }),
    );
  }

  return { started: true };
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd apps/web && pnpm test -- src/lib/actions/__tests__/queue.test.ts
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/actions/queue.ts apps/web/src/lib/actions/__tests__/queue.test.ts
git commit -m "feat(queue): add queue server actions and pure helpers"
```

---

## Task 4: Resume server actions

**Files:**
- Create: `apps/web/src/lib/actions/resume.ts`

- [ ] **Step 1: Create resume server actions**

Create `apps/web/src/lib/actions/resume.ts`:

```typescript
"use server";

import { eq } from "drizzle-orm";
import { db } from "@/db";
import { userResume } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";
import { extractKeywords } from "@/lib/resume/extract-keywords";

export type ResumeInfo = {
  filename: string;
  keywords: string[];
  updatedAt: string;
};

export async function getResume(): Promise<ResumeInfo | null> {
  const userId = await getSessionUserId();
  if (!userId) return null;

  const [row] = await db
    .select()
    .from(userResume)
    .where(eq(userResume.userId, userId))
    .limit(1);

  if (!row) return null;

  return {
    filename: row.filename,
    keywords: row.keywords,
    updatedAt: row.updatedAt.toISOString(),
  };
}

export async function uploadResume(params: {
  filename: string;
  text: string;
}): Promise<ResumeInfo> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const keywords = await extractKeywords(params.text);

  const [row] = await db
    .insert(userResume)
    .values({
      userId,
      filename: params.filename,
      keywords,
    })
    .onConflictDoUpdate({
      target: userResume.userId,
      set: {
        filename: params.filename,
        keywords,
        updatedAt: new Date(),
      },
    })
    .returning();

  return {
    filename: row.filename,
    keywords: row.keywords,
    updatedAt: row.updatedAt.toISOString(),
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/lib/actions/resume.ts
git commit -m "feat(resume): add uploadResume and getResume server actions"
```

---

## Task 5: QueueProvider context

**Files:**
- Create: `apps/web/src/components/QueueProvider.tsx`
- Modify: `apps/web/src/lib/actions/bootstrap.ts`
- Modify: `apps/web/src/components/AppBootstrapProvider.tsx`

- [ ] **Step 1: Add `queuedIds` to bootstrap data**

Open `apps/web/src/lib/actions/bootstrap.ts`. Add the import and update the type and function:

At the top, add:
```typescript
import { getQueuedIds } from "@/lib/actions/queue";
```

Update `AppBootstrapData`:
```typescript
export type AppBootstrapData = {
  user: SessionUser | null;
  prefs: AppPreferences | null;
  savedStatuses: SavedJobStatus[];
  starredIds: string[];
  queuedIds: string[];   // ← add this line
};
```

Update `fetchAppBootstrap`:
```typescript
export async function fetchAppBootstrap(): Promise<AppBootstrapData> {
  const session = await getSession();
  if (!session) {
    return { user: null, prefs: null, savedStatuses: [], starredIds: [], queuedIds: [] };
  }

  const [prefs, savedStatuses, starredIds, queuedIds] = await Promise.all([
    getPreferences(),
    getSavedJobStatuses(),
    getStarredCompanyIds(),
    getQueuedIds(),
  ]);

  return {
    user: session.user as SessionUser,
    prefs: prefs as AppPreferences | null,
    savedStatuses,
    starredIds,
    queuedIds,
  };
}
```

- [ ] **Step 2: Create QueueProvider**

Create `apps/web/src/components/QueueProvider.tsx`:

```typescript
"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { toggleQueue } from "@/lib/actions/queue";

type QueueContextValue = {
  isQueued: (id: string) => boolean;
  toggle: (id: string) => void;
  isToggling: (id: string) => boolean;
};

const QueueContext = createContext<QueueContextValue>({
  isQueued: () => false,
  toggle: () => {},
  isToggling: () => false,
});

export function QueueProvider({
  initialIds = [],
  children,
}: {
  initialIds?: string[];
  children: ReactNode;
}) {
  const [queuedSet, setQueuedSet] = useState(
    () => new Set<string>(initialIds),
  );

  // Sync when bootstrap data arrives
  useEffect(() => {
    if (initialIds.length === 0) return;
    setQueuedSet(new Set(initialIds));
  }, [initialIds]);

  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const queuedSetRef = useRef(queuedSet);
  queuedSetRef.current = queuedSet;

  const isQueued = useCallback((id: string) => queuedSet.has(id), [queuedSet]);
  const isToggling = useCallback((id: string) => togglingIds.has(id), [togglingIds]);

  const toggle = useCallback((id: string) => {
    if (lockRef.current.has(id)) return;
    lockRef.current.add(id);

    const wasQueued = queuedSetRef.current.has(id);

    // Optimistic update
    setQueuedSet((prev) => {
      const next = new Set(prev);
      if (wasQueued) next.delete(id);
      else next.add(id);
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(id));

    toggleQueue(id)
      .then((result) => {
        setQueuedSet((prev) => {
          const next = new Set(prev);
          if (result.queued) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .catch(() => {
        // Rollback on error
        setQueuedSet((prev) => {
          const next = new Set(prev);
          if (wasQueued) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .finally(() => {
        lockRef.current.delete(id);
        setTogglingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      });
  }, []);

  return (
    <QueueContext.Provider value={{ isQueued, toggle, isToggling }}>
      {children}
    </QueueContext.Provider>
  );
}

export function useQueue() {
  return useContext(QueueContext);
}
```

- [ ] **Step 3: Wrap AppBootstrapProvider with QueueProvider**

Open `apps/web/src/components/AppBootstrapProvider.tsx`. Add the import:
```typescript
import { QueueProvider } from "@/components/QueueProvider";
```

Wrap `SavedJobsProvider` (or the outermost relevant provider) with `QueueProvider`:
```typescript
return (
  <SessionProvider user={user} isPending={isPending}>
    <SavedJobsProvider initialStatuses={data?.savedStatuses}>
      <QueueProvider initialIds={data?.queuedIds}>
        <StarredCompaniesProvider initialIds={data?.starredIds}>
          <SalaryDisplayProvider
            displayCurrency={prefs?.displayCurrency ?? null}
            salaryPeriod={prefs?.salaryPeriod ?? null}
          >
            <BannerProvider serverDismissed={prefs?.dismissedBanners}>
              {prefs && (
                <PreferencesInitializer
                  theme={prefs.theme}
                  themeUpdatedAt={prefs.themeUpdatedAt ? String(prefs.themeUpdatedAt) : null}
                  locale={prefs.locale}
                  localeUpdatedAt={prefs.localeUpdatedAt ? String(prefs.localeUpdatedAt) : null}
                  cookieConsent={prefs.cookieConsent}
                />
              )}
              {children}
            </BannerProvider>
          </SalaryDisplayProvider>
        </StarredCompaniesProvider>
      </QueueProvider>
    </SavedJobsProvider>
  </SessionProvider>
);
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/QueueProvider.tsx \
        apps/web/src/lib/actions/bootstrap.ts \
        apps/web/src/components/AppBootstrapProvider.tsx
git commit -m "feat(queue): add QueueProvider context and bootstrap integration"
```

---

## Task 6: QueueButton component

**Files:**
- Create: `apps/web/src/components/search/queue-button.tsx`

- [ ] **Step 1: Create QueueButton**

Create `apps/web/src/components/search/queue-button.tsx`:

```typescript
"use client";

import { ListPlus, ListChecks } from "lucide-react";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useAuth } from "@/lib/useAuth";
import { useLocalePath } from "@/lib/useLocalePath";
import { useQueue } from "@/components/QueueProvider";
import { tooltipClass } from "@/components/ui/tooltip-styles";

interface QueueButtonProps {
  postingId: string;
  /** When true, renders as a compact icon-only button (for job cards) */
  compact?: boolean;
}

export function QueueButton({ postingId, compact = false }: QueueButtonProps) {
  const { t } = useLingui();
  const { isLoggedIn, isPending } = useAuth();
  const lp = useLocalePath();
  const { isQueued, toggle, isToggling } = useQueue();

  const queued = isQueued(postingId);
  const toggling = isToggling(postingId);

  const label = queued
    ? t({ id: "search.queue.remove", comment: "Tooltip for remove from queue button", message: "Remove from queue" })
    : t({ id: "search.queue.add", comment: "Tooltip for add to queue button", message: "Add to queue" });

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    if (isPending) return;
    if (!isLoggedIn) {
      window.location.href = lp("/sign-in");
      return;
    }
    toggle(postingId);
  }

  const Icon = queued ? ListChecks : ListPlus;

  if (compact) {
    // Icon-only for job card hover row (same size as SaveButton)
    return (
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <button
              onClick={handleClick}
              disabled={toggling}
              className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70 disabled:cursor-default disabled:opacity-50"
              aria-label={label}
            >
              <Icon size={14} className={queued ? "fill-current" : ""} />
            </button>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content className={tooltipClass} sideOffset={6}>
              {label}
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
    );
  }

  // Full button for job detail panel header
  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={handleClick}
            disabled={toggling}
            aria-label={label}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold transition-colors disabled:cursor-default disabled:opacity-50 ${
              queued
                ? "border-indigo-500 bg-indigo-500 text-white"
                : "border-indigo-500 bg-transparent text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-950"
            }`}
          >
            <Icon size={12} />
            {queued
              ? t({ id: "search.queue.queued", comment: "Label when job is already queued", message: "Queued" })
              : t({ id: "search.queue.queue", comment: "Label for queue button", message: "Queue" })}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content className={tooltipClass} sideOffset={6}>
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
```

- [ ] **Step 2: Add QueueButton to job detail panel header**

Open `apps/web/src/components/search/job-detail-dialog.tsx`.

Add the import near the top (after the `SaveButton` import):
```typescript
import { QueueButton } from "@/components/search/queue-button";
```

In `DetailContent`, find the header action row at approximately line 204–215:
```tsx
<div className="ml-auto flex shrink-0 items-center gap-2">
  <span suppressHydrationWarning className="text-[10px] tabular-nums text-muted">{timeAgoShort(detail.firstSeenAt)}</span>
  <SaveButton postingId={detail.id} />
  <a ...>View posting</a>
</div>
```

Insert `<QueueButton postingId={detail.id} />` between `<SaveButton>` and the `<a>` tag:
```tsx
<div className="ml-auto flex shrink-0 items-center gap-2">
  <span suppressHydrationWarning className="text-[10px] tabular-nums text-muted">{timeAgoShort(detail.firstSeenAt)}</span>
  <SaveButton postingId={detail.id} />
  <QueueButton postingId={detail.id} />
  <a
    href={withUtmSource(detail.sourceUrl)}
    target="_blank"
    rel="noopener noreferrer"
    className="inline-flex items-center gap-1.5 rounded-full border border-primary bg-primary px-3 py-1 text-xs font-semibold text-primary-contrast transition-opacity hover:opacity-90"
  >
    <Trans id="search.detail.viewPosting" comment="Link to the original job posting">View posting</Trans>
  </a>
</div>
```

- [ ] **Step 3: Add compact QueueButton to company-card posting rows**

Open `apps/web/src/components/search/company-card.tsx`.

Add the import near the `SaveButton` import line:
```typescript
import { QueueButton } from "@/components/search/queue-button";
```

In the posting row (around line 165), add `<QueueButton postingId={posting.id} compact />` immediately after `<SaveButton postingId={posting.id} />`:
```tsx
<SaveButton postingId={posting.id} />
<QueueButton postingId={posting.id} compact />
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/search/queue-button.tsx \
        apps/web/src/components/search/job-detail-dialog.tsx \
        apps/web/src/components/search/company-card.tsx
git commit -m "feat(queue): add QueueButton to detail panel and job card rows"
```

---

## Task 7: Queue page — job card component

**Files:**
- Create: `apps/web/src/components/queue/queue-job-card.tsx`

- [ ] **Step 1: Create the queue job card component**

Create `apps/web/src/components/queue/queue-job-card.tsx`:

```typescript
"use client";

import Image from "next/image";
import { Building2, X } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import type { QueueItemEntry } from "@/lib/actions/queue";
import { scoreColor, formatScore } from "@/lib/actions/queue";

interface QueueJobCardProps {
  item: QueueItemEntry;
  onRemove: (postingId: string) => void;
}

const accentColorMap = {
  green: "bg-emerald-500",
  amber: "bg-amber-400",
  red: "bg-red-500",
};

const badgeColorMap = {
  green: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200",
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  red: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
};

export function QueueJobCard({ item, onRemove }: QueueJobCardProps) {
  const isAnalyzed = item.analyzedAt !== null;
  const tier = item.overlapScore != null ? scoreColor(item.overlapScore) : null;

  return (
    <div className="relative flex overflow-hidden rounded-md border border-divider bg-surface">
      {/* Left accent bar */}
      {tier && (
        <div className={`w-1 shrink-0 ${accentColorMap[tier]}`} />
      )}

      <div className="flex flex-1 flex-col gap-2 p-4">
        {/* Header row */}
        <div className="flex items-start gap-3">
          {/* Company logo */}
          {item.companyIcon ? (
            <Image
              src={item.companyIcon}
              alt={item.companyName}
              width={36}
              height={36}
              className="size-9 shrink-0 rounded"
            />
          ) : (
            <div className="flex size-9 shrink-0 items-center justify-center rounded bg-border-soft text-sm font-semibold text-muted">
              {item.companyName.slice(0, 2).toUpperCase()}
            </div>
          )}

          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{item.title ?? "—"}</p>
            <p className="text-xs text-muted">{item.companyName}</p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {/* Fit score badge */}
            {isAnalyzed && item.overlapScore != null && tier && (
              <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${badgeColorMap[tier]}`}>
                {formatScore(item.overlapScore)}
              </span>
            )}
            {/* Remove button */}
            <button
              onClick={() => onRemove(item.postingId)}
              className="rounded p-1 text-muted hover:bg-border-soft hover:text-foreground"
              aria-label="Remove from queue"
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Keyword pills */}
        {isAnalyzed && (
          <div className="flex flex-wrap gap-1">
            {item.matchedKeywords?.slice(0, 8).map((kw) => (
              <span
                key={kw}
                className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200"
              >
                {kw}
              </span>
            ))}
            {item.missingKeywords?.slice(0, 5).map((kw) => (
              <span
                key={kw}
                className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-800 dark:bg-red-900 dark:text-red-200"
              >
                {kw}
              </span>
            ))}
          </div>
        )}

        {/* Fit explanation */}
        {isAnalyzed && item.fitExplanation && (
          <p className="text-xs text-muted leading-relaxed">{item.fitExplanation}</p>
        )}

        {/* Pending state */}
        {!isAnalyzed && (
          <p className="text-xs text-muted">
            <Trans id="queue.card.pending" comment="Label shown on queue card before analysis runs">
              Pending analysis
            </Trans>
          </p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/queue/queue-job-card.tsx
git commit -m "feat(queue): add QueueJobCard component with score badge and keyword pills"
```

---

## Task 8: Queue page route

**Files:**
- Create: `apps/web/app/[lang]/(app)/queue/page.tsx`
- Create: `apps/web/app/[lang]/(app)/queue/queue-page.tsx`

- [ ] **Step 1: Create the queue page client component**

Create `apps/web/app/[lang]/(app)/queue/queue-page.tsx`:

```typescript
"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Upload } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getQueueItems, getQueueStatus, analyzeQueue, removeFromQueue } from "@/lib/actions/queue";
import { getResume } from "@/lib/actions/resume";
import type { QueueItemEntry, QueueStatus } from "@/lib/actions/queue";
import type { ResumeInfo } from "@/lib/actions/resume";
import { QueueJobCard } from "@/components/queue/queue-job-card";
import { useQueue } from "@/components/QueueProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { formatScore } from "@/lib/actions/queue";

export function QueuePage({ locale }: { locale: string }) {
  const [items, setItems] = useState<QueueItemEntry[]>([]);
  const [status, setStatus] = useState<QueueStatus>({ total: 0, analyzed: 0, avgScore: null });
  const [resume, setResume] = useState<ResumeInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lp = useLocalePath();
  const { t } = useLingui();
  const { toggle: toggleQueueContext } = useQueue();

  const loadData = useCallback(async () => {
    const [fetchedItems, fetchedStatus, fetchedResume] = await Promise.all([
      getQueueItems(),
      getQueueStatus(),
      getResume(),
    ]);
    setItems(fetchedItems);
    setStatus(fetchedStatus);
    setResume(fetchedResume);
  }, []);

  useEffect(() => {
    loadData().finally(() => setLoading(false));
  }, [loadData]);

  // Polling while analysis is running
  useEffect(() => {
    if (!analyzing) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }
    pollRef.current = setInterval(async () => {
      const [fetchedItems, fetchedStatus] = await Promise.all([
        getQueueItems(),
        getQueueStatus(),
      ]);
      setItems(fetchedItems);
      setStatus(fetchedStatus);

      // Stop polling when all items are analyzed
      if (fetchedStatus.analyzed >= fetchedStatus.total && fetchedStatus.total > 0) {
        setAnalyzing(false);
      }
    }, 2000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [analyzing]);

  async function handleAnalyzeAll() {
    if (!resume || items.length === 0) return;
    setAnalyzing(true);
    await analyzeQueue();
  }

  async function handleRemove(postingId: string) {
    await removeFromQueue(postingId);
    toggleQueueContext(postingId);  // sync context so explore buttons update
    setItems((prev) => prev.filter((i) => i.postingId !== postingId));
    setStatus((prev) => ({
      ...prev,
      total: Math.max(0, prev.total - 1),
    }));
  }

  const analyzedItems = items.filter((i) => i.analyzedAt !== null);
  const pendingItems = items.filter((i) => i.analyzedAt === null);
  const unanalyzedCount = pendingItems.length;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-xl font-bold">
          <Trans id="queue.page.title" comment="Job fit queue page title">Job Fit Queue</Trans>
        </h1>

        <div className="flex items-center gap-2">
          {/* Resume status */}
          {resume ? (
            <div className="flex items-center gap-1.5 text-xs text-muted">
              <span className="size-2 rounded-full bg-emerald-500" />
              <span>{resume.filename}</span>
              <span className="text-[10px]">· {resume.keywords.length} keywords</span>
            </div>
          ) : (
            <span className="text-xs text-muted">
              <Trans id="queue.page.noResume" comment="Message shown when no resume is uploaded">No resume uploaded</Trans>
            </span>
          )}

          {/* Upload resume link */}
          <a
            href={lp("/settings")}
            className="inline-flex items-center gap-1 rounded border border-divider px-2 py-1 text-xs text-muted hover:bg-border-soft"
          >
            <Upload size={11} />
            <Trans id="queue.page.uploadResume" comment="Link to upload resume in settings">Upload resume</Trans>
          </a>

          {/* Analyze all button */}
          {unanalyzedCount > 0 && resume && (
            <button
              onClick={handleAnalyzeAll}
              disabled={analyzing}
              className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {analyzing
                ? t({ id: "queue.page.analyzing", comment: "Button label while analysis is running", message: "Analyzing…" })
                : t({ id: "queue.page.analyzeAll", comment: "Analyze all queued jobs button", message: `Analyze all (${unanalyzedCount})` })}
            </button>
          )}
        </div>
      </div>

      {/* Stats bar */}
      {status.total > 0 && (
        <div className="flex gap-4 rounded-md border border-divider bg-surface-alt/50 px-4 py-3 text-sm">
          <span>
            <span className="font-semibold">{status.total}</span>{" "}
            <span className="text-muted">
              <Trans id="queue.stats.queued" comment="Queued count label">queued</Trans>
            </span>
          </span>
          <span>
            <span className="font-semibold">{status.analyzed}</span>{" "}
            <span className="text-muted">
              <Trans id="queue.stats.analyzed" comment="Analyzed count label">analyzed</Trans>
            </span>
          </span>
          {status.avgScore != null && (
            <span>
              <span className="font-semibold">{formatScore(status.avgScore)}</span>{" "}
              <span className="text-muted">
                <Trans id="queue.stats.avgScore" comment="Average fit score label">avg fit</Trans>
              </span>
            </span>
          )}
        </div>
      )}

      {/* Empty state */}
      {items.length === 0 && (
        <div className="rounded-md border border-divider bg-surface px-6 py-12 text-center">
          <p className="text-sm text-muted">
            <Trans id="queue.empty" comment="Empty queue message">
              No jobs in your queue yet. Hit the Queue button on any job to add it.
            </Trans>
          </p>
        </div>
      )}

      {/* Analyzed section */}
      {analyzedItems.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            <Trans id="queue.section.analyzed" comment="Section heading for analyzed jobs">Analyzed</Trans>
          </h2>
          {analyzedItems.map((item) => (
            <QueueJobCard key={item.id} item={item} onRemove={handleRemove} />
          ))}
        </section>
      )}

      {/* Pending section */}
      {pendingItems.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            <Trans id="queue.section.pending" comment="Section heading for pending jobs awaiting analysis">Pending</Trans>
          </h2>
          {pendingItems.map((item) => (
            <QueueJobCard key={item.id} item={item} onRemove={handleRemove} />
          ))}
        </section>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create the queue page route**

Create `apps/web/app/[lang]/(app)/queue/page.tsx`:

```typescript
import { isLocale, defaultLocale } from "@/lib/i18n";
import { QueuePage } from "./queue-page";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function QueueRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <QueuePage locale={locale} />;
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/app/[lang]/\(app\)/queue/
git commit -m "feat(queue): add queue page route with polling and layout"
```

---

## Task 9: Resume Settings card

**Files:**
- Create: `apps/web/src/components/settings/ResumeSettings.tsx`
- Modify: `apps/web/app/[lang]/(app)/settings/settings-loader.tsx`

- [ ] **Step 1: Create ResumeSettings component**

Create `apps/web/src/components/settings/ResumeSettings.tsx`:

```typescript
"use client";

import { useState, useRef } from "react";
import { Upload, FileText } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { uploadResume } from "@/lib/actions/resume";
import type { ResumeInfo } from "@/lib/actions/resume";

interface ResumeSettingsProps {
  initialResume: ResumeInfo | null;
}

export function ResumeSettings({ initialResume }: ResumeSettingsProps) {
  const [resume, setResume] = useState<ResumeInfo | null>(initialResume);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { t } = useLingui();

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    // Accept PDF or plain text
    if (!file.type.includes("pdf") && !file.type.includes("text")) {
      setError(t({ id: "settings.resume.invalidType", comment: "Error when wrong file type is uploaded", message: "Only PDF or plain text files are supported." }));
      return;
    }

    setError(null);
    setUploading(true);

    try {
      let text = "";
      if (file.type.includes("text")) {
        text = await file.text();
      } else {
        // PDF: read as ArrayBuffer and extract text via simple heuristic
        // (strips binary, keeps printable ASCII runs ≥ 4 chars)
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        const chars: string[] = [];
        let run = "";
        for (let i = 0; i < bytes.length; i++) {
          const c = bytes[i];
          if (c >= 32 && c < 127) {
            run += String.fromCharCode(c);
          } else {
            if (run.length >= 4) chars.push(run);
            run = "";
          }
        }
        if (run.length >= 4) chars.push(run);
        text = chars.join(" ");
      }

      const result = await uploadResume({ filename: file.name, text });
      setResume(result);
    } catch {
      setError(t({ id: "settings.resume.uploadError", comment: "Generic upload error message", message: "Upload failed. Please try again." }));
    } finally {
      setUploading(false);
      // Reset input so same file can be re-uploaded
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">
        <Trans id="settings.resume.heading" comment="Resume settings section heading">Resume</Trans>
      </h2>

      {resume ? (
        <div className="flex items-center gap-3 rounded-md border border-divider bg-surface-alt/50 px-4 py-3">
          <FileText size={18} className="shrink-0 text-muted" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{resume.filename}</p>
            <p className="text-xs text-muted">
              {resume.keywords.length}{" "}
              <Trans id="settings.resume.keywordCount" comment="Keyword count label after resume processing">keywords extracted</Trans>
              {" · "}
              <Trans id="settings.resume.updated" comment="Resume last updated label">Updated</Trans>{" "}
              {new Date(resume.updatedAt).toLocaleDateString()}
            </p>
          </div>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="inline-flex items-center gap-1.5 rounded border border-divider px-3 py-1.5 text-xs text-muted hover:bg-border-soft disabled:opacity-50"
          >
            <Upload size={12} />
            <Trans id="settings.resume.replace" comment="Replace resume button label">Replace</Trans>
          </button>
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-divider px-6 py-8 text-center">
          <p className="mb-3 text-sm text-muted">
            <Trans id="settings.resume.empty" comment="Empty state text when no resume is uploaded">Upload your resume to enable AI fit analysis in the Queue.</Trans>
          </p>
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="inline-flex items-center gap-2 rounded bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
          >
            <Upload size={14} />
            {uploading
              ? t({ id: "settings.resume.uploading", comment: "Label while resume is being uploaded", message: "Uploading…" })
              : t({ id: "settings.resume.upload", comment: "Upload resume button label", message: "Upload resume" })}
          </button>
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.txt,text/plain,application/pdf"
        className="sr-only"
        onChange={handleFileChange}
      />

      {error && (
        <p className="text-xs text-red-500">{error}</p>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Update settings-loader to include resume data and render ResumeSettings**

Open `apps/web/app/[lang]/(app)/settings/settings-loader.tsx`.

Add the import at the top:
```typescript
import { getResume } from "@/lib/actions/resume";
import { ResumeSettings } from "@/components/settings/ResumeSettings";
import type { ResumeInfo } from "@/lib/actions/resume";
```

Update `SettingsData` type:
```typescript
type SettingsData = {
  jobLanguages: string[];
  displayCurrency: string;
  salaryPeriod: string | null;
  availableCurrencies: string[];
  availableLanguages: AvailableLanguage[];
  resume: ResumeInfo | null;  // ← add this
};
```

Update the `Promise.all` call:
```typescript
Promise.all([getPreferences(), getAvailableJobLanguages(), getCurrencyRates(), getResume()]).then(
  ([prefs, availableLanguages, currencyRates, resume]) => {
    setData({
      jobLanguages: prefs?.jobLanguages ?? [],
      displayCurrency: prefs?.displayCurrency ?? "EUR",
      salaryPeriod: prefs?.salaryPeriod ?? null,
      availableCurrencies: currencyRates.map((r) => r.currency),
      availableLanguages,
      resume,
    });
  },
);
```

Add `<ResumeSettings initialResume={data.resume} />` to the return JSX, below `<GeneralSettings .../>`:
```tsx
return (
  <div className="space-y-8">
    <GeneralSettings
      savedJobLanguages={data.jobLanguages}
      savedDisplayCurrency={data.displayCurrency}
      savedSalaryPeriod={data.salaryPeriod}
      availableCurrencies={data.availableCurrencies}
      availableLanguages={data.availableLanguages}
      locale={locale}
    />
    <ResumeSettings initialResume={data.resume} />
  </div>
);
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/settings/ResumeSettings.tsx \
        apps/web/app/[lang]/\(app\)/settings/settings-loader.tsx
git commit -m "feat(resume): add resume upload card to settings page"
```

---

## Task 10: Add Queue nav link

**Files:**
- Modify: `apps/web/src/components/AppHeader.tsx`

- [ ] **Step 1: Add Queue to the nav**

Open `apps/web/src/components/AppHeader.tsx`.

Add the `Inbox` icon to the import line (it already imports `Compass, Briefcase, Eye, Settings, LogIn, LogOut`):
```typescript
import { Compass, Briefcase, Eye, Inbox, Settings, LogIn, LogOut } from "lucide-react";
```

Add a `queueLabel` translation below the existing label definitions (around line 57):
```typescript
const queueLabel = t({ id: "app.header.nav.queue", comment: "Queue nav icon tooltip", message: "Queue" });
```

In the **desktop nav** (around line 146–159), add a `NavIcon` for Queue after `myJobs`:
```tsx
<NavIcon href={lp("/queue")} label={queueLabel}>
  <Inbox size={18} />
</NavIcon>
```

Full desktop nav after the change:
```tsx
<nav className="hidden items-center gap-1 md:flex">
  <NavIcon href={appHref} label={exploreLabel}>
    <Compass size={18} />
  </NavIcon>
  <NavIcon href={lp("/watchlists")} label={watchlistsLabel}>
    <Eye size={18} />
  </NavIcon>
  <NavIcon href={lp("/my-jobs")} label={myJobsLabel}>
    <Briefcase size={18} />
  </NavIcon>
  <NavIcon href={lp("/queue")} label={queueLabel}>
    <Inbox size={18} />
  </NavIcon>
  <NavIcon href={lp(siteConfig.nav.settings.href)} label={settingsLabel}>
    <Settings size={18} />
  </NavIcon>
</nav>
```

In the **mobile bottom bar** (around line 185–196), add a `BottomBarLink` for Queue after the `my-jobs` link:
```tsx
<BottomBarLink href={lp("/queue")} label={queueLabel}>
  <Inbox size={20} />
</BottomBarLink>
```

Full mobile bottom bar after the change:
```tsx
<nav className="fixed bottom-0 left-0 right-0 z-50 flex items-center border-t border-divider bg-surface-alpha backdrop-blur-md md:hidden">
  <BottomBarLink href={appHref} label={exploreLabel}>
    <Compass size={20} />
  </BottomBarLink>
  <BottomBarLink href={lp("/watchlists")} label={watchlistsLabel}>
    <Eye size={20} />
  </BottomBarLink>
  <BottomBarLink href={lp("/my-jobs")} label={myJobsLabel}>
    <Briefcase size={20} />
  </BottomBarLink>
  <BottomBarLink href={lp("/queue")} label={queueLabel}>
    <Inbox size={20} />
  </BottomBarLink>
  <span className="flex flex-1">
    {/* auth area unchanged */}
  </span>
</nav>
```

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/components/AppHeader.tsx
git commit -m "feat(queue): add Queue link to app nav (desktop + mobile)"
```

---

## Task 11: Build verification

**Files:** No new files.

- [ ] **Step 1: Run the full test suite**

```bash
cd apps/web && pnpm test
```

Expected: all vitest tests pass (including the 11 new tests from Tasks 2 and 3).

- [ ] **Step 2: Run a production build**

```bash
cd apps/web && pnpm build
```

Expected: build completes without TypeScript errors or missing module errors.

- [ ] **Step 3: Smoke test manually (dev server)**

```bash
cd apps/web && pnpm dev
```

- Navigate to `/en/explore` — Queue icon buttons appear on hover on each posting row and in the job detail panel header.
- Click Queue on a job — button transitions from outline to filled (optimistic update), toggle back works.
- Navigate to `/en/settings` — Resume section is visible with upload button.
- Upload a plain-text file — keywords are extracted and displayed.
- Navigate to `/en/queue` — queued jobs appear with "Pending analysis" state.
- Click "Analyze all" (requires `MINIMAX_API_KEY` in `.env.local`) — cards update every 2 s as results arrive.
- Queue nav item is visible in desktop header and mobile bottom bar.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final build verification for Job Fit Queue feature"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Queue button in job detail panel | Task 6 |
| Queue button (compact) on job card hover row | Task 6 |
| QueueProvider context (preloads on boot) | Task 5 |
| Resume upload in settings | Task 9 |
| Stop-word pre-processing | Task 2 |
| LLM keyword normalization | Task 2 |
| `user_resume` table | Task 1 |
| `job_queue` table | Task 1 |
| Queue page `/queue` | Task 8 |
| Queue page stats bar | Task 8 |
| Queue page analyzed/pending sections | Task 8 |
| QueueJobCard with score badge + keyword pills | Task 7 |
| Accent bar color-coded by score tier | Task 7 |
| analyzeQueue server action (batches of 3) | Task 3 |
| LLM prompt exact shape | Task 3 |
| Queue page polling every 2 s | Task 8 |
| "Analyze all (N)" button | Task 8 |
| Queue nav link | Task 10 |
| Remove (✕) button on queue card | Task 7 |

All spec requirements are covered.

**Type consistency check:**

- `QueueItemEntry` defined in Task 3 (`queue.ts`) and used in Tasks 7, 8 — consistent.
- `QueueStatus` defined in Task 3, used in Task 8 — consistent.
- `ResumeInfo` defined in Task 4 (`resume.ts`), used in Tasks 9, 8 — consistent.
- `scoreColor` and `formatScore` exported from Task 3, imported in Tasks 7, 8 — consistent.
- `toggleQueue` (server action in Task 3) called by `QueueProvider` in Task 5 — consistent.
- `getQueuedIds` (Task 3) called by bootstrap in Task 5 — consistent.
- `QueueProvider` wraps children in Task 5, `useQueue()` consumed in Tasks 6, 8 — consistent.
