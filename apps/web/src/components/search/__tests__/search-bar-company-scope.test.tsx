import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  submitSearch: vi.fn(),
  parseSearchFilters: vi.fn(),
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
  runSuggestLocations: vi.fn(async () => []),
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
  it("submits free text through the company page instead of navigating to Explore", async () => {
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
