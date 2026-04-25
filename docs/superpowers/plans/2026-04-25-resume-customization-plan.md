# Resume Customization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users generate a tailored `.tex` resume per queued job — the AI makes contextually valid keyword substitutions in work experience bullets to maximize fit score without fabricating experience.

**Architecture:** A new `customizeResume` server action calls the Anthropic API (Sonnet 4.6, GPT-4o fallback) with the user's raw `.tex` source, the job's missing keywords, and the JD text. The result is stored in R2 under `resumes/{user_id}/{posting_id}.tex` and referenced via `job_queue.customized_r2_key`. A new download route serves the file. The Queue job card gains "Generate resume" and "Remove customized resume" buttons; Settings gains `.tex` file acceptance.

**Tech Stack:** Next.js 15 App Router, Drizzle ORM (PostgreSQL), `@aws-sdk/client-s3` + `@aws-sdk/s3-request-presigner` (R2), Anthropic API (`claude-sonnet-4-6`) + OpenAI API (`gpt-4o`) fallback, Vitest, Tailwind CSS.

---

## File Map

### New files

| Path | Responsibility |
|---|---|
| `apps/web/src/lib/resume/strip-latex.ts` | `stripLatexCommands(source: string): string` — plain text extractor for keyword pipeline |
| `apps/web/src/lib/resume/__tests__/strip-latex.test.ts` | Vitest unit tests for LaTeX stripping |
| `apps/web/src/lib/actions/customize-resume.ts` | `customizeResume`, `removeCustomizedResume`, `getCustomizedResumeUrl` server actions |
| `apps/web/src/lib/actions/__tests__/customize-resume.test.ts` | Vitest unit tests for pure helpers |
| `apps/web/app/api/resumes/[jobQueueId]/route.ts` | Download route: auth check → R2 fetch → stream `.tex` |

### Modified files

| Path | Change |
|---|---|
| `apps/web/src/db/schema.ts` | Add `latex_source` to `userResume`; add `customized_r2_key`, `customized_at` to `jobQueue` |
| `apps/web/drizzle/0077_add_resume_customization.sql` | Migration SQL |
| `apps/web/src/lib/actions/resume.ts` | Store `latex_source` on `.tex` upload; call `stripLatexCommands` for keyword extraction from LaTeX |
| `apps/web/src/components/queue/queue-job-card.tsx` | Add "Generate resume" + "Remove customized resume" buttons |
| `apps/web/src/components/settings/ResumeSettings.tsx` | Accept `.tex` in file input; store raw source |
| `apps/web/package.json` | Add `@aws-sdk/s3-request-presigner` |

---

## Task 1: Add presigner dependency + DB migration

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/src/db/schema.ts`
- Create: `apps/web/drizzle/0077_add_resume_customization.sql`

- [ ] **Step 1: Install presigner package**

```bash
cd apps/web && pnpm add @aws-sdk/s3-request-presigner
```

Expected: package added to `package.json` and `pnpm-lock.yaml` updated.

- [ ] **Step 2: Add columns to Drizzle schema**

Open `apps/web/src/db/schema.ts`.

Find the `userResume` table definition and add `latexSource`:

```typescript
export const userResume = pgTable("user_resume", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: text("user_id")
    .notNull()
    .unique()
    .references(() => user.id, { onDelete: "cascade" }),
  filename: text("filename").notNull(),
  keywords: text("keywords").array().notNull().default([]),
  latexSource: text("latex_source"),   // ← add this line
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .defaultNow()
    .$onUpdate(() => new Date())
    .notNull(),
});
```

Find the `jobQueue` table definition and add `customizedR2Key` + `customizedAt`:

```typescript
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
    customizedR2Key: text("customized_r2_key"),      // ← add this line
    customizedAt: timestamp("customized_at", { withTimezone: true }),  // ← add this line
  },
  (table) => [
    uniqueIndex("idx_jq_user_posting").on(table.userId, table.postingId),
    index("idx_jq_user_added").on(table.userId, table.addedAt),
  ],
);
```

- [ ] **Step 3: Write migration SQL**

Create `apps/web/drizzle/0077_add_resume_customization.sql`:

```sql
ALTER TABLE "user_resume"
  ADD COLUMN "latex_source" text;

ALTER TABLE "job_queue"
  ADD COLUMN "customized_r2_key" text,
  ADD COLUMN "customized_at" timestamp with time zone;
