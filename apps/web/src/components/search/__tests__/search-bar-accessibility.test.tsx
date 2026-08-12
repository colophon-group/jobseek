import { useEffect } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";

let currentPathname = "/en";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => currentPathname,
  useParams: () => ({ lang: "en" }),
}));

vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: vi.fn(),
}));

vi.mock("@/components/search/search-bar-typeahead", () => ({
  matchWorkModes: () => [],
  useSearchBarTypeahead: ({
    onOpen,
  }: {
    onOpen: () => void;
  }) => ({
    locationResults: [],
    companyResults: [],
    occupationResults: [],
    seniorityResults: [],
    technologyResults: [],
    clearResults: vi.fn(),
    fetchSuggestions: onOpen,
  }),
}));

import {
  SearchStateProvider,
  useSearchStateStore,
  type SearchPageActions,
} from "@/components/providers/SearchStateProvider";
import { SearchBar } from "../search-bar";

const noOpPageActions: Omit<SearchPageActions, "accessibleLabel" | "placeholder"> = {
  addLocation: vi.fn(),
  addOccupation: vi.fn(),
  addSeniority: vi.fn(),
  submitSearch: vi.fn(),
  getLocations: () => [],
  getKeywords: () => [],
  getOccupations: () => [],
  getSeniorities: () => [],
};

function CompanyHeaderSearch() {
  const { setPageActions } = useSearchStateStore();

  useEffect(() => {
    setPageActions({
      ...noOpPageActions,
      placeholder: "Search at Acme...",
      accessibleLabel: "Search jobs at Acme",
    });
    return () => setPageActions(null);
  }, [setPageActions]);

  return <SearchBar />;
}

async function expectOwnedListbox(input: HTMLElement) {
  expect(input.getAttribute("aria-expanded")).toBe("false");
  expect(input.getAttribute("aria-controls")).toBeNull();

  await userEvent.type(input, "engineer");

  expect(input.getAttribute("aria-expanded")).toBe("true");
  const controls = input.getAttribute("aria-controls");
  expect(controls).toBeTruthy();

  const listbox = document.getElementById(controls!);
  expect(listbox).toBe(screen.getByRole("listbox"));
  expect(within(listbox!).getAllByRole("option").length).toBeGreaterThan(0);

  await userEvent.type(input, "{ArrowDown}");
  const activeDescendant = input.getAttribute("aria-activedescendant");
  expect(activeDescendant).toBeTruthy();
  expect(document.getElementById(activeDescendant!)?.getAttribute("role")).toBe(
    "option",
  );

  await userEvent.type(input, "{Escape}");
  expect(input.getAttribute("aria-expanded")).toBe("false");
  expect(input.getAttribute("aria-controls")).toBeNull();
  expect(input.getAttribute("aria-activedescendant")).toBeNull();
}

describe("SearchBar accessibility contract", () => {
  beforeEach(() => {
    currentPathname = "/en";
  });

  it.each([
    "/en",
    "/en/alice/engineering",
    "/en/companies/request",
    "/en/explore",
    "/en/my-jobs",
    "/en/my-jobs/stats",
    "/en/progress",
    "/en/settings",
    "/en/settings/account",
    "/en/settings/billing",
    "/en/watchlists",
  ])(
    "gives the shared search on %s a stable job-search name and owned popup",
    async (pathname) => {
      currentPathname = pathname;
      render(<SearchBar />);

      const input = screen.getByRole("combobox", { name: "Search jobs" });
      await expectOwnedListbox(input);
    },
  );

  it("reactively gives the company header search its localized company scope", async () => {
    currentPathname = "/en/company/acme";
    render(
      <SearchStateProvider>
        <CompanyHeaderSearch />
      </SearchStateProvider>,
    );

    const input = await screen.findByRole("combobox", {
      name: "Search jobs at Acme",
    });
    expect(input.getAttribute("placeholder")).toBe("Search at Acme...");
    await expectOwnedListbox(input);
  });

  it("keeps popup and option IDs unique when desktop and mobile searches coexist", async () => {
    render(
      <>
        <SearchBar accessibleLabel="Search jobs" />
        <SearchBar accessibleLabel="Search jobs at Acme" companyId="acme" />
      </>,
    );

    const globalInput = screen.getByRole("combobox", { name: "Search jobs" });
    const companyInput = screen.getByRole("combobox", {
      name: "Search jobs at Acme",
    });
    // Change both inputs without pointer focus transitions so both responsive
    // variants stay expanded in the DOM at once. A real pointer interaction
    // intentionally closes the other instance through its outside-click hook.
    fireEvent.change(globalInput, { target: { value: "engineer" } });
    fireEvent.change(companyInput, { target: { value: "designer" } });

    const globalControls = globalInput.getAttribute("aria-controls");
    const companyControls = companyInput.getAttribute("aria-controls");
    expect(globalControls).toBeTruthy();
    expect(companyControls).toBeTruthy();
    expect(globalControls).not.toBe(companyControls);

    const optionIds = screen
      .getAllByRole("option")
      .map((option) => option.id);
    expect(new Set(optionIds).size).toBe(optionIds.length);
  });
});
