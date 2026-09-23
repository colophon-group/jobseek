import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { SearchStateSnapshot } from "@/components/providers/SearchStateProvider";

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

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ isLoggedIn: false }),
}));

const snapshotMocks = vi.hoisted(() => ({
  snapshot: null as SearchStateSnapshot | null,
  save: vi.fn(),
}));

vi.mock("@/components/providers/SearchStateProvider", () => ({
  useSearchStateStore: () => ({
    get: () => snapshotMocks.snapshot,
    set: snapshotMocks.save,
    setPageActions: vi.fn(),
  }),
  buildCacheKey: () => "",
  shouldRestoreSnapshot: () => snapshotMocks.snapshot !== null,
}));

// Heavy children: stub to deterministic markers so the test focuses
// on the h1 we just added.
vi.mock("@/components/search/search-toolbar", () => ({
  SearchToolbar: ({
    onToggleWorkMode,
    unresolvedExplicitSlugs,
  }: {
    onToggleWorkMode: (mode: "remote") => void;
    unresolvedExplicitSlugs?: Record<string, string[]>;
  }) => (
    <div
      data-testid="search-toolbar-stub"
      data-unresolved={JSON.stringify(unresolvedExplicitSlugs ?? {})}
    >
      <button onClick={() => onToggleWorkMode("remote")}>toggle remote</button>
    </div>
  ),
}));

vi.mock("@/components/search/search-results", () => ({
  SearchResults: ({ companies, hasMore, load }: {
    companies: { company: { id: string } }[];
    hasMore: boolean;
    load: () => Promise<void>;
  }) => <div data-testid="search-results-stub">
    <span data-testid="visible-company-ids">{companies.map((c) => c.company.id).join(",")}</span>
    {hasMore && <button onClick={load}>Load more results</button>}
  </div>,
}));

vi.mock("@/components/search/explore-repository-fallback", () => ({
  ExploreRepositoryFallback: () => <div role="alert" data-testid="repository-fallback-stub" />,
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
  tryListTopCompaniesDirect: vi.fn().mockResolvedValue(null),
}));

vi.mock("@/lib/actions/explore-page-data", () => ({
  fetchExploreFilterPageData: vi.fn().mockResolvedValue({
    degraded: false,
    parsed: {
      keywords: [],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    },
  }),
}));

vi.mock("@/lib/search/query-params", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/search/query-params")>()),
  buildFilteredPath: (
    basePath: string,
    _keywords: string[],
    _locations: unknown[],
    extra?: Record<string, string>,
  ) => {
    const query = new URLSearchParams(extra).toString();
    return `${basePath}${query ? `?${query}` : ""}`;
  },
}));

import { SearchPage, resolveInitialRepositoryFallbackCompanies } from "../search-page";
import { fetchExploreFilterPageData } from "@/lib/actions/explore-page-data";
import {
  runListTopCompanies,
  runSearchJobs,
  tryListTopCompaniesDirect,
} from "@/lib/search/search-runner";

const fetchExploreFilterPageDataMock = vi.mocked(fetchExploreFilterPageData);
const runListTopCompaniesMock = vi.mocked(runListTopCompanies);
const runSearchJobsMock = vi.mocked(runSearchJobs);
const tryListTopCompaniesDirectMock = vi.mocked(tryListTopCompaniesDirect);

