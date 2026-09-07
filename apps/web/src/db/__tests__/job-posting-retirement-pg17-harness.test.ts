import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const harness = readFileSync(
  resolve(webRoot, "scripts/test-job-posting-retirement-pg17.ts"),
  "utf8",
);

describe("PostgreSQL 17 job_posting retirement execution harness", () => {
  it("requires an explicitly named disposable database and PostgreSQL 17", () => {
    expect(harness).toContain('argv.indexOf("--database-url")');
    expect(harness).toContain("disposable database name must contain 'retirement' or 'fixture'");
    expect(harness).not.toMatch(/process\.env\.(?:DATABASE_URL|DATABASE_URL_UNPOOLED)/);
    expect(harness).toContain("server.version >= 170_000 && server.version < 180_000");
    expect(harness).toContain("Connected database does not match the explicitly supplied URL");
  });

  it("uses the real journal SQL hashes and the real migration runner", () => {
    expect(harness).toContain("readMigrationFiles({ migrationsFolder: migrationFolder })");
    expect(harness).toContain("Expected 77 real journal migrations");
    expect(harness).toContain("const seed = [through0085[0], ...through0085]");
    expect(harness).toContain('resolve(webRoot, "src/db/migrate.ts")');
    expect(harness).toContain("spawn(process.execPath, [tsxRunner, migrationRunner]");
    expect(harness).toContain(
      "assertLedger(afterSuccess, seed, [retirement, ...subsequent])",
    );
  });

  it("binds both attestation modes to the production runner contract", () => {
    for (const contractValue of [
      "production-drop",
      "restore-drill",
      "DROP-ONLY-JOB-POSTING-0086",
      "RESTORE-ONLY-JOB-POSTING-0086",
      "RETIREMENT_BACKUP_RESTORE_RUN_ID",
      "RETIREMENT_CRAWLER_DEPLOY_RUN_ID",
      "RETIREMENT_TYPESENSE_BACKFILL_RUN_ID",
      "RETIREMENT_WEB_DEPLOY_SHA",
      "RETIREMENT_READINESS_DIGEST",
    ]) {
      expect(harness).toContain(contractValue);
    }
    expect(harness).toContain('].join("\\n")');
    expect(harness).toContain('createHash("sha256")');
  });

  it("proves preservation, guarded atomic failures, and restore-only convergence", () => {
    expect(harness).toContain("afterSuccess.savedJobDigest === beforeSuccess.savedJobDigest");
    expect(harness).toContain("afterSuccess.relationshipDigest === beforeSuccess.relationshipDigest");
    expect(harness).toContain('table !== "job_posting"');

    for (const negativeFixture of [
      "retirement_fixture_inbound_fk_job_posting_fkey",
      "public routine text reference",
      "non-public application routine text reference",
      "ordinary migration CLI without TEMP attestation",
      "restore-only shape without TEMP attestation",
      "restore-only shape under production-drop mode",
    ]) {
      expect(harness).toContain(negativeFixture);
    }

    expect(harness).toContain("assertEqual(after, before");
    expect(harness).toContain('invokeRealMigration(databaseUrl, "restore-drill")');
    expect(harness).toContain(
      "assertLedger(restored, seed, [retirement, ...subsequent])",
    );
  });
});
