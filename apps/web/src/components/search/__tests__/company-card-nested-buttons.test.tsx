/**
 * Regression tests for issue #3166 — un-nesting interactive elements
 * in the search company-card posting list.
 *
 * Before the fix, each posting row was a `<div role="button" tabIndex=
 * "0">` containing a real `<SaveButton>` (`<button>`). The outer was
 * keyboard-reachable but the WCAG 4.1.2 violation (button inside
 * button) remained, and screen readers conflated the two controls.
 *
 * The fix restructures the row into a `relative` wrapper containing
 * two sibling `<button>` elements:
 *   - an "Open posting" overlay (`absolute inset-0`),
 *   - the existing SaveButton wrapped in a `relative z-10` span so it
 *     overlays the open-overlay and stays keyboard-reachable.
 *
 * Assertions here mirror the watchlist tests:
 *   1. No `<button>` (or [role=button]) is nested inside another.
 *   2. Both row-level controls are real buttons and keyboard-reachable.
 *   3. Clicking SaveButton does NOT trigger `onShowPosting`.
 *   4. Clicking the row open-overlay DOES trigger `onShowPosting`.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import "@/test-utils/lingui-mock";

// --- Mocks ------------------------------------------------------------------
// Keep the heavy subtree light, but render SaveButton as a REAL <button>
// so we can verify the nested-button invariant on the rendered DOM.

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

const saveClickMock = vi.fn();
// IMPORTANT: render the SaveButton as a real <button> so the
// "no nested <button>" assertion has teeth.
vi.mock("@/components/search/save-button", () => ({
  SaveButton: ({ postingId }: { postingId: string }) => (
    <button
      type="button"
      data-testid={`save-button-${postingId}`}
      aria-label={`Save job ${postingId}`}
      onClick={(e) => {
        // Preserve the original stopPropagation guard.
        e.stopPropagation();
        saveClickMock(postingId);
      }}
    >
      save
    </button>
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

describe("CompanyCard row a11y (issue #3166)", () => {
  beforeEach(() => {
    saveClickMock.mockClear();
  });

  it("does NOT nest a <button> (or [role=button]) inside another <button>", () => {
    const onShow = vi.fn();
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
        onShowPosting={onShow}
        selectedPostingId={null}
      />,
    );

    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const btn of buttons) {
      const nested = btn.querySelector('button, [role="button"]');
      expect(
        nested,
        `nested interactive inside <button>: ${btn.outerHTML.slice(0, 200)}`,
      ).toBeNull();
    }

    // Also: there must be no `[role="button"]` elements at all in the
    // posting rows — those were the original violation surface.
    const roleButtons = container.querySelectorAll('[role="button"]');
    expect(roleButtons.length).toBe(0);
  });

  it("renders posting rows with real keyboard-reachable open buttons", () => {
    render(
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

    // Two postings => two open-row buttons. The aria-label uses the
    // posting title (per the new implementation).
    const seniorBtn = screen.getByRole("button", { name: "Senior Engineer" });
    const juniorBtn = screen.getByRole("button", { name: "Junior Engineer" });
    expect(seniorBtn.tagName).toBe("BUTTON");
    expect(juniorBtn.tagName).toBe("BUTTON");
    expect(seniorBtn.getAttribute("tabindex")).not.toBe("-1");
    expect(juniorBtn.getAttribute("tabindex")).not.toBe("-1");
  });

  it("clicking SaveButton does NOT call onShowPosting (sibling, not nested)", () => {
    const onShow = vi.fn();
    render(
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
        onShowPosting={onShow}
        selectedPostingId={null}
      />,
    );

    const saveBtn = screen.getByTestId("save-button-p1");
    fireEvent.click(saveBtn);

    expect(saveClickMock).toHaveBeenCalledTimes(1);
    expect(saveClickMock).toHaveBeenCalledWith("p1");
    expect(onShow).not.toHaveBeenCalled();
  });

  it("clicking the row's open button DOES call onShowPosting", () => {
    const onShow = vi.fn();
    render(
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
        onShowPosting={onShow}
        selectedPostingId={null}
      />,
    );

    const seniorBtn = screen.getByRole("button", { name: "Senior Engineer" });
    fireEvent.click(seniorBtn);

    expect(onShow).toHaveBeenCalledTimes(1);
    expect(onShow).toHaveBeenCalledWith("p1");
    expect(saveClickMock).not.toHaveBeenCalled();
  });
});
