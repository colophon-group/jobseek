import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import postgres, { type Sql } from "postgres";
import { auditCompanyReferences, type CompanyReferenceMode } from "./company-reference-contract";

export type RehearsalIdentity = { tag: string; createdAt: number; hash: string };
const tables = ["user", "session", "account", "verification", "user_preferences", "industry", "company",
  "company_description", "job_board", "saved_job", "application_interview", "followed_company", "company_request",
  "hiring_signal", "outreach_draft", "watchlist", "watchlist_company"] as const;
let phase = "configuration";
function assert(condition: unknown, message: string): asserts condition { if (!condition) throw new Error(message); }

async function fingerprints(sql: Sql, includeReferences = false) {
  const result: Record<string, { rows: number; digest: string }> = {};
  await sql.begin(async tx => {
    await tx`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`;
    await tx`SET LOCAL statement_timeout = '60s'`;
    for (const table of [...tables, ...(includeReferences ? ["company_reference"] : [])]) {
      const digest = createHash("sha256"); let rows = 0;
      // Private rows are streamed into a digest and never returned or logged.
      for await (const batch of tx`SELECT to_jsonb(t)::text AS row FROM ${tx(table)} t ORDER BY to_jsonb(t)::text`.cursor(100)) {
        for (const value of batch) { digest.update(value.row + "\n"); rows++; }
      }
      result[table] = { rows, digest: digest.digest("hex") };
    }
  });
  return result;
}

/** Actual production verifier/normalizer and exact reviewed SQL on an isolated restore. */
export async function rehearseCompanyReference(sql: Sql, target: RehearsalIdentity) {
  assert(["0100_company_references", "0101_company_reference_selection_contract"].includes(target.tag), "Unreviewed rehearsal target");
  const contract = target.tag === "0101_company_reference_selection_contract";
  const beforeMode: CompanyReferenceMode = contract ? "contract-preflight" as CompanyReferenceMode : "preflight";
  const afterMode: CompanyReferenceMode = contract ? "contract-postflight" as CompanyReferenceMode : "postflight";
  const migration = readFileSync(resolve("drizzle", `${target.tag}.sql`), "utf8");
  assert(createHash("sha256").update(migration).digest("hex") === target.hash, "Rehearsal migration hash differs");
  const journal = JSON.parse(readFileSync("drizzle/meta/_journal.json", "utf8")) as {
    entries: { tag: string; when: number }[];
  };
  const index = journal.entries.findIndex(row => row.tag === target.tag);
  assert(index > 0 && journal.entries[index].when === target.createdAt, "Rehearsal journal identity differs");
  const prerequisite = journal.entries[index - 1];
  const prerequisiteHash = createHash("sha256").update(readFileSync(`drizzle/${prerequisite.tag}.sql`)).digest("hex");
  phase = "isolated_role_scaffolding";
  // pg_restore --no-privileges omits production credentials/role ACLs. Recreate
  // only the verifier's reviewed browser/auditor role contract in this disposable DB.
  await sql.unsafe(`DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='jobseek_migration_auditor') THEN
      CREATE ROLE jobseek_migration_auditor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
  END $$;
  ALTER ROLE jobseek_migration_auditor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  ALTER ROLE jobseek_migration_auditor SET default_transaction_read_only=on`);
  phase = "restored_preflight";
  await auditCompanyReferences(sql, beforeMode, "postgres");
  const before = await fingerprints(sql, contract);
  phase = "exact_migration_transaction";
  await sql.begin(async tx => {
    await tx`SET LOCAL lock_timeout='10s'`;
    await tx`SET LOCAL statement_timeout='10min'`;
    await tx`SET LOCAL idle_in_transaction_session_timeout='2min'`;
    const [lock] = await tx<{ acquired: boolean }[]>`SELECT pg_try_advisory_xact_lock(hashtextextended('jobseek:web-schema-migrations',0)) AS acquired`;
    assert(lock?.acquired, "Rehearsal schema migration lock unavailable");
    await tx`LOCK TABLE drizzle.__drizzle_migrations IN EXCLUSIVE MODE`;
    const ledger = await tx<{ hash: string; createdAt: string }[]>`
      SELECT hash,created_at::text AS "createdAt" FROM drizzle.__drizzle_migrations ORDER BY created_at DESC,id DESC`;
    assert(ledger[0]?.hash === prerequisiteHash && Number(ledger[0]?.createdAt) === prerequisite.when
      && ledger.filter(row => row.hash === prerequisiteHash || Number(row.createdAt) === prerequisite.when).length === 1
      && !ledger.some(row => row.hash === target.hash || Number(row.createdAt) === target.createdAt),
    "Rehearsal prerequisite/target ledger changed under lock");
    for (const statement of migration.split("--> statement-breakpoint").filter(part => part.trim())) await tx.unsafe(statement);
    await tx`INSERT INTO drizzle.__drizzle_migrations (hash, created_at) VALUES (${target.hash}, ${target.createdAt})`;
  });
  phase = "restored_postflight";
  const audit = await auditCompanyReferences(sql, afterMode, "postgres");
  const after = await fingerprints(sql, contract);
  assert(JSON.stringify(before) === JSON.stringify(after), "Restored reference/ownership/filter/history fingerprints changed");
  const [{ rows: referenceRows }] = await sql<{ rows: number }[]>`SELECT count(*)::integer AS rows FROM company_reference`;
  return { target, preflight: "passed", postflight: "passed", preserved: after, referenceRows, dependencies: audit.dependencies };
}

