import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

// `getGlobalLocationsGroupedMock` returns the unpaged shape — the test
// fixture below stays in the pre-#2982 form so existing assertions read
// naturally. The paged action mock slices the fixture into pages of
// `LOCATION_PAGE_SIZE` (mirroring the production `getGlobalLocationsPage`
// implementation) so the modal sees paginated input but tests don't have
// to rebuild every page boundary by hand.
const getGlobalLocationsGroupedMock = vi.fn();
const searchGlobalLocationsMock = vi.fn();
// LOCATION_PAGE_SIZE is inlined in the mock factory because vi.mock
// hoists above all module-level variables (the factory cannot reference
// top-level `const`s — see vitest docs on hoisting). The constant value
// must mirror `LOCATION_PAGE_SIZE` in `apps/web/src/lib/actions/locations.ts`.
vi.mock("@/lib/actions/locations", () => ({
  getGlobalLocationsGrouped: (...args: unknown[]) => getGlobalLocationsGroupedMock(...args),
  // Paged variant: callers pass cursor + limit; we build the slice over
  // the same fixture the unpaged mock returns. Keeps existing tests one
  // mock invocation away from the paged shape.
  getGlobalLocationsPage: async (
    locale: string,
    cursor: number,
    filters?: unknown,
    limit: number = 30,
  ) => {
    const full = await getGlobalLocationsGroupedMock(locale, filters);
    const start = Math.max(0, cursor);
    const end = Math.min(full.countries.length, start + limit);
    return {
      macros: start === 0 ? full.macros : [],
      countries: full.countries.slice(start, end),
      nextCursor: end < full.countries.length ? end : null,
      totalCountries: full.countries.length,
    };
  },
  // Search action — stubbed to whatever each test sets via
  // `searchGlobalLocationsMock`; default return is empty so tests that
  // don't care about server-side search still get the in-memory filter
  // path.
  searchGlobalLocations: (...args: unknown[]) => searchGlobalLocationsMock(...args),
  LOCATION_PAGE_SIZE: 30,
}));

vi.mock("@/lib/country-flags", () => ({
  countryIso: () => "DE",
}));

vi.mock("@/components/country-flag", () => ({
  CountryFlag: () => null,
}));

vi.mock("server-only", () => ({}));

import { LocationSearchModal } from "../location-search-modal";

const _response = (overrides: Partial<Awaited<ReturnType<typeof getGlobalLocationsGroupedMock>>> = {}) => ({
  macros: [
    {
      id: 4,
      slug: "eu",
      name: "European Union",
      abbreviation: "EU",
      count: 146,
      memberCountryNames: ["Germany", "France", "Italy"],
      memberCountryIds: [100, 101, 102],
    },
    {
      id: 1,
      slug: "emea",
      name: "Europe, Middle East & Africa",
      abbreviation: "EMEA",
      count: 1433,
      memberCountryNames: ["Germany", "Saudi Arabia", "Egypt"],
      memberCountryIds: [100, 110, 111],
    },
    {
      id: 5,
      slug: "dach",
      name: "DACH (Germany, Austria, Switzerland)",
      abbreviation: "DACH",
      count: 6,
      memberCountryNames: ["Germany", "Austria", "Switzerland"],
      memberCountryIds: [100, 120, 121],
    },
  ],
  countries: [
    {
      countryId: 100,
      countrySlug: "germany",
      countryName: "Germany",
      countryCount: 50,
      regions: [
        {
          regionId: 0,
          regionSlug: "",
          regionName: "",
          regionCount: 25,
          locations: [
            { id: 200, slug: "berlin", name: "Berlin", type: "city", count: 25 },
          ],
        },
      ],
    },
  ],
  ...overrides,
});

beforeEach(() => {
  getGlobalLocationsGroupedMock.mockReset();
  searchGlobalLocationsMock.mockReset();
  searchGlobalLocationsMock.mockResolvedValue([]);
});

