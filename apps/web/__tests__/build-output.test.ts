import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * Build-output classifier (#2885 — successor to the retired
 * `app/__tests__/isr-routes.test.ts` line scanner).
 *
 * The build itself rejects the patterns the old scanner looked for
 * once `cacheComponents: true` is on (#2835). What it doesn't do:
 * tell a developer who broke ISR which page slipped to dynamic and
 * what the canonical fix is. The build error is "X is dynamic"
 * without the prescribed remediation, so the regressing PR has to
 * dig through Next.js docs to recover.
 *
 * This test reads Next 16's per-route classification from the build
 * stdout — one of:
 *
 *   ○  (Static)             prerendered as static content
 *   ◐  (Partial Prerender)  prerendered static HTML + dynamic streams
 *   ƒ  (Dynamic)            server-rendered on demand
 *
 * For an explicit list of routes that must stay cacheable (the 4 ISR
 * pages from #2835 + the public marketing surfaces), it asserts the
 * classification is `◐` or `○` — not `ƒ`. On failure the diagnostic
 * names the route AND prescribes the canonical fix.
 *
 * The build stdout is captured by the vitest globalSetup at
 * `test-setup/run-prod-build.ts`. CI may pre-build and pass the
 * captured log via `BUILD_OUTPUT_LOG=<path>` to skip a redundant
 * second build.
 *
 * Background incident: #2243 (ISR-leakage CPU-quota incident — a
 * single dynamic-API leak on `/[lang]/company/[slug]` blew the
 * monthly Vercel function quota).
 */

/**
 * Routes that must stay cacheable. Each entry is a Next.js route key
 * exactly as Next prints it in the build summary (no locale prefix —
 * dynamic `[lang]` is preserved, the locale row is the parent).
 *
 * The list comprises:
 *   - The 4 ISR pages explicitly migrated in #2835:
 *       `/[lang]/explore`, `/[lang]/company/[slug]`,
 *       `/[lang]/[userSlug]/[watchlistSlug]`, `/[lang]/blog`,
 *       plus `/[lang]/blog/[slug]` (per-post render path is the
 *       hot SEO surface — should also stay cacheable).
 *   - Public marketing surfaces under `(public)`: the home page
 *     `/[lang]`, plus `/[lang]/{about,faq,how-we-index,license,
 *     privacy-policy,terms}`. These are static-shell pages that
 *     should never opt into dynamic rendering.
 *
 * If you intentionally remove a route from this list (say a marketing
 * page that becomes auth-gated), update the list AND link the
 * justification in the PR — falling off this list is a CPU-cost
 * regression.
 */
const MUST_STAY_CACHEABLE: ReadonlyArray<string> = [
  // 4 ISR pages (#2835 migration targets)
  "/[lang]/explore",
  "/[lang]/company/[slug]",
  "/[lang]/[userSlug]/[watchlistSlug]",
  "/[lang]/blog",
  "/[lang]/blog/[slug]",
  // Public marketing surfaces — home + (public) route group children
  "/[lang]",
  "/[lang]/about",
  "/[lang]/faq",
  "/[lang]/how-we-index",
  "/[lang]/license",
  "/[lang]/privacy-policy",
  "/[lang]/terms",
];

type Classification = "static" | "partial" | "dynamic";

const SYMBOL_TO_CLASSIFICATION: Record<string, Classification> = {
  "○": "static",
  "◐": "partial",
  "ƒ": "dynamic",
};

const CLASSIFICATION_LABEL: Record<Classification, string> = {
  static: "○ Static",
  partial: "◐ Partial Prerender",
  dynamic: "ƒ Dynamic",
};

/**
 * Parse Next 16's route summary out of the build stdout.
 *
 * The summary block starts with a `Route (app)` header and lists each
 * route on its own line in the form:
 *
 *   ┌ ○ /_not-found
 *   ├ ◐ /[lang]
 *   │ ├ /[lang]
 *   │ ├ /en
 *   │ └ [+2 more paths]
 *   ├ ƒ /api/v1/job
 *   └ ƒ /sitemap.xml
 *
 * The leading box-drawing characters and the indented child rows are
 * irrelevant — we only want the parent rows that begin with a top-level
 * `┌`/`├`/`└` followed by a single classification glyph.
 */
function parseRouteClassifications(buildOutput: string): Map<string, Classification> {
  const map = new Map<string, Classification>();
  // Match a top-level route row: starts with one of `┌├└` (no `│ ` prefix
  // which marks indented children), then the classification symbol, then
  // the route path (everything up to optional whitespace + revalidate
  // columns at the end).
  const lineRe = /^[┌├└]\s*([○◐ƒ])\s+(\S+)/u;
  for (const rawLine of buildOutput.split(/\r?\n/)) {
    const line = rawLine.replace(/\s+$/u, "");
    const match = lineRe.exec(line);
    if (!match) continue;
    const symbol = match[1];
    const route = match[2];
    const cls = SYMBOL_TO_CLASSIFICATION[symbol];
    if (!cls) continue;
    map.set(route, cls);
  }
  return map;
}

function fixHint(route: string): string {
  return [
    `Route \`${route}\` was classified as ƒ Dynamic in the production build,`,
    "but it must stay statically prerenderable (◐ Partial Prerender or ○ Static).",
    "",
    "Canonical fix: check that the page body and `generateMetadata` both have",
    "`'use cache'` + `cacheLife({ revalidate: N })` and that no helper on the",
    "render path reads runtime APIs (`cookies()`, `headers()`, `searchParams`",
    "without `await`-then-passing-into-a-cache-fn). Helpers that internally",
    "read request state — `getSession`, `getSessionUserId`, `getViewerLanguages`,",
    "`getGeoFromHeaders`, `getPreferences`, `fetchExploreData`, `listTopCompanies` —",
    "must move into a `<Suspense>`-wrapped child or a server action fired",
    "from the client. See `apps/web/docs/cache-components.md` and #2243.",
  ].join("\n");
}

describe("Production build keeps must-stay-cacheable routes prerenderable", () => {
  const logPath = process.env.BUILD_OUTPUT_LOG;

  it("globalSetup captured the build output", () => {
    expect(
      logPath,
      "BUILD_OUTPUT_LOG must be set by the globalSetup (test-setup/run-prod-build.ts)",
    ).toBeTruthy();
    expect(existsSync(logPath!), `build output log not found at ${logPath}`).toBe(true);
  });

  // Eagerly parse so per-route assertions don't reparse the whole log.
  const buildOutput = logPath && existsSync(logPath) ? readFileSync(logPath, "utf8") : "";
  const classifications = parseRouteClassifications(buildOutput);

  it("parsed at least one route from the build output", () => {
    // If the parser found nothing, every per-route assertion below will
    // give a confusing "expected partial got undefined" — surface the
    // root cause first.
    expect(
      classifications.size,
      [
        "Could not parse any route classifications from the build output.",
        `Log path: ${logPath ?? "(unset)"}`,
        "Either the build failed before the route summary printed, or the",
        "Next.js stdout format changed. Re-run `pnpm build` and inspect the",
        "tail of the log — the summary should look like `┌ ○ /_not-found`.",
      ].join("\n"),
    ).toBeGreaterThan(0);
  });

  for (const route of MUST_STAY_CACHEABLE) {
    it(`${route} is not classified as Dynamic`, () => {
      const cls = classifications.get(route);
      expect(
        cls,
        [
          `Route \`${route}\` was not present in the production build's route summary.`,
          "Either the route was removed (in which case update MUST_STAY_CACHEABLE),",
          "or the build never printed the summary (look for build failures earlier",
          "in the log).",
        ].join("\n"),
      ).toBeDefined();
      expect(cls, fixHint(route)).not.toBe("dynamic");
      expect(
        cls === "static" || cls === "partial",
        `Route \`${route}\` was classified as ${CLASSIFICATION_LABEL[cls!]} — expected ◐ or ○.`,
      ).toBe(true);
    });
  }
});
