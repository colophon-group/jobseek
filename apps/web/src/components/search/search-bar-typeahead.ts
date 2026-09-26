"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CompanySuggestion } from "@/lib/actions/company";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import type { WorkMode } from "@/lib/search/types";
import type { SearchBarTermResult, SearchBarTypeaheadResults } from "@/lib/search/typeahead-contract";
import { runSearchBarTermTypeahead, runSearchBarTypeahead } from "@/lib/search/typeahead-runner";

type TermSuggestion<T> = T & { sourceTerm?: string };

/** Earlier terms plus one adjacent phrase, capped at four taxonomy probes. */
export function priorTermQueries(query: string): string[] {
  const words = query.trim().split(/[\s,\/|]+/).filter(Boolean);
  // In a long sentence the three words before the cursor are often
  // connective text or fragments of a larger title. The active term stays
  // fast; Jev handles the full sentence after its longer idle period.
  if (words.length > 7) return [];
  const earlier = words.slice(0, -1).filter((word) => word.length >= 2 && word.length <= 80);
  const singles = earlier.slice(-3);
  const terms = [...singles];
  if (singles.length === 2 || (singles.length === 3 && singles[2] === singles[2].toLowerCase())) {
    const pair = `${singles[singles.length - 2]} ${singles[singles.length - 1]}`;
    if (query.toLowerCase().includes(pair.toLowerCase())) terms.push(pair);
  }
  return [...new Set(terms)].slice(0, 4);
}

function distinctById<T extends { id: number }>(items: T[]): T[] {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function exactTermCandidate<T extends { name: string; slug: string; matchedName?: string }>(
  term: string, candidates: T[],
): T[] {
  const normalized = term.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "");
  const exact = candidates.filter((item) => [item.name, item.slug, item.matchedName]
    .some((name) => name?.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "") === normalized));
  // Ambiguous place/alias names remain a choice rather than silently taking
  // the highest-ranked hit; the dropdown still lets the user pick explicitly.
  return exact.slice(0, 2);
}

/**
 * Work-mode autocomplete entries - fixed three values, matched
 * client-side. Issue #2983. Synonyms mirror the server-side
 * tokenizer in `parseSearchFilters` so typing `wfh` here surfaces
 * Remote, and submitting "wfh engineer" picks up the same mode
 * via free-text parsing.
 */
const WORK_MODE_AUTOCOMPLETE: { value: WorkMode; aliases: string[] }[] = [
  {
    value: "remote",
    aliases: ["remote", "wfh", "work from home", "work-from-home"],
  },
  { value: "hybrid", aliases: ["hybrid"] },
  {
    value: "onsite",
    aliases: ["onsite", "on site", "on-site", "in office", "in-office"],
  },
];

/**
 * Returns the work-mode values whose name or one of the synonyms is
 * prefixed by the trimmed lower-cased user input. Returns an empty
 * array for inputs shorter than 2 characters or with no match.
 */
export function matchWorkModes(query: string, alreadySelected: ReadonlySet<WorkMode>): WorkMode[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2) return [];
  const out: WorkMode[] = [];
  for (const entry of WORK_MODE_AUTOCOMPLETE) {
    if (alreadySelected.has(entry.value)) continue;
    if (entry.aliases.some((alias) => alias.startsWith(q))) {
      out.push(entry.value);
    }
  }
  return out;
}

type TypeaheadResults = {
  sourceQuery: string;
  locationResults: TermSuggestion<LocationSuggestion>[];
  companyResults: CompanySuggestion[];
  occupationResults: TermSuggestion<TaxonomySuggestion>[];
  seniorityResults: TermSuggestion<TaxonomySuggestion>[];
  technologyResults: TermSuggestion<TaxonomySuggestion>[];
};

type TypeaheadFilters = {
  companyId?: string;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  languages?: string[];
};

