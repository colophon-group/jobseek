"use client";

import {
  suggestLocations as serverSuggestLocations,
  type LocationSuggestion,
} from "@/lib/actions/locations";
import {
  suggestOccupations as serverSuggestOccupations,
  suggestSeniorities as serverSuggestSeniorities,
  suggestTechnologies as serverSuggestTechnologies,
  type TaxonomySuggestion,
} from "@/lib/actions/taxonomy";
import type { TypeaheadBoostFilters } from "./typeahead-boost";
import type {
  SearchBarTypeaheadParams,
  SearchBarTypeaheadResults,
} from "./typeahead-contract";

const directEnabled = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT === "1";

type LocationParams = {
  query: string;
  locale: string;
  userLat?: number;
  userLng?: number;
  filters?: TypeaheadBoostFilters;
};

type TaxonomyParams = {
  query: string;
  locale: string;
  filters?: TypeaheadBoostFilters;
};

async function tryBrowser<T>(fn: () => Promise<T>): Promise<T | null> {
  if (!directEnabled) return null;
  try {
    return await fn();
  } catch {
    return null;
  }
}

/**
 * Executes one complete search-bar query through a bounded request plan.
 *
 * Application-data request budget per debounced query:
 * - direct disabled: exactly one server-action request;
 * - direct enabled, warm key: at most three Typesense `multi_search` requests
 *   (initial candidates, non-English fallback, posting-count boosts);
 * - direct enabled, cold key: the same plus one scoped-key request;
 * - if a direct candidate/fallback phase fails, one server-action fallback is
 *   added. Because a failure stops the direct plan, the maximum is four
 *   application-initiated data requests. Boost failures deliberately retain
 *   the unboosted candidates and do not trigger another request.
 *
 * This budget does not count Next.js chunks loaded by the dynamic imports or
 * browser-generated CORS OPTIONS preflights. Those are transport/cache work,
 * not data requests initiated by this runner; docs/11-typesense.md records
 * both exclusions so the bound is not mistaken for a wire-level ceiling.
 */
export async function runSearchBarTypeahead(
  params: SearchBarTypeaheadParams,
): Promise<SearchBarTypeaheadResults> {
  const browser = await tryBrowser(async () => {
    const m = await import("./typesense-browser-typeahead");
    return m.suggestSearchBarBrowser(params);
  });
  if (browser !== null) return browser;

  const m = await import("@/lib/actions/typeahead");
  return m.suggestSearchBarTypeahead(params);
}

export async function runSuggestLocations(
  params: LocationParams,
): Promise<LocationSuggestion[]> {
  const browser = await tryBrowser(async () => {
    const m = await import("./typesense-browser-typeahead");
    return m.suggestLocationsBrowser(params);
  });
  if (browser !== null) return browser;
  return serverSuggestLocations(params);
}

export async function runSuggestOccupations(
  params: TaxonomyParams,
): Promise<TaxonomySuggestion[]> {
  const browser = await tryBrowser(async () => {
    const m = await import("./typesense-browser-typeahead");
    return m.suggestOccupationsBrowser(params);
  });
  if (browser !== null) return browser;
  return serverSuggestOccupations(params);
}

export async function runSuggestSeniorities(
  params: TaxonomyParams,
): Promise<TaxonomySuggestion[]> {
  const browser = await tryBrowser(async () => {
    const m = await import("./typesense-browser-typeahead");
    return m.suggestSenioritiesBrowser(params);
  });
  if (browser !== null) return browser;
  return serverSuggestSeniorities(params);
}

export async function runSuggestTechnologies(
  params: TaxonomyParams,
): Promise<TaxonomySuggestion[]> {
  const browser = await tryBrowser(async () => {
    const m = await import("./typesense-browser-typeahead");
    return m.suggestTechnologiesBrowser(params);
  });
  if (browser !== null) return browser;
  return serverSuggestTechnologies(params);
}
