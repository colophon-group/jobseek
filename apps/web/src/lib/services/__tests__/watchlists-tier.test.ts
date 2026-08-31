/**
 * Service-tier boundary check for issue #3332.
 *
 * Public REST handlers should call a plain server-only watchlists service
 * instead of importing the `"use server"` action wrapper. UI callers keep
 * importing `@/lib/actions/watchlists`, where declared async wrappers
 * preserve the server-action surface.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const repoRoot = resolve(__dirname, "../../../..");

function readSource(rel: string): string {
  return readFileSync(resolve(repoRoot, rel), "utf8");
}

function firstNonEmptyLine(src: string): string | undefined {
  return src
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0 && !line.startsWith("//"));
}

function exportedAsyncFunctionNames(src: string): string[] {
  return [...src.matchAll(/^export\s+async\s+function\s+(\w+)\s*\(/gm)]
    .map((match) => match[1])
    .sort();
}

const PRIVATE_ROUTE_SERVICE_READERS = [
  "getOwnedWatchlistById",
  "getOwnedWatchlistByLegacyPath",
] as const;

describe("watchlists service tier boundary (#3332)", () => {
  it("`@/lib/services/watchlists` is server-only, not a server-action module", () => {
    const src = readSource("src/lib/services/watchlists.ts");
    expect(firstNonEmptyLine(src)).not.toMatch(/^["']use server["'];?$/);
    expect(src).toContain('import "server-only"');
    expect(src).not.toContain('from "@/lib/actions/watchlists"');
    expect(src).toContain('from "@/lib/services/taxonomy"');
    expect(src).not.toContain('from "@/lib/actions/taxonomy"');
  });

  it("wraps the client-callable service surface as declared server actions", () => {
    const actionSrc = readSource("src/lib/actions/watchlists.ts");
    const serviceSrc = readSource("src/lib/services/watchlists.ts");

    expect(firstNonEmptyLine(actionSrc)).toMatch(/^["']use server["'];?$/);

    const serviceNames = exportedAsyncFunctionNames(serviceSrc);
    const actionNames = exportedAsyncFunctionNames(actionSrc);
    const actionEligibleServiceNames = serviceNames.filter(
      (name) =>
        !PRIVATE_ROUTE_SERVICE_READERS.includes(
          name as (typeof PRIVATE_ROUTE_SERVICE_READERS)[number],
        ),
    );
    expect(serviceNames.length).toBeGreaterThan(10);
    expect(actionNames).toEqual(actionEligibleServiceNames);

    for (const name of actionEligibleServiceNames) {
      expect(actionSrc).toMatch(
        new RegExp(String.raw`export\s+async\s+function\s+${name}\b`),
      );
      expect(actionSrc).toContain(`return service.${name}(...args);`);
    }

    for (const name of PRIVATE_ROUTE_SERVICE_READERS) {
      expect(serviceNames).toContain(name);
      expect(actionNames).not.toContain(name);
      expect(actionSrc).not.toContain(`service.${name}`);
    }

    const valueReExport = /^\s*export\s*\{[^}]+\}\s*from\s*["']@\/lib\/services\//m;
    expect(valueReExport.test(actionSrc)).toBe(false);
  });

  it("keeps private-route readers owner-scoped, uncached, and server-internal", () => {
    const actionSrc = readSource("src/lib/actions/watchlists.ts");
    const serviceSrc = readSource("src/lib/services/watchlists.ts");
    const routeSrc = readSource(
      "app/[lang]/(app)/[userSlug]/[watchlistSlug]/route.ts",
    );
    const privateReaderStart = serviceSrc.indexOf(
      "async function _getOwnedWatchlistDetail",
    );
    const privateReaderEnd = serviceSrc.indexOf(
      "async function _resolveUserListingCounts",
    );

    expect(privateReaderStart).toBeGreaterThan(-1);
    expect(privateReaderEnd).toBeGreaterThan(privateReaderStart);
    const privateReaderSrc = serviceSrc.slice(
      privateReaderStart,
      privateReaderEnd,
    );

    expect(privateReaderSrc).toContain(
      "WHERE w.user_id = ${userId} AND ${predicate}",
    );
    expect(privateReaderSrc).not.toContain('"use cache"');
    expect(privateReaderSrc).not.toContain("'use cache'");
    expect(privateReaderSrc).not.toContain("cached(");
    expect(actionSrc).not.toContain("getOwnedWatchlistById");
    expect(actionSrc).not.toContain("getOwnedWatchlistByLegacyPath");

    expect(routeSrc).toContain("const session = await getSession()");
    expect(routeSrc).toContain("getOwnedWatchlistByLegacyPath(");
    expect(routeSrc).toContain("session.user.id");
    expect(routeSrc.match(/private, no-store/g)).toHaveLength(2);
    expect(routeSrc).not.toContain('"use cache"');
    expect(routeSrc).not.toContain("'use cache'");
  });

  it("retired REST discovery is isolated while owner-scoped domain seams remain", () => {
    const routeSrc = readSource("app/api/v1/watchlists/route.ts");
    const serviceSrc = readSource("src/lib/services/watchlists.ts");

    expect(routeSrc).not.toContain('from "@/lib/services/watchlists"');
    expect(routeSrc).not.toContain('from "@/lib/actions/watchlists"');
    expect(serviceSrc).toMatch(/export async function getUserWatchlists\b/);
    expect(serviceSrc).toMatch(
      /export async function getWatchlistByUserAndSlug\b/,
    );
  });

  it("keeps every mutation entrypoint private and copy authorization visibility-free", () => {
    const actionSrc = readSource("src/lib/actions/watchlists.ts");
    const serviceSrc = readSource("src/lib/services/watchlists.ts");
    const handoffSrc = readSource("src/lib/services/watchlist-handoff.ts");
    const copyPolicySrc = readSource("src/lib/watchlist-copy-policy.ts");

    expect(actionSrc).toContain("return service.createWatchlist(...args);");
    expect(actionSrc).toContain("return service.updateWatchlist(...args);");
    expect(actionSrc).toContain("return service.copyWatchlist(...args);");

    expect(serviceSrc).toContain(
      'if (params.isPublic === true) return { error: "visibility_locked" };',
    );
    expect(serviceSrc).toContain(
      'if (params.isPublic !== undefined) return { error: "visibility_locked" };',
    );
    expect(serviceSrc).not.toContain("isPublic: params.isPublic");
    expect(serviceSrc).not.toContain("updates.isPublic");
    expect(serviceSrc).not.toContain("copy_watchlist_mirror_count");
    expect(serviceSrc).not.toContain("_getWatchlistMirrorCount");

    expect(handoffSrc).not.toContain("isPublic");
    expect(copyPolicySrc).not.toContain("source.isPublic");
    expect(copyPolicySrc).toContain('case "owned"');
    expect(copyPolicySrc).toContain('case "grant"');
    expect(copyPolicySrc).toContain('case "share"');
    expect(copyPolicySrc).toContain('case "template"');
  });
});
