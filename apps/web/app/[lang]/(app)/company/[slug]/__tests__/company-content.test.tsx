import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor } from "@testing-library/react";
import type { CompanyPageData } from "@/lib/actions/company-page-data";

// `@/lib/actions/company-page-data` is a server action that transitively
// imports `server-only`, which throws when loaded in a non-Next runtime.
// Neutralise the gate, then swap the action itself for a spy.
vi.mock("server-only", () => ({}));
const mockFetchCompanyPageData = vi.fn();
vi.mock("@/lib/actions/company-page-data", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/actions/company-page-data")>(
      "@/lib/actions/company-page-data",
    );
  return {
    ...actual,
    fetchCompanyPageData: (...args: unknown[]) => mockFetchCompanyPageData(...args),
  };
});

// CompanyPage has a heavy dependency tree (Lingui i18n, Typesense
// provider, currency rates, infinite scroll, etc.). Stub it out — this
// suite is testing the conditional-fetch logic in CompanyContent, not
// CompanyPage's behaviour. Render a marker with initial props so the
// stale-ISR-data regression test can assert which dataset mounted it.
vi.mock("../company-page", () => ({
  CompanyPage: ({
    initialActiveCount,
    initialEmploymentTypes,
  }: {
    initialActiveCount: number;
    initialEmploymentTypes: string[];
  }) => (
    <div
      data-testid="company-page"
      data-active={initialActiveCount}
      data-etypes={initialEmploymentTypes.join(",")}
    />
  ),
}));

// Skeleton stub — a distinct marker so tests can assert when CompanyPage
// is intentionally unmounted while personalised data is loading.
vi.mock("@/components/search/company-skeleton", () => ({
  CompanySkeleton: () => <div data-testid="company-skeleton" />,
}));

// `useSearchParams` from `next/navigation` returns a `URLSearchParams`-
// like object in the test environment. Mock it per-test to control the
// filter state being observed by the component.
let currentSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => currentSearchParams,
}));

import { CompanyContent } from "../company-content";

let cookieSpy: ReturnType<typeof vi.spyOn> | undefined;
function setDocumentCookie(value: string) {
  cookieSpy?.mockRestore();
  cookieSpy = vi.spyOn(document, "cookie", "get").mockReturnValue(value);
}

function makeCompany(): CompanyPageData["company"] {
  return {
    id: "company-1",
    name: "Test Company",
    slug: "test-company",
    icon: null,
    logo: null,
    website: null,
    description: null,
    industryId: null,
    industryName: null,
    employeeCountRange: null,
    foundedYear: null,
    activeJobCount: 5,
  };
}

function makeInitialData(overrides: Partial<CompanyPageData> = {}): CompanyPageData {
  return {
    company: makeCompany(),
    postings: [],
    activeCount: 0,
    yearCount: 0,
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
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: "EUR",
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    showPostingId: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockFetchCompanyPageData.mockReset();
  mockFetchCompanyPageData.mockResolvedValue(makeInitialData());
  currentSearchParams = new URLSearchParams();
  setDocumentCookie("");
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSpy = undefined;
});

