import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const migrationSql = readFileSync(
  join(root, "drizzle", "0083_saved_job_snapshot.sql"),
  "utf8",
);
const journal = readFileSync(join(root, "drizzle", "meta", "_journal.json"), "utf8");
const schema = readFileSync(join(root, "src", "db", "schema.ts"), "utf8");

describe("0083 saved-job snapshot migration", () => {
  it("copies posting and company fields before removing the posting FK", () => {
    const backfillAt = migrationSql.indexOf('UPDATE "saved_job" AS sj');
    const dropFkAt = migrationSql.indexOf("DROP CONSTRAINT IF EXISTS");

    expect(backfillAt).toBeGreaterThan(-1);
    expect(dropFkAt).toBeGreaterThan(backfillAt);
    expect(migrationSql).toContain('"posting_source_url" = jp.source_url');
    expect(migrationSql).toContain('"company_slug" = c.slug');
  });

  it("registers the migration in drizzle's journal", () => {
    expect(journal).toContain('"tag": "0083_saved_job_snapshot"');
  });

  it("keeps the posting UUID but intentionally removes the schema FK", () => {
    const savedJobBlock = schema.slice(
      schema.indexOf('export const savedJob = pgTable('),
      schema.indexOf('export const applicationInterview = pgTable('),
    );

    expect(savedJobBlock).toContain('jobPostingId: uuid("job_posting_id").notNull()');
    expect(savedJobBlock).not.toMatch(/jobPostingId[\s\S]*?references\(\(\) => jobPosting\.id/);
  });
});
