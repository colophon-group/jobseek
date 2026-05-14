/**
 * Service-tier boundary check for issue #3329 (follow-up to PR #3335 /
 * issue #3231).
 *
 * Public REST handlers under `apps/web/app/api/v1/{taxonomies,resolve}`
 * were calling `"use server"` action exports as their service tier. The
 * fix moved the implementation into `@/lib/services/taxonomy.ts` (plain
 * server-only module, no `"use server"` directive). The thin
 * `@/lib/actions/taxonomy.ts` wrapper re-exports the same callables with
 * `"use server"` so existing UI callers (search-bar typeaheads, modal
 * counts, watchlist resolve flows) keep working as actions.
 *
 * This file pins two contracts:
 *
 *   1. The service module does **not** carry the `"use server"`
 *      directive — that's the entire point of the refactor.
 *   2. The action wrapper continues to expose the same callable surface
 *      via **declared** async wrappers, so legacy UI callers do not
 *      regress and the Next.js / Turbopack production build does not
 *      drop the wrapper's exports (PR #3335 build regression).
 *
 * No DB / Typesense / Redis touched — we just walk the bundle and verify
 * the module shape.
 */
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

vi.mock("server-only", () => ({}));

import * as taxonomyService from "@/lib/services/taxonomy";
import * as taxonomyActions from "@/lib/actions/taxonomy";

const repoRoot = resolve(__dirname, "../../../..");

function readSource(rel: string): string {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

describe("service tier boundary — taxonomy (#3329)", () => {
  it("`@/lib/services/taxonomy` source has no `'use server'` directive", () => {
    const src = readSource("src/lib/services/taxonomy.ts");
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

  it("`@/lib/actions/taxonomy` keeps the `'use server'` directive for UI callers", () => {
    const src = readSource("src/lib/actions/taxonomy.ts");
    const firstNonEmpty = src
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0 && !line.startsWith("//"));
    // Allow optional trailing semicolon — prettier auto-inserts one.
    expect(firstNonEmpty).toMatch(/^["']use server["'];?$/);
  });

  it("service and action wrappers expose the same callable surface for taxonomy", () => {
    // Every callable that exists on the action wrapper must come from
    // the service tier — that's what guarantees parity for UI callers.
    const serviceCallables = Object.entries(taxonomyService)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    const actionCallables = Object.entries(taxonomyActions)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
    expect(actionCallables).toEqual(serviceCallables);
    // Surface is non-trivial (sanity check — guards against an empty
    // re-export accidentally erasing the whole module).
    expect(actionCallables.length).toBeGreaterThan(5);
  });

  // Regression guard for the build break observed on PR #3335.
  //
  // Next.js / Turbopack processes `"use server"` files by scanning for
  // **declared** async functions and converting each into a
  // server-action reference. Pure `export { foo } from "..."` re-exports
  // yield ZERO exports from the wrapper's perspective — production
  // builds then fail at every client component that imports from the
  // wrapper.
  //
  // The fix is to declare each wrapper explicitly and delegate to the
  // service implementation, e.g.:
  //
  //     export async function suggestOccupations(
  //       ...args: Parameters<typeof service.suggestOccupations>
  //     ): ReturnType<typeof service.suggestOccupations> {
  //       return service.suggestOccupations(...args);
  //     }
  //
  // These tests scan the wrapper source and assert each callable listed
  // in the service module appears with an `await service.<name>(` or
  // `return service.<name>(` delegation. Catches future regressions
  // back to the broken `export { ... } from "..."` pattern.
  function callableServiceNames(mod: Record<string, unknown>): string[] {
    return Object.entries(mod)
      .filter(([, v]) => typeof v === "function")
      .map(([k]) => k)
      .sort();
  }

  function assertExplicitDelegation(wrapperSrc: string, name: string): void {
    // Allow either `return service.<name>(` (sync delegation) or
    // `await service.<name>(` (if a wrapper ever needs to do post-await
    // work). The hard rule we're enforcing: a literal call expression
    // against the service module must appear in the wrapper source.
    const pattern = new RegExp(
      String.raw`(?:await|return)\s+service\.` + name + String.raw`\(`,
    );
    expect(
      pattern.test(wrapperSrc),
      `wrapper must contain an explicit \`await service.${name}(\` or ` +
        `\`return service.${name}(\` delegation — pure re-exports break ` +
        `Next.js "use server" processing (PR #3335).`,
    ).toBe(true);
  }

  it("`@/lib/actions/taxonomy` declares an explicit async wrapper for every service callable", () => {
    const src = readSource("src/lib/actions/taxonomy.ts");
    const names = callableServiceNames(taxonomyService);
    // Sanity check — we expect a non-trivial surface so a future
    // accidental wipe of the service module surfaces here too.
    expect(names.length).toBeGreaterThan(5);
    for (const name of names) {
      // Each callable must appear as a declared async export in the
      // wrapper. Without this the `"use server"` module ships zero
      // exports, and Next.js client imports of
      // `@/lib/actions/taxonomy` fail at production build time.
      expect(
        new RegExp(
          String.raw`export\s+async\s+function\s+` + name + String.raw`\b`,
        ).test(src),
        `wrapper must declare \`export async function ${name}\` — pure ` +
          `re-exports break Next.js "use server" processing (PR #3335).`,
      ).toBe(true);
      assertExplicitDelegation(src, name);
    }
  });

  it("`@/lib/actions/taxonomy` contains no `export { foo } from \"@/lib/services/...\"` re-exports of callables", () => {
    // Type-only re-exports (`export type { ... } from ...`) ARE legal —
    // they're erased at compile-time and don't participate in the
    // `"use server"` transform. We just forbid plain value re-exports
    // from the wrapper, which is the exact pattern that broke PR #3335.
    const src = readSource("src/lib/actions/taxonomy.ts");
    // Match `export { ... } from "@/lib/services/..."` but NOT
    // `export type { ... } from "@/lib/services/..."`.
    const valueReExport = /^\s*export\s*\{[^}]+\}\s*from\s*["']@\/lib\/services\//m;
    expect(
      valueReExport.test(src),
      `src/lib/actions/taxonomy.ts must not use ` +
        `\`export { foo } from "@/lib/services/..."\` for callable ` +
        `re-exports — Next.js "use server" processing yields zero ` +
        `exports for that form (PR #3335). Use a declared ` +
        `\`export async function\` wrapper that delegates to the ` +
        `service instead.`,
    ).toBe(false);
  });
});