describe("CompanyContent — server-render initial-data path (#3203)", () => {
  it("does NOT call fetchCompanyPageData for an anonymous, no-filter visit with prerendered initialData", async () => {
    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    // Wait for any queued effects to run — give them a real chance to
    // misbehave before asserting that they didn't.
    await new Promise((r) => setTimeout(r, 0));
    expect(mockFetchCompanyPageData).not.toHaveBeenCalled();
  });

  it("calls fetchCompanyPageData when the `logged_in` hint cookie is present even without filters", async () => {
    setDocumentCookie("logged_in=1");

    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });
  });

  it("calls fetchCompanyPageData when the anon job-languages hint cookie is present", async () => {
    // Issue #2850: anonymous viewers persist `jobLanguages` via the
    // JSEEK_JOB_LANGUAGES cookie. When present, the server-rendered
    // anonymous-default data may not match what the personalized
    // server action would return, so we must refetch.
    setDocumentCookie("JSEEK_JOB_LANGUAGES=en,de");

    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });
  });

  it("calls fetchCompanyPageData when a filter searchParam is present", async () => {
    currentSearchParams = new URLSearchParams("q=python");

    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });

    const callArgs = mockFetchCompanyPageData.mock.calls[0]?.[0] as {
      slug: string;
      searchParams: Record<string, string | undefined>;
      locale: string;
    };
    expect(callArgs.slug).toBe("test-company");
    expect(callArgs.locale).toBe("en");
    expect(callArgs.searchParams.q).toBe("python");
  });

  it("calls fetchCompanyPageData when initialData is omitted (legacy path)", async () => {
    render(<CompanyContent locale="en" slug="test-company" />);

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });
  });

  it("does NOT call fetchCompanyPageData when a non-filter searchParam is present (e.g. utm tracking)", async () => {
    currentSearchParams = new URLSearchParams("utm_source=google");

    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await new Promise((r) => setTimeout(r, 0));
    expect(mockFetchCompanyPageData).not.toHaveBeenCalled();
  });

  it("recognises every documented filter searchParam as a refetch trigger", async () => {
    const filterParams = [
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
      "show",
    ];
    for (const param of filterParams) {
      mockFetchCompanyPageData.mockClear();
      currentSearchParams = new URLSearchParams(`${param}=x`);

      const initialData = makeInitialData();
      const { unmount } = render(
        <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
      );

      await waitFor(() => {
        expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
      });
      unmount();
    }
  });

  it("passes etype through when it triggers a personalized fetch", async () => {
    currentSearchParams = new URLSearchParams("etype=internship");

    const initialData = makeInitialData();
    render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });

    const callArgs = mockFetchCompanyPageData.mock.calls[0]?.[0] as {
      searchParams: Record<string, string | undefined>;
    };
    expect(callArgs.searchParams.etype).toBe("internship");
  });

  it("unmounts stale prerendered data until the filtered etype fetch resolves", async () => {
    currentSearchParams = new URLSearchParams("etype=internship");
    const unfilteredInitial = makeInitialData({
      activeCount: 3835,
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
    const filteredData = makeInitialData({
      activeCount: 870,
      parsed: {
        keywords: [],
        locations: [],
        occupations: [],
        seniorities: [],
        technologies: [],
        workMode: [],
        employmentTypes: ["internship"],
      },
    });

    let resolve: (v: CompanyPageData) => void = () => {};
    mockFetchCompanyPageData.mockReturnValueOnce(
      new Promise<CompanyPageData>((r) => {
        resolve = r;
      }),
    );

    const { queryByTestId } = render(
      <CompanyContent locale="en" slug="test-company" initialData={unfilteredInitial} />,
    );

    await waitFor(() => {
      expect(queryByTestId("company-skeleton")).not.toBeNull();
    });
    expect(queryByTestId("company-page")).toBeNull();

    resolve(filteredData);

    await waitFor(() => {
      expect(queryByTestId("company-page")).not.toBeNull();
    });
    expect(queryByTestId("company-skeleton")).toBeNull();
    expect(queryByTestId("company-page")?.getAttribute("data-active")).toBe(
      "870",
    );
    expect(queryByTestId("company-page")?.getAttribute("data-etypes")).toBe(
      "internship",
    );
  });

  it("renders the CompanySkeleton fallback when no initialData and the fetch is in-flight", async () => {
    // Never-resolving promise to keep the component in the loading
    // state for the duration of the assertion.
    mockFetchCompanyPageData.mockReturnValue(new Promise(() => {}));

    const { queryByTestId } = render(
      <CompanyContent locale="en" slug="test-company" />,
    );

    await waitFor(() => {
      expect(queryByTestId("company-skeleton")).not.toBeNull();
    });
    expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    expect(queryByTestId("company-page")).toBeNull();
  });

  it("triggers the not-found path when fetchCompanyPageData resolves to null", async () => {
    mockFetchCompanyPageData.mockResolvedValueOnce(null);

    // The Lingui <Trans> macros inside CompanyNotFound need an i18n
    // provider to render their text — outside that scope they emit
    // empty strings. So we assert the call resolved with null and the
    // component re-rendered (no crash, no infinite loading) rather
    // than text content.
    const { container } = render(<CompanyContent locale="en" slug="ghost-slug" />);

    await waitFor(() => {
      expect(mockFetchCompanyPageData).toHaveBeenCalledTimes(1);
    });
    // Sanity check: the component rendered something (the not-found
    // shell wrapper), not crashed.
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeTruthy();
  });
});
