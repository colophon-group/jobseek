/**
 * Source-level regression for issue #3116.
 *
 * These checks cover render hygiene problems that are easier to regress than
 * to observe through user-visible behavior: unstable index keys in small
 * component loops and render-time random skeleton widths.
 */
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = resolve(__dirname, "../../../..");

const cleanupFiles = [
  "app/[lang]/(app)/company/[slug]/company-head.tsx",
  "app/[lang]/(app)/my-jobs/my-jobs-page.tsx",
  "app/[lang]/(public)/faq/faq-content.tsx",
  "src/components/my-jobs/activity-heatmap.tsx",
  "src/components/search/job-detail-dialog.tsx",
  "src/components/search/skeleton-card.tsx",
] as const;

function readSource(relPath: string): string {
  return readFileSync(join(repoRoot, relPath), "utf8");
}

describe("web cleanup source hygiene (#3116)", () => {
  it("keeps the audited component loops off bare array-index keys", () => {
    const offenders = cleanupFiles.filter((path) => /\bkey=\{(?:i|index)\}/.test(readSource(path)));

    expect(offenders).toEqual([]);
  });

  it("keeps job-detail skeleton widths deterministic during render", () => {
    const source = readSource("src/components/search/job-detail-dialog.tsx");

    expect(source).toContain("DETAIL_DESCRIPTION_SKELETON_LINES");
    expect(source).not.toContain("Math.random()");
  });
});
