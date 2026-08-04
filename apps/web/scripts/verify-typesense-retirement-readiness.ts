import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

import dotenv from "dotenv";
import postgres from "postgres";

export const MAX_MULTI_SEARCHES = 1;

type EvidenceFailure = {
  scope: "verifier" | "job_posting";
  kind:
    | "guard"
    | "empty_source"
    | "empty_typesense"
    | "coverage_regression";
  minimum?: number;
  actual?: number;
  delta?: number;
  message?: string;
};

export type ReadinessEvidence = {
  schemaVersion: 1;
  checkedAt: string;
  status: "passed" | "failed";
  ready: boolean;
  sourceAuthority: {
    postingFloor: "frozen_supabase_job_posting";
    taxonomies: "crawler_local_postgres_attestation";
  };
  requestBudget: {
    httpRequests: number;
    multiSearches: number;
    maximumHttpRequests: 1;
    maximumMultiSearches: typeof MAX_MULTI_SEARCHES;
  };
  posting: {
    frozenSourceRows: number;
    typesenseDocuments: number;
    coverageDelta: number;
    coverageNonRegressing: boolean;
  } | null;
  failures: EvidenceFailure[];
};

type MultiSearch = {
  collection: "job_posting";
  q: "*";
  query_by: "title";
  per_page: 0;
};

type MultiSearchResult = {
  found?: unknown;
  code?: unknown;
  error?: unknown;
};

class ReadinessGuardError extends Error {}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new ReadinessGuardError(`${name} must be set`);
  return value;
}

/** Refuse the Supabase transaction pooler for a drop-readiness proof. */
export function validateDatabaseUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new ReadinessGuardError("DATABASE_URL_UNPOOLED must be a valid URL");
  }
  if (url.port === "6543") {
    throw new ReadinessGuardError(
      "Refusing retirement readiness verification through the transaction pooler",
    );
  }
  return value;
}

function finiteCount(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new ReadinessGuardError(`${label} returned an invalid document count`);
  }
  return value;
}

/**
 * Supabase is frozen before this proof. Its job_posting count is a coverage
 * floor, not the live crawler source of truth. Exact posting reconciliation
 * and count-and-sample taxonomy readiness are proved by the bound crawler-host run.
 */
export function evaluatePostingReadiness(
  frozenSourceRows: number,
  typesenseDocuments: number,
  checkedAt = new Date().toISOString(),
): ReadinessEvidence {
  const sourceCount = finiteCount(frozenSourceRows, "job_posting source");
  const indexedCount = finiteCount(typesenseDocuments, "job_posting Typesense");
  const delta = indexedCount - sourceCount;
  const failures: EvidenceFailure[] = [];

  if (sourceCount === 0) {
    failures.push({ scope: "job_posting", kind: "empty_source" });
  }
  if (indexedCount === 0) {
    failures.push({ scope: "job_posting", kind: "empty_typesense" });
  }
  if (delta < 0) {
    failures.push({
      scope: "job_posting",
      kind: "coverage_regression",
      minimum: sourceCount,
      actual: indexedCount,
      delta,
    });
  }

  return {
    schemaVersion: 1,
    checkedAt,
    status: failures.length === 0 ? "passed" : "failed",
    ready: failures.length === 0,
    sourceAuthority: {
      postingFloor: "frozen_supabase_job_posting",
      taxonomies: "crawler_local_postgres_attestation",
    },
    requestBudget: {
      httpRequests: 1,
      multiSearches: 1,
      maximumHttpRequests: 1,
      maximumMultiSearches: MAX_MULTI_SEARCHES,
    },
    posting: {
      frozenSourceRows: sourceCount,
      typesenseDocuments: indexedCount,
      coverageDelta: delta,
      coverageNonRegressing: delta >= 0,
    },
    failures,
  };
}

export function buildPostingSearchPlan(): MultiSearch[] {
  return [{ collection: "job_posting", q: "*", query_by: "title", per_page: 0 }];
}

async function readFrozenPostingCount(databaseUrl: string): Promise<number> {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    connection: { application_name: "jobseek-typesense-retirement-posting-floor" },
  });
  try {
    return await sql.begin(async (tx) => {
      await tx`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`;
      await tx`SET LOCAL statement_timeout = '45s'`;
      const [row] = await tx<{ count: number }[]>`
        SELECT count(*)::integer AS count FROM public.job_posting
      `;
      if (!row) {
        throw new ReadinessGuardError("Could not read frozen job_posting count");
      }
      return finiteCount(row.count, "job_posting source");
    });
  } finally {
    await sql.end();
  }
}

