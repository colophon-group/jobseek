import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/test-utils/lingui-mock";
import type { SearchResultCompany, SearchResultPosting } from "@/lib/search";

const mocks = vi.hoisted(() => ({
  loadMorePostings: vi.fn(),
  load: undefined as undefined | (() => Promise<void>),
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
  useInfiniteScroll: ({ load }: { load: () => Promise<void> }) => {
    mocks.load = load;
    return { sentinelRef: { current: null }, isLoading: false };
  },
}));

vi.mock("@/lib/actions/search", () => ({
  loadMorePostings: mocks.loadMorePostings,
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
    mocks.loadMorePostings.mockReset();
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

    mocks.loadMorePostings.mockResolvedValueOnce({
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

  it("uses posting id as deterministic tie-breaker", () => {
    expect(
      sortPostingsByFreshness([
        posting("b", "B", "2026-06-22T11:55:00.000Z"),
        posting("a", "A", "2026-06-22T11:55:00.000Z"),
      ]).map((p) => p.id),
    ).toEqual(["a", "b"]);
  });
});
