import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Build-output classifier (#2885 — successor to the retired
 * `app/__tests__/isr-routes.test.ts` line scanner).
 *
 * Once `cacheComponents: true` is on (#2835) the production build itself
 * rejects most of the patterns the old line scanner caught — a regression
 * fails compile, not lint. So this test deliberately does NOT try to
 * recreate the build's own ƒ-Dynamic guard. Instead it covers the gaps
 * the build cannot:
 *
 *   1. Parse-guard. The build summary is captured to disk by the vitest
 *      globalSetup (`test-setup/run-prod-build.ts`), but terminal output is
 *      not a stable API. The classifier reads `.next` artifacts first and
 *      only falls back to stdout when artifacts are unavailable.
 *
 *   2. Classification drift (the load-bearing assertion). For each
 *      must-stay-cacheable route we encode the EXPECTED glyph — `◐`
 *      (Partial Prerender) or `○` (Static) — and assert the build
 *      classified it that way. The build already fails on `ƒ`; what
 *      it does not catch is e.g. an `◐` route accidentally collapsing
 *      to `○` (lost a `<Suspense>` boundary, lost a dynamic island —
 *      no longer streams personalised data) or vice-versa (`○` legal
 *      page accidentally pulled in a server-side fetch). Both shapes
 *      compile cleanly but represent semantic regressions.
 *
 *   3. Routes-of-interest list freshness. If a route in the must-stay
 *      list was deleted or renamed in the source tree, the route key
 *      stops appearing in the build artifacts/summary. The list rots silently —
 *      we'd think we were guarding a route we no longer ship. The
 *      "expected this route in build artifacts/summary" assertion catches that.
 *
 * Background incident: #2243 (ISR-leakage CPU-quota incident — a single
 * dynamic-API leak on `/[lang]/company/[slug]` blew the monthly Vercel
 * function quota). The old line scanner caught this in lint; the new
 * test catches the residual classes that survive `cacheComponents: true`.
 */

type Classification = "static" | "partial" | "dynamic";

/**
 * Routes that must stay cacheable, plus the EXPECTED Next 16 glyph.
 *
 * - `partial` (◐ Partial Prerender) is the right answer for pages that
 *   stream request-specific content inside a static shell (the #2835 app
 *   ISR pages such as explore/company/watchlist).
 *
 * - `static` (○ Static) is the right answer for pages with zero dynamic
 *   islands — pure content. Public marketing pages and blog pages are in
 *   this bucket: they are locale-parametrized and prerendered, but do not
 *   read request state at render time.
 *
 * If a route is intentionally removed (e.g. a marketing page becomes
 * auth-gated), update this map AND link the justification in the PR —
 * silent drop is a CPU-cost regression, see #2243.
 */
const EXPECTED_CLASSIFICATIONS: ReadonlyMap<string, Classification> = new Map([
  // ISR pages explicitly migrated in #2835 — all stream request-specific
  // data inside a static shell.
  ["/[lang]/explore", "partial"],
  ["/[lang]/company/[slug]", "partial"],
  ["/[lang]/[userSlug]/[watchlistSlug]", "partial"],
  // Blog and public marketing surfaces are pure prerendered content.
  ["/[lang]/blog", "static"],
  ["/[lang]/blog/[slug]", "static"],
  ["/[lang]", "static"],
  ["/[lang]/about", "static"],
  ["/[lang]/faq", "static"],
  ["/[lang]/how-we-index", "static"],
  ["/[lang]/license", "static"],
  ["/[lang]/privacy-policy", "static"],
  ["/[lang]/terms", "static"],
]);

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

const REPRESENTATIVE_LOCALES = ["en", "de", "fr", "it"] as const;

type BuildMeta = {
  headers?: Record<string, string>;
  postponed?: unknown;
};

type ExportDetail = {
  success?: boolean;
};

function readJson(path: string): unknown | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch {
    return null;
  }
}

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/gu, "");
}

function buildSucceeded(distDir: string | null): boolean | null {
  if (!distDir) return null;
  const detail = readJson(join(distDir, "export-detail.json")) as ExportDetail | null;
  return typeof detail?.success === "boolean" ? detail.success : null;
}

function tail(text: string, lines = 80): string {
  return text.split(/\r?\n/u).slice(-lines).join("\n");
}

function routeConcreteCandidates(route: string): string[] {
  if (!route.startsWith("/[lang]")) return [route];
  return REPRESENTATIVE_LOCALES.map((locale) => route.replace("/[lang]", `/${locale}`));
}

