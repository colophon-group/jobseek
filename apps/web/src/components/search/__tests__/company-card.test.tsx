/**
 * Tests for CompanyCard memoization (issue #3198).
 *
 * Before the fix, every filter mutation in `SearchPage` re-rendered every
 * `CompanyCard` in `SearchResults` — there was no `React.memo`, and the
 * filter props (`keywords`, `locationIds`, `locations`, ...) were
 * reconstructed inline at the parent (e.g. `locations.map((l) => l.id)`)
 * so even a default `React.memo` shallow check would have failed.
 *
 * The fix is two-step:
 *   1. Wrap `CompanyCard` in `React.memo(impl, companyCardPropsEqual)`
 *      where the comparator deep-equals each array prop by id / value.
 *   2. Stabilize the array-ish props at the parent (`useMemo`'d
 *      `locationIds`, `useCallback`'d `handleOpenPosting`).
 *
 * Two test groups:
 *
 * - Unit tests on `companyCardPropsEqual` — the comparator is pure,
 *   exhaustive coverage is cheap, and it's the load-bearing piece:
 *   returning `true` when a prop actually changed = stale UI.
 *
 * - Render-count integration test via `React.Profiler` — confirms the
 *   `memo` wrapper actually skips renders when filter values are
 *   logically identical but referentially fresh (the realistic
 *   parent-render scenario described in #3198).
 */
import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

// --- Mocks for CompanyCard's heavy subtree -----------------------------------
// Keep them minimal — we only need the component to mount and re-render
// deterministically. None of these affect the memo decision (which is
// what's actually under test).

vi.mock("next/navigation", () => ({
  useParams: () => ({ lang: "en" }),
}));

vi.mock("next/link", () => ({
  // `prefetch` is a `next/link`-specific prop and must NOT be forwarded
  // to the underlying `<a>` (React warns about non-boolean attrs on DOM).
  default: ({ children, href, prefetch: _prefetch, ...props }: Record<string, unknown>) => (
    <a href={href as string} {...props}>{children as React.ReactNode}</a>
  ),
}));

// CompanyIcon is rendered exactly once per CompanyCard subtree, so we use
// it as a render-counter probe. When the memo skips, the icon won't be
// re-invoked. Counts are keyed by `alt` (set by CompanyCard to
// `company.name`), so each card has its own counter.
const iconRenderCounts = new Map<string, number>();
vi.mock("@/components/CompanyIcon", () => ({
  CompanyIcon: ({ alt }: { alt: string }) => {
    iconRenderCounts.set(alt, (iconRenderCounts.get(alt) ?? 0) + 1);
    return <span data-testid="company-icon" data-name={alt} />;
  },
}));

vi.mock("@/components/InfiniteScrollSentinel", () => ({
  InfiniteScrollSentinel: () => null,
}));

vi.mock("@/components/TruncationPrompt", () => ({
  TruncationPrompt: () => null,
}));

vi.mock("@/components/TrackingDot", () => ({
  TrackingDot: () => null,
}));

vi.mock("@/components/PendingJobWarning", () => ({
  PendingJobIcon: () => null,
}));

vi.mock("@/components/search/save-button", () => ({
  SaveButton: () => null,
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
  loadMorePostings: vi.fn().mockResolvedValue({ postings: [], truncated: false }),
}));

vi.mock("@/lib/time", () => ({
  timeAgoShort: () => "1d",
}));

vi.mock("@/lib/search/query-params", async () => {
  // Keep the type-exporting module intact while overriding `buildFilteredPath`.
  return {
    buildFilteredPath: () => "/en/company/acme",
  };
});

// Import AFTER mocks so vi.mock hoisting sees them.
import { CompanyCard, companyCardPropsEqual } from "../company-card";
import type { SearchResultCompany } from "@/lib/search";
import type {
  SerializableLocation,
  SerializableOccupation,
  SerializableSeniority,
  SerializableTechnology,
} from "@/lib/search/query-params";

function makeResult(id: string, name = `Company ${id}`): SearchResultCompany {
  return {
    company: { id, name, slug: `company-${id}`, icon: null },
    activeMatches: 1,
    yearMatches: 1,
    postings: [
      {
        id: `p-${id}`,
        title: `Posting ${id}`,
        firstSeenAt: "2026-05-01T00:00:00Z",
        relevanceScore: 1,
        locations: [],
        isActive: true,
      },
    ],
  };
}

function loc(id: number, name = `Loc ${id}`): SerializableLocation {
  return { id, slug: `loc-${id}`, name, type: "city" };
}
function occ(id: number): SerializableOccupation {
  return { id, slug: `occ-${id}`, name: `Occ ${id}` };
}
function sen(id: number): SerializableSeniority {
  return { id, slug: `sen-${id}`, name: `Sen ${id}` };
}
function tech(id: number): SerializableTechnology {
  return { id, slug: `tech-${id}`, name: `Tech ${id}` };
}

