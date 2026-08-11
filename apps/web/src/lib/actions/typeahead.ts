"use server";

import { suggestCompanies } from "@/lib/services/company";
import { suggestLocations } from "@/lib/services/locations";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/services/taxonomy";
import type {
  SearchBarTypeaheadParams,
  SearchBarTypeaheadResults,
} from "@/lib/search/typeahead-contract";

/**
 * One server-action boundary for a complete search-bar suggestion query.
 * The service functions retain their independent cache slots while the
 * browser pays for one action request instead of five.
 */
export async function suggestSearchBarTypeahead(
  params: SearchBarTypeaheadParams,
): Promise<SearchBarTypeaheadResults> {
  const [locations, companies, occupations, seniorities, technologies] =
    await Promise.all([
      suggestLocations({
        query: params.query,
        locale: params.locale,
        userLat: params.userLat,
        userLng: params.userLng,
        filters: params.locationFilters,
      }),
      params.includeCompanies
        ? suggestCompanies({ query: params.query })
        : Promise.resolve([]),
      suggestOccupations({
        query: params.query,
        locale: params.locale,
        filters: params.occupationFilters,
      }),
      suggestSeniorities({
        query: params.query,
        locale: params.locale,
        filters: params.seniorityFilters,
      }),
      suggestTechnologies({
        query: params.query,
        locale: params.locale,
        filters: params.technologyFilters,
      }),
    ]);

  return { locations, companies, occupations, seniorities, technologies };
}