function routeMetaCandidates(distDir: string, route: string): string[] {
  return routeConcreteCandidates(route).map((concreteRoute) =>
    join(distDir, "server", "app", `${concreteRoute.replace(/^\//u, "")}.meta`),
  );
}

function classifyFromMeta(meta: BuildMeta): Classification {
  const isPrerender = meta.headers?.["x-nextjs-prerender"] === "1";
  if (!isPrerender) return "dynamic";
  return Object.hasOwn(meta, "postponed") ? "partial" : "static";
}

/**
 * Structured Next artifact parser. Next writes one `.meta` file per
 * prerendered route. `headers.x-nextjs-prerender` means the route is
 * cacheable; a `postponed` payload means it is Partial Prerender.
 *
 * This is deliberately preferred over stdout because the human build table
 * has already changed/suppressed itself under CI once (#3399), while these
 * artifacts are the runtime inputs.
 */
function parseRouteClassificationsFromArtifacts(distDir: string | null): Map<string, Classification> {
  const map = new Map<string, Classification>();
  if (!distDir) return map;

  const appRoutes = readJson(join(distDir, "app-path-routes-manifest.json"));
  if (!appRoutes || typeof appRoutes !== "object") return map;

  for (const route of Object.values(appRoutes)) {
    if (typeof route !== "string") continue;

    const classifications: Classification[] = [];
    for (const metaPath of routeMetaCandidates(distDir, route)) {
      const meta = readJson(metaPath) as BuildMeta | null;
      if (meta) classifications.push(classifyFromMeta(meta));
    }

    if (classifications.includes("partial")) {
      map.set(route, "partial");
    } else if (classifications.includes("static")) {
      map.set(route, "static");
    } else {
      // The route exists in the app manifest but no prerender artifact was
      // emitted for any representative concrete path.
      map.set(route, "dynamic");
    }
  }

  return map;
}

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
  for (const rawLine of stripAnsi(buildOutput).split(/\r?\n/u)) {
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

/**
 * Diagnostic for a route that was found in the build output but with
 * the wrong glyph. Distinguishes the two non-trivial drift directions
 * (the build itself catches `→ ƒ`, but `◐ → ○` and `○ → ◐` survive).
 */
function driftDiagnostic(route: string, expected: Classification, actual: Classification): string {
  const expectedLabel = CLASSIFICATION_LABEL[expected];
  const actualLabel = CLASSIFICATION_LABEL[actual];
  const lines: string[] = [
    `Route \`${route}\` was classified as ${actualLabel} in the production`,
    `build, but EXPECTED_CLASSIFICATIONS pins it to ${expectedLabel}.`,
    "",
  ];
  if (actual === "dynamic") {
    // Should be unreachable with `cacheComponents: true` — the build
    // itself rejects this — but keep the prescription for the rare
    // case where the build loosens or the assertion order changes.
    lines.push(
      "Canonical fix: check that the page body and `generateMetadata` both have",
      "`'use cache'` + `cacheLife({ revalidate: N })` and that no helper on the",
      "render path reads runtime APIs (`cookies()`, `headers()`, `searchParams`",
      "without `await`-then-passing-into-a-cache-fn). Helpers that internally",
      "read request state — `getSession`, `getSessionUserId`, `getViewerLanguages`,",
      "`getGeoFromHeaders`, `getPreferences`, `fetchExploreData`, `listTopCompanies` —",
      "must move into a `<Suspense>`-wrapped child or a server action fired",
      "from the client. See `apps/web/docs/cache-components.md` and #2243.",
    );
  } else if (expected === "partial" && actual === "static") {
    lines.push(
      "A `◐ → ○` drift means the page lost a dynamic island — the build no",
      "longer detects any per-viewer streaming content inside the shell.",
      "That is silently a UX regression (e.g. a personalised CTA collapsed",
      "to a hard-coded one) and a cacheability regression for pages that",
      "depend on viewer-language routing.",
      "",
      "Likely causes: a `<Suspense>`-wrapped island was deleted or its data",
      "fetcher was inlined into the cached parent. Review the latest diff",
      "to the page file and any helper it imports — anything that previously",
      "read `headers()` / `cookies()` / `getViewerLanguages()` should still be",
      "doing so inside a Suspense boundary.",
      "",
      "If this drift is intentional (page genuinely no longer needs per-viewer",
      "data), update EXPECTED_CLASSIFICATIONS to `static` AND link the",
      "justification in the PR.",
    );
  } else {
    lines.push(
      "A `○ → ◐` drift means a page that should be pure-static now contains",
      "a dynamic island. That is a CPU-cost regression — every request now",
      "incurs a function invocation for the streamed subtree.",
      "",
      "Likely causes: a helper imported by the page (or its layout) started",
      "reading runtime state (`headers()`, `cookies()`, viewer-derived data)",
      "where it previously did not.",
      "",
      "If this drift is intentional, update EXPECTED_CLASSIFICATIONS to",
      "`partial` AND link the justification in the PR.",
    );
  }
  return lines.join("\n");
}

function missingRouteDiagnostic(route: string, expected: Classification): string {
  return [
    `Route \`${route}\` was not present in the production build artifacts/summary,`,
    `but EXPECTED_CLASSIFICATIONS pins it to ${CLASSIFICATION_LABEL[expected]}.`,
    "",
    "Either the route was renamed/removed in the source tree (in which case",
    "remove it from EXPECTED_CLASSIFICATIONS — the must-stay-cacheable list",
    "rots silently if entries no longer match real routes), or the build",
    "never emitted usable route artifacts/summary (look for build failures earlier in the log).",
    "",
    "This guard exists because a stale list would silently drop coverage —",
    "we'd think we were watching a route we no longer ship. See #2885.",
  ].join("\n");
}

describe("build-output classifier (slow lane, #2885)", () => {
  const logPath = process.env.BUILD_OUTPUT_LOG;
  const distDir = logPath ? dirname(logPath) : null;

  /** Parse-guard: the globalSetup must have produced a log path. */
  it("globalSetup captured a build-output log", () => {
    expect(
      logPath,
      "BUILD_OUTPUT_LOG must be set by the globalSetup (test-setup/run-prod-build.ts)",
    ).toBeTruthy();
    expect(existsSync(logPath!), `build-output log not found at ${logPath}`).toBe(true);
  });

  // Eagerly parse so per-route assertions don't reparse the whole log.
  const buildOutput = logPath && existsSync(logPath) ? readFileSync(logPath, "utf8") : "";
  const artifactClassifications = parseRouteClassificationsFromArtifacts(distDir);
  const stdoutClassifications = parseRouteClassifications(buildOutput);
  const classifications =
    artifactClassifications.size > 0 ? artifactClassifications : stdoutClassifications;
  const classificationSource =
    artifactClassifications.size > 0 ? ".next build artifacts" : "build stdout";

  it("production build completed before classification", () => {
    const success = buildSucceeded(distDir);
    expect(
      success,
      [
        "The production build did not complete successfully, so route",
        "classification would be based on partial artifacts or missing stdout.",
        `Log path: ${logPath ?? "(unset)"}`,
        "",
        "Build log tail:",
        tail(buildOutput),
      ].join("\n"),
    ).not.toBe(false);
  });

  /**
   * Parse-guard #2: ensure the parser actually matched routes. If the
   * build silently failed, Next's artifact shape changed, and stdout is
   * unavailable/suppressed, every per-route assertion below would degrade
   * to a confusing "got undefined" — surface the root cause first.
   */
  it("parsed at least one route from build artifacts or stdout", () => {
    expect(
      classifications.size,
      [
        "Could not parse any route classifications from `.next` artifacts or build output.",
        `Log path: ${logPath ?? "(unset)"}`,
        `Artifact routes parsed: ${artifactClassifications.size}`,
        `Stdout routes parsed: ${stdoutClassifications.size}`,
        "Either the build failed before route artifacts were written, or the",
        "Next.js artifact/stdout format changed. Re-run `pnpm build` and",
        "inspect `.next/app-path-routes-manifest.json`, `.next/server/app/**/*.meta`,",
        "and the build-output log tail.",
      ].join("\n"),
    ).toBeGreaterThan(0);
  });

  for (const [route, expected] of EXPECTED_CLASSIFICATIONS) {
    const expectedLabel = CLASSIFICATION_LABEL[expected];

    /**
     * Per-route assertion. Combines (b) drift and (c) list freshness:
     *
     *   - If the route is missing from the build output → list rotted
     *     (route was renamed/removed). Diagnose with missingRouteDiagnostic.
     *   - If the actual glyph differs from expected → semantic drift
     *     (◐↔○ collapse, or the rare ƒ that the build itself missed).
     *     Diagnose with driftDiagnostic, naming both glyphs and the
     *     direction-specific fix prescription.
     */
    it(`${route} stays ${expectedLabel}`, () => {
      const actual = classifications.get(route);
      expect(
        actual,
        `${missingRouteDiagnostic(route, expected)}\n\nClassification source: ${classificationSource}.`,
      ).toBeDefined();
      expect(actual, driftDiagnostic(route, expected, actual!)).toBe(expected);
    });
  }
});