```

- [ ] **Step 4: Run the migration**

```bash
cd apps/web && pnpm db:migrate
```

Expected: migration runs without error. Verify with:
```bash
psql $DATABASE_URL -c "\d user_resume" | grep latex
psql $DATABASE_URL -c "\d job_queue" | grep customized
```

Expected output includes `latex_source` and `customized_r2_key` columns.

- [ ] **Step 5: Commit**

```bash
git add apps/web/package.json apps/web/pnpm-lock.yaml \
        apps/web/src/db/schema.ts \
        apps/web/drizzle/0077_add_resume_customization.sql
git commit -m "feat(db): add latex_source and customized_r2_key columns"
```

---

## Task 2: LaTeX text stripper

**Files:**
- Create: `apps/web/src/lib/resume/strip-latex.ts`
- Create: `apps/web/src/lib/resume/__tests__/strip-latex.test.ts`

- [ ] **Step 1: Write failing tests**

Create `apps/web/src/lib/resume/__tests__/strip-latex.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { stripLatexCommands } from "@/lib/resume/strip-latex";

describe("stripLatexCommands", () => {
  it("strips backslash commands", () => {
    const input = "\\textbf{Go} and \\textit{PostgreSQL}";
    expect(stripLatexCommands(input)).toContain("Go");
    expect(stripLatexCommands(input)).toContain("PostgreSQL");
    expect(stripLatexCommands(input)).not.toContain("\\textbf");
  });

  it("strips LaTeX environments", () => {
    const input = "\\begin{itemize}\n\\item Kubernetes\n\\end{itemize}";
    expect(stripLatexCommands(input)).toContain("Kubernetes");
    expect(stripLatexCommands(input)).not.toContain("\\begin");
    expect(stripLatexCommands(input)).not.toContain("\\item");
  });

  it("strips preamble commands", () => {
    const input = "\\documentclass{article}\n\\usepackage{hyperref}\nJavaScript";
    expect(stripLatexCommands(input)).toContain("JavaScript");
    expect(stripLatexCommands(input)).not.toContain("\\documentclass");
  });

  it("preserves plain text words", () => {
    const input = "Built distributed systems with Go and React";
    const result = stripLatexCommands(input);
    expect(result).toContain("distributed");
    expect(result).toContain("Go");
    expect(result).toContain("React");
  });

  it("strips braces and ampersands (tabular separators)", () => {
    const input = "Java & Kotlin & Scala";
    const result = stripLatexCommands(input);
    expect(result).toContain("Java");
    expect(result).toContain("Kotlin");
    expect(result).not.toContain("&");
  });

  it("returns empty string for empty input", () => {
    expect(stripLatexCommands("")).toBe("");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd apps/web && pnpm test -- src/lib/resume/__tests__/strip-latex.test.ts
```

Expected: FAIL with "Cannot find module '@/lib/resume/strip-latex'"

- [ ] **Step 3: Create strip-latex module**

Create `apps/web/src/lib/resume/strip-latex.ts`:

```typescript
/**
 * Extracts plain text from a LaTeX source string.
 * Strips commands, environments, preamble, and special characters
 * to produce tokenizable text for the keyword extraction pipeline.
 */
export function stripLatexCommands(source: string): string {
  return source
    // Remove comments
    .replace(/%[^\n]*/g, " ")
    // Remove \begin{...} and \end{...}
    .replace(/\\(begin|end)\{[^}]*\}/g, " ")
    // Remove preamble commands that take arguments: \command{...}
    .replace(/\\[a-zA-Z]+\{[^}]*\}/g, " ")
    // Remove standalone commands: \command
    .replace(/\\[a-zA-Z]+\*?/g, " ")
    // Remove curly braces
    .replace(/[{}]/g, " ")
    // Remove tabular separators and special chars
    .replace(/[&~^_$\\]/g, " ")
    // Collapse whitespace
    .replace(/\s+/g, " ")
    .trim();
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd apps/web && pnpm test -- src/lib/resume/__tests__/strip-latex.test.ts
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/resume/strip-latex.ts \
        apps/web/src/lib/resume/__tests__/strip-latex.test.ts
git commit -m "feat(resume): add LaTeX text stripper for keyword extraction pipeline"
```

---

## Task 3: Resume action — store latex_source on .tex upload

**Files:**
- Modify: `apps/web/src/lib/actions/resume.ts`
- Modify: `apps/web/src/components/settings/ResumeSettings.tsx`

- [ ] **Step 1: Update resume.ts to store latex_source**

Open `apps/web/src/lib/actions/resume.ts`.

Add the import at the top:
```typescript
import { stripLatexCommands } from "@/lib/resume/strip-latex";
```

Update `ResumeInfo` type to include `hasLatexSource`:
```typescript
export type ResumeInfo = {
  filename: string;
  keywords: string[];
  updatedAt: string;
  hasLatexSource: boolean;
};
```

Update `getResume` to return `hasLatexSource`:
```typescript
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
    hasLatexSource: row.latexSource !== null,
  };
}
```

Update `uploadResume` to accept and store `latexSource`:
```typescript
export async function uploadResume(params: {
  filename: string;
  text: string;
  latexSource?: string;  // raw .tex content, only present for .tex uploads
}): Promise<ResumeInfo> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  // If .tex uploaded, strip LaTeX commands before keyword extraction
  const textForKeywords = params.latexSource
    ? stripLatexCommands(params.latexSource)
    : params.text;

  const keywords = await extractKeywords(textForKeywords);

  const [row] = await db
    .insert(userResume)
    .values({
      userId,
      filename: params.filename,
      keywords,
      latexSource: params.latexSource ?? null,
    })
    .onConflictDoUpdate({
      target: userResume.userId,
      set: {
        filename: params.filename,
        keywords,
        latexSource: params.latexSource ?? null,
        updatedAt: new Date(),
      },
    })
    .returning();

  return {
    filename: row.filename,
    keywords: row.keywords,
    updatedAt: row.updatedAt.toISOString(),
    hasLatexSource: row.latexSource !== null,
  };
}
```

- [ ] **Step 2: Update ResumeSettings to accept .tex and pass latexSource**

Open `apps/web/src/components/settings/ResumeSettings.tsx`.

Update the file input `accept` attribute:
```tsx
<input
  ref={fileInputRef}
  type="file"
  accept=".pdf,.txt,.tex,text/plain,application/pdf,application/x-tex"
  className="sr-only"
  onChange={handleFileChange}
