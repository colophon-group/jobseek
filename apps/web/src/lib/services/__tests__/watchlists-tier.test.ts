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

describe("watchlists service tier boundary (#3332)", () => {
  it("`@/lib/services/watchlists` is server-only, not a server-action module", () => {
    const src = readSource("src/lib/services/watchlists.ts");
    expect(firstNonEmptyLine(src)).not.toMatch(/^["']use server["'];?$/);
    expect(src).toContain('import "server-only"');
    expect(src).not.toContain('from "@/lib/actions/watchlists"');
    expect(src).toContain('from "@/lib/services/taxonomy"');
    expect(src).not.toContain('from "@/lib/actions/taxonomy"');
  });

  it("`@/lib/actions/watchlists` remains a declared server-action wrapper", () => {
    const actionSrc = readSource("src/lib/actions/watchlists.ts");
    const serviceSrc = readSource("src/lib/services/watchlists.ts");

    expect(firstNonEmptyLine(actionSrc)).toMatch(/^["']use server["'];?$/);

    const serviceNames = exportedAsyncFunctionNames(serviceSrc);
    const actionNames = exportedAsyncFunctionNames(actionSrc);
    expect(serviceNames.length).toBeGreaterThan(10);
    expect(actionNames).toEqual(serviceNames);

    for (const name of serviceNames) {
      expect(actionSrc).toMatch(
        new RegExp(String.raw`export\s+async\s+function\s+${name}\b`),
      );
      expect(actionSrc).toContain(`return service.${name}(...args);`);
    }

    const valueReExport = /^\s*export\s*\{[^}]+\}\s*from\s*["']@\/lib\/services\//m;
    expect(valueReExport.test(actionSrc)).toBe(false);
  });

  it("public watchlists REST route imports the service tier", () => {
    const src = readSource("app/api/v1/watchlists/route.ts");
    expect(src).toContain('from "@/lib/services/watchlists"');
    expect(src).not.toContain('from "@/lib/actions/watchlists"');
  });
});
