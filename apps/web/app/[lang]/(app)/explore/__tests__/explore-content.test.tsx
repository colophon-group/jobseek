import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
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
// in ExploreContent, not SearchPage's behaviour.
vi.mock("../search-page", () => ({
  SearchPage: () => null,
}));

// Skeleton stub — we just need a deterministic render output to check
// that the component falls back to it when no data is available.
vi.mock("@/components/search/explore-skeleton", () => ({
  ExploreSkeleton: () => null,
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

    // Wait for any queued effects to run — give them a real chance to
    // misbehave before asserting that they didn't.
    await new Promise((r) => setTimeout(r, 0));
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

    await new Promise((r) => setTimeout(r, 0));
    expect(mockFetchExploreData).not.toHaveBeenCalled();
  });

  it("recognises every documented filter searchParam as a refetch trigger", async () => {
    const filterParams = ["q", "loc", "occ", "sen", "tech", "sal", "salcur", "exp"];
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
    // Wait an additional tick to confirm no second call sneaks in
    await new Promise((r) => setTimeout(r, 50));
    expect(mockFetchExploreData).toHaveBeenCalledTimes(1);
    expect(console.warn).not.toHaveBeenCalled();
  });
});
