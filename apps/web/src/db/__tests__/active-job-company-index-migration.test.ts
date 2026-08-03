import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";

import { jobPosting } from "@/db/schema";

const migrationSql = readFileSync(
  resolve(process.cwd(), "drizzle/0083_reconcile_supabase_baseline.sql"),
  "utf8",
);

describe("Supabase baseline reconciliation", () => {
  it("creates or verifies the exact partial index used by watchlist counts", () => {
    expect(migrationSql).toMatch(
      /CREATE INDEX idx_jp_active_company ON public\.job_posting \(company_id\) WHERE is_active = true/i,
    );
    expect(migrationSql).toContain("indisvalid");
    expect(migrationSql).toContain("pg_get_indexdef");

    const index = getTableConfig(jobPosting).indexes.find(
      (candidate) => candidate.config.name === "idx_jp_active_company",
    );

    expect(index).toBeDefined();
    expect(index?.config.where).toBeDefined();
  });
});
