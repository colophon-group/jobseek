import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

import "@/test-utils/lingui-mock";

/**
 * Regression test for #3196 — `/explore` had no `<h1>` and screen-reader
 * users pressing H from the top of the page skipped straight into job
 * titles. The fix mounts a visually-hidden `<h1>` inside `SearchPage`.
 *
 * `SearchPage` has a heavy dependency tree (Typesense client, session
 * provider, search toolbar with its own sub-modals, etc.). This suite
 * stubs every non-essential dependency so the render exercises only the
 * heading + skeleton outline that we care about for the a11y assertion.
 */

vi.mock("server-only", () => ({}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/en/explore",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: false }),
}));

vi.mock("@/components/SearchStateProvider", () => ({
  useSearchStateStore: () => ({
    get: () => null,
    set: vi.fn(),
    setPageActions: vi.fn(),
  }),
  buildCacheKey: () => "",
  shouldRestoreSnapshot: () => false,
}));

// Heavy children: stub to deterministic markers so the test focuses
// on the h1 we just added.
vi.mock("@/components/search/search-toolbar", () => ({
  SearchToolbar: () => <div data-testid="search-toolbar-stub" />,
}));

vi.mock("@/components/search/search-results", () => ({
  SearchResults: () => <div data-testid="search-results-stub" />,
}));

vi.mock("@/components/search/zero-results", () => ({
  ZeroResults: () => <div data-testid="zero-results-stub" />,
}));

vi.mock("@/components/search/skeleton-card", () => ({
  SkeletonCards: () => <div data-testid="skeleton-cards-stub" />,
}));

vi.mock("@/components/search/job-detail-dialog", () => ({
  JobDetailPanel: () => null,
}));

vi.mock("@/lib/actions/search", () => ({
  getCurrencyRates: () => Promise.resolve([]),
}));

vi.mock("@/lib/search/search-runner", () => ({
  runSearchJobs: vi.fn().mockResolvedValue({ companies: [], totalCompanies: 0, truncated: false }),
  runListTopCompanies: vi.fn().mockResolvedValue({ companies: [], totalCompanies: 0, truncated: false }),
}));

vi.mock("@/lib/search/use-clear-typesense-on-auth-change", () => ({
  useClearTypesenseOnAuthChange: () => {},
}));

vi.mock("@/lib/actions/search-input", () => ({
  parseSearchFilters: vi.fn().mockResolvedValue({
    keywords: [],
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
    workMode: [],
  }),
}));

vi.mock("@/lib/search/query-params", () => ({
  buildFilteredPath: () => "/en/explore",
}));

import { SearchPage } from "../search-page";

beforeEach(() => {
  // jsdom/happy-dom may not set up window.history.replaceState identically
  // across versions; stub to a no-op so the component's URL syncs do not
  // throw in the test environment.
  window.history.replaceState = vi.fn() as typeof window.history.replaceState;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SearchPage — heading landmark (#3196)", () => {
  it("renders exactly one level-1 heading for /explore", async () => {
    await act(async () => {
      render(
        <SearchPage
          initialCompanies={[]}
          initialTotalCompanies={0}
          initialKeywords={[]}
          initialLocations={[]}
          initialOccupations={[]}
          initialSeniorities={[]}
          initialTechnologies={[]}
          initialWorkMode={[]}
          locale="en"
          displayCurrency="EUR"
          jobLanguages={[]}
          languages={[]}
        />,
      );
    });

    // `getByRole` throws if there are zero or multiple matches — this
    // is the contract that makes the test a regression guard rather
    // than a coincidence: it pins down "exactly one h1 in the page".
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toBeTruthy();
    expect(h1.textContent).toMatch(/explore/i);
    // sr-only is the load-bearing class — without it, a visible h1
    // would shift the visual design unexpectedly.
    expect(h1.className).toMatch(/\bsr-only\b/);
  });
});
