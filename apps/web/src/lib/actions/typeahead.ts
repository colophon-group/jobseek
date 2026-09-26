"use server";

import { suggestCompanies } from "@/lib/services/company";
import { suggestLocations } from "@/lib/services/locations";
import { getTypesenseClient, type TypesenseHit } from "@/lib/search/typesense-client";
import { isLocale } from "@/lib/i18n";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/services/taxonomy";
import type {
  SearchBarTypeaheadParams,
  SearchBarTypeaheadResults,
  SearchBarTermTypeaheadParams,
  SearchBarTermResult,
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

/** One action boundary for the bounded prior-term batch. */
export async function suggestSearchBarTermTypeahead(
  params: SearchBarTermTypeaheadParams,
): Promise<SearchBarTermResult[]> {
  if (!isLocale(params.locale) || !Array.isArray(params.terms)) return [];
  const terms = [...new Set(params.terms.filter((term): term is string => typeof term === "string")
    .map((term) => term.trim()).filter((term) => term.length >= 2 && term.length <= 80))].slice(0, 4);
  if (terms.length === 0) return [];
  // Only exact term hits are rendered from this batch, so per-filter posting
  // boosts and broad locale fallback would add work without changing choices.
  // One multi_search replaces up to sixteen independent Typesense round trips.
  const searches = terms.flatMap((term) => [
    {
      collection: "location", q: term,
      query_by: params.locale === "en" ? "name_en,aliases" : `name_${params.locale},name_en,aliases`,
      filter_by: "has_active_postings:true", sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5, prefix: "true", num_typos: "1", drop_tokens_threshold: 0,
    },
    ...(["occupation", "seniority"] as const).map((collection) => ({
      collection, q: term, query_by: "name,aliases",
      filter_by: `has_active_postings:true && locale:${params.locale}`,
      sort_by: "_text_match:desc,active_posting_count:desc", per_page: 5,
      prefix: "true", num_typos: "1",
    })),
    {
      collection: "technology", q: term, query_by: "name,slug",
      filter_by: "has_active_postings:true", sort_by: "_text_match:desc,active_posting_count:desc",
      per_page: 5, prefix: "true", num_typos: "0",
    },
  ]);
  const raw = await getTypesenseClient().multiSearch.perform({ searches });
  const results = (raw as unknown as { results?: Array<{ hits?: TypesenseHit[]; error?: string }> }).results;
  if (!results || results.length !== searches.length || results.some((result) => result.error)) {
    throw new Error("Incomplete term suggestion batch");
  }
  return terms.map((term, termIndex) => {
    const hits = (offset: number) => results[termIndex * 4 + offset].hits ?? [];
    const taxonomy = (offset: number, idField: "occupation_id" | "seniority_id" | "technology_id") =>
      hits(offset).map(({ document, highlights }) => {
        const name = String(document.name ?? document.slug);
        const alias = highlights?.find((highlight) => highlight.field === "aliases")
          ?.snippets?.[0]?.replace(/<\/?mark>/g, "");
        return {
          id: Number(document[idField]), slug: String(document.slug), name,
          ...(alias && alias !== name ? { matchedName: alias } : {}),
        };
      });
    return {
      term,
      locations: hits(0).map(({ document }) => ({
        id: Number(document.location_id), slug: String(document.slug),
        name: String(document[`name_${params.locale}`] ?? document.name_en ?? document.slug),
        type: document.type as SearchBarTermResult["locations"][number]["type"],
        parentName: document.parent_name ? String(document.parent_name) : null,
      })),
      occupations: taxonomy(1, "occupation_id"),
      seniorities: taxonomy(2, "seniority_id"),
      technologies: taxonomy(3, "technology_id"),
    };
  });
}
