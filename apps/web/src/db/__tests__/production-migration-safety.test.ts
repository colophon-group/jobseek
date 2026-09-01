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
    expect(tags).toContain("0083_reconcile_supabase_baseline");
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

    expect(runner).toContain("await lockSql.reserve()");
    expect(runner).toContain("drizzle(migrationSql)");
    expect(runner).not.toMatch(/drizzle\((?:reserved|lockConnection)\)/);
    expect(runner).toContain("connect_timeout: 15");
    expect(runner).toContain("lockSql.end({ timeout: 5 })");
    expect(runner).toContain("migrationSql.end({ timeout: 5 })");
    expect(runner).toContain("pg_try_advisory_lock");
    expect(runner).toContain("pg_advisory_unlock");
    expect(runner).toContain("MIGRATION_REQUIRE_UNPOOLED");
    expect(runner).toContain('parsedUrl.port === "6543"');
    expect(runner).toContain("pg_temp.jobseek_retirement_attestation");
    expect(runner).toContain("RETIREMENT_BACKUP_RESTORE_RUN_ID");
    expect(runner).toContain("RETIREMENT_CRAWLER_DEPLOY_RUN_ID");
    expect(runner).toContain("RETIREMENT_TYPESENSE_BACKFILL_RUN_ID");
    expect(runner).toContain("RETIREMENT_READINESS_DIGEST");
    expect(runner).toContain("does not bind the evidence inputs");
  });

  it("separates approved writes from unattended read-only drift checks", () => {
    const workflow = readRepo(
      ".github/workflows/web-database-migrations.yml",
    );
    const evidenceValidator = readRepo(
      "scripts/validate-supabase-retirement-evidence.sh",
    );

    expect(workflow).toContain("environment: production-migrations");
    expect(workflow).toContain("environment: production-migration-drift");
    expect(workflow).toContain(
      "DATABASE_URL_UNPOOLED: ${{ secrets.DATABASE_URL_UNPOOLED }}",
    );
    expect(workflow).toContain(
      "DATABASE_URL_UNPOOLED: ${{ secrets.DATABASE_URL_READONLY }}",
    );
    expect(evidenceValidator).toContain('test "$GIT_REF" = "refs/heads/main"');
    expect(evidenceValidator).toContain(
      'test "$DISPATCH_CONFIRMATION" = "DROP-ONLY-JOB-POSTING-0086"',
    );
    expect(workflow).not.toContain("APPLY-0085");
    expect(workflow).not.toContain("APPLY-0084");
    expect(workflow).toContain("db:migrate:verify-retirement");
    expect(workflow).toContain("backup_restore_run_id:");
    expect(workflow).toContain("crawler_deploy_run_id:");
    expect(workflow).toContain("typesense_backfill_run_id:");
    expect(workflow).toContain("actions: read");
    expect(workflow).toContain("deployments: read");
    expect(workflow).toContain("verify-typesense-retirement-readiness.ts");
    expect(workflow).toContain("verify-saved-job-typesense-coverage.ts");
    expect(workflow).toContain("test-job-posting-retirement-pg17.ts");
    expect(workflow).toContain("RETIREMENT_ATTESTATION_MODE: production-drop");
    expect(workflow).toContain("needs: [validate, authorize]");
    expect(workflow.match(/validate-supabase-retirement-evidence\.sh/g)).toHaveLength(2);
    expect(evidenceValidator).toContain("Operate Web PostgreSQL Backup (Hetzner)");
    expect(evidenceValidator).toContain("latest_matching_run_id");
    expect(evidenceValidator).toContain("validate_workflow_dispatch_input");
    expect(evidenceValidator).toContain(
      'actions/runs/${run_id}/logs',
    );
    expect(evidenceValidator).toContain(
      "'*_preauthorize.txt'",
    );
    expect(evidenceValidator).not.toContain("'preauthorize/*.txt'");
    expect(evidenceValidator).toContain("DISPATCH_MODE:");
    expect(evidenceValidator).toContain("- restore)");
    expect(evidenceValidator).not.toContain(
      "Web PostgreSQL backup operation: restore",
    );
    expect(evidenceValidator).toContain('git/ref/heads/main');
    expect(evidenceValidator).toContain('test "$main_sha" = "$CURRENT_SHA"');
    expect(evidenceValidator).toContain("Crawler maintenance: backfill-typesense @");
    expect(
      evidenceValidator
        .split("\n")
        .filter((line) => line.trim() === '"$backfill_title" \\'),
    ).toHaveLength(2);
    expect(evidenceValidator).not.toContain(
      "'Crawler scheduled maintenance' \\",
    );
    expect(evidenceValidator).toContain("apps/crawler \\");
    expect(workflow).toContain('cancel-in-progress: false');
    expect(workflow).toContain(
      "timeout --signal=TERM --kill-after=15s 12m pnpm db:migrate",
    );
    expect(workflow).toContain("Check exact Drizzle migration head");
    expect(workflow).toContain("db:migrate:verify-head");

    const maintenance = readRepo(
      ".github/workflows/crawler-scheduled-maintenance.yml",
    );
    expect(maintenance).toContain(
      "crawler reconcile --repair --full --fresh-cycle --target typesense",
    );
    expect(maintenance).toContain("crawler verify-typesense-taxonomies");
    expect(maintenance).toContain(
      'run-name: "Crawler maintenance: ${{ inputs.task || \'refresh-typesense\' }} @ ${{ inputs.expected_crawler_revision || \'scheduled\' }}"',
    );
  });

  it("blocks production promotion unless the exact local migration head exists", () => {
    const deployWorkflow = readRepo(
      ".github/workflows/deploy-web-production.yml",
    );
    const verifier = readWeb("scripts/verify-production-migration-head.ts");

    expect(
      deployWorkflow.match(/run: pnpm db:migrate:verify-head/g),
    ).toHaveLength(2);
    expect(deployWorkflow).not.toContain("db:migrate:apply-account-issuer");
    expect(deployWorkflow).not.toMatch(/run: pnpm db:migrate\s*$/m);
    expect(verifier).toContain("readMigrationFiles");
    expect(verifier).toContain("SET TRANSACTION READ ONLY");
    expect(verifier).toContain("DATABASE_URL_UNPOOLED");
    expect(verifier).toContain('new URL(databaseUrl).port === "6543"');
    expect(verifier).toContain("assertMigrationHead(expected, observed)");
  });
});