/>
```

Update `handleFileChange` to detect `.tex` and pass `latexSource`:
```typescript
async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
  const file = e.target.files?.[0];
  if (!file) return;

  const isLatex = file.name.endsWith(".tex");
  const isPdf = file.type.includes("pdf");
  const isText = file.type.includes("text") || isLatex;

  if (!isPdf && !isText) {
    setError(t({ id: "settings.resume.invalidType", comment: "Error when wrong file type is uploaded", message: "Only PDF, plain text, or .tex files are supported." }));
    return;
  }

  setError(null);
  setUploading(true);

  try {
    let text = "";
    let latexSource: string | undefined;

    if (isLatex) {
      latexSource = await file.text();
      text = latexSource; // strip-latex runs server-side inside uploadResume
    } else if (isText) {
      text = await file.text();
    } else {
      // PDF: extract printable ASCII runs ≥ 4 chars
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

    const result = await uploadResume({ filename: file.name, text, latexSource });
    setResume(result);
  } catch {
    setError(t({ id: "settings.resume.uploadError", comment: "Generic upload error message", message: "Upload failed. Please try again." }));
  } finally {
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/actions/resume.ts \
        apps/web/src/components/settings/ResumeSettings.tsx
git commit -m "feat(resume): store latex_source on .tex upload, expose hasLatexSource"
```

---

## Task 4: customize-resume server action (with tests)

**Files:**
- Create: `apps/web/src/lib/actions/customize-resume.ts`
- Create: `apps/web/src/lib/actions/__tests__/customize-resume.test.ts`

- [ ] **Step 1: Write failing tests for pure helpers**

Create `apps/web/src/lib/actions/__tests__/customize-resume.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { buildCustomizePrompt, parseCustomizeResponse } from "@/lib/actions/customize-resume";

describe("buildCustomizePrompt", () => {
  it("includes missing keywords in user message", () => {
    const prompt = buildCustomizePrompt({
      title: "Backend Engineer",
      company: "Stripe",
      jdText: "We use Kafka and gRPC extensively.",
      missingKeywords: ["Kafka", "gRPC"],
      matchedKeywords: ["Go", "PostgreSQL"],
      latexSource: "\\begin{document}\\item Built APIs with Go\\end{document}",
    });
    expect(prompt.user).toContain("Kafka");
    expect(prompt.user).toContain("gRPC");
    expect(prompt.user).toContain("Go");
    expect(prompt.user).toContain("PostgreSQL");
    expect(prompt.user).toContain("Backend Engineer");
    expect(prompt.user).toContain("Stripe");
  });

  it("includes latex source in user message", () => {
    const prompt = buildCustomizePrompt({
      title: "SWE",
      company: "Acme",
      jdText: "Java required",
      missingKeywords: ["Java"],
      matchedKeywords: [],
      latexSource: "\\item Developed systems",
    });
    expect(prompt.user).toContain("\\item Developed systems");
  });

  it("system prompt contains all required rules", () => {
    const prompt = buildCustomizePrompt({
      title: "SWE",
      company: "Acme",
      jdText: "Java required",
      missingKeywords: [],
      matchedKeywords: [],
      latexSource: "",
    });
    expect(prompt.system).toContain("work experience bullet points");
    expect(prompt.system).toContain("one page");
    expect(prompt.system).toContain("customized_latex");
    expect(prompt.system).toContain("changes");
  });
});

describe("parseCustomizeResponse", () => {
  it("parses valid JSON response", () => {
    const raw = `{"changes":[{"original":"Built APIs with Java","replacement":"Built APIs with Kotlin","keyword_added":"Kotlin","rationale":"Kotlin is JVM-compatible"}],"customized_latex":"\\\\item Built APIs with Kotlin"}`;
    const result = parseCustomizeResponse(raw);
    expect(result).not.toBeNull();
    expect(result!.changes).toHaveLength(1);
    expect(result!.changes[0].keyword_added).toBe("Kotlin");
    expect(result!.customized_latex).toContain("Kotlin");
  });

  it("extracts JSON from markdown code block", () => {
    const raw = "```json\n{\"changes\":[],\"customized_latex\":\"hello\"}\n```";
    const result = parseCustomizeResponse(raw);
    expect(result).not.toBeNull();
    expect(result!.customized_latex).toBe("hello");
  });

  it("returns null for invalid JSON", () => {
    expect(parseCustomizeResponse("not json at all")).toBeNull();
  });

  it("returns null if required fields missing", () => {
    expect(parseCustomizeResponse('{"changes":[]}')).toBeNull();
    expect(parseCustomizeResponse('{"customized_latex":""}')).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd apps/web && pnpm test -- src/lib/actions/__tests__/customize-resume.test.ts
```

Expected: FAIL with "Cannot find module '@/lib/actions/customize-resume'"

- [ ] **Step 3: Create customize-resume server action**

Create `apps/web/src/lib/actions/customize-resume.ts`:

```typescript
"use server";

import { eq, and } from "drizzle-orm";
import { S3Client, PutObjectCommand, DeleteObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { GetObjectCommand } from "@aws-sdk/client-s3";
import { db } from "@/db";
import { jobQueue, userResume, jobPosting, company } from "@/db/schema";
import { getSessionUserId } from "@/lib/sessionCache";

// ── Types ────────────────────────────────────────────────────────────

export type CustomizeChange = {
  original: string;
  replacement: string;
  keyword_added: string;
  rationale: string;
};

export type CustomizeResult = {
  changes: CustomizeChange[];
  customized_latex: string;
};

// ── Pure helpers (exported for tests) ───────────────────────────────

export function buildCustomizePrompt(params: {
  title: string;
  company: string;
  jdText: string;
  missingKeywords: string[];
  matchedKeywords: string[];
  latexSource: string;
}): { system: string; user: string } {
  const system = `You are a resume editor. Given a LaTeX resume source, a list of missing keywords from a job description, and the job description itself, make targeted edits to the resume to naturally incorporate missing keywords.

Rules:
1. Only edit work experience bullet points — never touch contact info, education, skills section structure, LaTeX preamble, or column/spacing definitions.
2. Focus edits on the most recent experience entry first, then earlier entries if needed.
3. Never fabricate experience. Only substitute within compatible technology ecosystems:
   - JVM: Java ↔ Kotlin ↔ Scala (context-dependent)
   - Scripting/backend: Python ↔ TypeScript (only for scripting/tooling contexts, not web frameworks)
   - Container orchestration: Docker ↔ Kubernetes (only if candidate already has containerization)
   - Message queues: mention Kafka if the candidate has any event-driven or async experience
   Do NOT pair incompatible stacks (e.g., Python + Spring Boot, PHP + Go microservices).
4. Preserve all LaTeX formatting exactly: alignment environments, column widths, spacing commands, custom macros. The document must remain compilable and one page.
5. Make the minimum changes needed. Do not rewrite bullets that don't need changing.
6. Return a JSON object with exactly two fields:
   - "changes": array of {original, replacement, keyword_added, rationale}
   - "customized_latex": the full modified .tex source as a string`;

  const user = `Missing keywords: [${params.missingKeywords.join(", ")}]
Matched keywords: [${params.matchedKeywords.join(", ")}]

Job: ${params.title} at ${params.company}

${params.jdText}

Resume LaTeX:
${params.latexSource}`;

  return { system, user };
}

export function parseCustomizeResponse(raw: string): CustomizeResult | null {
  // Extract JSON from possible markdown code block
  const jsonMatch = raw.match(/```(?:json)?\s*([\s\S]*?)```/) ?? raw.match(/(\{[\s\S]*\})/);
  if (!jsonMatch) return null;

  try {
    const parsed = JSON.parse(jsonMatch[1] ?? jsonMatch[0]) as unknown;
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      !Array.isArray((parsed as Record<string, unknown>).changes) ||
      typeof (parsed as Record<string, unknown>).customized_latex !== "string"
    ) {
      return null;
    }
    return parsed as CustomizeResult;
  } catch {
    return null;
  }
}

// ── R2 client ────────────────────────────────────────────────────────

let _r2: S3Client | null = null;

function getR2Client(): S3Client {
  if (_r2) return _r2;
  const endpoint = process.env.R2_ENDPOINT_URL;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  if (!endpoint || !accessKeyId || !secretAccessKey) {
    throw new Error("R2 credentials not configured");
  }
  _r2 = new S3Client({ endpoint, region: "auto", credentials: { accessKeyId, secretAccessKey } });
  return _r2;
}

function getR2Bucket(): string {
  const bucket = process.env.R2_BUCKET;
  if (!bucket) throw new Error("R2_BUCKET not set");
  return bucket;
}

// ── LLM call (Anthropic primary, GPT-4o fallback) ──────────────────

async function callLlm(system: string, user: string): Promise<string> {
  // Try Anthropic Sonnet 4.6 first
  const anthropicKey = process.env.ANTHROPIC_API_KEY;
  if (anthropicKey) {
    try {
      const resp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "x-api-key": anthropicKey,
          "anthropic-version": "2023-06-01",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          model: "claude-sonnet-4-6",
          max_tokens: 4096,
          system,
          messages: [{ role: "user", content: user }],
        }),
      });

      if (resp.ok) {
        const data = (await resp.json()) as {
          content?: { type: string; text: string }[];
        };
        const text = data.content?.find((b) => b.type === "text")?.text ?? "";
        if (text) return text;
      }
    } catch {
      // fall through to GPT-4o
    }
  }

  // Fallback: GPT-4o
  const openaiKey = process.env.OPENAI_API_KEY;
  if (!openaiKey) throw new Error("No LLM API key configured (ANTHROPIC_API_KEY or OPENAI_API_KEY)");

  const resp = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${openaiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "gpt-4o",
      max_tokens: 4096,
      messages: [
        { role: "system", content: system },
        { role: "user", content: user },
      ],
    }),
  });

  if (!resp.ok) throw new Error(`GPT-4o request failed: ${resp.status}`);

  const data = (await resp.json()) as {
    choices?: { message?: { content?: string } }[];
  };
  return data.choices?.[0]?.message?.content ?? "";
}

