import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  parse: vi.fn(),
  typeahead: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams("etype=contract&sal=50000-&qmode=literal"),
  usePathname: () => "/en/explore",
  useParams: () => ({ lang: "en" }),
}));
vi.mock("@/lib/search/typeahead-runner", () => ({
  runSearchBarTypeahead: (...args: unknown[]) => mocks.typeahead(...args),
  runSearchBarTermTypeahead: async () => [],
}));
vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: (...args: unknown[]) => mocks.parse(...args),
}));
vi.mock("server-only", () => ({}));

import { SearchBar } from "../search-bar";

const proposal = {
  version: "jev-search-catalog7-v1",
  query: "junior accountant Zurich hybrid",
  locale: "en",
  intent: "jobSearch",
  keywords: [],
  locations: [{ id: 12, slug: "zurich", name: "Zurich", type: "city" }],
  occupations: [{ id: 13, slug: "accountant", name: "Accountant" }],
  seniorities: [{ id: 14, slug: "junior", name: "Junior" }],
  technologies: [],
  workMode: ["hybrid"],
  employmentTypes: ["full_time"],
  terms: [],
};

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SEARCH_QUERY_JEV_ENABLED", "true");
  mocks.push.mockReset();
  mocks.parse.mockReset().mockResolvedValue({ keywords: [], locations: [], occupations: [], seniorities: [], technologies: [], workMode: [] });
  mocks.typeahead.mockReset().mockResolvedValue({ locations: [], companies: [], occupations: [], seniorities: [], technologies: [] });
  mocks.fetch.mockReset().mockResolvedValue({ ok: true, json: async () => proposal });
  vi.stubGlobal("fetch", mocks.fetch);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("SearchBar Jev proposal lifecycle", () => {
  it("shows one idle proposal and reuses it on Enter while preserving hard filters", async () => {
    render(<SearchBar />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: proposal.query } });
    await waitFor(() => expect(screen.getByTestId("search-bar-query-proposal")).toBeTruthy(), { timeout: 2500 });
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
    const url = new URL(mocks.push.mock.calls[0][0], "https://jobseek.test");
    expect(url.searchParams.get("qmode")).toBe("literal");
    expect(url.searchParams.get("loc")).toBe("zurich");
    expect(url.searchParams.get("occ")).toBe("accountant");
    expect(url.searchParams.get("sen")).toBe("junior");
    expect(url.searchParams.get("wm")).toBe("hybrid");
    expect(url.searchParams.get("etype")).toBe("contract,full_time");
    expect(url.searchParams.get("sal")).toBe("50000-");
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    expect(mocks.parse).not.toHaveBeenCalled();
  });

  it("starts on Enter without waiting for idle and ignores a stale completion", async () => {
    let resolveFetch!: (value: unknown) => void;
    mocks.fetch.mockImplementation(() => new Promise((resolve) => { resolveFetch = resolve; }));
    render(<SearchBar />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: proposal.query } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "nurse Berlin" } });
    resolveFetch({ ok: true, json: async () => proposal });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(mocks.push).not.toHaveBeenCalled();
    expect(screen.queryByTestId("search-bar-query-proposal")).toBeNull();
  });

  it("routes an unfamiliar single term through Jev when Enter beats typeahead", async () => {
    mocks.fetch.mockResolvedValue({ ok: true, json: async () => ({
      ...proposal, query: "pharmacst", keywords: ["pharmacst"],
      locations: [], occupations: [], seniorities: [], workMode: [], employmentTypes: [],
    }) });
    render(<SearchBar />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "pharmacst" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
    const url = new URL(mocks.push.mock.calls[0][0], "https://jobseek.test");
    expect(url.searchParams.get("q")).toBe("pharmacst");
    expect(url.searchParams.get("qmode")).toBe("literal");
    expect(mocks.parse).not.toHaveBeenCalled();
  });

  it("clears an old keyboard highlight before immediate Enter on edited text", async () => {
    mocks.fetch.mockResolvedValue({ ok: true, json: async () => ({ ...proposal, query: "nurse Berlin" }) });
    render(<SearchBar />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: proposal.query } });
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toMatch(/option-0$/);
    fireEvent.change(input, { target: { value: "nurse Berlin" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
    expect(mocks.parse).not.toHaveBeenCalled();
  });

  it("does not submit an old answer after the same text is retyped", async () => {
    const resolvers: Array<(value: unknown) => void> = [];
    mocks.fetch.mockImplementation(() => new Promise((resolve) => { resolvers.push(resolve); }));
    render(<SearchBar />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: proposal.query } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.change(input, { target: { value: "nurse Berlin" } });
    fireEvent.change(input, { target: { value: proposal.query } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.fetch).toHaveBeenCalledTimes(2);
    resolvers[0]({ ok: true, json: async () => proposal });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mocks.push).not.toHaveBeenCalled();
    resolvers[1]({ ok: true, json: async () => proposal });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
  });

  it("lets the user resolve an ambiguous place before Enter without another Jev call", async () => {
    const city = { id: 1, slug: "new-york-city", name: "New York", type: "city", parentName: "New York" };
    const state = { id: 2, slug: "new-york-state", name: "New York", type: "state", parentName: "United States" };
    mocks.fetch.mockResolvedValue({ ok: true, json: async () => ({
      ...proposal,
      query: "New York designer",
      keywords: ["New", "York", "designer"],
      locations: [], occupations: [], seniorities: [], workMode: [], employmentTypes: [],
      terms: [{
        span: { id: "s_0", segment: 0, start: 0, end: 2, text: "New York", category: "location", probability: 1 },
        status: "ambiguous", candidate: null, alternatives: [city, state],
      }],
    }) });
    render(<SearchBar />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "New York designer" } });
    await waitFor(() => expect(screen.getAllByTestId("search-bar-query-correction")).toHaveLength(2), { timeout: 2500 });
    fireEvent.mouseDown(screen.getAllByTestId("search-bar-query-correction")[0]);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
    const url = new URL(mocks.push.mock.calls[0][0], "https://jobseek.test");
    expect(url.searchParams.get("loc")).toBe("new-york-city");
    expect(url.searchParams.get("q")).toBe("designer");
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
  });

  it("keeps a keyboard-highlighted term choice stable when Jev finishes", async () => {
    let resolveFetch!: (value: unknown) => void;
    mocks.fetch.mockImplementation(() => new Promise((resolve) => { resolveFetch = resolve; }));
    render(<SearchBar />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: proposal.query } });
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(1), { timeout: 2500 });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
    resolveFetch({ ok: true, json: async () => proposal });
    await waitFor(() => expect(screen.getByRole("combobox").getAttribute("aria-activedescendant")).toMatch(/option-0$/));
    expect(screen.queryByTestId("search-bar-query-proposal")).toBeNull();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
    expect(mocks.parse).toHaveBeenCalledTimes(1);
  });

  it("routes a single unresolved typo after idle but skips a clear atomic match", async () => {
    mocks.typeahead.mockImplementation(async ({ query }: { query: string }) => ({
      locations: [], companies: [], seniorities: [], technologies: [],
      occupations: query === "pharmacist"
        ? [{ id: 27, slug: "pharmacist", name: "Pharmacist" }] : [],
    }));
    render(<SearchBar />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pharmacist" } });
    await new Promise((resolve) => setTimeout(resolve, 1150));
    expect(mocks.fetch).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pharmacst" } });
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(1), { timeout: 1800 });
  });
});
