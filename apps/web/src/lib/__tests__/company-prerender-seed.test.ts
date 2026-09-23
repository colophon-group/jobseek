import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { companyPrerenderSeed } from "../../../script/company-prerender-seed";

const roots: string[] = [];

function registry(content: string): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "company-seed-"));
  roots.push(root);
  fs.mkdirSync(path.join(root, "crawler/data"), { recursive: true });
  fs.mkdirSync(path.join(root, "web"));
  fs.writeFileSync(path.join(root, "crawler/data/companies.csv"), content);
  return path.join(root, "web");
}

afterEach(() => {
  for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe("company prerender seed", () => {
  it("selects one canonical slug independent of CSV ordering or quoted names", () => {
    const first = registry('slug,name\nzeta,"Zeta, Inc."\nalpha,Alpha\n');
    const reordered = registry('slug,name\nalpha,Alpha\nzeta,"Zeta, Inc."\n');
    expect(companyPrerenderSeed(first)).toBe("alpha");
    expect(companyPrerenderSeed(reordered)).toBe("alpha");
  });

  it.each(["slug,name\n", "name\nAlpha\n", "slug,name\n../unsafe,Unsafe\n"])(
    "rejects a registry that cannot supply a valid route seed",
    (csv) => expect(() => companyPrerenderSeed(registry(csv))).toThrow("canonical company registry"),
  );
});
