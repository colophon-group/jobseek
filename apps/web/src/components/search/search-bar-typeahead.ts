"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { suggestCompanies } from "@/lib/actions/company";
import type { CompanySuggestion } from "@/lib/actions/company";
import type { LocationSuggestion } from "@/lib/actions/locations";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import type { WorkMode } from "@/lib/search/types";
import {
  runSuggestLocations as suggestLocations,
  runSuggestOccupations as suggestOccupations,
  runSuggestSeniorities as suggestSeniorities,
  runSuggestTechnologies as suggestTechnologies,
} from "@/lib/search/typeahead-runner";

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

  const clearResults = useCallback(() => {
    setLocationResults([]);
    setCompanyResults([]);
    setOccupationResults([]);
    setSeniorityResults([]);
    setTechnologyResults([]);
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
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

      if (matchWorkModes(query, selectedWorkModes).length > 0) {
        onOpen();
      }

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
      const setFilteredResults = <T,>(
        promise: Promise<T[]>,
        filter: (items: T[]) => T[],
        setResults: (items: T[]) => void,
      ) => {
        promise.then((items) => {
          const filtered = filter(items);
          setResults(filtered);
          if (filtered.length > 0) onOpen();
        });
      };

      debounceRef.current = setTimeout(() => {
        onResetActiveIndex();

        if (scopedToCompany) {
          setCompanyResults([]);
        } else {
          suggestCompanies({ query }).then((companies) => {
            setCompanyResults(companies);
            if (companies.length > 0 || query.trim().length >= 2) {
              onOpen();
            }
          });
        }

        setFilteredResults(
          suggestLocations({
            query,
            locale: lang,
            userLat,
            userLng,
            filters: filtersExcluding("locationIds"),
          }),
          (locs) =>
            selectedLocationIds
              ? locs.filter((r) => !selectedLocationIds.has(r.id))
              : locs.filter((r) => !selectedLocationSlugs.has(r.slug)),
          setLocationResults,
        );
        setFilteredResults(
          suggestOccupations({
            query,
            locale: lang,
            filters: filtersExcluding("occupationIds"),
          }),
          (occs) => occs.filter((r) => !selectedOccupationIds.has(r.id)),
          setOccupationResults,
        );
        setFilteredResults(
          suggestSeniorities({
            query,
            locale: lang,
            filters: filtersExcluding("seniorityIds"),
          }),
          (sens) => sens.filter((r) => !selectedSeniorityIds.has(r.id)),
          setSeniorityResults,
        );
        setFilteredResults(
          suggestTechnologies({
            query,
            locale: lang,
            filters: filtersExcluding("technologyIds"),
          }),
          (techs) => techs.filter((r) => !selectedTechnologyIds.has(r.id)),
          setTechnologyResults,
        );
      }, 200);
    },
    [
      baseKeywords,
      baseLanguages,
      baseLocationIds,
      baseOccupationIds,
      baseSeniorityIds,
      baseTechnologyIds,
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
