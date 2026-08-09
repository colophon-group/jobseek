import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  submitSearch: vi.fn(),
  parseSearchFilters: vi.fn(),
  suggestLocations: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/en/company/acme",
  useParams: () => ({ lang: "en" }),
}));

vi.mock("@/lib/actions/company", () => ({
  suggestCompanies: vi.fn(async () => []),
}));

vi.mock("@/lib/search/typeahead-runner", () => ({
  runSuggestLocations: (...args: unknown[]) => mocks.suggestLocations(...args),
  runSuggestOccupations: vi.fn(async () => []),
  runSuggestSeniorities: vi.fn(async () => []),
  runSuggestTechnologies: vi.fn(async () => []),
}));

vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: (...args: unknown[]) => mocks.parseSearchFilters(...args),
}));

vi.mock("server-only", () => ({}));

import { SearchBar } from "../search-bar";
import {
  SearchStateProvider,
  useSearchStateStore,
} from "@/components/providers/SearchStateProvider";

beforeEach(() => {
  mocks.push.mockReset();
  mocks.submitSearch.mockReset();
  mocks.parseSearchFilters.mockReset();
  mocks.suggestLocations.mockReset();
});

function CompanySearchHarness() {
  const { setPageActions } = useSearchStateStore();

  useEffect(() => {
    setPageActions({
      addLocation: vi.fn(),
      addOccupation: vi.fn(),
      addSeniority: vi.fn(),
      submitSearch: mocks.submitSearch,
      getLocations: () => [],
      getKeywords: () => [],
      getOccupations: () => [],
      getSeniorities: () => [],
      getTechnologies: () => [],
      placeholder: "Search at Acme...",
    });
    return () => setPageActions(null);
  }, [setPageActions]);

  return <SearchBar />;
}

describe("SearchBar company scope", () => {
  it("submits free text on Enter after additional suggestions arrive", async () => {
    mocks.suggestLocations.mockResolvedValue([
      {
        id: 10,
        slug: "merced-california",
        name: "Merced",
        type: "city",
        parentName: "California",
      },
    ]);
    mocks.parseSearchFilters.mockResolvedValue({
      keywords: ["merck"],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
    });

    render(
      <SearchStateProvider>
        <CompanySearchHarness />
      </SearchStateProvider>,
    );

    const input = screen.getByRole("combobox");
    await userEvent.type(input, "merck");
    expect(await screen.findByText("Merced")).toBeTruthy();
    await userEvent.type(input, "{Enter}");

    await waitFor(() => {
      expect(mocks.submitSearch).toHaveBeenCalledWith(
        ["merck"], [], [], [], [], [],
      );
    });
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("submits free text through the company page instead of navigating to Explore", async () => {
    mocks.suggestLocations.mockResolvedValue([]);
    mocks.parseSearchFilters.mockResolvedValue({
      keywords: ["safety"],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
    });

    render(
      <SearchStateProvider>
        <CompanySearchHarness />
      </SearchStateProvider>,
    );

    const input = screen.getByRole("combobox");
    expect(input.getAttribute("placeholder")).toBe("Search at Acme...");
    await userEvent.type(input, "safety{Enter}");

    await waitFor(() => {
      expect(mocks.submitSearch).toHaveBeenCalledWith(
        ["safety"], [], [], [], [], [],
      );
    });
    expect(mocks.push).not.toHaveBeenCalled();
  });
});