// ── Server actions ───────────────────────────────────────────────────

export async function customizeResume(
  jobQueueId: string,
): Promise<{ success: boolean; error?: string }> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  // Fetch resume latex source
  const [resume] = await db
    .select({ latexSource: userResume.latexSource })
    .from(userResume)
    .where(eq(userResume.userId, userId))
    .limit(1);

  if (!resume?.latexSource) {
    return { success: false, error: "No LaTeX resume uploaded. Upload a .tex file in Settings." };
  }

  // Fetch queue item with job data
  const [item] = await db
    .select({
      postingId: jobQueue.postingId,
      missingKeywords: jobQueue.missingKeywords,
      matchedKeywords: jobQueue.matchedKeywords,
      title: jobPosting.titles,
      companyName: company.name,
      descriptionR2Hash: jobPosting.descriptionR2Hash,
    })
    .from(jobQueue)
    .innerJoin(jobPosting, eq(jobQueue.postingId, jobPosting.id))
    .innerJoin(company, eq(jobPosting.companyId, company.id))
    .where(and(eq(jobQueue.id, jobQueueId), eq(jobQueue.userId, userId)))
    .limit(1);

  if (!item) return { success: false, error: "Queue item not found." };

  // Fetch JD text from R2
  let jdText = "";
  const r2Base = process.env.R2_PUBLIC_URL ?? "";
  if (item.descriptionR2Hash && r2Base) {
    try {
      const r = await fetch(`${r2Base}/${item.descriptionR2Hash}.html`);
      if (r.ok) {
        const html = await r.text();
        jdText = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 6000);
      }
    } catch {
      // proceed with empty jdText
    }
  }

  const title = (item.title as string[] | null)?.[0] ?? "Unknown role";
  const { system, user } = buildCustomizePrompt({
    title,
    company: item.companyName,
    jdText,
    missingKeywords: item.missingKeywords ?? [],
    matchedKeywords: item.matchedKeywords ?? [],
    latexSource: resume.latexSource,
  });

  let rawResponse: string;
  try {
    rawResponse = await callLlm(system, user);
  } catch (err) {
    return { success: false, error: String(err) };
  }

  const result = parseCustomizeResponse(rawResponse);
  if (!result) {
    return { success: false, error: "LLM returned an unexpected format. Please try again." };
  }

  // Upload to R2
  const r2Key = `resumes/${userId}/${item.postingId}.tex`;
  await getR2Client().send(
    new PutObjectCommand({
      Bucket: getR2Bucket(),
      Key: r2Key,
      Body: result.customized_latex,
      ContentType: "text/x-tex",
    }),
  );

  // Upsert DB
  await db
    .update(jobQueue)
    .set({ customizedR2Key: r2Key, customizedAt: new Date() })
    .where(eq(jobQueue.id, jobQueueId));

  return { success: true };
}