describe("LocationSearchModal — Regions cluster (#2940)", () => {
  /**
   * Modal renders a dedicated "Regions" header above the country list when
   * macros are present. Chips show the canonical name (e.g. "European
   * Union") plus the count.
   */
  it("renders the Regions cluster with macro chips above the country list", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    // Wait for the data to load
    await waitFor(() => expect(screen.getByText("Regions")).toBeTruthy());
    expect(screen.getByText("European Union")).toBeTruthy();
    expect(screen.getByText("Europe, Middle East & Africa")).toBeTruthy();
    expect(screen.getByText("DACH (Germany, Austria, Switzerland)")).toBeTruthy();
    // Country list is still rendered below
    expect(screen.getByText("Germany")).toBeTruthy();
  });

  /**
   * Clicking an EU chip emits onToggle with `type: "macro"` and the
   * canonical `name: "European Union"` so the FilterBar/SearchBar chip
   * displays the full label rather than the abbreviation.
   */
  it("clicking the EU chip emits a macro filter with the canonical 'European Union' name", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    const onToggle = vi.fn();
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("European Union"));
    await userEvent.click(screen.getByText("European Union"));
    expect(onToggle).toHaveBeenCalledWith({
      id: 4,
      slug: "eu",
      name: "European Union",
      type: "macro",
      parentName: null,
    });
  });

  /**
   * Modal-internal text search filters the Regions cluster: typing
   * "Europe" keeps both "European Union" and "Europe, Middle East &
   * Africa" visible while filtering out DACH (no "europe" match in name,
   * abbreviation, or member countries).
   */
  it("local search filters the Regions cluster by canonical name", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("European Union"));
    const input = screen.getByPlaceholderText("Search locations...");
    await userEvent.type(input, "Europe");
    expect(screen.queryByText("European Union")).toBeTruthy();
    expect(screen.queryByText("Europe, Middle East & Africa")).toBeTruthy();
    expect(screen.queryByText("DACH (Germany, Austria, Switzerland)")).toBeNull();
  });

  /**
   * Local search by abbreviation: typing "DACH" still keeps the macro
   * chip visible (matched via `abbreviation` field).
   */
  it("local search matches macros by abbreviation", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("European Union"));
    const input = screen.getByPlaceholderText("Search locations...");
    await userEvent.type(input, "DACH");
    expect(screen.queryByText("DACH (Germany, Austria, Switzerland)")).toBeTruthy();
    expect(screen.queryByText("European Union")).toBeNull();
  });

  /**
   * Member-country tooltip: hover on the chip shows the comma-separated
   * member countries via the native `title` attribute.
   */
  it("renders member-country names as the chip's hover tooltip", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("European Union"));
    const euButton = screen.getByText("European Union").closest("button");
    expect(euButton?.getAttribute("title")).toBe("Germany, France, Italy");
  });

  /**
   * Empty-macros fallback: when no macros have postings, the cluster is
   * not rendered (no orphan "Regions" header).
   */
  it("does not render the Regions header when there are no macros", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue({
      macros: [],
      countries: [
        {
          countryId: 100,
          countrySlug: "germany",
          countryName: "Germany",
          countryCount: 5,
          regions: [
            {
              regionId: 0,
              regionSlug: "",
              regionName: "",
              regionCount: 5,
              locations: [{ id: 200, slug: "berlin", name: "Berlin", type: "city", count: 5 }],
            },
          ],
        },
      ],
    });
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Germany"));
    expect(screen.queryByText("Regions")).toBeNull();
  });

  /**
   * Both clusters empty: search query that matches nothing still renders
   * the empty-state "no locations match" text. Verifies the empty-state
   * gate now considers BOTH macros and countries (previously only
   * `filtered.length === 0`).
   */
  it("renders empty state when both macros and countries are empty after search", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("European Union"));
    const input = screen.getByPlaceholderText("Search locations...");
    await userEvent.type(input, "ZZZNomatch");
    // Wait for debounce + server-side search to settle (returns empty
    // by default per beforeEach). After that, the empty-state text appears
    // since neither in-memory pages nor server hits matched.
    await waitFor(() => expect(screen.getByText("No locations match your search.")).toBeTruthy());
  });

  // Suppress unused-import lint
  void within;
});

describe("LocationSearchModal — hierarchical disable (#2978)", () => {
  /** Country header should appear with `aria-disabled` once a macro that includes it is selected. */
  it("disables the country header when its macro is selected", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response({
      countries: [
        {
          countryId: 100,
          countrySlug: "germany",
          countryName: "Germany",
          countryCount: 50,
          regions: [
            {
              regionId: 0,
              regionSlug: "",
              regionName: "",
              regionCount: 25,
              locations: [
                { id: 200, slug: "berlin", name: "Berlin", type: "city", count: 25 },
              ],
            },
          ],
        },
      ],
    }));
    // EU is selected — Germany (member) and Berlin (descendant) should disable
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 4, slug: "eu", name: "European Union", type: "macro", parentName: null }]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Germany"));
    const germanyButton = screen.getByText("Germany").closest("button");
    expect(germanyButton?.getAttribute("aria-disabled")).toBe("true");
    expect(germanyButton?.getAttribute("tabindex")).toBe("-1");
    const berlinButton = screen.getByText("Berlin").closest("button");
    expect(berlinButton?.getAttribute("aria-disabled")).toBe("true");
  });

  /** Selecting a country auto-deselects its descendants (parity contract). */
  it("auto-deselects child city when parent country is committed", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    const onToggle = vi.fn();
    // Pre-select Berlin (a city under Germany)
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 200, slug: "berlin", name: "Berlin", type: "city", parentName: "Germany" }]}
        onToggle={onToggle}
      />,
    );
    await waitFor(() => screen.getByText("Germany"));
    // Click Germany — should toggle Germany ON and Berlin OFF
    await userEvent.click(screen.getByText("Germany"));
    // First call: add Germany. Second call: remove Berlin (auto-deselect).
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onToggle.mock.calls[0][0]).toMatchObject({ id: 100, type: "country" });
    expect(onToggle.mock.calls[1][0]).toMatchObject({ id: 200 });
  });

  /** Selecting a country disables its child cities. */
  it("disables city pills when their country is selected", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[{ id: 100, slug: "germany", name: "Germany", type: "country", parentName: null }]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Berlin"));
    const berlinButton = screen.getByText("Berlin").closest("button");
    expect(berlinButton?.getAttribute("aria-disabled")).toBe("true");
  });
});

