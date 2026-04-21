import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..", "..");

/**
 * Pages that MUST stay statically prerenderable (ISR), per the Vercel
 * CPU-cost audit. Each of these pages exports `revalidate = N`, which
 * Next.js silently downgrades to dynamic rendering if any dynamic
 * API (`searchParams`, `headers()`, `cookies()`, `draftMode()`, or any
 * helper that internally reads them) is touched on the render path.
 *
 * When that happens, the route serves `cache-control: private,
 * no-store` instead of being CDN-cached, and every request hits the
 * function. SEO landing pages like `/[lang]/company/[slug]` get the
 * bulk of organic traffic, so a single regression here can blow the
 * monthly CPU quota.
 *
 * If you need request-bound data on one of these pages, hoist the
 * read into a client subtree (`useSearchParams()`) or a server action
 * fired from the client. After deploy, verify with:
 *
 *   curl -sSI https://jseek.co/<a representative path> | grep cache
 *
 * The response must show `cache-control: public` and — after a
 * warmup request — `x-vercel-cache: HIT`.
 *
 * See issue #2243 for the original incident.
 */
const ISR_PROTECTED_PAGES = [
  "app/[lang]/(app)/company/[slug]/page.tsx",
  "app/[lang]/(app)/[userSlug]/[watchlistSlug]/page.tsx",
];

/**
 * Direct dynamic API references whose presence anywhere in a server
 * page module forces dynamic rendering. The `fix` field is shown in
 * the assertion error so a developer who breaks this knows what to
 * do without having to dig through Next.js docs.
 */
const FORBIDDEN_IN_ISR: ReadonlyArray<{ pattern: RegExp; fix: string }> = [
  {
    pattern: /\bawait\s+searchParams\b/,
    fix: "Reading `searchParams` in a server component opts the page into dynamic rendering. Read it in a client subcomponent via `useSearchParams()` instead.",
  },
  {
    pattern: /\bsearchParams\s*:\s*Promise\s*</,
    fix: "Declaring `searchParams` in Props signals server-side reads. Drop the prop and read in a client subtree.",
  },
  {
    pattern: /\bawait\s+headers\(\s*\)/,
    fix: "`headers()` forces dynamic rendering. Move the read into a server action fired from the client, not the page render path.",
  },
  {
    pattern: /\bawait\s+cookies\(\s*\)/,
    fix: "`cookies()` forces dynamic rendering. Move the read into a server action fired from the client.",
  },
  {
    pattern: /\bawait\s+draftMode\(\s*\)/,
    fix: "`draftMode()` forces dynamic rendering. Not appropriate on a public ISR page.",
  },
  {
    pattern: /\bawait\s+connection\(\s*\)/,
    fix: "`connection()` forces dynamic rendering. Restructure so the dynamic data is fetched from a Suspense boundary or a client child.",
  },
];

/**
 * Server helpers that internally read `headers()` / `cookies()` / a
 * session token. Calling any of them from an ISR-protected page's
 * render path will silently break ISR. Maintained alongside the
 * `sessionCache` and `viewer` modules — if you add another helper
 * that reads request state, list it here too.
 */
const TAINTED_HELPERS: ReadonlyArray<string> = [
  "getSession",
  "getSessionUserId",
  "getViewerLanguages",
  "getGeoFromHeaders",
  "getPreferences",
];

function isClientComponent(source: string): boolean {
  // Match the leading "use client" directive (allow whitespace/comments above).
  return /^\s*(?:\/\*[\s\S]*?\*\/\s*|\/\/.*\n\s*)*['"]use client['"]/.test(
    source,
  );
}

function lineOf(source: string, index: number): number {
  return source.slice(0, index).split("\n").length;
}

function scanFile(label: string, source: string): void {
  for (const { pattern, fix } of FORBIDDEN_IN_ISR) {
    const match = source.match(pattern);
    if (match) {
      throw new Error(
        `${label} contains forbidden pattern matching ${pattern} (line ${
          lineOf(source, match.index ?? 0)
        }).\n  ${fix}`,
      );
    }
  }
  for (const helper of TAINTED_HELPERS) {
    const re = new RegExp(`\\b${helper}\\s*\\(`);
    const match = source.match(re);
    if (match) {
      throw new Error(
        `${label} calls "${helper}()" (line ${
          lineOf(source, match.index ?? 0)
        }), which internally reads request headers/cookies and forces dynamic rendering.\n  Move the call into a client component or a server action fired from the client.`,
      );
    }
  }
}

describe("ISR-protected page templates stay static", () => {
  for (const pagePath of ISR_PROTECTED_PAGES) {
    it(`${pagePath} renders without dynamic-render triggers`, () => {
      const fullPath = join(webRoot, pagePath);
      const source = readFileSync(fullPath, "utf-8");

      // Sanity: the page must declare `revalidate`. If it doesn't, the
      // test is guarding a route that's already dynamic — pointless.
      expect(
        source,
        `${pagePath} must export 'revalidate' to qualify as ISR-protected`,
      ).toMatch(/export\s+const\s+revalidate\s*=/);

      // The page module itself.
      scanFile(pagePath, source);

      // Co-located server components in the same directory. (Client
      // components — files starting with "use client" — are skipped:
      // their reads are request-scoped on the client, not the server,
      // so they don't taint static rendering.)
      const pageDir = dirname(fullPath);
      const siblings = readdirSync(pageDir, { withFileTypes: true })
        .filter(
          (d) =>
            d.isFile() &&
            (d.name.endsWith(".tsx") || d.name.endsWith(".ts")) &&
            d.name !== "page.tsx" &&
            !d.name.endsWith(".test.ts") &&
            !d.name.endsWith(".test.tsx"),
        )
        .map((d) => join(pageDir, d.name));

      for (const sibling of siblings) {
        const siblingSource = readFileSync(sibling, "utf-8");
        if (isClientComponent(siblingSource)) continue;
        const label = relative(webRoot, sibling);
        scanFile(label, siblingSource);
      }
    });
  }
});
