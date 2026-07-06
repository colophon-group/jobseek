import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import type { ExploreData } from "@/lib/actions/explore-data";

// `@/lib/actions/explore-data` is a server action that transitively imports
// `server-only`, which throws when loaded in a non-Next runtime. Neutralise
// the gate, then swap the action itself for a spy.
vi.mock("server-only", () => ({}));
const mockFetchExploreData = vi.fn();
vi.mock("@/lib/actions/explore-data", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/actions/explore-data")>(
      "@/lib/actions/explore-data",
    );
  return {
    ...actual,
    fetchExploreData: (...args: unknown[]) => mockFetchExploreData(...args),
  };
});

// SearchPage has a heavy dependency tree (Lingui i18n, Typesense provider,
// etc.). Stub it out — this suite is testing the conditional-fetch logic
// in ExploreContent, not SearchPage's behaviour. Renders a marker with the
// initialTotalCompanies prop so the data-routing regression test in #3350
// can assert which dataset the page mounted ``SearchPage`` with.
vi.mock("../search-page", () => ({
  SearchPage: ({ initialTotalCompanies }: { initialTotalCompanies: number }) => (
    <div data-testid="search-page" data-total={initialTotalCompanies} />
  ),
}));

// Skeleton stub — a distinct marker so the #3350 regression test can
// assert which branch (skeleton vs SearchPage) is currently rendered.
vi.mock("@/components/search/explore-skeleton", () => ({
  ExploreSkeleton: () => <div data-testid="explore-skeleton" />,
}));

// `useSearchParams` from `next/navigation` returns a `URLSearchParams`-like
// object in the test environment. Mock it per-test to control the filter
// state being observed by the component.
let currentSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => currentSearchParams,
}));

import { ExploreContent } from "../explore-content";

let cookieSpy: ReturnType<typeof vi.spyOn> | undefined;
function setDocumentCookie(value: string) {
  cookieSpy?.mockRestore();
  cookieSpy = vi.spyOn(document, "cookie", "get").mockReturnValue(value);
}

function makeInitialData(overrides: Partial<ExploreData> = {}): ExploreData {
  return {
    result: {
      companies: [],
      totalCompanies: 0,
      truncated: false,
    } as unknown as ExploreData["result"],
    parsed: {
      keywords: [],
      locations: [],
      occupations: [],
      seniorities: [],
      technologies: [],
      workMode: [],
      employmentTypes: [],
    },
    displayCurrency: "EUR",
    jobLanguages: [],
    languages: [],
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: "EUR",
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    ...overrides,
  };
}

async function flushQueuedEffects(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  mockFetchExploreData.mockReset();
  mockFetchExploreData.mockResolvedValue(makeInitialData());
  currentSearchParams = new URLSearchParams();
  setDocumentCookie("");
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSpy = undefined;
});