beforeEach(() => {
  snapshotMocks.snapshot = null;
  snapshotMocks.save.mockClear();
  // jsdom/happy-dom may not set up window.history.replaceState identically
  // across versions; stub to a no-op so the component's URL syncs do not
  // throw in the test environment.
  window.history.replaceState = vi.fn() as typeof window.history.replaceState;
  fetchExploreFilterPageDataMock.mockReset();
  fetchExploreFilterPageDataMock.mockResolvedValue({
    degraded: false,
    parsed: {
      keywords: [],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    },
  });
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
          initialEmploymentTypes={[]}
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

describe("SearchPage — mount direct refresh ownership (#8259)", () => {
  it("does not repeat a result read completed by ExploreContent", async () => {
    render(
      <SearchPage
        initialCompanies={[]}
        initialTotalCompanies={0}
        initialKeywords={[]}
        initialLocations={[]}
        initialOccupations={[]}
        initialSeniorities={[]}
        initialTechnologies={[]}
        initialEmploymentTypes={[]}
        initialWorkMode={[]}
        locale="en"
        displayCurrency="EUR"
        jobLanguages={[]}
        languages={["en"]}
        initialDirectRefreshAttempted
      />,
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(tryListTopCompaniesDirectMock).not.toHaveBeenCalled();
  });
});

describe("SearchPage — impossible empty search state (#3403)", () => {
  it("retains the offline profiles for a matching degraded filtered snapshot", () => {
    const fallback = [{ name: "Acme", slug: "acme" }];

    expect(
      resolveInitialRepositoryFallbackCompanies({
        shouldRestore: true,
        cachedCompaniesLength: 0,
        cachedDegraded: true,
        initialCompanies: fallback,
      }),
    ).toBe(fallback);
    expect(
      resolveInitialRepositoryFallbackCompanies({
        shouldRestore: true,
        cachedCompaniesLength: 0,
        cachedDegraded: false,
        initialCompanies: fallback,
      }),
    ).toEqual([]);
    expect(
      resolveInitialRepositoryFallbackCompanies({
        shouldRestore: true,
        cachedCompaniesLength: 1,
        cachedDegraded: true,
        initialCompanies: fallback,
      }),
    ).toEqual([]);
  });

  it("keeps repository fallback profiles separate from live results", async () => {
    await act(async () => {
      render(
        <SearchPage
          initialCompanies={[]}
          initialTotalCompanies={0}
          initialDegraded
          initialRepositoryFallbackCompanies={[
            { name: "Acme", slug: "acme" },
          ]}
          initialKeywords={[]}
          initialLocations={[]}
          initialOccupations={[]}
          initialSeniorities={[]}
          initialTechnologies={[]}
          initialEmploymentTypes={[]}
          initialWorkMode={[]}
          locale="en"
          displayCurrency="EUR"
          jobLanguages={[]}
          languages={[]}
        />,
      );
    });

    expect(screen.getByTestId("repository-fallback-stub")).toBeTruthy();
    expect(screen.queryByTestId("search-results-stub")).toBeNull();
    expect(screen.queryByTestId("zero-results-stub")).toBeNull();
  });

  it("shows a refresh prompt for an empty unfiltered result set", async () => {
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
          initialEmploymentTypes={[]}
          initialWorkMode={[]}
          locale="en"
          displayCurrency="EUR"
          jobLanguages={[]}
          languages={[]}
        />,
      );
    });

    expect(screen.getByText(/oops, something went wrong/i)).toBeTruthy();
    expect(screen.getByText(/try refreshing the page/i)).toBeTruthy();
    expect(screen.queryByTestId("search-results-stub")).toBeNull();
    expect(screen.queryByTestId("zero-results-stub")).toBeNull();
  });

  it("keeps the normal zero-results state for an empty filtered search", async () => {
    await act(async () => {
      render(
        <SearchPage
          initialCompanies={[]}
          initialTotalCompanies={0}
          initialKeywords={["python"]}
          initialLocations={[]}
          initialOccupations={[]}
          initialSeniorities={[]}
          initialTechnologies={[]}
          initialEmploymentTypes={[]}
          initialWorkMode={[]}
          locale="en"
          displayCurrency="EUR"
          jobLanguages={[]}
          languages={[]}
        />,
      );
    });

    expect(screen.getByTestId("zero-results-stub")).toBeTruthy();
    expect(screen.queryByText(/oops, something went wrong/i)).toBeNull();
  });

  it("shows a refresh prompt for degraded filtered results", async () => {
    await act(async () => {
      render(
        <SearchPage
          initialCompanies={[]}
          initialTotalCompanies={0}
          initialDegraded
          initialKeywords={["python"]}
          initialLocations={[]}
          initialOccupations={[]}
          initialSeniorities={[]}
          initialTechnologies={[]}
          initialEmploymentTypes={[]}
          initialWorkMode={[]}
          locale="en"
          displayCurrency="EUR"
          jobLanguages={[]}
          languages={[]}
        />,
      );
    });

    expect(screen.getByText(/oops, something went wrong/i)).toBeTruthy();
    expect(screen.queryByTestId("zero-results-stub")).toBeNull();
  });
});

