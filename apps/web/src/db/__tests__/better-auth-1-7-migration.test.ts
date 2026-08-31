import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const migration = readFileSync(
  resolve(webRoot, "drizzle/0087_better_auth_account_issuer.sql"),
  "utf8",
);
const preferences = readFileSync(
  resolve(webRoot, "src/lib/actions/preferences.ts"),
  "utf8",
);
const applyScript = readFileSync(
  resolve(webRoot, "scripts/apply-better-auth-account-issuer.ts"),
  "utf8",
);
const verifier = readFileSync(
  resolve(webRoot, "scripts/verify-better-auth-account-issuer.ts"),
  "utf8",
);
const pg17Harness = readFileSync(
  resolve(webRoot, "scripts/test-better-auth-account-issuer-pg17.ts"),
  "utf8",
);
const deployWorkflow = readFileSync(
  resolve(webRoot, "../../.github/workflows/deploy-web-production.yml"),
  "utf8",
);
const driftWorkflow = readFileSync(
  resolve(webRoot, "../../.github/workflows/web-database-migrations.yml"),
  "utf8",
);

describe("0087 Better Auth account issuer migration", () => {
  it("backfills the exact issuers used by the configured auth providers", () => {
    expect(migration).toContain("WHEN 'credential' THEN 'local:credential'");
    expect(migration).toContain("WHEN 'github' THEN 'local:oauth:github'");
    expect(migration).toContain(
      "WHEN 'google' THEN 'https://accounts.google.com'",
    );
    expect(migration).toContain(
      "WHEN 'linkedin' THEN 'local:oauth:linkedin'",
    );
  });

  it("fails closed before enforcing the new account identity", () => {
    expect(migration).toContain(
      "provider_id NOT IN ('credential', 'github', 'google', 'linkedin')",
    );
    expect(migration).toContain("account_id IS DISTINCT FROM user_id");
    expect(migration).toContain("GROUP BY issuer, account_id");

    const contractCheck = migration.indexOf("DO $contract$");
    const notNull = migration.indexOf(
      "ALTER TABLE public.account ALTER COLUMN issuer SET NOT NULL",
    );
    const uniqueIndex = migration.indexOf(
      "CREATE UNIQUE INDEX account_issuer_account_id_uidx",
    );
    const compatibilityFunction = migration.indexOf(
      "CREATE FUNCTION public.jobseek_better_auth_account_issuer_compat()",
    );
    const compatibilityTrigger = migration.indexOf(
      "CREATE TRIGGER account_issuer_compat_before_write",
    );
    expect(contractCheck).toBeGreaterThan(0);
    expect(notNull).toBeGreaterThan(contractCheck);
    expect(uniqueIndex).toBeGreaterThan(notNull);
    expect(compatibilityFunction).toBeGreaterThan(uniqueIndex);
    expect(compatibilityTrigger).toBeGreaterThan(compatibilityFunction);
    expect(migration).toContain(
      "BEFORE INSERT OR UPDATE OF provider_id, issuer ON public.account",
    );
    expect(migration).toContain("expected_issuer text");
    expect(migration).toContain(
      "issuer does not match provider_id %",
    );
  });

  it("uses a targeted, atomic and idempotent migration runner", () => {
    expect(applyScript).toContain(
      'const targetTag = "0087_better_auth_account_issuer"',
    );
    expect(applyScript).toContain("MIGRATION_REQUIRE_UNPOOLED");
    expect(applyScript).toContain('new URL(databaseUrl).port !== "6543"');
    expect(applyScript).toContain("pg_advisory_xact_lock");
    expect(applyScript).toContain('return "already-applied" as const');
    expect(applyScript).toContain("for (const statement of target.sql)");
    expect(applyScript).not.toContain("src/db/migrate.ts");
  });

  it("verifies both the exact pre-migration and applied production contracts", () => {
    expect(verifier).toContain("SET TRANSACTION READ ONLY");
    expect(verifier).toContain('new URL(databaseUrl).port !== "6543"');
    expect(verifier).toContain("account_issuer_account_id_uidx");
    expect(verifier).toContain("account_issuer_compat_before_write");
    expect(verifier).toContain("jobseek_better_auth_account_issuer_compat");
    expect(verifier).toContain("prospective");
    expect(verifier).toContain("exactPreState || exactPostState");
    expect(verifier).toContain('status: "failed"');
  });

  it("applies 0087 after staged smoke and before promotion", () => {
    const stagedSmoke = deployWorkflow.indexOf(
      "Verify staged production functionality",
    );
    const preflight = deployWorkflow.indexOf(
      "Verify Better Auth account issuer preflight",
    );
    const currentMainGuard = deployWorkflow.indexOf(
      "Require the migration revision is still main",
    );
    const apply = deployWorkflow.indexOf(
      "Apply the reviewed Better Auth account issuer migration",
    );
    const postflight = deployWorkflow.indexOf(
      "Verify Better Auth account issuer postflight",
    );
    const promote = deployWorkflow.indexOf(
      "Promote only if this SHA is still main",
    );

    expect(stagedSmoke).toBeGreaterThan(0);
    expect(currentMainGuard).toBeGreaterThan(stagedSmoke);
    expect(preflight).toBeGreaterThan(currentMainGuard);
    expect(apply).toBeGreaterThan(preflight);
    expect(postflight).toBeGreaterThan(apply);
    expect(promote).toBeGreaterThan(postflight);
    expect(deployWorkflow).toContain("pnpm install --frozen-lockfile");
    expect(deployWorkflow).toContain("db:migrate:apply-account-issuer");
    expect(deployWorkflow).toContain("db:migrate:verify-account-issuer");
    expect(deployWorkflow).toContain(
      "DATABASE_URL_UNPOOLED: ${{ secrets.DATABASE_URL_UNPOOLED }}",
    );
    expect(deployWorkflow).toContain('MIGRATION_REQUIRE_UNPOOLED: "true"');
    expect(
      deployWorkflow.slice(preflight, promote),
    ).not.toMatch(/\bdb:migrate(?:\s|$)/);
  });

  it("checks the applied issuer contract in scheduled drift verification", () => {
    expect(driftWorkflow).toContain(
      "Check Better Auth account issuer drift",
    );
    expect(driftWorkflow).toContain("db:migrate:verify-account-issuer --");
    expect(driftWorkflow).toContain(
      'drift "$RUNNER_TEMP/better-auth-account-issuer-drift.json"',
    );
  });

  it("executes the real targeted migration against destructive PostgreSQL 17 fixtures", () => {
    expect(pg17Harness).toContain('argv.indexOf("--database-url")');
    expect(pg17Harness).toContain(
      "disposable database name must contain 'issuer' or 'fixture'",
    );
    expect(pg17Harness).not.toMatch(
      /process\.env\.(?:DATABASE_URL|DATABASE_URL_UNPOOLED)/,
    );
    expect(pg17Harness).toContain(
      "server.version >= 170_000 && server.version < 180_000",
    );
    expect(pg17Harness).toContain("readMigrationFiles");
    expect(pg17Harness).toContain(
      'scripts/apply-better-auth-account-issuer.ts',
    );
    expect(pg17Harness).toContain("already-applied");
    expect(pg17Harness).toContain("unsupported provider write");
    expect(pg17Harness).toContain("nonblank issuer/provider mismatch");
    expect(pg17Harness).toContain("mapped issuer/account_id collision");
  });

  it("returns the local account row id required by unlinkAccount", () => {
    expect(preferences).toContain(
      ".select({ providerId: account.providerId, accountId: account.id })",
    );
    expect(preferences).not.toContain(
      ".select({ providerId: account.providerId, accountId: account.accountId })",
    );
  });

  it("retains the exact 0087 journal identity", () => {
    const journal = JSON.parse(
      readFileSync(resolve(webRoot, "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: { idx: number; when: number; tag: string }[] };

    expect(
      journal.entries.find(
        (entry) => entry.tag === "0087_better_auth_account_issuer",
      ),
    ).toEqual({
      idx: 75,
      version: "7",
      when: 1_787_560_116_000,
      tag: "0087_better_auth_account_issuer",
      breakpoints: true,
    });
  });
});