// =============================================================================
// Comparator unit tests
// =============================================================================

describe("companyCardPropsEqual", () => {
  const baseResult = makeResult("1");
  const noop = () => {};

  function baseProps() {
    return {
      result: baseResult,
      keywords: ["engineer"],
      locationIds: [10, 20],
      locations: [loc(10), loc(20)],
      occupations: [occ(1)],
      seniorities: [sen(1)],
      technologies: [tech(1)],
      employmentTypes: ["fulltime"],
      workMode: ["remote" as const],
      salaryMinEur: 50000,
      salaryMaxEur: 100000,
      experienceMin: 1,
      experienceMax: 5,
      languages: ["en"],
      onShowPosting: noop,
      selectedPostingId: null,
    };
  }

  it("returns true when every prop is referentially identical", () => {
    const p = baseProps();
    expect(companyCardPropsEqual(p, p)).toBe(true);
  });

  it("returns true when arrays are reconstructed with the same contents", () => {
    const a = baseProps();
    // Reconstruct every array — same contents, fresh references. This is the
    // exact scenario described in #3198 (parent recomputing arrays inline).
    const b = {
      ...a,
      keywords: ["engineer"],
      locationIds: [10, 20],
      locations: [loc(10), loc(20)],
      occupations: [occ(1)],
      seniorities: [sen(1)],
      technologies: [tech(1)],
      employmentTypes: ["fulltime"],
      workMode: ["remote" as const],
      languages: ["en"],
    };
    expect(companyCardPropsEqual(a, b)).toBe(true);
  });

  it("returns false when `result` reference changes (new search response)", () => {
    const a = baseProps();
    const b = { ...a, result: makeResult("1") }; // same id, different object
    expect(companyCardPropsEqual(a, b)).toBe(false);
  });

  it("returns false when keywords differ", () => {
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, keywords: ["engineer", "rust"] }),
    ).toBe(false);
    expect(
      companyCardPropsEqual(a, { ...a, keywords: ["frontend"] }),
    ).toBe(false);
  });

  it("returns false when locationIds differ (length OR content)", () => {
    const a = baseProps();
    expect(companyCardPropsEqual(a, { ...a, locationIds: [10] })).toBe(false);
    expect(companyCardPropsEqual(a, { ...a, locationIds: [10, 30] })).toBe(false);
  });

  it("returns false when locations differ by id (even if same length)", () => {
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, locations: [loc(10), loc(99)] }),
    ).toBe(false);
  });

  it("returns true when locations have the same ids but different names", () => {
    // Name/slug don't change at runtime for a given id in this app — taxonomy
    // is server-rendered — so the comparator only checks id. If this rule ever
    // changes, this test should fail and force a comparator update.
    const a = baseProps();
    const b = {
      ...a,
      locations: [loc(10, "Berlin renamed"), loc(20, "Munich renamed")],
    };
    expect(companyCardPropsEqual(a, b)).toBe(true);
  });

  it("returns false when occupations / seniorities / technologies differ by id", () => {
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, occupations: [occ(99)] }),
    ).toBe(false);
    expect(
      companyCardPropsEqual(a, { ...a, seniorities: [sen(99)] }),
    ).toBe(false);
    expect(
      companyCardPropsEqual(a, { ...a, technologies: [tech(99)] }),
    ).toBe(false);
  });

  it("returns false when employmentTypes / workMode / languages differ", () => {
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, employmentTypes: ["parttime"] }),
    ).toBe(false);
    expect(
      companyCardPropsEqual(a, { ...a, workMode: ["onsite" as const] }),
    ).toBe(false);
    expect(companyCardPropsEqual(a, { ...a, languages: ["de"] })).toBe(false);
  });

  it("returns false when any numeric primitive prop changes", () => {
    const a = baseProps();
    expect(companyCardPropsEqual(a, { ...a, salaryMinEur: 60000 })).toBe(false);
    expect(companyCardPropsEqual(a, { ...a, salaryMaxEur: 120000 })).toBe(false);
    expect(companyCardPropsEqual(a, { ...a, experienceMin: 2 })).toBe(false);
    expect(companyCardPropsEqual(a, { ...a, experienceMax: 6 })).toBe(false);
  });

  it("returns false when selectedPostingId changes (highlight state)", () => {
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, selectedPostingId: "p-1" }),
    ).toBe(false);
  });

  it("returns false when onShowPosting identity changes", () => {
    // We intentionally do NOT mask function identity changes — that would
    // hide stale-closure bugs. Callers stabilize with `useCallback`.
    const a = baseProps();
    expect(
      companyCardPropsEqual(a, { ...a, onShowPosting: () => {} }),
    ).toBe(false);
  });

  it("handles undefined optional array props symmetrically", () => {
    const a = { ...baseProps(), locationIds: undefined, languages: undefined };
    const b = { ...baseProps(), locationIds: undefined, languages: undefined };
    expect(companyCardPropsEqual(a, b)).toBe(true);

    // One side defined, other undefined -> different
    expect(
      companyCardPropsEqual(a, { ...a, locationIds: [10] }),
    ).toBe(false);
  });
});

