import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const repoRoot = resolve(webRoot, "../..");
const readWeb = (path: string) => readFileSync(resolve(webRoot, path), "utf8");
const readRepo = (path: string) => readFileSync(resolve(repoRoot, path), "utf8");

const migration = readWeb("drizzle/0086_drop_supabase_job_posting.sql");
const verifier = readWeb("scripts/verify-job-posting-retirement.ts");

describe("0086 Supabase job_posting retirement", () => {
  it("accepts only the exact 0085 ledger and locks the source before saved jobs", () => {
    expect(migration).toContain("ledger_count <> 75");
    expect(migration).toContain("1785757200000");
    expect(migration).toContain(
      "eec5962093a1eb8a7058f9bf031877d148718e2531eaa981b86c5c6bc51165ab",
    );

    expect(migration).toContain("pg_temp.jobseek_retirement_attestation");
    expect(migration).toContain("mode = 'production-drop'");
    expect(migration).toContain("mode = 'restore-drill'");
    expect(migration).toContain("DROP-ONLY-JOB-POSTING-0086");
    expect(migration).toContain("RESTORE-ONLY-JOB-POSTING-0086");
    const postingLock = migration.indexOf(
      "LOCK TABLE public.job_posting IN ACCESS EXCLUSIVE MODE",
    );
    const savedJobLock = migration.indexOf(
      "LOCK TABLE public.saved_job IN SHARE ROW EXCLUSIVE MODE",
    );
    expect(postingLock).toBeGreaterThan(0);
    expect(savedJobLock).toBeGreaterThan(postingLock);
  });

  it("fails closed on saved-job drift and external dependencies", () => {
    for (const invariant of [
      "snapshot_column_count",
      "incomplete_snapshots",
      "posting_fk_count",
      "snapshot_check_count",
      "saved_job_user_fk_count",
      "saved_job_unique_index_count",
      "interview_fk_count",
      "inbound_fk_count",
      "dependent_view_count",
      "dependent_function_count",
      "noninternal_trigger_count",
      "publication_count",
      "compatibility_trigger_count",
      "compatibility_function_count",
      "referencing_routine_count",
    ]) {
      expect(migration).toContain(invariant);
    }
    expect(migration).toContain("saved_job_snapshot_text_nonblank_check");
    expect(migration).toContain("idx_sj_user_posting");
    expect(migration).toContain("application_interview_saved_job_id_fkey");
    expect(migration).toContain("saved_job_user_id_user_id_fk");
  });

  it("drops exactly one table with RESTRICT and retains 400 MiB headroom", () => {
    const drops = migration.match(/DROP\s+TABLE\s+[^;]+;/gi) ?? [];
    expect(drops).toEqual(["DROP TABLE IF EXISTS public.job_posting RESTRICT;"]);
    expect(migration).not.toMatch(/DROP\s+TABLE[\s\S]*\bCASCADE\b/i);
    expect(migration).toContain("projected_database_bytes >= 400 * 1024 * 1024");
    expect(migration).toContain(
      "pg_database_size(current_database())\n      - pg_total_relation_size(job_posting_oid)",
    );
    expect(migration).toContain("to_regclass('public.job_posting') IS NOT NULL");
  });

  it("keeps transaction ownership with Drizzle", () => {
    const executable = migration
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("--"))
      .join("\n");
    expect(executable).not.toMatch(/\b(?:BEGIN|COMMIT|ROLLBACK)\s*;/i);
  });

  it("removes the current schema and seed writer but keeps the durable ID", () => {
    const schema = readWeb("src/db/schema.ts");
    const generatedSchema = readWeb("drizzle/schema.ts");
    const generatedRelations = readWeb("drizzle/relations.ts");
    const seed = readWeb("src/db/seed.ts");

    expect(schema).not.toMatch(/export const jobPosting\b/);
    expect(schema).not.toContain('pgTable(\n  "job_posting"');
    expect(schema).toContain('jobPostingId: uuid("job_posting_id").notNull()');
    expect(generatedSchema).not.toMatch(/export const jobPosting\b/);
    expect(generatedRelations).not.toMatch(/\bjobPosting(?:Relations)?\b/);
    expect(seed).not.toMatch(/\bjobPosting\b/);
    expect(schema).toContain('name: "application_interview_saved_job_id_fkey"');
    expect(generatedSchema).toContain(
      'name: "application_interview_saved_job_id_fkey"',
    );
  });

  it("uses a dedicated read-only verifier before, after, and for drift", () => {
    expect(verifier).toContain('type Mode = "preflight" | "postflight" | "drift"');
    expect(verifier).toContain("SET TRANSACTION READ ONLY");
    expect(verifier).toContain('new URL(databaseUrl).port === "6543"');
    expect(verifier).toContain("relation.oid === null");
    expect(verifier).toContain("Number(database.bytes) < freePlanSafetyBytes");
    expect(verifier).toContain("ledger.rowCount === 76");
    expect(verifier).toContain('status: "failed"');
    expect(verifier).toContain("legacyReferences.compatibilityTriggers === 0");

    const workflow = readRepo(".github/workflows/web-database-migrations.yml");
    expect(workflow).toContain("DROP-ONLY-JOB-POSTING-0086");
    expect(workflow).toContain("db:migrate:verify-retirement");
    expect(workflow).not.toContain("APPLY-0085");
  });

  it("appends one monotonic journal entry", () => {
    const journal = JSON.parse(
      readWeb("drizzle/meta/_journal.json"),
    ) as { entries: { idx: number; when: number; tag: string }[] };
    expect(
      journal.entries.find(
        (entry) => entry.tag === "0086_drop_supabase_job_posting",
      ),
    ).toEqual({
      idx: 74,
      version: "7",
      when: 1_785_760_800_000,
      tag: "0086_drop_supabase_job_posting",
      breakpoints: true,
    });
  });
});
