/**
 * Regression tests for issue #3345 — preventing scroll jitter in the
 * CompanyCard posting list (mirrors the watchlist-job-list fix).
 *
 * The CompanyCard renders its posting list inside a local ScrollFade
 * scroll container, so it does NOT need the wider `overflow-anchor:
 * none` opt-out that the watchlist surface uses. But each posting row
 * needs the same defensive height + layout-containment so a re-render
 * cannot push neighbouring rows around.
 *
 *   - `min-h-7` (= 28px) reserves the row's natural height.
 *   - `[contain:layout]` isolates layout calculations per row.
 */
import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import "@/test-utils/lingui-mock";

// --- Mocks (mirror company-card-nested-buttons.test.tsx) -------------------

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
  useInfiniteScroll: () => ({ sentinelRef: { current: null }, isLoading: false }),
}));

vi.mock("@/lib/actions/search", () => ({
  getMorePostings: vi.fn(),
}));

vi.mock("@/lib/time", () => ({
  timeAgoShort: () => "1d",
}));

vi.mock("@/lib/search/query-params", () => ({
  buildFilteredPath: () => "/en/company/acme",
}));

// Import AFTER mocks.
import { CompanyCard } from "../company-card";
import type { SearchResultCompany } from "@/lib/search";

function makeResult(): SearchResultCompany {
  return {
    company: { id: "c1", name: "ACME", slug: "acme", icon: null },
    activeMatches: 2,
    yearMatches: 2,
    postings: [
      {
        id: "p1",
        title: "Senior Engineer",
        firstSeenAt: "2026-05-01T00:00:00Z",
        relevanceScore: 1,
        locations: [],
        isActive: true,
      },
      {
        id: "p2",
        title: "Junior Engineer",
        firstSeenAt: "2026-05-02T00:00:00Z",
        relevanceScore: 1,
        locations: [],
        isActive: true,
      },
    ],
  };
}

describe("CompanyCard — scroll stability (issue #3345)", () => {
  it("each posting row reserves vertical space with `min-h-7` and `contain: layout`", () => {
    const { container } = render(
      <CompanyCard
        result={makeResult()}
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

    // Two postings → two rows. Each row carries both `min-h-7` (locks
    // vertical extent at the current 28px natural height) and
    // `[contain:layout]` (Tailwind arbitrary syntax compiles to
    // `contain: layout`).
    const rows = container.querySelectorAll('div.relative.flex.min-h-7.\\[contain\\:layout\\]');
    expect(
      rows.length,
      "every CompanyCard posting row must carry `min-h-7` and `[contain:layout]` to prevent scroll jitter",
    ).toBe(2);
  });
});
