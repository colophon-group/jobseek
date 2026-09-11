import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = process.cwd();
const migration = readFileSync(
  resolve(webRoot, "drizzle/0090_migrate_internship_watchlist_filters.sql"),
  "utf8",
);

describe("0090 internship watchlist filter migration", () => {
  it("moves internship to seniority without replacing explicit levels", () => {
    expect(migration).toContain("filters - 'employmentType'");
    expect(migration).toContain("remaining_employment_types");
    expect(migration).toContain("has_explicit_seniority");
    expect(migration).toContain("was_internship_only");
    expect(migration).toContain("'[\"intern\"]'::jsonb");
    expect(migration).toContain("updated_at = now()");
  });

  it("is the latest monotonic journal entry", () => {
    const journal = JSON.parse(
      readFileSync(resolve(webRoot, "drizzle/meta/_journal.json"), "utf8"),
    ) as {
      entries: Array<{
        idx: number;
        version: string;
        when: number;
        tag: string;
        breakpoints: boolean;
      }>;
    };
    expect(journal.entries.at(-1)).toEqual({
      idx: 78,
      version: "7",
      when: 1_789_127_975_000,
      tag: "0090_migrate_internship_watchlist_filters",
      breakpoints: true,
    });
    expect(journal.entries.at(-2)?.when).toBeLessThan(
      journal.entries.at(-1)?.when ?? 0,
    );
  });
});