export async function removeCustomizedResume(
  jobQueueId: string,
): Promise<void> {
  const userId = await getSessionUserId();
  if (!userId) throw new Error("Not authenticated");

  const [item] = await db
    .select({ customizedR2Key: jobQueue.customizedR2Key })
    .from(jobQueue)
    .where(and(eq(jobQueue.id, jobQueueId), eq(jobQueue.userId, userId)))
    .limit(1);

  if (!item?.customizedR2Key) return;

  // Delete from R2
  try {
    await getR2Client().send(
      new DeleteObjectCommand({ Bucket: getR2Bucket(), Key: item.customizedR2Key }),
    );
  } catch {
    // Best-effort — clear DB reference regardless
  }

  await db
    .update(jobQueue)
    .set({ customizedR2Key: null, customizedAt: null })
    .where(eq(jobQueue.id, jobQueueId));
}

export async function getCustomizedResumeUrl(
  jobQueueId: string,
): Promise<string | null> {
  const userId = await getSessionUserId();
  if (!userId) return null;

  const [item] = await db
    .select({ customizedR2Key: jobQueue.customizedR2Key })
    .from(jobQueue)
    .where(and(eq(jobQueue.id, jobQueueId), eq(jobQueue.userId, userId)))
    .limit(1);

  if (!item?.customizedR2Key) return null;

  const url = await getSignedUrl(
    getR2Client(),
    new GetObjectCommand({
      Bucket: getR2Bucket(),
      Key: item.customizedR2Key,
      ResponseContentDisposition: `attachment; filename="resume-customized.tex"`,
    }),
    { expiresIn: 300 }, // 5 minutes
  );

  return url;
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd apps/web && pnpm test -- src/lib/actions/__tests__/customize-resume.test.ts
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/actions/customize-resume.ts \
        apps/web/src/lib/actions/__tests__/customize-resume.test.ts
git commit -m "feat(resume): add customizeResume/removeCustomizedResume/getCustomizedResumeUrl server actions"
```

---

## Task 5: Queue job card — Generate resume + Remove buttons

**Files:**
- Modify: `apps/web/src/components/queue/queue-job-card.tsx`
- Modify: `apps/web/src/lib/actions/queue.ts` (add `customizedR2Key`, `customizedAt` to `QueueItemEntry`)

- [ ] **Step 1: Extend QueueItemEntry type**

Open `apps/web/src/lib/actions/queue.ts`.

Add two fields to `QueueItemEntry`:
```typescript
export type QueueItemEntry = {
  id: string;
  postingId: string;
  addedAt: string;
  title: string | null;
  sourceUrl: string;
  companyId: string;
  companyName: string;
  companyIcon: string | null;
  companySlug: string;
  locations: string;
  overlapScore: number | null;
  matchedKeywords: string[] | null;
  missingKeywords: string[] | null;
  fitExplanation: string | null;
  analyzedAt: string | null;
  customizedR2Key: string | null;   // ← add
  customizedAt: string | null;      // ← add
};
```

Update `getQueueItems` select block to include the new columns:
```typescript
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
    customizedR2Key: jobQueue.customizedR2Key,   // ← add
    customizedAt: jobQueue.customizedAt,          // ← add
    title: jobPosting.titles,
    sourceUrl: jobPosting.sourceUrl,
    companyId: company.id,
    companyName: company.name,
    companyIcon: company.icon,
    companySlug: company.slug,
  })
  // ... rest unchanged
