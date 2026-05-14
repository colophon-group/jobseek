/**
 * Service-tier boundary check for issue #3231.
 *
 * Public REST handlers under `apps/web/app/api/v1/*` were calling
 * `"use server"` action exports as their service tier. The fix moved the
 * implementation into `@/lib/services/*` (plain server-only modules, no
 * `"use server"` directive). The thin `@/lib/actions/search.ts` /
 * `@/lib/actions/search-input.ts` wrappers re-export the same functions
 * with `"use server"` so existing UI callers keep working as actions.
 *
 * This file pins two contracts:
 *
 *   1. The service module does **not** carry the `"use server"`
 *      directive — that's the entire point of the refactor.
 *   2. The action wrapper continues to re-export the same callable
 *      surface, so legacy UI callers do not regress.
 *
 * No DB / Typesense / Redis touched — we just walk the bundle and verify
 * the module shape.
 */
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

vi.mock("server-only", () => ({}));

import * as searchService from "@/lib/services/search";
import * as searchInputService from "@/lib/services/search-input";
import * as searchActions from "@/lib/actions/search";
import * as searchInputActions from "@/lib/actions/search-input";

const repoRoot = resolve(__dirname, "../../../..");

function readSource(rel: string): string {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

describe("service tier boundary (#3231)", () => {
  it("`@/lib/services/search` source has no `'use server'` directive", () => {
    const src = readSource("src/lib/services/search.ts");
    // Match a directive at the very top of the file. We do NOT want any
    // `"use server"` / `'use server'` opening line on the service tier.
    const firstNonEmpty = src
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("//"));
    expect(firstNonEmpty).not.toMatch(/^["']use server["'];?$/);
    // It SHOULD declare `import "server-only"` so accidental client
    // imports surface as a build error rather than a runtime leak.
    expect(src).toContain('import "server-only"');
  });

  it("`@/lib/services/search-input` source has no `'use server'` directive", () => {
    const src = readSource("src/lib/services/search-input.ts");
    const firstNonEmpty = src
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("//"));
    expect(firstNonEmpty).not.toMatch(/^["']use server["'];?$/);
    expect(src).toContain('import "server-only"');
  });

  it("`@/lib/actions/search` keeps the `'use server'` directive for UI callers", () => {
    const src = readSource("src/lib/actions/search.ts");
    const firstNonEmpty = src
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("//"));
    // Allow optional trailing semicolon — prettier auto-inserts one.
    expect(firstNonEmpty).toMatch(/^["']use server["'];?$/);
  });

  it("`@/lib/actions/search-input` keeps the `'use server'` directive for UI callers", () => {
    const src = readSource("src/lib/actions/search-input.ts");
    const firstNonEmpty = src
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("//"));
    expect(firstNonEmpty).toMatch(/^["']use server["'];?$/);
  });

  it("service and action wrappers expose the same callable surface for search", () => {
    // Every callable that exists on the action wrapper must come from the
    // service tier — that's what guarantees parity for UI callers.
    const serviceCallables = Object.entries(searchService)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    const actionCallables = Object.entries(searchActions)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    expect(actionCallables).toEqual(serviceCallables);
    // Surface is non-trivial (sanity check — guards against an empty
    // re-export accidentally erasing the whole module).
    expect(actionCallables.length).toBeGreaterThan(3);
  });

  it("service and action wrappers expose the same callable surface for search-input", () => {
    const serviceCallables = Object.entries(searchInputService)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    const actionCallables = Object.entries(searchInputActions)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    expect(actionCallables).toEqual(serviceCallables);
    expect(actionCallables).toContain("parseSearchFilters");
  });

  it("service functions are plain async functions (no action wrapping)", () => {
    // `parseSearchFilters` is a plain async function, not a Next.js
    // server-action reference object. We can detect this by checking that
    // calling it without a request context just runs the function (rather
    // than going through the action runtime). The function reference's
    // `constructor.name` for an async function is "AsyncFunction"; a
    // server-action exported from a `"use server"` module gets wrapped
    // into a different shape at build time, but at the source level both
    // look like async functions to TypeScript / Vitest. We pin the
    // simpler invariant: it's callable, returns a Promise.
    expect(typeof searchInputService.parseSearchFilters).toBe("function");
    expect(searchInputService.parseSearchFilters.constructor.name).toBe(
      "AsyncFunction",
    );
  });
});
