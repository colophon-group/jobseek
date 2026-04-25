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