// =============================================================================
// Render-count integration test
// =============================================================================
//
// Wraps a stable `CompanyCard` in a parent that toggles ITS OWN state on
// every interaction (mimicking `SearchPage` toggling filters), while
// passing freshly-reconstructed arrays / identical-by-value callbacks to
// the card. With memo + stable callback, the card should render exactly
// ONCE. Without memo it would render twice.
//
// Render-count tracking uses the mocked `CompanyIcon` (see top of file):
// every CompanyCard render invokes its child CompanyIcon, so the icon's
// invocation count IS the card's render count. We don't use Profiler
// here because Profiler fires onRender even when a child memoizes-bails
// (the Profiler itself still commits when its parent does), so it can't
// distinguish "child re-rendered" from "child bailed but parent did
// not".

describe("CompanyCard memoization (render count)", () => {
  it("renders only ONCE when parent re-renders with same prop values (10 cards)", () => {
    iconRenderCounts.clear();
    // Stable references reused across renders, captured outside Harness.
    const results = Array.from({ length: 10 }, (_, i) => makeResult(String(i + 1)));
    const stableHandlerRef = () => {};

    function Harness() {
      // Parent state — toggle to force a re-render.
      const [tick, setTick] = useState(0);

      // Same logical values, fresh references each render. Mirrors the
      // pre-#3198 inline-construction pattern in search-page.tsx
      // (e.g. `locationIds={locations.map((l) => l.id)}`).
      const keywords = ["engineer"];
      const locationIds = [10, 20];
      const locations = [loc(10), loc(20)];
      const occupations = [occ(1)];
      const seniorities = [sen(1)];
      const technologies = [tech(1)];

      return (
        <>
          <button onClick={() => setTick((t) => t + 1)}>tick {tick}</button>
          {results.map((result) => (
            <CompanyCard
              key={result.company.id}
              result={result}
              keywords={keywords}
              locationIds={locationIds}
              locations={locations}
              occupations={occupations}
              seniorities={seniorities}
              technologies={technologies}
              employmentTypes={[]}
              workMode={[]}
              salaryMinEur={undefined}
              salaryMaxEur={undefined}
              experienceMin={undefined}
              experienceMax={undefined}
              languages={["en"]}
              onShowPosting={stableHandlerRef}
              selectedPostingId={null}
            />
          ))}
        </>
      );
    }

    render(<Harness />);

    // Initial mount: every card rendered once.
    for (const r of results) {
      expect(iconRenderCounts.get(r.company.name)).toBe(1);
    }

    // Force a parent re-render. With memo + correct comparator, no card
    // should re-render — they all should hit the equality short-circuit.
    fireEvent.click(screen.getByText(/^tick/));

    for (const r of results) {
      expect(
        iconRenderCounts.get(r.company.name),
        `card ${r.company.name} re-rendered after parent re-render with identical-by-value props`,
      ).toBe(1);
    }

    // Sanity: a SECOND parent re-render also stays at 1 (i.e. not flaky).
    fireEvent.click(screen.getByText(/^tick/));
    for (const r of results) {
      expect(iconRenderCounts.get(r.company.name)).toBe(1);
    }
  });

  it("DOES re-render when a card's own filter prop genuinely changes", () => {
    // Sanity check on the negative case — confirm the memo doesn't
    // accidentally over-skip when there's a real change.
    iconRenderCounts.clear();
    const stableHandler = () => {};
    const result = makeResult("solo");

    function Harness({ kw }: { kw: string[] }) {
      return (
        <CompanyCard
          result={result}
          keywords={kw}
          locationIds={[]}
          locations={[]}
          occupations={[]}
          seniorities={[]}
          technologies={[]}
          employmentTypes={[]}
          workMode={[]}
          languages={["en"]}
          onShowPosting={stableHandler}
          selectedPostingId={null}
        />
      );
    }

    const { rerender } = render(<Harness kw={["a"]} />);
    expect(iconRenderCounts.get(result.company.name)).toBe(1);

    // Same value — memo skips.
    rerender(<Harness kw={["a"]} />);
    expect(iconRenderCounts.get(result.company.name)).toBe(1);

    // Different value — memo lets it through.
    rerender(<Harness kw={["a", "b"]} />);
    expect(iconRenderCounts.get(result.company.name)).toBe(2);
  });
});
