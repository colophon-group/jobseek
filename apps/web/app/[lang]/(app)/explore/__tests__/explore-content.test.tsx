import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import type { ExploreData } from "@/lib/actions/explore-page-data";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  loadBrowserData: vi.fn(),
  searchPageProps: vi.fn(),
  rates: [{ currency: "CHF", toEur: 1.04 }],
  session: {
    isLoggedIn: false,
    isPending: false,
    preferences: null as null | {
      displayCurrency?: string;
      jobLanguages?: string[];
    },
  },
}));

vi.mock("@/lib/search/explore-browser-data", () => ({
  loadExploreBrowserData: (...args: unknown[]) =>
    mocks.loadBrowserData(...args),
}));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => mocks.session,
}));
vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryRates: () => mocks.rates,
}));
vi.mock("../search-page", () => ({
  SearchPage: (props: {
    initialCompanies: ExploreData["result"]["companies"];
    initialTotalCompanies: number;
    initialDegraded?: boolean;
    initialKeywords: string[];
    initialEmploymentTypes: string[];
    initialWorkMode: string[];
    initialRepositoryFallbackCompanies?:
      ExploreData["repositoryFallbackCompanies"];
    initialLanguageOverride?: string[] | null;
    initialUnresolvedExplicitSlugs?:
      ExploreData["parsed"]["unresolvedExplicitSlugs"];
    initialDirectRefreshAttempted?: boolean;
  }) => {
    mocks.searchPageProps(props);
    return (
      <div
        data-testid="search-page"
        data-total={props.initialTotalCompanies}
      />
    );
  },
}));
vi.mock("@/components/search/explore-skeleton", () => ({
  ExploreSkeleton: () => <div data-testid="explore-skeleton" />,
}));

import { ExploreContent } from "../explore-content";

let cookieSpy: ReturnType<typeof vi.spyOn> | undefined;

function setDocumentCookie(value: string) {
  cookieSpy?.mockRestore();
  cookieSpy = vi.spyOn(document, "cookie", "get").mockReturnValue(value);
}

function setBrowserSearch(search = "") {
  window.history.replaceState(
    null,
    "",
    `/en/explore${search ? `?${search}` : ""}`,
  );
}

