import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string): string =>
  readFileSync(resolve(process.cwd(), path), "utf8");

describe("Supabase location and taxonomy read cutover", () => {
  it("keeps public location, taxonomy, and company-location providers on Typesense", () => {
    for (const path of [
      "src/lib/services/locations.ts",
      "src/lib/services/taxonomy.ts",
      "src/lib/services/company.ts",
    ]) {
      const source = read(path);
      expect(source).not.toContain('from "@/db"');
      expect(source).not.toContain('from "@/db/schema"');
      expect(source).not.toContain('from "drizzle-orm"');
      expect(source).not.toMatch(/\bdb\.(execute|select)\b/);
      expect(source).not.toMatch(/\bsql`/);
    }

    const provider = read("src/lib/search/typesense-taxonomy.ts");
    expect(provider).toContain("getTypesenseClient");
    expect(provider).not.toContain("DATABASE_URL");
    expect(provider).not.toContain('from "@/db"');
  });

  it("limits sitemap SQL to durable web-owned watchlist tables", () => {
    const source = read("src/lib/sitemap.ts");
    const sqlBlocks = [...source.matchAll(/sql`([\s\S]*?)`/g)].map((match) => match[1]);
    const tables = sqlBlocks.flatMap((block) =>
      [...block.matchAll(/\b(?:FROM|JOIN)\s+("[^"]+"|[a-z_]+)/gi)].map(
        (match) => match[1].replaceAll('"', ""),
      ),
    );

    expect(new Set(tables)).toEqual(
      new Set(["watchlist", "user", "watchlist_company"]),
    );
    expect(source).not.toMatch(/\b(?:FROM|JOIN)\s+(?:job_posting|location|occupation|seniority|technology|industry|company)\b/i);
  });
});
