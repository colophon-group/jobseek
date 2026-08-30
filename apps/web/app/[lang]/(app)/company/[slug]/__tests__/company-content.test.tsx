import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import type { CompanyPageData } from "@/lib/actions/company-page-data";
import type { CompanyBrowserDataResult } from "@/lib/search/company-browser-data";

const mockLoadCompanyBrowserData = vi.fn();
vi.mock("@/lib/search/company-browser-data", () => ({
  loadCompanyBrowserData: (...args: unknown[]) =>
    mockLoadCompanyBrowserData(...args),
}));

let sessionState: {
  isLoggedIn: boolean;
  isPending: boolean;
  preferences: {
    displayCurrency?: string;
    jobLanguages?: string[];
  } | null;
};
const stableRates = [{ currency: "CHF", toEur: 1.04 }];
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => sessionState,
}));
vi.mock("@/components/providers/SalaryDisplayProvider", () => ({
  useSalaryRates: () => stableRates,
}));

vi.mock("../company-page", () => ({
  CompanyPage: ({
    company,
    initialActiveCount,
    initialEmploymentTypes,
    jobLanguages,
    displayCurrency,
    initialSearchUnavailable,
    initialDirectRefreshAttempted,
  }: {
    company: CompanyPageData["company"];
    initialActiveCount: number;
    initialEmploymentTypes: string[];
    jobLanguages: string[];
    displayCurrency: string;
    initialSearchUnavailable?: boolean;
    initialDirectRefreshAttempted?: boolean;
  }) => (
    <div
      data-testid="company-page"
      data-company={company.slug}
      data-active={initialActiveCount}
      data-etypes={initialEmploymentTypes.join(",")}
      data-languages={jobLanguages.join(",")}
      data-currency={displayCurrency}
      data-unavailable={String(Boolean(initialSearchUnavailable))}
      data-direct-attempted={String(Boolean(initialDirectRefreshAttempted))}
    />
  ),
}));
vi.mock("@/components/search/company-skeleton", () => ({
  CompanySkeleton: () => <div data-testid="company-skeleton" />,
}));

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

