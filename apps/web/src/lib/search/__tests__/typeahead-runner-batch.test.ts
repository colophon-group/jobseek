import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";
import type {
  SearchBarTypeaheadParams,
  SearchBarTypeaheadResults,
} from "../typeahead-contract";

const mocks = vi.hoisted(() => ({
  browserBatch: vi.fn(),
  serverBatch: vi.fn(),
}));

vi.mock("@/lib/actions/locations", () => ({ suggestLocations: vi.fn() }));
vi.mock("@/lib/actions/taxonomy", () => ({
  suggestOccupations: vi.fn(),
  suggestSeniorities: vi.fn(),
  suggestTechnologies: vi.fn(),
}));
vi.mock("@/lib/actions/typeahead", () => ({
  suggestSearchBarTypeahead: mocks.serverBatch,
}));
vi.mock("../typesense-browser-typeahead", () => ({
  suggestSearchBarBrowser: mocks.browserBatch,
}));

const params: SearchBarTypeaheadParams = {
  query: "engineer",
  locale: "en",
  includeCompanies: true,
};
const results: SearchBarTypeaheadResults = {
  locations: [],
  companies: [],
  occupations: [],
  seniorities: [],
  technologies: [],
};

describe("runSearchBarTypeahead", () => {
  withTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });
  });

  it("returns a successful direct batch without a server action", async () => {
    mocks.browserBatch.mockResolvedValue(results);
    const { runSearchBarTypeahead } = await import("../typeahead-runner");

    await expect(runSearchBarTypeahead(params)).resolves.toBe(results);
    expect(mocks.browserBatch).toHaveBeenCalledOnce();
    expect(mocks.serverBatch).not.toHaveBeenCalled();
  });

  it("falls back through exactly one batched server action", async () => {
    mocks.browserBatch.mockRejectedValue(new Error("direct search unavailable"));
    mocks.serverBatch.mockResolvedValue(results);
    const { runSearchBarTypeahead } = await import("../typeahead-runner");

    await expect(runSearchBarTypeahead(params)).resolves.toBe(results);
    expect(mocks.browserBatch).toHaveBeenCalledOnce();
    expect(mocks.serverBatch).toHaveBeenCalledOnce();
  });

  it("uses one server action when direct search is disabled", async () => {
    setTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "0" });
    vi.resetModules();
    mocks.serverBatch.mockResolvedValue(results);
    const { runSearchBarTypeahead } = await import("../typeahead-runner");

    await expect(runSearchBarTypeahead(params)).resolves.toBe(results);
    expect(mocks.browserBatch).not.toHaveBeenCalled();
    expect(mocks.serverBatch).toHaveBeenCalledOnce();
  });
});
