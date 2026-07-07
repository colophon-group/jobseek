import { describe, expect, it } from "vitest";
import { render, act } from "@testing-library/react";
import { useEffect } from "react";
import {
  SearchStateProvider,
  useSearchStateStore,
  shouldRestoreSnapshot,
  buildCacheKey,
} from "@/components/providers/SearchStateProvider";
import type { SearchResultCompany } from "@/lib/search";

/**
 * Regression test for #3354 — empty-snapshot poison.
 *
 * The #2989 strict cache-key match closed the "filtered snapshot leaks
 * onto unfiltered URL" leg of snapshot poisoning. But the no-filter ↔
 * no-filter leg (cacheKey ``||||`` on both sides) remained vulnerable:
 * if the snapshot's ``companies`` was empty (because the prior visit
 * hit a transient Typesense glitch / cached degraded prerender), every
 * subsequent /explore visit restored it, trapping the user on
 * "empty results AND no filters" even though the fresh
 * ``initialCompanies`` carried the top-10. The added poison guard in
 * ``shouldRestoreSnapshot`` rejects this exact shape.
 */

interface FakeSearchPageProps {
  initialKeywords: string[];
  initialCompanies: SearchResultCompany[];
  onResolved: (state: { keywords: string[]; companies: SearchResultCompany[] }) => void;
}

function FakeSearchPage({
  initialKeywords,
  initialCompanies,
  onResolved,
}: FakeSearchPageProps) {
  const { get, set } = useSearchStateStore();
  const cached = get();
  const currentCacheKey = buildCacheKey(initialKeywords, [], [], [], []);
  const shouldRestore = shouldRestoreSnapshot(cached, currentCacheKey);

  const resolvedKeywords = shouldRestore && cached ? cached.keywords : initialKeywords;
  const resolvedCompanies = shouldRestore && cached ? cached.companies : initialCompanies;

  useEffect(() => {
    onResolved({ keywords: resolvedKeywords, companies: resolvedCompanies });
  }, [onResolved, resolvedKeywords, resolvedCompanies]);

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

function ControlledHost({
  phase,
  onResolved,
  freshCompanies,
}: {
  phase: 1 | "between" | 2;
  onResolved: (state: { keywords: string[]; companies: SearchResultCompany[] }) => void;
  freshCompanies: SearchResultCompany[];
}) {
  if (phase === 1) {
    // Phase 1: SearchPage with NO filters AND 0 companies (simulating
    // a glitched initialCompanies from the cached prerender). State
    // initializes to companies=[], filters=[]. cacheKey="||||".
    return (
      <FakeSearchPage
        key="poisoned"
        initialKeywords={[]}
        initialCompanies={[]}
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
  return null;
}

describe("SearchStateProvider — #3354 empty-snapshot poison", () => {
  it("does NOT restore an empty-companies no-filter snapshot onto a fresh /explore mount", async () => {
    const observed: { keywords: string[]; companies: SearchResultCompany[] }[] = [];
    const onResolved = (s: typeof observed[number]) => observed.push(s);

    const top10 = Array.from({ length: 10 }, (_, i) =>
      makeCompany(`c-${i}`, `Company ${i}`),
    );

    let view: ReturnType<typeof render>;

    // Phase 1: FakeSearchPage mounts with EMPTY filters AND 0 companies,
    // mirroring the state SearchPage would land in if its first
    // ``initialCompanies`` were degraded — or if a ``runSearch`` returned
    // a transient empty result. Snapshot is not yet written.
    await act(async () => {
      view = render(
        <SearchStateProvider>
          <ControlledHost
            phase={1}
            onResolved={onResolved}
            freshCompanies={top10}
          />
        </SearchStateProvider>,
      );
    });
    expect(observed.at(-1)).toEqual({
      keywords: [],
      companies: [],
    });

    // Phase "between": unmount. The cleanup writes the poisoned snapshot
    // (cacheKey="||||", companies=[]). Without the intermediate phase
    // the cleanup would race with the next mount's ``get()`` call.
    await act(async () => {
      view!.rerender(
        <SearchStateProvider>
          <ControlledHost
            phase="between"
            onResolved={onResolved}
            freshCompanies={top10}
          />
        </SearchStateProvider>,
      );
    });

    // Phase 2: fresh /explore visit — URL has no filters, prerendered
    // ``initialCompanies`` has 10 top companies. With the poison guard
    // the snapshot's empty companies are rejected and the fresh
    // top-10 renders. Without it, the user would be trapped on
    // "empty + no filters" (the symptom in #3354).
    await act(async () => {
      view!.rerender(
        <SearchStateProvider>
          <ControlledHost
            phase={2}
            onResolved={onResolved}
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