describe("SearchPage — safe external filter navigation (#7218)", () => {
  it("ignores an older filter-resolution result that settles after a newer navigation", async () => {
    let resolveFirst!: (value: Awaited<ReturnType<typeof fetchExploreFilterPageData>>) => void;
    let resolveSecond!: (value: Awaited<ReturnType<typeof fetchExploreFilterPageData>>) => void;
    fetchExploreFilterPageDataMock
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));

    render(
      <SearchPage
        initialCompanies={[]}
        initialTotalCompanies={0}
        initialKeywords={[]}
        initialLocations={[]}
        initialOccupations={[]}
        initialSeniorities={[]}
        initialTechnologies={[]}
        initialEmploymentTypes={[]}
        initialWorkMode={[]}
        locale="en"
        displayCurrency="EUR"
        jobLanguages={[]}
        languages={[]}
      />,
    );

    await act(async () => { await Promise.resolve(); });
    await act(async () => {
      window.history.pushState(null, "", "/en/explore?loc=india");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(fetchExploreFilterPageDataMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      window.history.pushState(null, "", "/en/explore?q=python");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(fetchExploreFilterPageDataMock).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveSecond({
        degraded: false,
        parsed: {
          keywords: ["python"],
          locations: [],
          occupations: [],
          seniorities: [],
          technologies: [],
          workMode: [],
          employmentTypes: [],
        },
      });
    });
    await waitFor(() => expect(runSearchJobsMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      resolveFirst({
        degraded: true,
        parsed: {
          keywords: [],
          locations: [],
          occupations: [],
          seniorities: [],
          technologies: [],
          workMode: [],
          employmentTypes: [],
          unresolvedExplicitSlugs: { loc: ["india"] },
        },
      });
    });

    expect(runSearchJobsMock).toHaveBeenCalledTimes(1);
    expect(runSearchJobsMock.mock.calls[0]?.[0]).toMatchObject({
      keywords: ["python"],
    });
    expect(runListTopCompaniesMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/oops, something went wrong/i)).toBeNull();
  });

  it("preserves unresolved explicit slugs and refuses a broader search on toolbar changes", async () => {
    render(
      <SearchPage
        initialCompanies={[]}
        initialTotalCompanies={0}
        initialDegraded
        initialKeywords={[]}
        initialLocations={[]}
        initialOccupations={[]}
        initialSeniorities={[]}
        initialTechnologies={[]}
        initialUnresolvedExplicitSlugs={{ loc: ["india"] }}
        initialEmploymentTypes={[]}
        initialWorkMode={[]}
        locale="en"
        displayCurrency="EUR"
        jobLanguages={[]}
        languages={[]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "toggle remote" }));

    expect(window.history.replaceState).toHaveBeenCalledWith(
      null,
      "",
      expect.stringMatching(/[?&]loc=india(?:&|$)/),
    );
    expect(runSearchJobsMock).not.toHaveBeenCalled();
    expect(runListTopCompaniesMock).not.toHaveBeenCalled();
    expect(screen.getByText(/oops, something went wrong/i)).toBeTruthy();
  });

  it("clears stale companies and preserves URL filters when navigation resolution rejects", async () => {
    fetchExploreFilterPageDataMock.mockRejectedValueOnce(
      new Error("Explore filter resolution failed"),
    );
    const staleCompany = {
      company: { id: "stale", name: "Stale", slug: "stale", icon: null },
      activeMatches: 1,
      yearMatches: 1,
      postings: [],
    };

    render(
      <SearchPage
        initialCompanies={[staleCompany]}
        initialTotalCompanies={1}
        initialKeywords={[]}
        initialLocations={[]}
        initialOccupations={[]}
        initialSeniorities={[]}
        initialTechnologies={[]}
        initialEmploymentTypes={[]}
        initialWorkMode={[]}
        locale="en"
        displayCurrency="EUR"
        jobLanguages={[]}
        languages={[]}
      />,
    );

    await act(async () => { await Promise.resolve(); });
    await act(async () => {
      window.history.pushState(
        null,
        "",
        "/en/explore?q=python&loc=india&wm=remote&etype=full_time",
      );
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    await waitFor(() => {
      expect(screen.getByText(/oops, something went wrong/i)).toBeTruthy();
    });
    expect(screen.queryByTestId("search-results-stub")).toBeNull();
    expect(screen.getByTestId("search-toolbar-stub").getAttribute("data-unresolved"))
      .toBe(JSON.stringify({ loc: ["india"] }));
    expect(runSearchJobsMock).not.toHaveBeenCalled();
    expect(runListTopCompaniesMock).not.toHaveBeenCalled();
  });
});