```

Update the `.map()` return to include the new fields:
```typescript
return rows.map((r) => ({
  // ... existing fields ...
  customizedR2Key: r.customizedR2Key ?? null,
  customizedAt: r.customizedAt ? r.customizedAt.toISOString() : null,
}));
```

- [ ] **Step 2: Add resume buttons to QueueJobCard**

Open `apps/web/src/components/queue/queue-job-card.tsx`.

Add imports at top:
```typescript
import { useState } from "react";
import { Download, FileText, Trash2 } from "lucide-react";
import { customizeResume, removeCustomizedResume, getCustomizedResumeUrl } from "@/lib/actions/customize-resume";
```

Update the component signature to accept `hasLatexSource`:
```typescript
interface QueueJobCardProps {
  item: QueueItemEntry;
  onRemove: (postingId: string) => void;
  hasLatexSource: boolean;
}

export function QueueJobCard({ item, onRemove, hasLatexSource }: QueueJobCardProps) {
```

Add state for generation loading inside the component (after existing `const isAnalyzed` line):
```typescript
const [generating, setGenerating] = useState(false);
const [generateError, setGenerateError] = useState<string | null>(null);
const [localCustomizedKey, setLocalCustomizedKey] = useState(item.customizedR2Key);
```

Add handler functions inside the component:
```typescript
async function handleGenerate() {
  setGenerating(true);
  setGenerateError(null);
  try {
    const result = await customizeResume(item.id);
    if (result.success) {
      // Mark as having a customized resume (key is set server-side; refetch on next full load)
      setLocalCustomizedKey(`resumes/${item.postingId}.tex`);
    } else {
      setGenerateError(result.error ?? "Generation failed.");
    }
  } catch {
    setGenerateError("Generation failed. Please try again.");
  } finally {
    setGenerating(false);
  }
}

async function handleDownload() {
  const url = await getCustomizedResumeUrl(item.id);
  if (url) {
    const a = document.createElement("a");
    a.href = url;
    a.download = "resume-customized.tex";
    a.click();
  }
}

async function handleRemoveCustomized() {
  await removeCustomizedResume(item.id);
  setLocalCustomizedKey(null);
}
```

Add resume action row to the card JSX, after the `fitExplanation` paragraph and before the closing `</div>` of the content area:

```tsx
{/* Resume customization actions — only on analyzed cards */}
{isAnalyzed && (
  <div className="flex flex-wrap items-center gap-2 border-t border-divider pt-2">
    {!localCustomizedKey ? (
      <>
        <button
          onClick={handleGenerate}
          disabled={generating || !hasLatexSource}
          title={!hasLatexSource ? "Upload your .tex in Settings to enable this" : undefined}
          className="inline-flex items-center gap-1.5 rounded border border-indigo-400 px-2.5 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-indigo-600 dark:text-indigo-400 dark:hover:bg-indigo-950"
        >
          <FileText size={12} />
          {generating ? "Generating…" : "Generate resume"}
        </button>
        {generateError && (
          <span className="text-xs text-red-500">{generateError}</span>
        )}
      </>
    ) : (
      <>
        <button
          onClick={handleDownload}
          className="inline-flex items-center gap-1.5 rounded border border-emerald-400 px-2.5 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-50 dark:border-emerald-600 dark:text-emerald-400 dark:hover:bg-emerald-950"
        >
          <Download size={12} />
          Download .tex
        </button>
        <button
          onClick={handleRemoveCustomized}
          className="inline-flex items-center gap-1.5 rounded border border-divider px-2.5 py-1 text-xs text-muted hover:bg-border-soft"
        >
          <Trash2 size={12} />
          Remove
        </button>
      </>
    )}
  </div>
)}
```

- [ ] **Step 3: Update Queue page to pass hasLatexSource to QueueJobCard**

Open `apps/web/app/[lang]/(app)/queue/queue-page.tsx`.

The `resume` state already holds `ResumeInfo | null`. Pass `hasLatexSource` to each card:
```tsx
{analyzedItems.map((item) => (
  <QueueJobCard
    key={item.id}
    item={item}
    onRemove={handleRemove}
    hasLatexSource={resume?.hasLatexSource ?? false}
  />
))}
// Same for pendingItems:
{pendingItems.map((item) => (
  <QueueJobCard
    key={item.id}
    item={item}
    onRemove={handleRemove}
    hasLatexSource={resume?.hasLatexSource ?? false}
  />
))}
```

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/actions/queue.ts \
        apps/web/src/components/queue/queue-job-card.tsx \
        apps/web/app/[lang]/\(app\)/queue/queue-page.tsx
git commit -m "feat(resume): add Generate resume / Remove buttons to Queue job card"
```

---

## Task 6: Build verification

**Files:** No new files.

- [ ] **Step 1: Run the full test suite**

```bash
cd apps/web && pnpm test
```

Expected: all tests pass (13+ new tests: 6 strip-latex + 7 customize-resume).

- [ ] **Step 2: Type check**

```bash
cd apps/web && node_modules/.bin/tsc --noEmit
```

Expected: no TypeScript errors.

- [ ] **Step 3: Production build**

```bash
cd apps/web && pnpm build
```

Expected: build completes without errors.

- [ ] **Step 4: Smoke test (dev server)**

```bash
cd apps/web && pnpm dev
```

- Navigate to `/en/settings` — resume upload accepts `.tex` files.
- Upload a `.tex` file — card shows filename + keyword count.
- Navigate to `/en/queue` — analyzed job cards show "Generate resume" button (indigo outline).
- Click "Generate resume" — shows "Generating…" spinner, then transitions to "Download .tex" + "Remove" buttons.
- Click "Download .tex" — downloads the customized `.tex` file.
- Click "Remove" — reverts to "Generate resume" button.
- With no `.tex` uploaded — "Generate resume" button is disabled with tooltip.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: Phase 2-2 build verification complete"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `.tex` upload in Settings | Task 3 |
| `latex_source` stored in `user_resume` | Tasks 1, 3 |
| Keyword extraction from LaTeX via `stripLatexCommands` | Tasks 2, 3 |
| `customizedR2Key` + `customizedAt` in `job_queue` | Task 1 |
| "Generate resume" button on Queue job card | Task 5 |
| "Remove customized resume" button on Queue job card | Task 5 |
| Disabled state + tooltip when no `.tex` uploaded | Task 5 |
| `customizeResume` server action | Task 4 |
| `removeCustomizedResume` server action | Task 4 |
| `getCustomizedResumeUrl` server action (signed R2 URL) | Task 4 |
| Anthropic Sonnet 4.6 as primary LLM | Task 4 |
| GPT-4o fallback | Task 4 |
| R2 key: `resumes/{user_id}/{posting_id}.tex` | Task 4 |
| LLM prompt rules (ecosystem compatibility, one-page constraint) | Task 4 |
| 80% test coverage on pure helpers | Tasks 2, 4 |
| TDD (tests written before implementation) | Tasks 2, 4 |

All spec requirements covered.

**Type consistency:**

- `QueueItemEntry.customizedR2Key / customizedAt` added in Task 5 step 1; used in `QueueJobCard` Task 5 step 2 — consistent.
- `ResumeInfo.hasLatexSource` added in Task 3; consumed in Queue page Task 5 step 3 — consistent.
- `buildCustomizePrompt` / `parseCustomizeResponse` exported in Task 4; tested in Task 4 step 1 — consistent.
- `CustomizeChange` / `CustomizeResult` defined in Task 4; `parseCustomizeResponse` returns `CustomizeResult | null` — consistent.