describe("ExploreContent — server-render initial-data path (#2640)", () => {
  it("does NOT call fetchExploreData for an anonymous, no-filter visit with prerendered initialData", async () => {
    const initialData = makeInitialData();
    render(<ExploreContent locale="en" initialData={initialData} />);

    await flushQueuedEffects();
    expect(mockFetchExploreData).not.toHaveBeenCalled();
  });

  it("calls fetchExploreData when the `logged_in` hint cookie is present even without filters", async () => {
    setDocumentCookie("logged_in=1");

    const initialData = makeInitialData();
    render(<ExploreContent locale="en" initialData={initialData} />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    });
  });

  it("calls fetchExploreData when a filter searchParam is present", async () => {
    currentSearchParams = new URLSearchParams("q=python");

    const initialData = makeInitialData();
    render(<ExploreContent locale="en" initialData={initialData} />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    });

    const callArgs = mockFetchExploreData.mock.calls[0]?.[0] as {
      searchParams: Record<string, string | undefined>;
      locale: string;
    };
    expect(callArgs.locale).toBe("en");
    expect(callArgs.searchParams.q).toBe("python");
  });

  it("calls fetchExploreData when initialData is omitted (legacy path)", async () => {
    render(<ExploreContent locale="en" />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    });
  });

  it("does NOT call fetchExploreData when a non-filter searchParam is present (e.g. utm tracking)", async () => {
    currentSearchParams = new URLSearchParams("utm_source=google");

    const initialData = makeInitialData();
    render(<ExploreContent locale="en" initialData={initialData} />);

    await flushQueuedEffects();
    expect(mockFetchExploreData).not.toHaveBeenCalled();
  });

  it("recognises every documented filter searchParam as a refetch trigger", async () => {
    // Mirrors `FILTER_PARAMS` in `../explore-content.tsx`. Adding a new
    // filter param to that array MUST also add it here so the regression
    // surface stays explicit (e.g. #3275 — `wm` was added to the live
    // code in #2987 but the test list lagged behind, leaving the
    // refetch-trigger contract unguarded).
    const filterParams = ["q", "loc", "occ", "sen", "tech", "wm", "etype", "sal", "salcur", "exp"];
    for (const param of filterParams) {
      mockFetchExploreData.mockClear();
      currentSearchParams = new URLSearchParams(`${param}=x`);

      const initialData = makeInitialData();
      const { unmount } = render(
        <ExploreContent locale="en" initialData={initialData} />,
      );

      await waitFor(() => {
        expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
      });
      unmount();
    }
  });

  // Regression coverage for #3275. The `wm` (workMode) searchParam was
  // added to `FILTER_PARAMS` in #2987 alongside the work-mode filter
  // feature, but no test pinned the behaviour. A future revert/refactor
  // that drops `wm` would silently re-introduce the stale-data bug
  // (anonymous deep-link to `/explore?wm=remote` paints the unfiltered
  // homepage `initialData` and never refetches). These tests lock that
  // contract in place.
  describe("wm (workMode) filter-param refetch trigger (#3275)", () => {
    it("triggers refetch when ?wm=remote is in the URL", async () => {
      currentSearchParams = new URLSearchParams("wm=remote");

      render(
        <ExploreContent locale="en" initialData={makeInitialData()} />,
      );

      await waitFor(() => {
        expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
      });
      const callArgs = mockFetchExploreData.mock.calls[0]?.[0] as {
        searchParams: Record<string, string | undefined>;
      };
      expect(callArgs.searchParams.wm).toBe("remote");
    });

    it("triggers refetch when ?wm=hybrid is in the URL", async () => {
      currentSearchParams = new URLSearchParams("wm=hybrid");

      render(
        <ExploreContent locale="en" initialData={makeInitialData()} />,
      );

      await waitFor(() => {
        expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
      });
      const callArgs = mockFetchExploreData.mock.calls[0]?.[0] as {
        searchParams: Record<string, string | undefined>;
      };
      expect(callArgs.searchParams.wm).toBe("hybrid");
    });

    it("does NOT trigger refetch when the wm param is absent", async () => {
      // Sanity: a no-wm anonymous visit must still hit the prerendered
      // initialData path so #2640's zero-invocation guarantee holds.
      currentSearchParams = new URLSearchParams();

      render(
        <ExploreContent locale="en" initialData={makeInitialData()} />,
      );

      await flushQueuedEffects();
      expect(mockFetchExploreData).not.toHaveBeenCalled();
    });
  });

  describe("etype (employment type) filter-param refetch trigger (#3218)", () => {
    it("triggers refetch when ?etype=internship is in the URL", async () => {
      currentSearchParams = new URLSearchParams("etype=internship");

      render(
        <ExploreContent locale="en" initialData={makeInitialData()} />,
      );

      await waitFor(() => {
        expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
      });
      const callArgs = mockFetchExploreData.mock.calls[0]?.[0] as {
        searchParams: Record<string, string | undefined>;
      };
      expect(callArgs.searchParams.etype).toBe("internship");
    });
  });

  describe("filter-param visits unmount SearchPage until refetch lands (#3350)", () => {
    // Regression coverage for #3350. The #2746 prerender path embeds the
    // ANONYMOUS, NO-FILTER ``ExploreData`` as ``initialData``. If
    // ``ExploreContent`` mounted ``SearchPage`` with that stale dataset
    // and only later swapped ``data`` once the personalised fetch
    // returned, ``SearchPage``'s ``useState`` initialisers (companies,
    // locations, totals…) would already be locked in on the unfiltered
    // defaults — so the filtered result list would never appear. The fix
    // is to render the skeleton between mount and fetch completion when
    // a refetch is needed, so ``SearchPage`` mounts ONCE with the
    // filtered data.
    it("renders ExploreSkeleton — NOT SearchPage with stale initialData — between mount and the filtered fetch resolution", async () => {
      currentSearchParams = new URLSearchParams("loc=eu");
      const filteredData = makeInitialData({
        result: { companies: [], totalCompanies: 1133, truncated: false } as unknown as ExploreData["result"],
      });
      const unfilteredInitial = makeInitialData({
        result: { companies: [], totalCompanies: 3928, truncated: false } as unknown as ExploreData["result"],
      });

      // Use a never-resolving promise initially so we can observe the
      // skeleton-while-pending state, then resolve to the filtered data.
      let resolve: (v: ExploreData) => void = () => {};
      mockFetchExploreData.mockReturnValueOnce(
        new Promise<ExploreData>((r) => {
          resolve = r;
        }),
      );

      const { queryByTestId } = render(
        <ExploreContent locale="en" initialData={unfilteredInitial} />,
      );

      // While the fetch is pending the skeleton must be on screen — the
      // unfiltered ``initialTotalCompanies=3928`` MUST NOT have been
      // handed to ``SearchPage``, otherwise the filter regression
      // recurs.
      await waitFor(() => {
        expect(queryByTestId("explore-skeleton")).not.toBeNull();
      });
      expect(queryByTestId("search-page")).toBeNull();

      resolve(filteredData);

      await waitFor(() => {
        expect(queryByTestId("search-page")).not.toBeNull();
      });
      expect(queryByTestId("explore-skeleton")).toBeNull();
      expect(queryByTestId("search-page")?.getAttribute("data-total")).toBe(
        "1133",
      );
    });

    it("for an anonymous no-filter visit, SearchPage mounts directly with the prerendered initialData (#2640 ISR path preserved)", async () => {
      const initialData = makeInitialData({
        result: { companies: [], totalCompanies: 3928, truncated: false } as unknown as ExploreData["result"],
      });
      const { queryByTestId } = render(
        <ExploreContent locale="en" initialData={initialData} />,
      );

      await waitFor(() => {
        expect(queryByTestId("search-page")).not.toBeNull();
      });
      expect(queryByTestId("explore-skeleton")).toBeNull();
      expect(queryByTestId("search-page")?.getAttribute("data-total")).toBe(
        "3928",
      );
      expect(mockFetchExploreData).not.toHaveBeenCalled();
    });
  });
});

