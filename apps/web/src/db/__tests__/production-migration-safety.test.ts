import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const repoRoot = resolve(webRoot, "../..");
const readWeb = (path: string) => readFileSync(resolve(webRoot, path), "utf8");
const readRepo = (path: string) => readFileSync(resolve(repoRoot, path), "utf8");

describe("production migration safety", () => {
  it("retires the unsafe unrecorded sequence in favor of one reconciliation", () => {
    const journal = JSON.parse(
      readWeb("drizzle/meta/_journal.json"),
    ) as { entries: { tag: string }[] };
    const tags = journal.entries.map((entry) => entry.tag);

    expect(tags).not.toContain("0080_experience_decimal_years");
    expect(tags).not.toContain(
      "0081_private_watchlists_and_general_interviews",
    );
    expect(tags).not.toContain("0082_add_active_job_company_index");
    expect(tags.at(-1)).toBe("0083_reconcile_supabase_baseline");
  });

  it("keeps transaction ownership with Drizzle", () => {
    const migration = readWeb("drizzle/0083_reconcile_supabase_baseline.sql");
    const executableSql = migration
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("--"))
      .join("\n");

    expect(executableSql).not.toMatch(/\b(?:BEGIN|COMMIT|ROLLBACK)\s*;/i);
    expect(migration).toContain("1779148800000");
    expect(migration).toContain(
      "a5bcf949b24c1a7f90cb458db9b52366e8cf5ce4ebd3338242502a1701e16c42",
    );
  });

  it("reserves one physical session for the advisory lock", () => {
    const runner = readWeb("src/db/migrate.ts");

    expect(runner).toContain("await sql.reserve()");
    expect(runner).toContain("pg_try_advisory_lock");
    expect(runner).toContain("pg_advisory_unlock");
    expect(runner).toContain("MIGRATION_REQUIRE_UNPOOLED");
    expect(runner).toContain('parsedUrl.port === "6543"');
  });

  it("separates approved writes from unattended read-only drift checks", () => {
    const workflow = readRepo(
      ".github/workflows/web-database-migrations.yml",
    );

    expect(workflow).toContain("environment: production-migrations");
    expect(workflow).toContain("environment: production-migration-drift");
    expect(workflow).toContain(
      "DATABASE_URL_UNPOOLED: ${{ secrets.DATABASE_URL_UNPOOLED }}",
    );
    expect(workflow).toContain(
      "DATABASE_URL_UNPOOLED: ${{ secrets.DATABASE_URL_READONLY }}",
    );
    expect(workflow).toContain('test "$GIT_REF" = "refs/heads/main"');
    expect(workflow).toContain('cancel-in-progress: false');
  });
});
