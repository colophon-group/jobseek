"use server";

import { resolveLocationSlugs, suggestLocations, type LocationSuggestion } from "@/lib/actions/locations";
import type { SelectedLocation } from "@/components/search/location-pills";

export type ParsedSearchLocation = SelectedLocation;

export interface ParsedSearchFilters {
  keywords: string[];
  locations: ParsedSearchLocation[];
}

function uniqCaseInsensitive(values: string[]): string[] {
  const seen = new Set<string>();
  const deduped: string[] = [];

  for (const value of values) {
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(value);
  }

  return deduped;
}

function normalizeLocationText(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

export function tokenizeSearchInput(input: string): string[] {
  const tokens = input
    .split(/[,\n\r\t/|]+|-+/)
    .map((part) => part.trim())
    .filter(Boolean);

  return uniqCaseInsensitive(tokens);
}

function exactLocationMatch(
  token: string,
  suggestions: LocationSuggestion[],
): LocationSuggestion | null {
  const normalizedToken = normalizeLocationText(token);

  for (const suggestion of suggestions) {
    const candidates = [
      suggestion.name,
      suggestion.slug,
      suggestion.parentName ? `${suggestion.name} ${suggestion.parentName}` : null,
    ].filter((value): value is string => Boolean(value));

    if (candidates.some((candidate) => normalizeLocationText(candidate) === normalizedToken)) {
      return suggestion;
    }
  }

  return null;
}

export async function parseSearchFilters(params: {
  q?: string;
  loc?: string;
  locale: string;
  userLat?: number;
  userLng?: number;
}): Promise<ParsedSearchFilters> {
  const explicitLocSlugs = params.loc
    ? uniqCaseInsensitive(
        params.loc
          .split(",")
          .map((slug) => slug.trim())
          .filter(Boolean),
      )
    : [];

  const resolvedExplicitLocs = explicitLocSlugs.length > 0
    ? await resolveLocationSlugs(explicitLocSlugs, params.locale)
    : new Map();

  const locations: ParsedSearchLocation[] = explicitLocSlugs
    .map((slug) => resolvedExplicitLocs.get(slug))
    .filter((location): location is NonNullable<typeof location> => location !== undefined)
    .map((location) => ({
      id: location.id,
      slug: location.slug,
      name: location.name,
      type: location.type as SelectedLocation["type"],
      parentName: location.parentName,
    }));

  const locationIds = new Set(locations.map((location) => location.id));
  const tokens = params.q ? tokenizeSearchInput(params.q) : [];
  if (tokens.length === 0) {
    return { keywords: [], locations };
  }

  const suggestionLists = await Promise.all(
    tokens.map((token) =>
      suggestLocations({
        query: token,
        locale: params.locale,
        userLat: params.userLat,
        userLng: params.userLng,
      }),
    ),
  );

  const keywords: string[] = [];

  tokens.forEach((token, index) => {
    const match = exactLocationMatch(token, suggestionLists[index]);
    if (!match) {
      keywords.push(token);
      return;
    }
    if (locationIds.has(match.id)) return;

    locationIds.add(match.id);
    locations.push({
      id: match.id,
      slug: match.slug,
      name: match.name,
      type: match.type as SelectedLocation["type"],
      parentName: match.parentName,
    });
  });

  return {
    keywords,
    locations,
  };
}
