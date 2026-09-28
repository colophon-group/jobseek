// @vitest-environment node
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { buildBlogRouteManifest } from "../../../script/blog-route-manifest";

describe("blog route inventory", () => {
  it("includes canonical posts and only their existing translations", () => {
    const root = mkdtempSync(path.join(tmpdir(), "jseek-blog-routes-"));
    try {
      const dir = path.join(root, "src/content/blog");
      mkdirSync(dir, { recursive: true });
      for (const filename of ["english-only.mdx", "translated.mdx", "translated.de.mdx", "orphan.fr.mdx", "README.md"]) {
        writeFileSync(path.join(dir, filename), "");
      }
      expect(buildBlogRouteManifest(root)).toEqual({
        "english-only": ["en"],
        translated: ["en", "de"],
      });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
