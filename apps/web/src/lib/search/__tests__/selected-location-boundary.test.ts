/**
 * Boundary regression for issue #3227.
 *
 * `SelectedLocation` is a search/API wire type. It must be owned by the
 * search type layer, not re-exported from the client-only location pills
 * component.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = resolve(__dirname, "../../../..");
const sourceRoots = ["src", "app"];
const sourceExtensions = [".ts", ".tsx"];

function listSourceFiles(dir: string): string[] {
  const files: string[] = [];

  for (const entry of readdirSync(dir)) {
    if (entry === "__tests__" || entry === ".next" || entry === "node_modules") {
      continue;
    }

    const path = join(dir, entry);
    const stat = statSync(path);

    if (stat.isDirectory()) {
      files.push(...listSourceFiles(path));
      continue;
    }

    if (stat.isFile() && sourceExtensions.some((ext) => path.endsWith(ext))) {
      files.push(path);
    }
  }

  return files;
}

function readSource(relPath: string): string {
  return readFileSync(join(repoRoot, relPath), "utf8");
}

describe("SelectedLocation search type boundary (#3227)", () => {
  it("owns SelectedLocation in the shared search type module", () => {
    const source = readSource("src/lib/search/types.ts");

    expect(source).toMatch(/\bexport interface SelectedLocation\b/);
    expect(source).toMatch(/\bexport type LocationType\b/);
  });

  it("does not re-export SelectedLocation from the client location-pills component", () => {
    const source = readSource("src/components/search/location-pills.tsx");

    expect(source).not.toMatch(/\bexport\s+type\s*{\s*SelectedLocation\s*}/);
  });

  it("does not import SelectedLocation from the client location-pills component", () => {
    const restrictedImport =
      /import\s+(?:type\s+)?{[\s\S]*?\bSelectedLocation\b[\s\S]*?}\s+from\s+["']@\/components\/search\/location-pills["']/;
    const offenders = sourceRoots
      .flatMap((root) => listSourceFiles(join(repoRoot, root)))
      .filter((path) => restrictedImport.test(readFileSync(path, "utf8")))
      .map((path) => relative(repoRoot, path))
      .sort();

    expect(offenders).toEqual([]);
  });
});