export function useSearchBarTypeahead({
  lang,
  userLat,
  userLng,
  companyId,
  scopedToCompany,
  selectedLocationIds,
  selectedLocationSlugs,
  selectedOccupationIds,
  selectedSeniorityIds,
  selectedTechnologyIds,
  selectedWorkModes,
  baseKeywords,
  baseLocationIds,
  baseOccupationIds,
  baseSeniorityIds,
  baseTechnologyIds,
  baseLanguages,
  onOpen,
  onClose,
  onResetActiveIndex,
}: {
  lang: string;
  userLat?: number;
  userLng?: number;
  companyId?: string;
  scopedToCompany: boolean;
  selectedLocationIds: ReadonlySet<number> | null;
  selectedLocationSlugs: ReadonlySet<string>;
  selectedOccupationIds: ReadonlySet<number>;
  selectedSeniorityIds: ReadonlySet<number>;
  selectedTechnologyIds: ReadonlySet<number>;
  selectedWorkModes: ReadonlySet<WorkMode>;
  baseKeywords?: string[];
  baseLocationIds?: number[];
  baseOccupationIds?: number[];
  baseSeniorityIds?: number[];
  baseTechnologyIds?: number[];
  baseLanguages?: string[];
  onOpen: () => void;
  onClose: () => void;
  onResetActiveIndex: () => void;
}): TypeaheadResults & {
  clearResults: () => void;
  fetchSuggestions: (query: string) => void;
} {
  const [locationResults, setLocationResults] = useState<TermSuggestion<LocationSuggestion>[]>([]);
  const [companyResults, setCompanyResults] = useState<CompanySuggestion[]>([]);
  const [occupationResults, setOccupationResults] = useState<TermSuggestion<TaxonomySuggestion>[]>([]);
  const [seniorityResults, setSeniorityResults] = useState<TermSuggestion<TaxonomySuggestion>[]>([]);
  const [technologyResults, setTechnologyResults] = useState<TermSuggestion<TaxonomySuggestion>[]>([]);
  const [sourceQuery, setSourceQuery] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestGenerationRef = useRef(0);

  const clearResultState = useCallback(() => {
    setSourceQuery("");
    setLocationResults([]);
    setCompanyResults([]);
    setOccupationResults([]);
    setSeniorityResults([]);
    setTechnologyResults([]);
  }, []);

  const clearResults = useCallback(() => {
    requestGenerationRef.current += 1;
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    clearResultState();
  }, [clearResultState]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      requestGenerationRef.current += 1;
    };
  }, []);

  const fetchSuggestions = useCallback(
    (query: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);

      if (query.trim().length < 2) {
        clearResults();
        onClose();
        return;
      }

      // Show fast matches for the term the user is editing. Whole-query
      // routing is a separate, slower request after a longer idle pause.
      const termQuery = query.trim().split(/[\s,\/|]+/).filter(Boolean).at(-1) ?? query.trim();
      const priorTerms = priorTermQueries(query);

      const hasWorkModeMatches = matchWorkModes(termQuery, selectedWorkModes).length > 0;
      if (hasWorkModeMatches) {
        onOpen();
      }

      // Every accepted input owns a generation. Older promises may continue
      // (server actions cannot be cancelled reliably), but their completion
      // is ignored before any state or dropdown callbacks are touched.
      const generation = requestGenerationRef.current + 1;
      requestGenerationRef.current = generation;

      const baseFilters: TypeaheadFilters = {
        companyId,
        keywords: baseKeywords,
        locationIds: baseLocationIds,
        occupationIds: baseOccupationIds,
        seniorityIds: baseSeniorityIds,
        technologyIds: baseTechnologyIds,
        languages: baseLanguages,
      };
      const filtersExcluding = (
        omit: "locationIds" | "occupationIds" | "seniorityIds" | "technologyIds",
      ) => {
        const { [omit]: _omitted, ...rest } = baseFilters;
        return rest;
      };

      debounceRef.current = setTimeout(() => {
        debounceRef.current = null;
        onResetActiveIndex();

        const common = {
          locale: lang,
          userLat,
          userLng,
          locationFilters: filtersExcluding("locationIds"),
          occupationFilters: filtersExcluding("occupationIds"),
          seniorityFilters: filtersExcluding("seniorityIds"),
          technologyFilters: filtersExcluding("technologyIds"),
        };
        const activeRequest: Promise<SearchBarTypeaheadResults | null> =
          priorTerms.length > 0 && hasWorkModeMatches
            ? Promise.resolve(null)
            : runSearchBarTypeahead({
                query: termQuery,
                includeCompanies: !scopedToCompany,
                ...common,
              }).catch(() => null);
        // Older terms are useful once the user pauses on the active term.
        // Delay their batch by another 200 ms to avoid a taxonomy batch
        // on each moderately paced keystroke of a longer sentence.
        const priorRequest: Promise<SearchBarTermResult[]> = priorTerms.length
          ? new Promise((resolve) => setTimeout(() => {
              if (requestGenerationRef.current !== generation) { resolve([]); return; }
              void runSearchBarTermTypeahead({ terms: priorTerms, ...common })
                .then(resolve, () => resolve([]));
            }, 200))
          : Promise.resolve([]);
        const apply = (results: SearchBarTypeaheadResults | null, prior: SearchBarTermResult[]) => {
            if (requestGenerationRef.current !== generation) return;
            if (!results && prior.length === 0) {
              clearResultState();
              if (hasWorkModeMatches) onOpen();
              else onClose();
              return;
            }
            // When the active word already names a taxonomy item exactly,
            // suppress loose alias/prefix hits from other dimensions. For
            // example, "designer" should not advertise Staff merely because
            // a Staff alias contains the word designer.
            const activeCategories: Array<Array<{ name: string; slug: string; matchedName?: string }>> = results ? [results.locations, results.occupations,
              results.seniorities, results.technologies] : [];
            const hasActiveExact = priorTerms.length > 0 && activeCategories.some((items) =>
              exactTermCandidate(termQuery, items).length > 0);
            const active = <T extends { name: string; slug: string; matchedName?: string }>(items: T[] | undefined): T[] =>
              hasActiveExact ? exactTermCandidate(termQuery, items ?? []) : (items ?? []);
            const coveredByPhrase = new Set(prior.filter((entry) => entry.term.includes(" ") &&
              [entry.locations, entry.occupations, entry.seniorities, entry.technologies]
                .some((items) => exactTermCandidate(entry.term, items as Array<{ name: string; slug: string; matchedName?: string }>).length > 0))
              .flatMap((entry) => entry.term.toLowerCase().split(/\s+/)));
            const usefulPrior = prior.filter((entry) => entry.term.includes(" ") ||
              !coveredByPhrase.has(entry.term.toLowerCase()));
            const locations = distinctById([
              ...usefulPrior.flatMap((entry) => exactTermCandidate(entry.term, entry.locations).map((item) => ({ ...item, sourceTerm: entry.term }))),
              ...active(results?.locations).map((item) => ({ ...item, sourceTerm: termQuery })),
            ]).filter((item) => selectedLocationIds
              ? !selectedLocationIds.has(item.id) : !selectedLocationSlugs.has(item.slug));
            const occupations = distinctById([
              ...usefulPrior.flatMap((entry) => exactTermCandidate(entry.term, entry.occupations).map((item) => ({ ...item, sourceTerm: entry.term }))),
              ...active(results?.occupations).map((item) => ({ ...item, sourceTerm: termQuery })),
            ]).filter(
              (item) => !selectedOccupationIds.has(item.id),
            );
            const seniorities = distinctById([
              ...usefulPrior.flatMap((entry) => exactTermCandidate(entry.term, entry.seniorities).map((item) => ({ ...item, sourceTerm: entry.term }))),
              ...active(results?.seniorities).map((item) => ({ ...item, sourceTerm: termQuery })),
            ]).filter(
              (item) => !selectedSeniorityIds.has(item.id),
            );
            const technologies = distinctById([
              ...usefulPrior.flatMap((entry) => exactTermCandidate(entry.term, entry.technologies).map((item) => ({ ...item, sourceTerm: entry.term }))),
              ...active(results?.technologies).map((item) => ({ ...item, sourceTerm: termQuery })),
            ]).filter(
              (item) => !selectedTechnologyIds.has(item.id),
            );

            setLocationResults(locations);
            setSourceQuery(termQuery);
            setCompanyResults(scopedToCompany || hasActiveExact ? [] : (results?.companies ?? []));
            setOccupationResults(occupations);
            setSeniorityResults(seniorities);
            setTechnologyResults(technologies);

            if (
              !scopedToCompany ||
              hasWorkModeMatches ||
              locations.length > 0 ||
              occupations.length > 0 ||
              seniorities.length > 0 ||
              technologies.length > 0
            ) {
              onOpen();
            } else {
              onClose();
            }
        };
        if (priorTerms.length > 0) {
          void activeRequest.then((results) => { if (results) apply(results, []); });
        }
        void Promise.all([activeRequest, priorRequest]).then(([results, prior]) => apply(results, prior));
      }, 200);
    },
    [
      baseKeywords,
      baseLanguages,
      baseLocationIds,
      baseOccupationIds,
      baseSeniorityIds,
      baseTechnologyIds,
      clearResultState,
      clearResults,
      companyId,
      lang,
      onClose,
      onOpen,
      onResetActiveIndex,
      scopedToCompany,
      selectedLocationIds,
      selectedLocationSlugs,
      selectedOccupationIds,
      selectedSeniorityIds,
      selectedTechnologyIds,
      selectedWorkModes,
      userLat,
      userLng,
    ],
  );

  return {
    locationResults,
    sourceQuery,
    companyResults,
    occupationResults,
    seniorityResults,
    technologyResults,
    clearResults,
    fetchSuggestions,
  };
}
