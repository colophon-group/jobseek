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

  it("keeps sitemap generation independent of Supabase", () => {
    const source = read("src/lib/sitemap.ts");
    expect(source).not.toContain('from "@/db"');
    expect(source).not.toContain('from "drizzle-orm"');
    expect(source).not.toMatch(/\bdb\.(execute|select)\b/);
    expect(source).not.toMatch(/\bsql`/);
  });
});
