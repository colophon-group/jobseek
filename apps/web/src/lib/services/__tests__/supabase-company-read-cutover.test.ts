import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string): string =>
  readFileSync(resolve(process.cwd(), path), "utf8");

const between = (source: string, start: string, end: string): string => {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  expect(startIndex).toBeGreaterThanOrEqual(0);
  expect(endIndex).toBeGreaterThan(startIndex);
  return source.slice(startIndex, endIndex);
};

describe("Supabase crawler-mirror read cutover", () => {
  it("keeps company autocomplete and watchlist company search on Typesense", () => {
    const source = read("src/lib/services/company.ts");
    const autocomplete = between(
      source,
      "// ── Company suggestions",
      "// ── Paginated company search",
    );
    const watchlistSearch = between(
      source,
      "// ── Paginated company search",
      "// ── Industry suggestions",
    );

    for (const surface of [autocomplete, watchlistSearch]) {
      expect(surface).toContain('collections("company")');
      expect(surface).not.toMatch(/\bdb\.(execute|select)\b/);
      expect(surface).not.toMatch(/\bsql`/);
      expect(surface).not.toMatch(/Postgres fallback/i);
    }
    expect(autocomplete).not.toMatch(/\bjob_posting\b/);
  });

  it("keeps company detail independent of the web database", () => {
    const service = read("src/lib/services/company-detail.ts");
    const resolver = read("src/lib/services/company-detail-lookup.ts");

    for (const source of [service, resolver]) {
      expect(source).not.toContain('from "@/db"');
      expect(source).not.toContain('from "drizzle-orm"');
      expect(source).not.toMatch(/Postgres/i);
      expect(source).not.toContain("DATABASE_URL");
    }
  });

  it("reads public site counts from Typesense only", () => {
    const source = read("src/lib/actions/stats.ts");

    expect(source).toContain('collections("company")');
    expect(source).toContain('collections("job_posting")');
    expect(source).not.toContain('from "@/db"');
    expect(source).not.toContain('from "@/db/schema"');
    expect(source).not.toContain('from "drizzle-orm"');
  });
});
