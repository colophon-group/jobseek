import { createHash } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { pathToFileURL } from "node:url";

import dotenv from "dotenv";
import postgres from "postgres";

type SavedPostingSource = {
  postingId: string;
  title: string | null;
  sourceUrl: string | null;
  firstSeenAt: number | null;
  isActive: boolean | null;
  salaryMin: number | null;
  salaryMax: number | null;
  salaryCurrency: string | null;
  salaryPeriod: string | null;
  companyId: string | null;
  companyName: string | null;
  companySlug: string | null;
  companyIcon: string | null;
};

type TypesensePostingDocument = Record<string, unknown>;

// Cloudflare protects the public Typesense endpoint with a per-minute request
// budget. Keep this proof below that budget so a complete saved-job audit does
// not turn healthy documents into verifier-induced HTTP 429 failures.
const REQUEST_INTERVAL_MS = 400;
const RATE_LIMIT_FALLBACK_MS = 65_000;
const RETRIEVE_ATTEMPTS = 3;

const REQUIRED_TEXT_FIELDS = [
  ["id", "postingId"],
  ["title", "title"],
  ["source_url", "sourceUrl"],
  ["company_id", "companyId"],
  ["company_name", "companyName"],
  ["company_slug", "companySlug"],
] as const;

const OPTIONAL_TEXT_FIELDS = [
  ["salary_currency", "salaryCurrency"],
  ["salary_period", "salaryPeriod"],
  ["company_icon", "companyIcon"],
] as const;

const OPTIONAL_NUMBER_FIELDS = [
  ["salary_min", "salaryMin"],
  ["salary_max", "salaryMax"],
] as const;

function normalizedOptional(value: unknown): unknown {
  return value === undefined ? null : value;
}

