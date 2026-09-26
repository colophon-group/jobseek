// Explicit opt-in microbenchmark of the production parser with I/O mocked.
// RUN_SEARCH_INPUT_LATENCY_BENCH=1 pnpm exec vitest run src/lib/actions/__tests__/search-input-latency.test.ts
import { it, vi } from "vitest";
import { performance } from "node:perf_hooks";

const mocks = vi.hoisted(() => ({
  suggestLocations: vi.fn(async ({ query }: { query: string }) =>
    query.toLowerCase() === "zurich" ? [{ id: 1, slug: "zurich", name: "Zurich", type: "city", parentName: "Switzerland" }]
      : query.toLowerCase() === "berlin" ? [{ id: 2, slug: "berlin", name: "Berlin", type: "city", parentName: "Germany" }] : []),
  suggestOccupations: vi.fn(async ({ query }: { query: string }) =>
    query.toLowerCase() === "engineer" || query.toLowerCase() === "developer"
      ? [{ id: 3, slug: "software-engineer", name: "Software Engineer" }] : []),
  suggestSeniorities: vi.fn(async ({ query }: { query: string }) =>
    ["senior", "staff"].includes(query.toLowerCase())
      ? [{ id: 4, slug: query.toLowerCase(), name: query }] : []),
  suggestTechnologies: vi.fn(async ({ query }: { query: string }) =>
    ["python", "rust", "kubernetes"].includes(query.toLowerCase())
      ? [{ id: 5, slug: query.toLowerCase(), name: query }] : []),
  resolveLocationSlugs: vi.fn(async () => new Map()),
  resolveOccupationSlugs: vi.fn(async () => new Map()),
  resolveSenioritySlugs: vi.fn(async () => new Map()),
  resolveTechnologySlugs: vi.fn(async () => new Map()),
}));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/services/locations", () => ({
  suggestLocations: mocks.suggestLocations,
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));
vi.mock("@/lib/services/taxonomy", () => ({
  suggestOccupations: mocks.suggestOccupations,
  suggestSeniorities: mocks.suggestSeniorities,
  suggestTechnologies: mocks.suggestTechnologies,
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));

import { parseSearchFilters } from "../search-input";

function percentile(values: number[], fraction: number) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(fraction * sorted.length) - 1];
}

it.skipIf(process.env.RUN_SEARCH_INPUT_LATENCY_BENCH !== "1")(
  "measures parsing after taxonomy suggestions are available",
  async () => {
    for (const [name, q] of [
      ["short", "remote engineer"],
      ["typical", "senior Python developer Zurich remote"],
      ["long", "hybrid staff backend engineer Rust Kubernetes Berlin"],
    ]) {
      for (let i = 0; i < 100; i++) await parseSearchFilters({ q, locale: "en" });
      const values: number[] = [];
      for (let i = 0; i < 2_000; i++) {
        const start = performance.now();
        await parseSearchFilters({ q, locale: "en" });
        values.push(performance.now() - start);
      }
      console.log(JSON.stringify({
        fixture: name,
        samples: values.length,
        medianMs: percentile(values, 0.5),
        p95Ms: percentile(values, 0.95),
        maxMs: Math.max(...values),
      }));
    }
  },
  60_000,
);
