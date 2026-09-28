import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SimilarCompany } from "@/lib/actions/company";

import { withTestEnv } from "@/test-utils/env";

withTestEnv({ NEXT_PUBLIC_TYPESENSE_DIRECT: "1" });

let loadNextPage: () => Promise<void>;

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
  useSession: () => ({ isLoggedIn: false, isPending: false }),
}));
vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: ({ load }: { load: () => Promise<void> }) => {
    loadNextPage = load;
    return { sentinelRef: vi.fn(), isLoading: false };
  },
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
    <li>{company.name}: {company.activeJobCount}</li>
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
      expect(screen.getByText("Fresh Peer: 6")).toBeTruthy();
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

  it("ignores entry filters and shows global open-position totals", async () => {
    currentSearchParams = new URLSearchParams("q=python&loc=zurich&wm=remote&show=posting-1");
    renderStrip();

    await waitFor(() => expect(screen.getByText("Fresh Peer: 6")).toBeTruthy());
    expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenCalledExactlyOnceWith({
      companyId: "company-1", industryId: 7, limit: 10,
    });
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
  });

  it("does not refetch peers when filters are cleared", async () => {
    currentSearchParams = new URLSearchParams("q=python");
    const view = renderStrip();

    await waitFor(() => {
      expect(screen.getByText("Fresh Peer: 6")).toBeTruthy();
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
      expect(screen.getByText("Fresh Peer: 6")).toBeTruthy();
    });
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
    expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenCalledTimes(1);
  });
  it("paginates without filter parameters or a server action", async () => {
    currentSearchParams = new URLSearchParams("q=python");
    renderStrip();
    await waitFor(() => expect(screen.getByText("Fresh Peer: 6")).toBeTruthy());
    mocks.tryGetSimilarCompaniesDirect.mockResolvedValue({
      companies: [{ id: "next-peer", slug: "next-peer", name: "Next Peer", icon: null, activeJobCount: 4 }],
      hasMore: false,
    });
    await act(() => loadNextPage());
    expect(mocks.tryGetSimilarCompaniesDirect).toHaveBeenLastCalledWith({
      companyId: "company-1", industryId: 7, offset: 1, limit: 10,
    }, false);
    expect(screen.getByText("Next Peer: 4")).toBeTruthy();
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
  });

  it("preserves peers and lets the scroll hook back off after a failed page", async () => {
    renderStrip();
    await waitFor(() => expect(screen.getByText("Fresh Peer: 6")).toBeTruthy());
    mocks.tryGetSimilarCompaniesDirect.mockResolvedValue(null);
    await expect(loadNextPage()).rejects.toThrow("Related companies unavailable");
    expect(screen.getByText("Fresh Peer: 6")).toBeTruthy();
    expect(mocks.getSimilarCompanies).not.toHaveBeenCalled();
  });

});
