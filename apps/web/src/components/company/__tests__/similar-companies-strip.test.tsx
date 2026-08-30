import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SimilarCompany } from "@/lib/actions/company";

const mocks = vi.hoisted(() => ({
  getSimilarCompanies: vi.fn(),
  tryGetSimilarCompaniesDirect: vi.fn(),
}));

let currentSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useParams: () => ({ lang: "en" }),
  useSearchParams: () => currentSearchParams,
}));
vi.mock("@lingui/react/macro", () => ({
  Trans: ({ children }: { children: React.ReactNode }) => children,
  useLingui: () => ({
    t: ({ message }: { message: string }) => message,
  }),
}));
vi.mock("@/lib/actions/company", () => ({
  getSimilarCompanies: (...args: unknown[]) =>
    mocks.getSimilarCompanies(...args),
}));
vi.mock("@/lib/search/search-runner", () => ({
  tryGetSimilarCompaniesDirect: (...args: unknown[]) =>
    mocks.tryGetSimilarCompaniesDirect(...args),
}));
vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => ({ isPending: false }),
}));
vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: () => ({ sentinelRef: vi.fn(), isLoading: false }),
}));
vi.mock("@/components/ui/scroll-fade", () => ({
  ScrollFade: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));
vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => <li data-testid="sentinel" />,
}));
vi.mock("../similar-company-card", () => ({
  SimilarCompanyCard: ({ company }: { company: SimilarCompany }) => (
    <li>{company.name}</li>
  ),
}));

import { SimilarCompaniesStrip } from "../similar-companies-strip";

const initialCompanies: SimilarCompany[] = [
  {
    id: "peer-1",
    slug: "peer-one",
    name: "Peer One",
    icon: null,
    activeJobCount: 4,
  },
];

function renderStrip(overrides: Partial<React.ComponentProps<typeof SimilarCompaniesStrip>> = {}) {
  return render(
    <SimilarCompaniesStrip
      companyId="company-1"
      industryId={7}
      initialCompanies={initialCompanies}
      initialHasMore={false}
      locale="en"
      {...overrides}
    />,
  );
}

beforeEach(() => {
  currentSearchParams = new URLSearchParams();
  mocks.getSimilarCompanies.mockReset();
  mocks.tryGetSimilarCompaniesDirect.mockReset();
  mocks.getSimilarCompanies.mockResolvedValue({
    companies: [
      {
        id: "filtered-peer",
        slug: "filtered-peer",
        name: "Filtered Peer",
        icon: null,
        activeJobCount: 2,
      },
    ],
    hasMore: false,
  });
  mocks.tryGetSimilarCompaniesDirect.mockResolvedValue({
    companies: [
      {
        id: "fresh-peer",
        slug: "fresh-peer",
        name: "Fresh Peer",
        icon: null,
        activeJobCount: 6,
      },
    ],
    hasMore: false,
  });
});

describe("SimilarCompaniesStrip cached initial page", () => {
  it("refreshes an unfiltered visit browser-direct without a Server Action", async () => {
    renderStrip();

    await waitFor(() => {
      expect(screen.getByText("Fresh Peer")).toBeTruthy();
    });
    expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenCalledWith({
      companyId: "company-1",
      industryId: 7,
      limit: 10,
    });
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
  });

  it("keeps a cached empty result when the direct refresh is unavailable", async () => {
    mocks.tryGetSimilarCompaniesDirect.mockResolvedValue(null);
    const { container } = renderStrip({ initialCompanies: [] });

    await waitFor(() => {
      expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenCalledOnce();
    });
    expect(container.innerHTML).toBe("");
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
  });

  it("loads a filtered ranking when the entry URL has filters", async () => {
    currentSearchParams = new URLSearchParams("q=python");
    renderStrip();

    await waitFor(() => {
      expect(mocks.getSimilarCompanies).toHaveBeenCalledTimes(1);
    });
    expect(mocks.getSimilarCompanies).toHaveBeenCalledWith("company-1", 7, {
      offset: 0,
      limit: 10,
      searchParams: { q: "python" },
      locale: "en",
    });
    expect(screen.getByText("Filtered Peer")).toBeTruthy();
  });

  it("refreshes browser-direct when filters are cleared", async () => {
    currentSearchParams = new URLSearchParams("q=python");
    const view = renderStrip();

    await waitFor(() => {
      expect(screen.getByText("Filtered Peer")).toBeTruthy();
    });

    currentSearchParams = new URLSearchParams();
    view.rerender(
      <SimilarCompaniesStrip
        companyId="company-1"
        industryId={7}
        initialCompanies={initialCompanies}
        initialHasMore={false}
        locale="en"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Fresh Peer")).toBeTruthy();
    });
    expect(mocks.getSimilarCompanies).toHaveBeenCalledTimes(1);
    expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenCalledTimes(1);
  });
});