async function fetchTypesensePostingCount(baseUrl: URL, apiKey: string): Promise<number> {
  const response = await fetch(new URL("/multi_search", baseUrl), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-TYPESENSE-API-KEY": apiKey,
    },
    body: JSON.stringify({ searches: buildPostingSearchPlan() }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    throw new ReadinessGuardError(
      `Typesense multi-search returned HTTP ${response.status}`,
    );
  }

  const payload: unknown = await response.json();
  if (typeof payload !== "object" || payload === null) {
    throw new ReadinessGuardError("Typesense multi-search returned an invalid payload");
  }
  const results = Reflect.get(payload, "results");
  if (!Array.isArray(results) || results.length !== 1) {
    throw new ReadinessGuardError("Typesense posting-count search was incomplete");
  }
  const result = results[0] as MultiSearchResult;
  if (typeof result.error === "string" || typeof result.code === "number") {
    throw new ReadinessGuardError("Typesense posting-count search failed");
  }
  return finiteCount(result.found, "job_posting Typesense");
}

function safeFailure(error: unknown): EvidenceFailure {
  return {
    scope: "verifier",
    kind: "guard",
    message:
      error instanceof ReadinessGuardError
        ? error.message
        : "Live retirement readiness verification could not complete",
  };
}

export function failureEvidence(
  error: unknown,
  checkedAt = new Date().toISOString(),
): ReadinessEvidence {
  return {
    schemaVersion: 1,
    checkedAt,
    status: "failed",
    ready: false,
    sourceAuthority: {
      postingFloor: "frozen_supabase_job_posting",
      taxonomies: "crawler_local_postgres_attestation",
    },
    requestBudget: {
      httpRequests: 0,
      multiSearches: 0,
      maximumHttpRequests: 1,
      maximumMultiSearches: MAX_MULTI_SEARCHES,
    },
    posting: null,
    failures: [safeFailure(error)],
  };
}

export type EvidenceWriter = {
  writeOutput: (path: string, rendered: string) => void;
  writeStdout: (rendered: string) => void;
};

const defaultEvidenceWriter: EvidenceWriter = {
  writeOutput: (path, rendered) =>
    writeFileSync(path, rendered, { encoding: "utf8", mode: 0o600 }),
  writeStdout: (rendered) => process.stdout.write(rendered),
};

export function persistEvidence(
  evidence: ReadinessEvidence,
  outputPath?: string,
  writer: EvidenceWriter = defaultEvidenceWriter,
): string {
  const rendered = `${JSON.stringify(evidence, null, 2)}\n`;
  if (outputPath) writer.writeOutput(outputPath, rendered);
  writer.writeStdout(rendered);
  return rendered;
}

function parseOutputPath(argv: string[]): string | undefined {
  const args = argv.filter((argument) => argument !== "--");
  if (args.length > 1) {
    throw new ReadinessGuardError(
      "Usage: tsx scripts/verify-typesense-retirement-readiness.ts [output.json]",
    );
  }
  return args[0];
}

async function runCli(): Promise<number> {
  const outputPath = (() => {
    try {
      return parseOutputPath(process.argv.slice(2));
    } catch (error) {
      persistEvidence(failureEvidence(error));
      return null;
    }
  })();
  if (outputPath === null) return 1;

  dotenv.config({
    path: process.env.JOBSEEK_ENV_FILE ?? ".env.local",
    quiet: true,
  });

  let evidence: ReadinessEvidence;
  try {
    const databaseUrl = validateDatabaseUrl(
      requiredEnvironment("DATABASE_URL_UNPOOLED"),
    );
    const protocol = requiredEnvironment("TYPESENSE_PROTOCOL");
    const host = requiredEnvironment("TYPESENSE_HOST");
    const port = requiredEnvironment("TYPESENSE_PORT");
    const apiKey = requiredEnvironment("TYPESENSE_SEARCH_KEY");
    const baseUrl = new URL(`${protocol}://${host}:${port}`);

    const frozenSourceRows = await readFrozenPostingCount(databaseUrl);
    const typesenseDocuments = await fetchTypesensePostingCount(baseUrl, apiKey);
    evidence = evaluatePostingReadiness(frozenSourceRows, typesenseDocuments);
  } catch (error) {
    evidence = failureEvidence(error);
  }

  try {
    persistEvidence(evidence, outputPath);
  } catch {
    persistEvidence(
      failureEvidence(
        new ReadinessGuardError("Could not write the requested evidence output"),
      ),
    );
    return 1;
  }
  return evidence.ready ? 0 : 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void runCli().then((exitCode) => {
    process.exitCode = exitCode;
  });
}
