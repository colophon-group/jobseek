"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { CompanySuggestion } from "@/lib/actions/company";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import type { WorkMode } from "@/lib/search/types";
import { runSearchBarTypeahead } from "@/lib/search/typeahead-runner";

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
  locationResults: LocationSuggestion[];
  companyResults: CompanySuggestion[];
  occupationResults: TaxonomySuggestion[];
  seniorityResults: TaxonomySuggestion[];
  technologyResults: TaxonomySuggestion[];
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
  const [locationResults, setLocationResults] = useState<LocationSuggestion[]>([]);
  const [companyResults, setCompanyResults] = useState<CompanySuggestion[]>([]);
  const [occupationResults, setOccupationResults] = useState<TaxonomySuggestion[]>([]);
  const [seniorityResults, setSeniorityResults] = useState<TaxonomySuggestion[]>([]);
  const [technologyResults, setTechnologyResults] = useState<TaxonomySuggestion[]>([]);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestGenerationRef = useRef(0);

  const clearResultState = useCallback(() => {
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

      const hasWorkModeMatches = matchWorkModes(query, selectedWorkModes).length > 0;
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

        void runSearchBarTypeahead({
          query,
          locale: lang,
          userLat,
          userLng,
          includeCompanies: !scopedToCompany,
          locationFilters: filtersExcluding("locationIds"),
          occupationFilters: filtersExcluding("occupationIds"),
          seniorityFilters: filtersExcluding("seniorityIds"),
          technologyFilters: filtersExcluding("technologyIds"),
        })
          .then((results) => {
            if (requestGenerationRef.current !== generation) return;

            const locations = selectedLocationIds
              ? results.locations.filter((item) => !selectedLocationIds.has(item.id))
              : results.locations.filter((item) => !selectedLocationSlugs.has(item.slug));
            const occupations = results.occupations.filter(
              (item) => !selectedOccupationIds.has(item.id),
            );
            const seniorities = results.seniorities.filter(
              (item) => !selectedSeniorityIds.has(item.id),
            );
            const technologies = results.technologies.filter(
              (item) => !selectedTechnologyIds.has(item.id),
            );

            setLocationResults(locations);
            setCompanyResults(scopedToCompany ? [] : results.companies);
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
          })
          .catch(() => {
            if (requestGenerationRef.current !== generation) return;
            // Explicit failure policy: retain prior results while a current
            // query is loading, then clear them if every direct/server
            // fallback path for that generation fails. Client-only work-mode
            // matches remain visible because they need no network response.
            clearResultState();
            if (hasWorkModeMatches) onOpen();
            else onClose();
          });
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
    companyResults,
    occupationResults,
    seniorityResults,
    technologyResults,
    clearResults,
    fetchSuggestions,
  };
}