function makeInitialData(overrides: Partial<ExploreData> = {}): ExploreData {
  return {
    result: {
      companies: [],
      totalCompanies: 0,
      truncated: false,
    },
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
    languages: ["en"],
    languageOverride: null,
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

async function flushEffects(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  mocks.loadBrowserData.mockReset();
  mocks.searchPageProps.mockReset();
  mocks.session.isLoggedIn = false;
  mocks.session.isPending = false;
  mocks.session.preferences = null;
  mocks.loadBrowserData.mockImplementation(
    async ({ initialData }: { initialData: ExploreData }) => ({
      data: initialData,
      unavailable: false,
      directAttempted: true,
    }),
  );
  setBrowserSearch();
  setDocumentCookie("");
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSpy = undefined;
  vi.restoreAllMocks();
});

describe("ExploreContent browser initialization", () => {
  it("uses the prerendered shell without a mount read for an anonymous default visit", async () => {
    const initialData = makeInitialData({
      result: { companies: [], totalCompanies: 3928, truncated: false },
    });
    const { queryByTestId } = render(
      <ExploreContent locale="en" initialData={initialData} />,
    );

    await flushEffects();
    expect(mocks.loadBrowserData).not.toHaveBeenCalled();
    expect(queryByTestId("search-page")?.getAttribute("data-total")).toBe(
      "3928",
    );
    expect(queryByTestId("explore-skeleton")).toBeNull();
  });

  it("does not load for attribution-only query parameters", async () => {
    setBrowserSearch("utm_source=google");
    render(
      <ExploreContent locale="en" initialData={makeInitialData()} />,
    );

    await flushEffects();
    expect(mocks.loadBrowserData).not.toHaveBeenCalled();
  });

  it.each([
    "q",
    "loc",
    "occ",
    "sen",
    "tech",
    "wm",
    "etype",
    "sal",
    "salcur",
    "exp",
    "lang",
  ])("initializes the %s URL through the browser loader", async (param) => {
    setBrowserSearch(`${param}=x`);
    const initialData = makeInitialData();
    const { unmount } = render(
      <ExploreContent locale="en" initialData={initialData} />,
    );

    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
    const input = mocks.loadBrowserData.mock.calls[0]?.[0] as {
      searchParams: URLSearchParams;
      locale: string;
    };
    expect(input.locale).toBe("en");
    expect(input.searchParams.get(param)).toBe("x");
    unmount();
  });

  it("unmounts broad SearchPage state until filtered direct data lands", async () => {
    setBrowserSearch("loc=zurich");
    const broad = makeInitialData({
      result: { companies: [], totalCompanies: 3928, truncated: false },
    });
    const filtered = makeInitialData({
      result: { companies: [], totalCompanies: 1133, truncated: false },
    });
    let resolve!: (value: unknown) => void;
    mocks.loadBrowserData.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );

    const { queryByTestId } = render(
      <ExploreContent locale="en" initialData={broad} />,
    );
    await waitFor(() =>
      expect(queryByTestId("explore-skeleton")).not.toBeNull(),
    );
    expect(queryByTestId("search-page")).toBeNull();

    resolve({
      data: filtered,
      unavailable: false,
      directAttempted: true,
    });
    await waitFor(() =>
      expect(queryByTestId("search-page")?.getAttribute("data-total")).toBe(
        "1133",
      ),
    );
    expect(mocks.searchPageProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ initialDirectRefreshAttempted: true }),
    );
  });

  it("passes authenticated bootstrap preferences to the browser loader", async () => {
    setDocumentCookie("logged_in=1");
    mocks.session.isLoggedIn = true;
    mocks.session.preferences = {
      displayCurrency: "CHF",
      jobLanguages: ["de", "en"],
    };

    render(
      <ExploreContent locale="de" initialData={makeInitialData()} />,
    );

    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
    expect(mocks.loadBrowserData).toHaveBeenCalledWith(
      expect.objectContaining({
        displayCurrency: "CHF",
        jobLanguages: ["de", "en"],
        rates: mocks.rates,
        isLoggedIn: true,
      }),
    );
  });

  it("waits for a hinted authenticated bootstrap before loading", async () => {
    setDocumentCookie("logged_in=1");
    mocks.session.isPending = true;
    const initialData = makeInitialData();
    const view = render(
      <ExploreContent locale="en" initialData={initialData} />,
    );

    await waitFor(() =>
      expect(view.queryByTestId("explore-skeleton")).not.toBeNull(),
    );
    expect(mocks.loadBrowserData).not.toHaveBeenCalled();

    mocks.session.isPending = false;
    mocks.session.isLoggedIn = true;
    mocks.session.preferences = { displayCurrency: "CHF" };
    view.rerender(<ExploreContent locale="en" initialData={initialData} />);
    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
  });

  it("parses anonymous language preferences without a Server Action", async () => {
    setDocumentCookie(
      `JSEEK_JOB_LANGUAGES=${encodeURIComponent(JSON.stringify(["de", "en"]))}`,
    );
    render(
      <ExploreContent locale="de" initialData={makeInitialData()} />,
    );

    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
    expect(mocks.loadBrowserData).toHaveBeenCalledWith(
      expect.objectContaining({ jobLanguages: ["de", "en"] }),
    );
  });

  it("does not duplicate a filtered load when anonymous bootstrap settles", async () => {
    setBrowserSearch("wm=remote");
    mocks.session.isPending = true;
    let resolveLoad!: (value: {
      data: ExploreData;
      unavailable: boolean;
      directAttempted: boolean;
    }) => void;
    mocks.loadBrowserData.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveLoad = resolve;
      }),
    );
    const initialData = makeInitialData();
    const view = render(
      <ExploreContent locale="en" initialData={initialData} />,
    );
    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());

    mocks.session.isPending = false;
    view.rerender(<ExploreContent locale="en" initialData={initialData} />);
    await flushEffects();
    expect(mocks.loadBrowserData).toHaveBeenCalledOnce();
    expect(view.queryByTestId("explore-skeleton")).not.toBeNull();

    resolveLoad({
      data: makeInitialData({
        result: { companies: [], totalCompanies: 7 },
      }),
      unavailable: false,
      directAttempted: true,
    });
    await waitFor(() =>
      expect(view.getByTestId("search-page").dataset.total).toBe("7"),
    );
  });

  it("invalidates a pending filtered load when browser navigation changes the URL", async () => {
    setBrowserSearch("wm=remote");
    type LoadResult = {
      data: ExploreData;
      unavailable: boolean;
      directAttempted: boolean;
    };
    let resolveFirst!: (value: LoadResult) => void;
    let resolveSecond!: (value: LoadResult) => void;
    mocks.loadBrowserData
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecond = resolve;
        }),
      );

    const view = render(
      <ExploreContent locale="en" initialData={makeInitialData()} />,
    );
    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
    expect(
      (mocks.loadBrowserData.mock.calls[0][0] as { searchParams: URLSearchParams })
        .searchParams.toString(),
    ).toBe("wm=remote");

    act(() => {
      window.history.pushState(null, "", "/en/explore?loc=zurich");
    });
    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledTimes(2));
    expect(
      (mocks.loadBrowserData.mock.calls[1][0] as { searchParams: URLSearchParams })
        .searchParams.toString(),
    ).toBe("loc=zurich");

    resolveFirst({
      data: makeInitialData({ result: { companies: [], totalCompanies: 1 } }),
      unavailable: false,
      directAttempted: true,
    });
    await flushEffects();
    expect(view.queryByTestId("explore-skeleton")).not.toBeNull();

    resolveSecond({
      data: makeInitialData({ result: { companies: [], totalCompanies: 2 } }),
      unavailable: false,
      directAttempted: true,
    });
    await waitFor(() =>
      expect(view.getByTestId("search-page").dataset.total).toBe("2"),
    );
  });

  it("leaves URL changes to SearchPage after browser initialization commits", async () => {
    setBrowserSearch("wm=remote");
    render(
      <ExploreContent locale="en" initialData={makeInitialData()} />,
    );
    await waitFor(() => expect(mocks.loadBrowserData).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(document.querySelector('[data-testid="search-page"]')).not.toBeNull(),
    );

    act(() => {
      window.history.replaceState(null, "", "/en/explore?loc=zurich");
    });
    await flushEffects();

    expect(mocks.loadBrowserData).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-testid="search-page"]')).not.toBeNull();
  });

  it("keeps a rejected unexpected browser load on the skeleton", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    setBrowserSearch("q=python");
    mocks.loadBrowserData.mockRejectedValueOnce(new Error("unexpected"));
    const { queryByTestId } = render(
      <ExploreContent locale="en" initialData={makeInitialData()} />,
    );

    await waitFor(() => expect(console.error).toHaveBeenCalled());
    expect(queryByTestId("explore-skeleton")).not.toBeNull();
    expect(queryByTestId("search-page")).toBeNull();
  });

  it("forwards fail-closed data and direct-attempt state", async () => {
    setBrowserSearch("q=python&loc=missing&wm=remote");
    const unavailable = makeInitialData({
      result: { companies: [], totalCompanies: 0, degraded: true },
      parsed: {
        ...makeInitialData().parsed,
        keywords: ["python"],
        workMode: ["remote"],
        unresolvedExplicitSlugs: { loc: ["missing"] },
      },
    });
    mocks.loadBrowserData.mockResolvedValueOnce({
      data: unavailable,
      unavailable: true,
      directAttempted: true,
    });

    render(
      <ExploreContent locale="en" initialData={makeInitialData()} />,
    );
    await waitFor(() =>
      expect(mocks.searchPageProps).toHaveBeenLastCalledWith(
        expect.objectContaining({
          initialCompanies: [],
          initialDegraded: true,
          initialKeywords: ["python"],
          initialWorkMode: ["remote"],
          initialUnresolvedExplicitSlugs: { loc: ["missing"] },
          initialDirectRefreshAttempted: true,
        }),
      ),
    );
  });

  it("preserves fallback identities and language override on the default shell", async () => {
    const repositoryFallbackCompanies = [{ name: "Acme", slug: "acme" }];
    render(
      <ExploreContent
        locale="en"
        initialData={makeInitialData({
          repositoryFallbackCompanies,
          languageOverride: [],
        })}
      />,
    );
    await flushEffects();
    expect(mocks.searchPageProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        initialRepositoryFallbackCompanies: repositoryFallbackCompanies,
        initialLanguageOverride: [],
      }),
    );
  });
});
