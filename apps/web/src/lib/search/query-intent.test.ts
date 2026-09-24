import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import policy from "./jev-query-policy.json";
import {
  buildQueryIntentRequest,
  chooseQueryCandidate,
  selectedRouteSpans,
  spansForQuery,
  validateJevAnswers,
} from "./query-intent";

const experimentPath = (file: string) => resolve(process.cwd(), "../../docs/experiments", file);

describe("search-query Jev contract", () => {
  it("keeps the deployed prompt and catalog identical to the evaluated freeze", () => {
    const catalog = readFileSync(resolve(process.cwd(), "../../scripts/experiments/jev-occupation-catalog.json"), "utf8");
    const freeze = JSON.parse(readFileSync(experimentPath("jev-routing-diverse-frozen-config-2026-09-24.json"), "utf8"));
    const hash = createHash("sha256").update(policy.policy + "\n" + catalog).digest("hex");
    expect(hash).toBe(freeze.promptAndCatalogSha256);
    expect(policy.version).toBe("jev-search-catalog5-v1");
    expect(policy.model).toBe(freeze.model);
  });

  it("matches all 32 fresh human span labels with the recorded Jev responses", () => {
    const run = JSON.parse(readFileSync(experimentPath("jev-routing-diverse-catalog5-holdout-2026-09-24.json"), "utf8"));
    const gold = JSON.parse(readFileSync(experimentPath("jev-routing-diverse-intent-gold-2026-09-24.json"), "utf8"));
    const byId = new Map<string, { terms: Array<{ segment: number; start: number; end: number; category: string }> }>(
      gold.records.map((record: { id: string; terms: unknown[] }) => [record.id, record]),
    );
    expect(run.records).toHaveLength(32);
    for (const record of run.records) {
      const { spans } = spansForQuery(record.q);
      const answers = validateJevAnswers(record.answers, spans);
      const selected = selectedRouteSpans(record.q, answers)
        .map(({ segment, start, end, category }) => ({ segment, start, end, category }));
      expect(selected, record.id).toEqual(byId.get(record.id)?.terms.map(
        ({ segment, start, end, category }) => ({ segment, start, end, category }),
      ));
    }
  });

  it("bounds model work and rejects incomplete responses", () => {
    expect(() => buildQueryIntentRequest("a".repeat(181), "en")).toThrow(RangeError);
    const request = buildQueryIntentRequest("junior accountant Zurich hybrid", "en");
    const spans = spansForQuery("junior accountant Zurich hybrid").spans;
    expect(Object.keys(request.questions)).toHaveLength(spans.length);
    expect(() => validateJevAnswers({}, spans)).toThrow(TypeError);
  });

  it("keeps ambiguous places and unrelated fuzzy roles out of automatic filters", () => {
    expect(chooseQueryCandidate("New York", [
      { id: 1, slug: "new-york-state", name: "New York", type: "state" },
      { id: 2, slug: "new-york-city", name: "New York", type: "city" },
    ]).status).toBe("ambiguous");
    expect(chooseQueryCandidate("finance analyst", [
      { id: 3, slug: "finance-manager", name: "Finance Manager" },
      { id: 4, slug: "business-analyst", name: "Business Analyst" },
    ]).status).toBe("unresolved");
    expect(chooseQueryCandidate("compliace officer", [
      { id: 5, slug: "compliance-officer", name: "Compliance Officer" },
    ])).toMatchObject({ status: "approximate", candidate: { slug: "compliance-officer" } });
  });
});
