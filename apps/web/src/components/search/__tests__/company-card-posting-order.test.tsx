import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
import type { SearchResultCompany, SearchResultPosting } from "@/lib/search";

const mocks = vi.hoisted(() => ({
  getMorePostings: vi.fn(),
  load: undefined as undefined | (() => Promise<void>),
  hasMore: undefined as boolean | undefined,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ lang: "en" }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _prefetch, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: ({ alt }: { alt: string }) => <span data-testid="company-icon">{alt}</span>,
}));

vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => null,
}));

vi.mock("@/components/TruncationPrompt", () => ({
  TruncationPrompt: () => null,
}));

vi.mock("@/components/TrackingDot", () => ({
  TrackingDot: () => <span data-testid="tracking-dot" />,
}));

vi.mock("@/components/PendingJobWarning", () => ({
  PendingJobIcon: () => <span data-testid="pending-job-icon" />,
}));

vi.mock("@/components/search/save-button", () => ({
  SaveButton: ({ postingId }: { postingId: string }) => (
    <button type="button" aria-label={`Save job ${postingId}`}>save</button>
  ),
}));

vi.mock("@/components/search/star-button", () => ({
  StarButton: () => null,
}));

vi.mock("@/components/ui/scroll-fade", () => ({
  ScrollFade: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/lib/use-infinite-scroll", () => ({
  useInfiniteScroll: ({ hasMore, load }: { hasMore: boolean; load: () => Promise<void> }) => {
    mocks.load = load;
    mocks.hasMore = hasMore;
    return { sentinelRef: { current: null }, isLoading: false };
  },
}));

vi.mock("@/lib/actions/search", () => ({
  getMorePostings: mocks.getMorePostings,
}));

vi.mock("@/lib/time", () => ({
  timeAgoShort: () => "1m",
}));

vi.mock("@/lib/search/query-params", () => ({
  buildFilteredPath: () => "/en/company/acme",
}));

import { CompanyCard, sortPostingsByFreshness } from "../company-card";

function posting(
  id: string,
  title: string,
  firstSeenAt: string,
): SearchResultPosting {
  return {
    id,
    title,
    firstSeenAt,
    relevanceScore: 1,
    locations: [],
    isActive: true,
  };
}

function result(postings: SearchResultPosting[]): SearchResultCompany {
  return {
    company: { id: "company-1", name: "High Churn Co", slug: "high-churn", icon: null },
    activeMatches: 100,
    yearMatches: 100,
    postings,
  };
}

function renderCard(postings: SearchResultPosting[]) {
  return render(
    <CompanyCard
      result={result(postings)}
      keywords={[]}
      locationIds={[]}
      locations={[]}
      occupations={[]}
      seniorities={[]}
      technologies={[]}
      employmentTypes={[]}
      workMode={[]}
      languages={["en"]}
      onShowPosting={vi.fn()}
      selectedPostingId={null}
    />,
  );
}

function visiblePostingIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("[data-posting-id]")).map(
    (node) => node.getAttribute("data-posting-id") ?? "",
  );
}

describe("CompanyCard posting order", () => {
  beforeEach(() => {
    mocks.load = undefined;
    mocks.hasMore = undefined;
    mocks.getMorePostings.mockReset();
  });

  it("sorts initial postings by newest first before rendering", () => {
    const { container } = renderCard([
      posting("old", "Old role", "2026-06-22T11:20:00.000Z"),
      posting("fresh", "Fresh role", "2026-06-22T11:50:00.000Z"),
      posting("middle", "Middle role", "2026-06-22T11:40:00.000Z"),
    ]);

    expect(visiblePostingIds(container)).toEqual(["fresh", "middle", "old"]);
  });

  it("resorts after load-more appends fresher postings from high-churn companies", async () => {
    const { container } = renderCard([
      posting("visible-older", "Visible older role", "2026-06-22T11:30:00.000Z"),
      posting("visible-oldest", "Visible oldest role", "2026-06-22T11:20:00.000Z"),
    ]);

    mocks.getMorePostings.mockResolvedValueOnce({
      postings: [
        posting("loaded-fresh", "Loaded fresh role", "2026-06-22T11:55:00.000Z"),
      ],
      truncated: false,
    });

    await act(async () => {
      await mocks.load?.();
    });

    expect(visiblePostingIds(container)).toEqual([
      "loaded-fresh",
      "visible-older",
      "visible-oldest",
    ]);
  });

  it("stops infinite scroll when a full loaded page contains only duplicate posting ids", async () => {
    const initialPostings = Array.from({ length: 20 }, (_, index) =>
      posting(
        `visible-${index}`,
        `Visible role ${index}`,
        `2026-06-22T11:${String(59 - index).padStart(2, "0")}:00.000Z`,
      ),
    );
    const { container } = renderCard(initialPostings);

    mocks.getMorePostings.mockResolvedValueOnce({
      postings: initialPostings,
      truncated: false,
    });

    expect(mocks.hasMore).toBe(true);

    await act(async () => {
      await mocks.load?.();
    });

    expect(mocks.getMorePostings).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 20, limit: 20 }),
    );
    expect(visiblePostingIds(container)).toEqual(initialPostings.map((p) => p.id));
    await waitFor(() => {
      expect(mocks.hasMore).toBe(false);
    });
  });

  it("uses posting id as deterministic tie-breaker", () => {
    expect(
      sortPostingsByFreshness([
        posting("b", "B", "2026-06-22T11:55:00.000Z"),
        posting("a", "A", "2026-06-22T11:55:00.000Z"),
      ]).map((p) => p.id),
    ).toEqual(["a", "b"]);
  });
});
