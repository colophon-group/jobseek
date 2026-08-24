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
      "CREATE UNIQUE INDEX account_issuer_accountId_uidx",
    );
    expect(contractCheck).toBeGreaterThan(0);
    expect(notNull).toBeGreaterThan(contractCheck);
    expect(uniqueIndex).toBeGreaterThan(notNull);
  });

  it("returns the local account row id required by unlinkAccount", () => {
    expect(preferences).toContain(
      ".select({ providerId: account.providerId, accountId: account.id })",
    );
    expect(preferences).not.toContain(
      ".select({ providerId: account.providerId, accountId: account.accountId })",
    );
  });

  it("appends one monotonic journal entry", () => {
    const journal = JSON.parse(
      readFileSync(resolve(webRoot, "drizzle/meta/_journal.json"), "utf8"),
    ) as { entries: { idx: number; when: number; tag: string }[] };

    expect(journal.entries.at(-1)).toEqual({
      idx: 75,
      version: "7",
      when: 1_787_560_116_000,
      tag: "0087_better_auth_account_issuer",
      breakpoints: true,
    });
  });
});