async function main() {
  const manifest = JSON.parse(readFileSync("manifest.json", "utf8")) as { sourceRevision: string; sourceClean: boolean; runtimeImage: string;
    files: Record<string, string>; migrations: RehearsalIdentity[] };
  assert(createHash("sha256").update(readFileSync("manifest.json")).digest("hex") === process.env.REHEARSAL_MANIFEST_SHA256, "Rehearsal manifest digest differs");
  assert(manifest.runtimeImage === process.env.REHEARSAL_RUNTIME_IMAGE, "Rehearsal runtime identity differs");
  assert(manifest.sourceClean === true && manifest.sourceRevision === process.env.REHEARSAL_SOURCE_REVISION, "Rehearsal source revision differs");
  for (const [path, digest] of Object.entries(manifest.files)) {
    assert(/^[a-z0-9_./-]+$/i.test(path) && !path.startsWith("/") && !path.split("/").includes(".."), "Rehearsal resource path differs");
    assert(createHash("sha256").update(readFileSync(path)).digest("hex") === digest, "Rehearsal resource hash differs");
  }
  const target = manifest.migrations.find(row => row.tag === process.env.REHEARSAL_MIGRATION_TAG);
  assert(target, "Rehearsal target absent from immutable allowlist");
  const operation = process.env.REHEARSAL_OPERATION_ID;
  const host = process.env.REHEARSAL_DATABASE_HOST;
  assert(/^[0-9a-f]{32}$/.test(operation ?? "") && host === `jobseek-web-postgresql-restore-${operation}`, "Rehearsal requires exact isolated restore host");
  assert(/^[0-9a-f]{64}$/.test(process.env.REHEARSAL_ARCHIVE_SHA256 ?? ""), "Rehearsal archive identity absent");
  // Never read the production URL or backup database credential.
  const password = readFileSync("/run/secrets/postgres-password", "utf8").trim();
  const sql = postgres({ host, port: 5432, database: "web_restore", username: "postgres", password,
    max: 1, prepare: false, connect_timeout: 15, connection: { application_name: "jobseek-isolated-company-reference-rehearsal" } });
  try {
    const proof = await rehearseCompanyReference(sql, target);
    console.log(JSON.stringify({ contract: "company_reference_archive_rehearsal", outcome: "passed", sourceRevision: manifest.sourceRevision,
      runtimeImage: manifest.runtimeImage, archiveSha256: process.env.REHEARSAL_ARCHIVE_SHA256,
      manifestSha256: createHash("sha256").update(readFileSync("manifest.json")).digest("hex"), ...proof }));
  } finally { await sql.end({ timeout: 5 }); }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) void main().catch(() => {
  console.error(JSON.stringify({ contract: "company_reference_archive_rehearsal", outcome: "failed", phase })); process.exitCode = 1;
});
