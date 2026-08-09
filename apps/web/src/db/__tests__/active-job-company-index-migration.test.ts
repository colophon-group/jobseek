import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const migrationSql = readFileSync(
  resolve(process.cwd(), "drizzle/0083_reconcile_supabase_baseline.sql"),
  "utf8",
);
const currentSchema = readFileSync(
  resolve(process.cwd(), "src/db/schema.ts"),
  "utf8",
);

describe("Supabase baseline reconciliation", () => {
  it("creates or verifies the exact partial index used by watchlist counts", () => {
    expect(migrationSql).toMatch(
      /CREATE INDEX idx_jp_active_company ON public\.job_posting \(company_id\) WHERE is_active = true/i,
    );
    expect(migrationSql).toContain("indisvalid");
    expect(migrationSql).toContain("pg_get_indexdef");

    // The index remains part of the audited 0083 history, but 0086 retires the
    // entire Supabase mirror and therefore its current Drizzle declaration.
    expect(currentSchema).not.toMatch(/export const jobPosting\b/);
  });
});
