import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const migration = readFileSync(
  resolve(webRoot, "drizzle/0091_relax_better_auth_account_issuer.sql"),
  "utf8",
);
const runtimeSchema = readFileSync(
  resolve(webRoot, "src/db/schema.ts"),
  "utf8",
);
const generatedSchema = readFileSync(
  resolve(webRoot, "drizzle/schema.ts"),
  "utf8",
);

describe("0091 Better Auth account issuer relaxation", () => {
  it("removes the temporary 1.7.0-1.7.2 write constraints", () => {
    expect(migration).toContain(
      "DROP TRIGGER IF EXISTS account_issuer_compat_before_write",
    );
    expect(migration).toContain(
      "DROP FUNCTION IF EXISTS public.jobseek_better_auth_account_issuer_compat()",
    );
    expect(migration).toContain(
      "DROP INDEX IF EXISTS public.account_issuer_account_id_uidx",
    );
    expect(migration).toContain(
      "ALTER TABLE public.account ALTER COLUMN issuer DROP NOT NULL",
    );
    expect(migration).not.toMatch(/DROP\s+COLUMN\s+issuer/i);
  });

  it("restores the Better Auth 1.6 and 1.7.3+ Drizzle account shape", () => {
    for (const schema of [runtimeSchema, generatedSchema]) {
      expect(schema).not.toContain("issuer: text");
      expect(schema).not.toContain("account_issuer_account_id_uidx");
    }
  });

  it("retains the historical issuer data for reversible cleanup", () => {
    expect(migration).not.toMatch(/\bUPDATE\s+public\.account\b/i);
    expect(migration).not.toMatch(/\bDELETE\s+FROM\s+public\.account\b/i);
  });
});
