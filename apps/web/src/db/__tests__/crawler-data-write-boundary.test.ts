/**
 * Web runtime boundary for issue #6248.
 *
 * Crawler postings are owned by the crawler's local PostgreSQL pipeline. The
 * web app may consume indexed posting data, but it must not become a second
 * writer to the Supabase crawler mirror.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const webRoot = resolve(__dirname, "../../..");
const runtimeRoots = ["app", "src"];
const sourceExtensions = [".ts", ".tsx"];
const developmentOnlyFiles = new Set([join(webRoot, "src/db/seed.ts")]);

function listRuntimeSourceFiles(dir: string): string[] {
  const files: string[] = [];

  for (const entry of readdirSync(dir)) {
    if (entry === "__tests__" || entry === ".next" || entry === "node_modules") {
      continue;
    }

    const path = join(dir, entry);
    const stat = statSync(path);

    if (stat.isDirectory()) {
      files.push(...listRuntimeSourceFiles(path));
      continue;
    }

    if (
      stat.isFile() &&
      sourceExtensions.some((extension) => path.endsWith(extension)) &&
      !developmentOnlyFiles.has(path) &&
      !path.match(/\.(?:test|spec)\.[^.]+$/)
    ) {
      files.push(path);
    }
  }

  return files;
}

function drizzleJobPostingAliases(source: string): string[] {
  const aliases: string[] = [];
  const schemaImports = source.matchAll(
    /import\s*{([\s\S]*?)}\s*from\s*["']@\/db\/schema["']/g,
  );

  for (const match of schemaImports) {
    for (const imported of match[1].split(",")) {
      const jobPostingImport = imported
        .trim()
        .match(/^jobPosting(?:\s+as\s+([A-Za-z_$][\w$]*))?$/);
      if (jobPostingImport) {
        aliases.push(jobPostingImport[1] ?? "jobPosting");
      }
    }
  }

  return aliases;
}

function mutatesCrawlerPostings(source: string): boolean {
  const aliases = drizzleJobPostingAliases(source);
  const drizzleMutation = aliases.some((alias) => {
    const escapedAlias = alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(
      String.raw`\.(?:insert|update|delete)\(\s*${escapedAlias}(?![\w$])`,
    ).test(source);
  });
  const rawSqlMutation =
    /\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:public\.)?["`]?job_posting\b/i.test(
      source,
    );
  const supabaseMutation =
    /\.from\(\s*["'`]job_posting["'`]\s*\)[\s\S]{0,300}?\.(?:insert|update|upsert|delete)\s*\(/i.test(
      source,
    );

  return drizzleMutation || rawSqlMutation || supabaseMutation;
}

describe("web crawler-data write boundary (#6248)", () => {
  it("keeps the retired Meta Apify endpoint and importer absent", () => {
    expect(existsSync(join(webRoot, "app/api/admin/meta/apify-import/route.ts"))).toBe(
      false,
    );
    expect(existsSync(join(webRoot, "src/lib/admin/meta-apify-import.ts"))).toBe(
      false,
    );
  });

  it("does not mutate crawler job_posting data from web runtime code", () => {
    const offenders = runtimeRoots
      .flatMap((root) => listRuntimeSourceFiles(join(webRoot, root)))
      .filter((path) => mutatesCrawlerPostings(readFileSync(path, "utf8")))
      .map((path) => relative(webRoot, path))
      .sort();

    expect(offenders).toEqual([]);
  });

  it.each([
    [
      "Drizzle mutation",
      'import { jobPosting } from "@/db/schema"; db.insert(jobPosting).values({});',
    ],
    [
      "aliased Drizzle mutation",
      'import { jobPosting as posting } from "@/db/schema"; db.update(posting).set({});',
    ],
    ["raw SQL mutation", "await db.execute(sql`DELETE FROM public.job_posting`);"],
    [
      "Supabase mutation",
      'await supabase.from("job_posting").upsert({ id: "posting-1" });',
    ],
  ])("detects a %s", (_name, source) => {
    expect(mutatesCrawlerPostings(source)).toBe(true);
  });

  it("does not restore Apify access in the web runtime", () => {
    const apifyRuntimeMarker = /\bAPIFY_TOKEN\b|api\.apify\.com\/v2/;
    const offenders = runtimeRoots
      .flatMap((root) => listRuntimeSourceFiles(join(webRoot, root)))
      .filter((path) => apifyRuntimeMarker.test(readFileSync(path, "utf8")))
      .map((path) => relative(webRoot, path))
      .sort();

    expect(offenders).toEqual([]);
  });
});
