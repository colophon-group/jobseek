import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import policy from "./jev-query-policy.json";
import {
  buildQueryIntentRequest,
  chooseQueryCandidate,
  spansForQuery,
  validateJevAnswers,
} from "./query-intent";

const experimentPath = (file: string) => resolve(process.cwd(), "../../docs/experiments", file);

describe("search-query Jev contract", () => {
  it("keeps the deployed prompt and catalog identical to the evaluated freeze", () => {
    const freeze = JSON.parse(readFileSync(experimentPath("jev-routing-discard-catalog7-freeze-2026-09-24.json"), "utf8"));
    const hash = createHash("sha256").update(JSON.stringify({
      policy: policy.policy,
      categories: policy.categories,
      intentCriteria: policy.intentCriteria,
      catalog: policy.occupationCatalog,
    })).digest("hex");
    expect(hash).toBe(freeze.policySha256);
    expect(policy.version).toBe("jev-search-catalog7-v1");
    expect(policy.model).toBe(freeze.model);
  });

  it("records the frozen holdout scores and leaves informational queries untouched", () => {
    const run = JSON.parse(readFileSync(experimentPath("jev-routing-discard-catalog7-holdout-2026-09-24.json"), "utf8"));
    expect(run.records).toHaveLength(16);
    expect(run.filterExact).toBe(16);
    expect(run.discardExact).toBe(16);
    for (const record of run.records) {
      if (record.intent === "other") expect(record.selected).toEqual([]);
    }
  });

  it("bounds model work and rejects incomplete responses", () => {
    expect(() => buildQueryIntentRequest("a".repeat(181), "en")).toThrow(RangeError);
    const request = buildQueryIntentRequest("junior accountant Zurich hybrid", "en");
    const spans = spansForQuery("junior accountant Zurich hybrid").spans;
    expect(Object.keys(request.questions)).toHaveLength(spans.length + 1);
    expect(() => validateJevAnswers({}, spans)).toThrow(TypeError);
    expect(spansForQuery("part-time nurse on-site").spans.map((span) => span.text))
      .toContain("part-time");
    expect(spansForQuery("part-time nurse on-site").spans.map((span) => span.text))
      .toContain("on-site");
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
