import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const suggestLocations = vi.fn();
const suggestOccupations = vi.fn();
const suggestSeniorities = vi.fn();
const suggestTechnologies = vi.fn();
vi.mock("./locations", () => ({ suggestLocations: (...args: unknown[]) => suggestLocations(...args) }));
vi.mock("./taxonomy", () => ({
  suggestOccupations: (...args: unknown[]) => suggestOccupations(...args),
  suggestSeniorities: (...args: unknown[]) => suggestSeniorities(...args),
  suggestTechnologies: (...args: unknown[]) => suggestTechnologies(...args),
}));

import { proposeQueryFilters } from "./query-intent";
import { buildQueryIntentRequest } from "@/lib/search/query-intent";

function mockJev(query: string, classified: Array<{ text: string; choice: string }>) {
  const request = buildQueryIntentRequest(query, "en");
  const categories = Object.keys(request.questions.s_0.criteria);
  const answers = Object.fromEntries(request.state.spans.map((span) => {
    const choice = classified.find((item) => item.text === span.text)?.choice ?? "keyword";
    return [span.id, { type: "choice", choice,
      probabilities: Object.fromEntries(categories.map((category) => [category, category === choice ? 0.99 : 0.001])) }];
  }));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ model: "jev-1.13.0", answers }), { status: 200 })));
}

describe("query-intent service", () => {
  beforeEach(() => {
    vi.stubEnv("TYPESAFE_AI_TOKEN", "test-token");
    suggestLocations.mockReset().mockResolvedValue([]);
    suggestOccupations.mockReset().mockResolvedValue([]);
    suggestSeniorities.mockReset().mockResolvedValue([]);
    suggestTechnologies.mockReset().mockResolvedValue([]);
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

  it("normalizes only the chosen occupation span and leaves no stray keyword", async () => {
    mockJev("compliance officer", [{ text: "compliance officer", choice: "occupation" }]);
    suggestOccupations.mockResolvedValue([{ id: 7, slug: "compliance-officer", name: "Compliance Officer" }]);
    const proposal = await proposeQueryFilters({ query: "compliance officer", locale: "en" });
    expect(proposal.occupations.map((item) => item.slug)).toEqual(["compliance-officer"]);
    expect(proposal.keywords).toEqual([]);
    expect(suggestOccupations).toHaveBeenCalledTimes(1);
    expect(suggestOccupations).toHaveBeenCalledWith(expect.objectContaining({ query: "compliance officer" }));
    expect(suggestLocations).not.toHaveBeenCalled();
    expect(suggestSeniorities).not.toHaveBeenCalled();
    expect(suggestTechnologies).not.toHaveBeenCalled();
  });

  it("keeps an informational question as keywords without Typesense calls", async () => {
    mockJev("what is a compliance officer", []);
    const proposal = await proposeQueryFilters({ query: "what is a compliance officer", locale: "en" });
    expect(proposal.occupations).toEqual([]);
    expect(proposal.keywords).toEqual(["what", "is", "a", "compliance", "officer"]);
    expect(suggestOccupations).not.toHaveBeenCalled();
    expect(suggestLocations).not.toHaveBeenCalled();
  });

  it("returns ambiguous city/state text as keywords for correction", async () => {
    mockJev("New York", [{ text: "New York", choice: "location" }]);
    suggestLocations.mockResolvedValue([
      { id: 2, slug: "new-york-state", name: "New York", type: "state", parentName: "United States" },
      { id: 3, slug: "new-york-city", name: "New York", type: "city", parentName: "New York" },
    ]);
    const proposal = await proposeQueryFilters({ query: "New York", locale: "en" });
    expect(proposal.locations).toEqual([]);
    expect(proposal.keywords).toEqual(["New", "York"]);
    expect(proposal.terms).toMatchObject([{ status: "ambiguous", candidate: null }]);
  });
});
