import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  load: vi.fn(),
  viewProps: vi.fn(),
  session: { isLoggedIn: false, isPending: false },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { prefetch?: boolean }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/components/providers/SessionProvider", () => ({
  useSession: () => mocks.session,
}));

vi.mock("@/lib/actions/session-watchlists", () => ({
  getSessionWatchlistPageData: (...args: unknown[]) => mocks.load(...args),
}));

vi.mock("@/components/watchlist/watchlist-view-page", () => ({
  WatchlistViewPage: (props: Record<string, unknown>) => {
    mocks.viewProps(props);
    return <div data-testid="normal-watchlist-view" />;
  },
}));

import {
  readPendingWatchlists,
  stagePendingWatchlistEntry,
} from "@/lib/pending-watchlist";
import { SessionWatchlistLoader } from "./session-watchlist-loader";

const sourceId = "11111111-1111-4111-8111-111111111111";
const draft = {
  title: "Swiss internships",
  companyIds: [],
  filters: { anyCompany: true, locationSlugs: ["switzerland"] },
  isPublic: false as const,
};
const pageData = {
  detail: {
    id: sourceId,
    title: draft.title,
    description: null,
    filters: draft.filters,
    companies: [],
    alertsEnabled: false,
  },
  isOwner: true,
  limitReached: false,
  postings: [],
  total: 0,
  truncated: false,
  yearTotal: 0,
  searchUnavailable: false,
  resolvedLocations: [],
  resolvedOccupations: [],
  resolvedSeniorities: [],
  resolvedTechnologies: [],
  jobLanguages: [],
  languages: ["en"],
  browserPostingFilters: null,
};

describe("SessionWatchlistLoader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    mocks.session = { isLoggedIn: false, isPending: false };
  });

  it("loads browser state into the normal owned watchlist view on the normal route", async () => {
    const entry = stagePendingWatchlistEntry({ kind: "create", draft })!;
    mocks.load.mockResolvedValue({
      data: { ...pageData, detail: { ...pageData.detail, id: entry.id } },
      draft,
    });

    render(
      <SessionWatchlistLoader
        locale="en"
        watchlistId={entry.id}
        overviewLabel="Watchlists"
      />,
    );

    expect(await screen.findByTestId("normal-watchlist-view")).toBeTruthy();
    expect(mocks.load).toHaveBeenCalledWith({
      sessionWatchlistId: entry.id,
      locale: "en",
      intent: { kind: "create", draft },
    });
    expect(mocks.viewProps).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        isOwner: true,
        detail: expect.objectContaining({ id: entry.id }),
      }),
      sessionWatchlistId: entry.id,
    }));
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("materializes a staged clone into an editable local watchlist", async () => {
    const entry = stagePendingWatchlistEntry({
      kind: "clone",
      watchlistId: sourceId,
      title: "Finance",
    })!;
    mocks.load.mockResolvedValue({
      data: { ...pageData, detail: { ...pageData.detail, id: entry.id, title: "Finance" } },
      draft: { ...draft, title: "Finance" },
    });

    render(
      <SessionWatchlistLoader
        locale="en"
        watchlistId={entry.id}
        overviewLabel="Watchlists"
      />,
    );

    await screen.findByTestId("normal-watchlist-view");
    await waitFor(() => expect(readPendingWatchlists()[0]?.intent).toEqual({
      kind: "create",
      draft: { ...draft, title: "Finance" },
    }));
  });

  it("returns authenticated viewers to the overview for database import", async () => {
    mocks.session = { isLoggedIn: true, isPending: false };

    render(
      <SessionWatchlistLoader
        locale="en"
        watchlistId={sourceId}
        overviewLabel="Watchlists"
      />,
    );

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/en/watchlists"));
    expect(mocks.load).not.toHaveBeenCalled();
  });
});