describe("ExploreContent — cold-start retry (#3008)", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("retries fetchExploreData once when the first call rejects (cold-start abort)", async () => {
    setDocumentCookie("logged_in=1");
    const successData = makeInitialData();
    mockFetchExploreData
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(successData);

    render(<ExploreContent locale="en" initialData={makeInitialData()} />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(2);
    });
    // First failure logged at warn level (the retry attempt)
    expect(console.warn).toHaveBeenCalled();
  });

  it("logs at error level when both attempts fail and falls back to initialData", async () => {
    setDocumentCookie("logged_in=1");
    mockFetchExploreData
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    render(<ExploreContent locale="en" initialData={makeInitialData()} />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(console.error).toHaveBeenCalled();
    });
    // The error log includes the marker so it can be filtered in
    // observability.
    const errorArg = (console.error as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]?.[0] as string | undefined;
    expect(errorArg).toMatch(/explore.*failed twice/i);
  });

  it("does NOT retry on the happy path (single resolved call)", async () => {
    setDocumentCookie("logged_in=1");
    mockFetchExploreData.mockResolvedValueOnce(makeInitialData());

    render(<ExploreContent locale="en" initialData={makeInitialData()} />);

    await waitFor(() => {
      expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    });
    await flushQueuedEffects();
    expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    expect(console.warn).not.toHaveBeenCalled();
  });
});