function makeData(overrides: Partial<CompanyPageData> = {}): CompanyPageData {
  return {
    company: makeCompany(),
    postings: [],
    activeCount: 5,
    yearCount: 8,
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

function successfulResult(
  data: CompanyPageData,
  directAttempted = true,
): CompanyBrowserDataResult {
  return { data, unavailable: false, directAttempted };
}

beforeEach(() => {
  currentSearchParams = new URLSearchParams();
  sessionState = { isLoggedIn: false, isPending: false, preferences: null };
  setDocumentCookie("");
  mockLoadCompanyBrowserData.mockReset();
  mockLoadCompanyBrowserData.mockImplementation(({ initialData }) =>
    Promise.resolve(successfulResult(initialData)),
  );
});

afterEach(() => {
  cookieSpy?.mockRestore();
  cookieSpy = undefined;
});

describe("CompanyContent browser initialization", () => {
  it("uses the prerendered shell with zero mount request for default anonymous views", async () => {
    const { getByTestId } = render(
      <CompanyContent locale="en" slug="test-company" initialData={makeData()} />,
    );

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockLoadCompanyBrowserData).not.toHaveBeenCalled();
    expect(getByTestId("company-page").getAttribute("data-active")).toBe("5");
  });

  it("ignores non-result params, including selected posting changes", async () => {
    currentSearchParams = new URLSearchParams("utm_source=test&show=posting-1");
    const initialData = makeData();
    const { rerender } = render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    currentSearchParams = new URLSearchParams("show=posting-2");
    rerender(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockLoadCompanyBrowserData).not.toHaveBeenCalled();
  });

  it("loads filter-bearing anonymous views through the browser path", async () => {
    currentSearchParams = new URLSearchParams("q=python&etype=internship");
    const filtered = makeData({
      activeCount: 2,
      parsed: {
        keywords: ["python"],
        locations: [],
        occupations: [],
        seniorities: [],
        technologies: [],
        workMode: [],
        employmentTypes: ["internship"],
      },
    });
    mockLoadCompanyBrowserData.mockResolvedValue(successfulResult(filtered));

    const { getByTestId } = render(
      <CompanyContent locale="en" slug="test-company" initialData={makeData()} />,
    );

    await waitFor(() => expect(mockLoadCompanyBrowserData).toHaveBeenCalledOnce());
    expect(mockLoadCompanyBrowserData.mock.calls[0]?.[0]).toMatchObject({
      locale: "en",
      isLoggedIn: false,
      jobLanguages: [],
    });
    await waitFor(() =>
      expect(getByTestId("company-page").getAttribute("data-active")).toBe("2"),
    );
    expect(getByTestId("company-page").getAttribute("data-etypes")).toBe(
      "internship",
    );
  });

  it.each(["q", "loc", "occ", "sen", "tech", "wm", "etype", "sal", "salcur", "exp"])(
    "treats %s as a result-bearing param",
    async (param) => {
      currentSearchParams = new URLSearchParams(`${param}=x`);
      const { unmount } = render(
        <CompanyContent locale="en" slug="test-company" initialData={makeData()} />,
      );
      await waitFor(() => expect(mockLoadCompanyBrowserData).toHaveBeenCalledOnce());
      unmount();
      mockLoadCompanyBrowserData.mockClear();
    },
  );

  it("waits for authenticated bootstrap and reuses its preferences", async () => {
    setDocumentCookie("logged_in=1");
    sessionState = { isLoggedIn: false, isPending: true, preferences: null };
    const initialData = makeData();
    const personalized = makeData({
      displayCurrency: "CHF",
      jobLanguages: ["de", "en"],
      languages: ["de", "en"],
    });
    mockLoadCompanyBrowserData.mockResolvedValue(successfulResult(personalized));
    const { getByTestId, rerender } = render(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockLoadCompanyBrowserData).not.toHaveBeenCalled();

    sessionState = {
      isLoggedIn: true,
      isPending: false,
      preferences: { displayCurrency: "CHF", jobLanguages: ["de", "en"] },
    };
    rerender(
      <CompanyContent locale="en" slug="test-company" initialData={initialData} />,
    );

    await waitFor(() => expect(mockLoadCompanyBrowserData).toHaveBeenCalledOnce());
    expect(mockLoadCompanyBrowserData.mock.calls[0]?.[0]).toMatchObject({
      displayCurrency: "CHF",
      jobLanguages: ["de", "en"],
      isLoggedIn: true,
    });
    await waitFor(() =>
      expect(getByTestId("company-page").getAttribute("data-currency")).toBe(
        "CHF",
      ),
    );
  });

  it("parses anonymous language preferences locally", async () => {
    setDocumentCookie(
      "JSEEK_JOB_LANGUAGES=%5B%22de%22%2C%22en%22%5D",
    );
    render(
      <CompanyContent locale="en" slug="test-company" initialData={makeData()} />,
    );

    await waitFor(() => expect(mockLoadCompanyBrowserData).toHaveBeenCalledOnce());
    expect(mockLoadCompanyBrowserData.mock.calls[0]?.[0]).toMatchObject({
      jobLanguages: ["de", "en"],
      isLoggedIn: false,
    });
  });

  it("renders an explicit unavailable state without restoring broad shell results", async () => {
    currentSearchParams = new URLSearchParams("loc=missing-place");
    const failed = makeData({ activeCount: 0, yearCount: 0, postings: [] });
    mockLoadCompanyBrowserData.mockResolvedValue({
      data: failed,
      unavailable: true,
      directAttempted: true,
    });
    const { getByTestId, queryByTestId } = render(
      <CompanyContent
        locale="en"
        slug="test-company"
        initialData={makeData({ activeCount: 99 })}
      />,
    );

    await waitFor(() =>
      expect(getByTestId("company-page").getAttribute("data-unavailable")).toBe(
        "true",
      ),
    );
    expect(getByTestId("company-page").getAttribute("data-active")).toBe("0");
    expect(getByTestId("company-page").getAttribute("data-direct-attempted")).toBe(
      "true",
    );
    expect(queryByTestId("company-skeleton")).toBeNull();
  });

  it("ignores stale browser responses across page identity changes", async () => {
    currentSearchParams = new URLSearchParams("q=engineer");
    let resolveAlpha: (result: CompanyBrowserDataResult) => void = () => {};
    let resolveBeta: (result: CompanyBrowserDataResult) => void = () => {};
    mockLoadCompanyBrowserData
      .mockReturnValueOnce(
        new Promise<CompanyBrowserDataResult>((resolve) => {
          resolveAlpha = resolve;
        }),
      )
      .mockReturnValueOnce(
        new Promise<CompanyBrowserDataResult>((resolve) => {
          resolveBeta = resolve;
        }),
      );
    const alpha = makeData({ company: { ...makeCompany(), slug: "alpha" } });
    const beta = makeData({ company: { ...makeCompany(), slug: "beta" } });
    const { queryByTestId, rerender } = render(
      <CompanyContent locale="en" slug="alpha" initialData={alpha} />,
    );
    await waitFor(() => expect(mockLoadCompanyBrowserData).toHaveBeenCalledOnce());

    rerender(<CompanyContent locale="en" slug="beta" initialData={beta} />);
    await waitFor(() =>
      expect(mockLoadCompanyBrowserData).toHaveBeenCalledTimes(2),
    );
    resolveAlpha(successfulResult(alpha));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(queryByTestId("company-page")).toBeNull();

    resolveBeta(successfulResult(beta));
    await waitFor(() =>
      expect(queryByTestId("company-page")?.getAttribute("data-company")).toBe(
        "beta",
      ),
    );
  });
});
