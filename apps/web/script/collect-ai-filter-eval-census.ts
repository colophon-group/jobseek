import { pathToFileURL } from "node:url";

import postgres from "postgres";

import {
  CensusGuardError,
  collectWatchlistFilterCensus,
  isValidCensusAuditIdentifier,
  serializeCensus,
  type CensusExecutor,
  type CensusQuery,
  type CensusRunContext,
} from "../src/lib/ai-filter-eval/census";

export const CENSUS_CONFIRMATION_FLAG =
  "--confirm-read-only-production-census";
export const CENSUS_DATABASE_ENV = "AI_FILTER_EVAL_DATABASE_URL";
export const CENSUS_APPROVAL_ID_ENV = "AI_FILTER_EVAL_APPROVAL_ID";
export const CENSUS_RUN_ID_ENV = "AI_FILTER_EVAL_RUN_ID";
export const CENSUS_APPROVED_HOST_ENV = "AI_FILTER_EVAL_APPROVED_HOST";
export const CENSUS_APPROVED_DATABASE_ENV =
  "AI_FILTER_EVAL_APPROVED_DATABASE";
export const CENSUS_APPROVED_ROLE_ENV = "AI_FILTER_EVAL_APPROVED_ROLE";
export const CENSUS_TLS_MODE = "verify-full" as const;

export type CensusInvocation = {
  databaseUrl: string;
  approvalId: string;
  runId: string;
  approvedHost: string;
  database: string;
  role: string;
};

function requireEnv(
  env: Readonly<Record<string, string | undefined>>,
  name: string,
  maxLength = 200,
): string {
  const value = env[name]?.trim();
  if (!value) throw new CensusGuardError(`${name} must be set`);
  if (value.length > maxLength || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new CensusGuardError(`${name} is invalid`);
  }
  return value;
}

export function requireCensusInvocation(
  argv: readonly string[],
  env: Readonly<Record<string, string | undefined>>,
): CensusInvocation {
  const args = argv.filter((argument) => argument !== "--");
  if (args.length !== 1 || args[0] !== CENSUS_CONFIRMATION_FLAG) {
    throw new CensusGuardError(
      `Refusing census without ${CENSUS_CONFIRMATION_FLAG}`,
    );
  }

  const databaseUrl = requireEnv(env, CENSUS_DATABASE_ENV, 4_096);
  const approvalId = requireEnv(env, CENSUS_APPROVAL_ID_ENV);
  const runId = requireEnv(env, CENSUS_RUN_ID_ENV);
  if (!isValidCensusAuditIdentifier(approvalId)) {
    throw new CensusGuardError(`${CENSUS_APPROVAL_ID_ENV} is invalid`);
  }
  if (!isValidCensusAuditIdentifier(runId)) {
    throw new CensusGuardError(`${CENSUS_RUN_ID_ENV} is invalid`);
  }
  const approvedHost = requireEnv(env, CENSUS_APPROVED_HOST_ENV).toLowerCase();
  const database = requireEnv(env, CENSUS_APPROVED_DATABASE_ENV);
  const role = requireEnv(env, CENSUS_APPROVED_ROLE_ENV);

  let parsed: URL;
  try {
    parsed = new URL(databaseUrl);
  } catch {
    throw new CensusGuardError(`${CENSUS_DATABASE_ENV} must be a valid URL`);
  }
  if (parsed.protocol !== "postgres:" && parsed.protocol !== "postgresql:") {
    throw new CensusGuardError(
      `${CENSUS_DATABASE_ENV} must use the PostgreSQL protocol`,
    );
  }
  let dsnDatabase: string;
  let dsnRole: string;
  try {
    if (!/^\/[^/]+$/.test(parsed.pathname)) throw new Error("invalid path");
    dsnDatabase = decodeURIComponent(parsed.pathname.slice(1));
    dsnRole = decodeURIComponent(parsed.username);
  } catch {
    throw new CensusGuardError(
      `${CENSUS_DATABASE_ENV} has an invalid database or role`,
    );
  }
  if (
    parsed.host.toLowerCase() !== approvedHost ||
    dsnDatabase !== database ||
    dsnRole !== role
  ) {
    throw new CensusGuardError(
      `${CENSUS_DATABASE_ENV} does not match the approved target`,
    );
  }
  const sslMode = parsed.searchParams.get("sslmode");
  if (sslMode !== CENSUS_TLS_MODE) {
    throw new CensusGuardError(
      `${CENSUS_DATABASE_ENV} must require certificate-verified TLS`,
    );
  }
  return {
    databaseUrl,
    approvalId,
    runId,
    approvedHost,
    database,
    role,
  };
}

function createExecutor(databaseUrl: string): {
  executor: CensusExecutor;
  close: () => Promise<void>;
} {
  const sql = postgres(databaseUrl, {
    max: 1,
    prepare: false,
    idle_timeout: 5,
    max_lifetime: 60,
    connection: { application_name: "jobseek-ai-filter-eval-census" },
    ssl: CENSUS_TLS_MODE,
  });

  return {
    executor: {
      transaction: (work) =>
        sql.begin(async (transaction) => {
          const query: CensusQuery = async (statement) => [
            ...(await transaction.unsafe<Record<string, unknown>[]>(statement)),
          ];
          return work(query);
        }),
    },
    close: () => sql.end({ timeout: 5 }),
  };
}

function safeFailureMessage(error: unknown): string {
  return error instanceof CensusGuardError
    ? error.message
    : "AI filter evaluation census could not complete";
}

export async function runCensusCli(
  argv = process.argv.slice(2),
  env = process.env,
): Promise<number> {
  let connection: ReturnType<typeof createExecutor> | undefined;
  try {
    const invocation = requireCensusInvocation(argv, env);
    connection = createExecutor(invocation.databaseUrl);
    const context: CensusRunContext = {
      database: invocation.database,
      role: invocation.role,
      approvalId: invocation.approvalId,
      runId: invocation.runId,
    };
    const census = await collectWatchlistFilterCensus(
      connection.executor,
      context,
    );
    process.stdout.write(serializeCensus(census));
    return 0;
  } catch (error) {
    process.stderr.write(`${safeFailureMessage(error)}\n`);
    return 1;
  } finally {
    await connection?.close().catch(() => undefined);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void runCensusCli().then((exitCode) => {
    process.exitCode = exitCode;
  });
}
