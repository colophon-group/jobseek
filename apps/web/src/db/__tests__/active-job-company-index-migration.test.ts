import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";

import { jobPosting } from "@/db/schema";

const migrationSql = readFileSync(
  resolve(process.cwd(), "drizzle/0082_add_active_job_company_index.sql"),
  "utf8",
);

describe("active job company index migration", () => {
  it("adds the partial index used by watchlist active-job counts", () => {
    expect(migrationSql).toMatch(
      /CREATE INDEX IF NOT EXISTS idx_jp_active_company\s+ON job_posting \(company_id\)\s+WHERE is_active = true/i,
    );

    const index = getTableConfig(jobPosting).indexes.find(
      (candidate) => candidate.config.name === "idx_jp_active_company",
    );

    expect(index).toBeDefined();
    expect(index?.config.where).toBeDefined();
  });
});