/** Return exact parity failures between the mirror source and one index doc. */
export function comparePostingSnapshot(
  source: SavedPostingSource,
  document: TypesensePostingDocument,
): string[] {
  const failures: string[] = [];

  for (const [documentField, sourceField] of REQUIRED_TEXT_FIELDS) {
    const actual = document[documentField];
    const expected = source[sourceField];
    if (
      typeof actual !== "string" ||
      actual.trim() === "" ||
      typeof expected !== "string" ||
      expected.trim() === "" ||
      actual !== expected
    ) {
      failures.push(documentField);
    }
  }

  if (
    typeof document.first_seen_at !== "number" ||
    !Number.isFinite(document.first_seen_at) ||
    document.first_seen_at !== source.firstSeenAt
  ) {
    failures.push("first_seen_at");
  }
  if (
    typeof document.is_active !== "boolean" ||
    document.is_active !== source.isActive
  ) {
    failures.push("is_active");
  }

  for (const [documentField, sourceField] of OPTIONAL_NUMBER_FIELDS) {
    const actual = normalizedOptional(document[documentField]);
    const expected = source[sourceField];
    if (
      (actual !== null &&
        (typeof actual !== "number" || !Number.isFinite(actual))) ||
      actual !== expected
    ) {
      failures.push(documentField);
    }
  }

  for (const [documentField, sourceField] of OPTIONAL_TEXT_FIELDS) {
    const actual = normalizedOptional(document[documentField]);
    const expected = source[sourceField];
    if ((actual !== null && typeof actual !== "string") || actual !== expected) {
      failures.push(documentField);
    }
  }

  return failures;
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

async function retrievePosting(
  baseUrl: string,
  apiKey: string,
  postingId: string,
): Promise<TypesensePostingDocument> {
  const url = new URL(
    `/collections/job_posting/documents/${encodeURIComponent(postingId)}`,
    baseUrl,
  );
  for (let attempt = 1; attempt <= RETRIEVE_ATTEMPTS; attempt += 1) {
    const response = await fetch(url, {
      headers: { "X-TYPESENSE-API-KEY": apiKey },
      signal: AbortSignal.timeout(10_000),
    });
    if (response.ok) {
      return (await response.json()) as TypesensePostingDocument;
    }
    if (response.status !== 429 || attempt === RETRIEVE_ATTEMPTS) {
      throw new Error(`Typesense retrieve returned HTTP ${response.status}`);
    }

    const retryAfter = Number(response.headers.get("retry-after"));
    await delay(
      Number.isFinite(retryAfter) && retryAfter > 0
        ? retryAfter * 1_000
        : RATE_LIMIT_FALLBACK_MS,
    );
  }

  throw new Error("Typesense retrieve exhausted retries");
}

async function main() {
  dotenv.config({
    path: process.env.JOBSEEK_ENV_FILE ?? ".env.local",
    quiet: true,
  });

  const databaseUrl = requiredEnvironment("DATABASE_URL_UNPOOLED");
  if (new URL(databaseUrl).port === "6543") {
    throw new Error("Refusing verification through the transaction pooler");
  }
  const protocol = requiredEnvironment("TYPESENSE_PROTOCOL");
  const host = requiredEnvironment("TYPESENSE_HOST");
  const port = requiredEnvironment("TYPESENSE_PORT");
  const apiKey = requiredEnvironment("TYPESENSE_SEARCH_KEY");
  const baseUrl = new URL(`${protocol}://${host}:${port}`);

  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: "jobseek-saved-job-typesense-coverage" },
  });

  try {
    const { rows, savedJobs } = await sql.begin(async (tx) => {
      await tx`SET TRANSACTION READ ONLY`;
      await tx`SET LOCAL statement_timeout = '30s'`;
      const [{ count: savedJobs }] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count FROM public.saved_job
      `;
      const rows = await tx<SavedPostingSource[]>`
        SELECT DISTINCT ON (jp.id)
          jp.id::text AS "postingId",
          jp.titles[1] AS title,
          jp.source_url AS "sourceUrl",
          floor(extract(epoch FROM jp.first_seen_at))::integer AS "firstSeenAt",
          jp.is_active AS "isActive",
          jp.salary_min AS "salaryMin",
          jp.salary_max AS "salaryMax",
          jp.salary_currency AS "salaryCurrency",
          jp.salary_period AS "salaryPeriod",
          c.id::text AS "companyId",
          c.name AS "companyName",
          c.slug AS "companySlug",
          c.icon AS "companyIcon"
        FROM public.saved_job AS sj
        LEFT JOIN public.job_posting AS jp ON jp.id = sj.job_posting_id
        LEFT JOIN public.company AS c ON c.id = jp.company_id
        ORDER BY jp.id
      `;
      return { rows, savedJobs };
    });
    if (rows.length === 0) {
      throw new Error("No saved posting sources found; refusing an unproven cutover");
    }

    const failures: {
      postingId: string;
      kind: "retrieve" | "parity";
      fields: string[];
    }[] = [];
    let retrieved = 0;
    for (const [index, row] of rows.entries()) {
      try {
        const document = await retrievePosting(
          baseUrl.toString(),
          apiKey,
          row.postingId,
        );
        retrieved += 1;
        const fields = comparePostingSnapshot(row, document);
        if (fields.length > 0) {
          failures.push({ postingId: row.postingId, kind: "parity", fields });
        }
      } catch (error) {
        failures.push({
          postingId: row.postingId,
          kind: "retrieve",
          fields: [error instanceof Error ? error.message : "retrieve failed"],
        });
      }

      if (index + 1 < rows.length) await delay(REQUEST_INTERVAL_MS);
    }

    const retrievalFailures = failures.filter(
      (failure) => failure.kind === "retrieve",
    ).length;
    const parityFailures = failures.length - retrievalFailures;
    const evidence = {
      checkedAt: new Date().toISOString(),
      savedJobs,
      uniqueSavedPostings: rows.length,
      documentsRetrieved: retrieved,
      failures: failures.length,
      retrievalFailures,
      parityFailures,
      postingIdDigest: createHash("sha256")
        .update(rows.map((row) => row.postingId).join("\n"))
        .digest("hex"),
      failureSample: failures.slice(0, 20),
    };
    process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
    if (failures.length > 0 || retrieved !== rows.length) {
      throw new Error(
        `${failures.length} of ${rows.length} saved posting documents failed coverage`,
      );
    }
  } finally {
    await sql.end();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error("Saved-job Typesense coverage failed:", error);
    process.exitCode = 1;
  });
}