describe("SearchPage grouped pagination", () => {
  beforeEach(() => {
    runSearchJobsMock.mockReset();
    runSearchJobsMock.mockResolvedValue({ companies: [], totalCompanies: 0 });
  });
  const company = (id: string) => ({
    company: { id, name: id, slug: id, icon: null },
    activeMatches: 1, yearMatches: 2, postings: [],
  });
  const props = {
    initialCompanies: [company("a")], initialTotalCompanies: 1, initialNextOffset: 10,
    initialKeywords: ["engineer"], initialLocations: [], initialOccupations: [],
    initialSeniorities: [], initialTechnologies: [], initialEmploymentTypes: [],
    initialWorkMode: [], locale: "en", displayCurrency: "EUR", jobLanguages: [], languages: [],
  };

  it("uses raw offsets through deduplication and restore, then stops despite an overestimate", async () => {
    runSearchJobsMock.mockResolvedValueOnce({
      companies: [company("a"), company("b")], totalCompanies: 1, nextOffset: 20,
    });
    const first = render(<SearchPage {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Load more results" }));
    await waitFor(() => expect(screen.getByTestId("visible-company-ids").textContent).toBe("a,b"));
    expect(runSearchJobsMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 10 }), false);
    first.unmount();
    const snapshot = snapshotMocks.save.mock.calls.at(-1)![0] as SearchStateSnapshot;
    expect(snapshot).toMatchObject({ nextOffset: 20, serverOffset: 20 });
    snapshotMocks.snapshot = snapshot;
    runSearchJobsMock.mockResolvedValueOnce({ companies: [], totalCompanies: 999, nextOffset: null });
    render(<SearchPage {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Load more results" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more results" })).toBeNull());
    expect(runSearchJobsMock).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 20 }), false);
    expect(screen.getByTestId("visible-company-ids").textContent).toBe("a,b");
  });

  it("does not apply a late page after filters change", async () => {
    let resolvePage!: (value: Awaited<ReturnType<typeof runSearchJobs>>) => void;
    runSearchJobsMock.mockImplementationOnce(() => new Promise((resolve) => { resolvePage = resolve; }));
    runSearchJobsMock.mockResolvedValueOnce({ companies: [company("filtered")], totalCompanies: 1, nextOffset: null });
    render(<SearchPage {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Load more results" }));
    fireEvent.click(screen.getByRole("button", { name: "toggle remote" }));
    await waitFor(() => expect(screen.getByTestId("visible-company-ids").textContent).toBe("filtered"));
    await act(async () => resolvePage({ companies: [company("stale")], totalCompanies: 999, nextOffset: 20 }));
    expect(screen.getByTestId("visible-company-ids").textContent).toBe("filtered");
    expect(screen.queryByRole("button", { name: "Load more results" })).toBeNull();
  });

  it("keeps anonymous truncation authoritative over a next offset", () => {
    render(<SearchPage {...props} initialTruncated />);
    expect(screen.queryByRole("button", { name: "Load more results" })).toBeNull();
  });
});
