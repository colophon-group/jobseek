import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = process.cwd();
const source = (path: string) => readFileSync(join(root, path), "utf8");

describe("Supabase posting read cutover", () => {
  it.each([
    "src/lib/actions/saved-jobs.ts",
    "src/lib/actions/my-jobs.ts",
  ])("%s reads only saved-job-owned display fields", (path) => {
    const contents = source(path);
    const readSection = path.endsWith("my-jobs.ts")
      ? contents.slice(
          contents.indexOf("export async function getMyJobs"),
          contents.indexOf("export async function updateJobStatus"),
        )
      : contents.slice(contents.indexOf("export async function getSavedJobs"));
    const schemaImport =
      contents.match(/import\s+\{([^}]*)\}\s+from\s+"@\/db\/schema"/)?.[1] ?? "";

    expect(schemaImport).not.toMatch(/\b(jobPosting|company)\b/);
    expect(readSection).not.toContain(".innerJoin(");
  });

  it("posting detail delegates to Typesense without crawler-table SQL", () => {
    const contents = source("src/lib/services/search.ts");
    const detailSection = contents.slice(
      contents.indexOf("export async function getPostingDetail"),
      contents.indexOf("export async function searchJobs"),
    );

    expect(detailSection).toContain("fetchIndexedPostingDetail");
    expect(detailSection).not.toContain("db.execute");
    expect(detailSection).not.toMatch(/FROM\s+(job_posting|company|location)/i);
  });
});
