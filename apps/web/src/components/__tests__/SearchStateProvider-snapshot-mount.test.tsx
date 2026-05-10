import { describe, expect, it } from "vitest";
import { render, act } from "@testing-library/react";
import { useEffect } from "react";
import {
  SearchStateProvider,
  useSearchStateStore,
  shouldRestoreSnapshot,
  buildCacheKey,
} from "@/components/SearchStateProvider";
import type { SearchResultCompany } from "@/lib/search";

/**
 * End-to-end mount test for #2989. Exercises the actual
 * ``SearchStateProvider`` ref store via a minimal consumer that mirrors
 * SearchPage's lifecycle:
 *   - On mount, decides whether to restore from the cached snapshot.
 *   - On unmount, persists its current state into the snapshot.
 *
 * The bug pre-fix: when the cached snapshot held a previous filtered
 * search's keywords + empty companies, a fresh ``/explore`` mount (no
 * URL filters) would restore the snapshot and surface ``ZeroResults``.
 *
 * The fix: ``shouldRestoreSnapshot`` requires a strict cache-key match,
 * so an unfiltered URL never inherits a filtered snapshot's state.
 */

interface FakeSearchPageProps {
  initialKeywords: string[];
  initialCompanies: SearchResultCompany[];
  /** Surface the resolved (post-restore) state for assertions. */
  onResolved: (state: { keywords: string[]; companies: SearchResultCompany[] }) => void;
}

function FakeSearchPage({
  initialKeywords,
  initialCompanies,
  onResolved,
}: FakeSearchPageProps) {
  const { get, set } = useSearchStateStore();

  // Mount-time restore decision (mirrors SearchPage lines 82-93 in
  // app/[lang]/(app)/explore/search-page.tsx).
  const cached = get();
  const currentCacheKey = buildCacheKey(initialKeywords, [], [], [], []);
  const shouldRestore = shouldRestoreSnapshot(cached, currentCacheKey);

  const resolvedKeywords = shouldRestore && cached ? cached.keywords : initialKeywords;
  const resolvedCompanies = shouldRestore && cached ? cached.companies : initialCompanies;

  // Surface the resolved state to the test.
  useEffect(() => {
    onResolved({ keywords: resolvedKeywords, companies: resolvedCompanies });
  }, [onResolved, resolvedKeywords, resolvedCompanies]);

  // Unmount-time snapshot save (mirrors SearchPage lines 266-294).
  // Empty deps array — runs cleanup once on unmount. Captures the
  // resolved values from the closure, exactly like SearchPage does.
  useEffect(() => {
    return () => {
      set({
        keywords: resolvedKeywords,
        locations: [],
        occupations: [],
        seniorities: [],
        technologies: [],
        workMode: [],
        salaryMinEur: undefined,
        salaryMaxEur: undefined,
        salaryCurrency: "EUR",
        experienceMin: undefined,
        experienceMax: undefined,
        companies: resolvedCompanies,
        totalCompanies: resolvedCompanies.length,
        showPostingId: null,
        scrollY: 0,
        cacheKey: buildCacheKey(resolvedKeywords, [], [], [], []),
      });
    };
  }, [set, resolvedCompanies, resolvedKeywords]);

  return null;
}

function makeCompany(id: string, name: string): SearchResultCompany {
  return {
    company: { id, name, slug: name.toLowerCase(), icon: null },
    activeMatches: 1,
    yearMatches: 1,
    postings: [],
  };
}

/**
 * Host that toggles between two phases. Phase 1 mounts a FakeSearchPage
 * configured to look like a 0-result filtered search. The host then
 * sets ``phase=null`` to fully unmount that page (so the cleanup fires
 * and writes the snapshot). Phase 2 mounts a fresh FakeSearchPage with
 * no filters — the question the test answers is whether mount #2
 * inherits state from the saved snapshot.
 */
function ControlledHost({
  phase,
  onResolved,
  filteredCompanies,
  freshCompanies,
}: {
  phase: 1 | "between" | 2;
  onResolved: (state: { keywords: string[]; companies: SearchResultCompany[] }) => void;
  filteredCompanies: SearchResultCompany[];
  freshCompanies: SearchResultCompany[];
}) {
  if (phase === 1) {
    return (
      <FakeSearchPage
        key="filtered"
        initialKeywords={["rare-keyword"]}
        initialCompanies={filteredCompanies}
        onResolved={onResolved}
      />
    );
  }
  if (phase === 2) {
    return (
      <FakeSearchPage
        key="fresh"
        initialKeywords={[]}
        initialCompanies={freshCompanies}
        onResolved={onResolved}
      />
    );
  }
  // "between": no FakeSearchPage mounted — forces full unmount of
  // phase 1's component so its cleanup runs.
  return null;
}

describe("SearchStateProvider — mount/unmount snapshot lifecycle (#2989)", () => {
  it("does NOT restore a filtered snapshot onto a fresh /explore mount", async () => {
    const observed: { keywords: string[]; companies: SearchResultCompany[] }[] = [];
    const onResolved = (s: typeof observed[number]) => observed.push(s);

    const top10 = Array.from({ length: 10 }, (_, i) =>
      makeCompany(`c-${i}`, `Company ${i}`),
    );

    let view: ReturnType<typeof render>;

    // Phase 1: filtered search, 0 results.
    await act(async () => {
      view = render(
        <SearchStateProvider>
          <ControlledHost
            phase={1}
            onResolved={onResolved}
            filteredCompanies={[]}
            freshCompanies={top10}
          />
        </SearchStateProvider>,
      );
    });
    expect(observed.at(-1)).toEqual({
      keywords: ["rare-keyword"],
      companies: [],
    });

    // Phase "between": NO child mounted. This forces React to unmount
    // the phase-1 FakeSearchPage and run its cleanup — which writes
    // the poisoned snapshot. Without this intermediate phase, the
    // cleanup would race with the phase-2 mount and the snapshot
    // wouldn't be visible to phase 2's `get()` call.
    await act(async () => {
      view!.rerender(
        <SearchStateProvider>
          <ControlledHost
            phase="between"
            onResolved={onResolved}
            filteredCompanies={[]}
            freshCompanies={top10}
          />
        </SearchStateProvider>,
      );
    });

    // Phase 2: fresh /explore visit — URL has no filters, prerendered
    // initialCompanies has 10 top companies. With the fix, mount #2
    // sees the (poisoned) snapshot but rejects it via the strict
    // cache-key check, falling back to the prerendered top 10. Pre-
    // fix, mount #2 would restore the snapshot's empty
    // ``companies`` and surface ``ZeroResults``.
    await act(async () => {
      view!.rerender(
        <SearchStateProvider>
          <ControlledHost
            phase={2}
            onResolved={onResolved}
            filteredCompanies={[]}
            freshCompanies={top10}
          />
        </SearchStateProvider>,
      );
    });

    const final = observed.at(-1);
    expect(final?.keywords).toEqual([]);
    expect(final?.companies).toHaveLength(10);
  });
});