describe("LocationSearchModal — paged fetch (#2982)", () => {
  /**
   * Build a fixture with N countries — used to verify the modal renders
   * only the first page on initial open, even when the underlying full
   * response has more.
   */
  const makeManyCountries = (n: number) => ({
    macros: [],
    countries: Array.from({ length: n }, (_, i) => ({
      countryId: 1000 + i,
      countrySlug: `c-${i}`,
      countryName: `Country ${i.toString().padStart(3, "0")}`,
      countryCount: 5,
      regions: [
        {
          regionId: 0,
          regionSlug: "",
          regionName: "",
          regionCount: 5,
          locations: [
            { id: 5000 + i, slug: `city-${i}`, name: `City ${i}`, type: "city", count: 5 },
          ],
        },
      ],
    })),
  });

  /**
   * First-paint contract: with 100 countries in the underlying data, the
   * modal renders only the first 30 (LOCATION_PAGE_SIZE) on open. The
   * 31st must NOT be in the DOM until the user scrolls.
   */
  it("renders only the first page of countries on open", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(makeManyCountries(100));
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    // Country 0 is in the first page — should appear
    await waitFor(() => screen.getByText("Country 000"));
    // Country 29 is the last in the first page — should appear
    expect(screen.getByText("Country 029")).toBeTruthy();
    // Country 30 is the first in page 2 — must NOT appear yet
    expect(screen.queryByText("Country 030")).toBeNull();
  });

  /**
   * Search input dispatches a server-side `searchGlobalLocations` call
   * (debounced) so long-tail cities not in the loaded country pages
   * still surface as chips. The "Matches" header is rendered.
   */
  it("renders server-side search hits when the user types", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(makeManyCountries(50));
    searchGlobalLocationsMock.mockResolvedValue([
      {
        id: 9999,
        slug: "salzburg",
        name: "Salzburg",
        type: "city",
        parentName: "Austria",
        count: 178,
      },
    ]);
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Country 000"));
    const input = screen.getByPlaceholderText("Search locations...");
    await userEvent.type(input, "Salz");
    // Debounced + server-side search resolves; Salzburg + Matches header
    await waitFor(() => screen.getByText("Salzburg"));
    expect(screen.getByText("Matches")).toBeTruthy();
    // The mock was called at least once with the search query
    expect(searchGlobalLocationsMock).toHaveBeenCalled();
  });

  /**
   * Server-side search hits already covered by an in-memory page should
   * be deduplicated — we don't render Berlin twice when it's both in
   * the first page AND a search hit.
   */
  it("deduplicates server-side search hits against loaded pages", async () => {
    getGlobalLocationsGroupedMock.mockResolvedValue(_response());
    searchGlobalLocationsMock.mockResolvedValue([
      // Same Berlin id (200) as in the in-memory fixture
      {
        id: 200,
        slug: "berlin",
        name: "Berlin",
        type: "city",
        parentName: "Germany",
        count: 25,
      },
    ]);
    render(
      <LocationSearchModal
        open
        onOpenChange={() => {}}
        locale="en"
        selected={[]}
        onToggle={() => {}}
      />,
    );
    await waitFor(() => screen.getByText("Berlin"));
    const input = screen.getByPlaceholderText("Search locations...");
    await userEvent.type(input, "Berlin");
    // Wait for the debounce to elapse + search call to resolve
    await new Promise((resolve) => setTimeout(resolve, 220));
    // Berlin should appear only once (in the country list, not also under Matches)
    const berlinNodes = screen.queryAllByText("Berlin");
    expect(berlinNodes.length).toBe(1);
  });
});
