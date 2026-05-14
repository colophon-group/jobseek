import "server-only";

import { resolveLocationSlugs, suggestLocations, type LocationSuggestion } from "@/lib/actions/locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, suggestOccupations, suggestSeniorities, suggestTechnologies, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import type { TaxonomySuggestion } from "@/lib/actions/taxonomy";
import type { SelectedLocation } from "@/lib/search/selected-location";
import { parseWorkModeParam } from "@/lib/search/query-params";
import type { WorkMode } from "@/lib/search/types";

export type ParsedSearchLocation = SelectedLocation;

export interface ParsedSearchFilters {
  keywords: string[];
  locations: ParsedSearchLocation[];
  occupations: { id: number; slug: string; name: string }[];
  seniorities: { id: number; slug: string; name: string }[];
  technologies: { id: number; slug: string; name: string }[];
  workMode: WorkMode[];
}

/**
 * Map of normalized lower-cased tokens / synonyms to canonical
 * {@link WorkMode} values. Single-word entries match against
 * `splitIntoWords` output during Pass 2; multi-word entries (e.g.
 * `"work from home"`, `"in office"`) match against the raw segment so
 * tokenizing them by whitespace doesn't lose them. Issue #2983.
 *
 * NOTE: synonyms are intentionally narrow — `"flex"` is NOT included
 * because it's an English noun common in job titles ("flex engineer").
 * `"office"` alone is also excluded for the same reason.
 */
const WORK_MODE_SINGLE_TOKEN: Record<string, WorkMode> = {
  remote: "remote",
  wfh: "remote",
  hybrid: "hybrid",
  onsite: "onsite",
};

const WORK_MODE_MULTI_TOKEN: Record<string, WorkMode> = {
  "work from home": "remote",
  "work-from-home": "remote",
  "on site": "onsite",
  "on-site": "onsite",
  "in office": "onsite",
  "in-office": "onsite",
};

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

function splitIntoSegments(input: string): string[] {
  return input
    .split(/[,\n\r\t/|]+|-+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function splitIntoWords(segment: string): string[] {
  return segment.split(/\s+/).filter(Boolean);
}

function exactLocationMatch(
  text: string,
  suggestions: LocationSuggestion[],
): LocationSuggestion | null {
  const normalizedToken = normalizeLocationText(text);

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

function exactTaxonomyMatch(
  text: string,
  suggestions: TaxonomySuggestion[],
): TaxonomySuggestion | null {
  const normalizedToken = text.toLowerCase().trim();
  for (const suggestion of suggestions) {
    if (
      suggestion.name.toLowerCase() === normalizedToken ||
      suggestion.slug === normalizedToken ||
      (suggestion.matchedName && suggestion.matchedName.toLowerCase() === normalizedToken)
    ) {
      return suggestion;
    }
  }
  return null;
}

/**
 * Check if word at index `i` is part of a multi-word occupation pair/triplet
 * with adjacent unconsumed words. If so, defer the single-word match so Pass 3
 * can match the more specific occupation (e.g. "Backend Developer" instead of
 * generic "Developer" → Software Engineer).
 */
function wordInMultiWordOccupation(
  words: string[],
  i: number,
  consumed: boolean[],
  occMap: Map<string, TaxonomySuggestion[]>,
): boolean {
  if (i > 0 && !consumed[i - 1]) {
    const pair = `${words[i - 1]} ${words[i]}`;
    if (exactTaxonomyMatch(pair, occMap.get(pair) ?? [])) return true;
  }
  if (i < words.length - 1 && !consumed[i + 1]) {
    const pair = `${words[i]} ${words[i + 1]}`;
    if (exactTaxonomyMatch(pair, occMap.get(pair) ?? [])) return true;
  }
  if (words.length <= 10) {
    if (i < words.length - 2 && !consumed[i + 1] && !consumed[i + 2]) {
      const t = `${words[i]} ${words[i + 1]} ${words[i + 2]}`;
      if (exactTaxonomyMatch(t, occMap.get(t) ?? [])) return true;
    }
    if (i > 0 && i < words.length - 1 && !consumed[i - 1] && !consumed[i + 1]) {
      const t = `${words[i - 1]} ${words[i]} ${words[i + 1]}`;
      if (exactTaxonomyMatch(t, occMap.get(t) ?? [])) return true;
    }
    if (i > 1 && !consumed[i - 1] && !consumed[i - 2]) {
      const t = `${words[i - 2]} ${words[i - 1]} ${words[i]}`;
      if (exactTaxonomyMatch(t, occMap.get(t) ?? [])) return true;
    }
  }
  return false;
}

export async function parseSearchFilters(params: {
  q?: string;
  loc?: string;
  occ?: string;
  sen?: string;
  tech?: string;
  /** `wm` URL param — comma-separated WorkMode values (issue #2983). */
  wm?: string;
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

  const explicitOccSlugs = params.occ
    ? uniqCaseInsensitive(
        params.occ
          .split(",")
          .map((slug) => slug.trim())
          .filter(Boolean),
      )
    : [];

  const explicitSenSlugs = params.sen
    ? uniqCaseInsensitive(
        params.sen
          .split(",")
          .map((slug) => slug.trim())
          .filter(Boolean),
      )
    : [];

  const explicitTechSlugs = params.tech
    ? uniqCaseInsensitive(
        params.tech
          .split(",")
          .map((slug) => slug.trim())
          .filter(Boolean),
      )
    : [];

  const [resolvedExplicitLocs, resolvedOccs, resolvedSens, resolvedTechs] = await Promise.all([
    explicitLocSlugs.length > 0
      ? resolveLocationSlugs(explicitLocSlugs, params.locale)
      : Promise.resolve(new Map()),
    explicitOccSlugs.length > 0
      ? resolveOccupationSlugs(explicitOccSlugs, params.locale)
      : Promise.resolve(new Map()),
    explicitSenSlugs.length > 0
      ? resolveSenioritySlugs(explicitSenSlugs, params.locale)
      : Promise.resolve(new Map()),
    explicitTechSlugs.length > 0
      ? resolveTechnologySlugs(explicitTechSlugs)
      : Promise.resolve(new Map()),
  ]);

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

  const occupations: { id: number; slug: string; name: string }[] = explicitOccSlugs
    .map((slug) => resolvedOccs.get(slug))
    .filter((o): o is NonNullable<typeof o> => o !== undefined);

  const seniorities: { id: number; slug: string; name: string }[] = explicitSenSlugs
    .map((slug) => resolvedSens.get(slug))
    .filter((s): s is NonNullable<typeof s> => s !== undefined);

  const technologies: { id: number; slug: string; name: string }[] = explicitTechSlugs
    .map((slug) => resolvedTechs.get(slug))
    .filter((t): t is NonNullable<typeof t> => t !== undefined);

  // Explicit `wm` URL param (issue #2983) — already constrained to valid
  // WorkMode values by parseWorkModeParam. Free-text matches found below
  // during tokenization extend this set without duplicates.
  const workMode: WorkMode[] = parseWorkModeParam(params.wm);
  const workModeSet = new Set<WorkMode>(workMode);

  const locationIds = new Set(locations.map((location) => location.id));
  const occupationIds = new Set(occupations.map((o) => o.id));
  const seniorityIds = new Set(seniorities.map((s) => s.id));
  const technologyIds = new Set(technologies.map((t) => t.id));

  // --- Word-level tokenization ---
  const segments = params.q ? splitIntoSegments(params.q) : [];
  const segmentWords = segments.map(splitIntoWords);

  // Collect unique single words and all candidates (singles + pairs + triplets)
  const singleSet = new Set<string>();
  const allCandidateSet = new Set<string>();
  for (const words of segmentWords) {
    for (const w of words) {
      singleSet.add(w);
      allCandidateSet.add(w);
    }
    for (let i = 0; i < words.length - 1; i++) {
      allCandidateSet.add(`${words[i]} ${words[i + 1]}`);
    }
    if (words.length <= 10) {
      for (let i = 0; i < words.length - 2; i++) {
        allCandidateSet.add(`${words[i]} ${words[i + 1]} ${words[i + 2]}`);
      }
    }
  }
  const singles = [...singleSet];
  const allCandidates = [...allCandidateSet];
  if (singles.length === 0) {
    return { keywords: [], locations, occupations, seniorities, technologies, workMode };
  }

  // All three suggest batches run in parallel. Locations use an expensive
  // recursive CTE but are only queried for singles. Occupations are queried for
  // ALL candidates (singles + pairs + triplets) for multi-word matching.
  const [senResults, locResults, occResults, techResults] = await Promise.all([
    Promise.all(
      singles.map((c) => suggestSeniorities({ query: c, locale: params.locale })),
    ),
    Promise.all(
      singles.map((c) =>
        suggestLocations({
          query: c,
          locale: params.locale,
          userLat: params.userLat,
          userLng: params.userLng,
        }),
      ),
    ),
    Promise.all(
      allCandidates.map((c) => suggestOccupations({ query: c, locale: params.locale })),
    ),
    Promise.all(
      singles.map((c) => suggestTechnologies({ query: c, locale: params.locale })),
    ),
  ]);

  // Build lookup maps
  const locMap = new Map<string, LocationSuggestion[]>();
  const occMap = new Map<string, TaxonomySuggestion[]>();
  const senMap = new Map<string, TaxonomySuggestion[]>();
  const techMap = new Map<string, TaxonomySuggestion[]>();
  singles.forEach((c, i) => {
    locMap.set(c, locResults[i]);
    senMap.set(c, senResults[i]);
    techMap.set(c, techResults[i]);
  });
  allCandidates.forEach((c, i) => {
    occMap.set(c, occResults[i]);
  });

  const keywords: string[] = [];

  for (const words of segmentWords) {
    const consumed = new Array<boolean>(words.length).fill(false);

    // --- Pass 1.5: Multi-word work-mode (e.g. "work from home", "in office") ---
    // Issue #2983. Try triplet then pair sliding windows so longer phrases
    // win over their shorter substrings (e.g. don't let "in" + "office"
    // bind to single-token "office" — which we don't ship anyway).
    if (words.length >= 3) {
      for (let i = 0; i <= words.length - 3; i++) {
        if (consumed[i] || consumed[i + 1] || consumed[i + 2]) continue;
        const triplet = `${words[i]} ${words[i + 1]} ${words[i + 2]}`.toLowerCase();
        const wm = WORK_MODE_MULTI_TOKEN[triplet];
        if (wm) {
          if (!workModeSet.has(wm)) {
            workModeSet.add(wm);
            workMode.push(wm);
          }
          consumed[i] = consumed[i + 1] = consumed[i + 2] = true;
        }
      }
    }
    if (words.length >= 2) {
      for (let i = 0; i <= words.length - 2; i++) {
        if (consumed[i] || consumed[i + 1]) continue;
        const pair = `${words[i]} ${words[i + 1]}`.toLowerCase();
        const wm = WORK_MODE_MULTI_TOKEN[pair];
        if (wm) {
          if (!workModeSet.has(wm)) {
            workModeSet.add(wm);
            workMode.push(wm);
          }
          consumed[i] = consumed[i + 1] = true;
        }
      }
    }

    // --- Pass 2: Single-word matching (work-mode → seniority → location → occupation) ---
    for (let i = 0; i < words.length; i++) {
      if (consumed[i]) continue;
      const word = words[i];

      // Work-mode single-token (`remote`, `hybrid`, `onsite`, `wfh`).
      // Tried before seniority because these tokens never overlap with
      // seniority/occupation/location names in practice.
      const wmMatch = WORK_MODE_SINGLE_TOKEN[word.toLowerCase()];
      if (wmMatch) {
        if (!workModeSet.has(wmMatch)) {
          workModeSet.add(wmMatch);
          workMode.push(wmMatch);
        }
        consumed[i] = true;
        continue;
      }

      const senMatch = exactTaxonomyMatch(word, senMap.get(word) ?? []);
      if (senMatch) {
        if (!seniorityIds.has(senMatch.id)) {
          seniorityIds.add(senMatch.id);
          seniorities.push({ id: senMatch.id, slug: senMatch.slug, name: senMatch.name });
        }
        consumed[i] = true;
        continue;
      }

      const techMatch = exactTaxonomyMatch(word, techMap.get(word) ?? []);
      if (techMatch) {
        if (!technologyIds.has(techMatch.id)) {
          technologyIds.add(techMatch.id);
          technologies.push({ id: techMatch.id, slug: techMatch.slug, name: techMatch.name });
        }
        consumed[i] = true;
        continue;
      }

      const locMatch = exactLocationMatch(word, locMap.get(word) ?? []);
      if (locMatch) {
        if (!locationIds.has(locMatch.id)) {
          locationIds.add(locMatch.id);
          locations.push({
            id: locMatch.id, slug: locMatch.slug, name: locMatch.name,
            type: locMatch.type as SelectedLocation["type"], parentName: locMatch.parentName,
          });
        }
        consumed[i] = true;
        continue;
      }

      // Defer if this word is part of a multi-word occupation (e.g. "Backend Developer")
      // so Pass 3 can match the specific child instead of the generic parent.
      const occMatch = exactTaxonomyMatch(word, occMap.get(word) ?? []);
      if (occMatch && !wordInMultiWordOccupation(words, i, consumed, occMap)) {
        if (!occupationIds.has(occMatch.id)) {
          occupationIds.add(occMatch.id);
          occupations.push({ id: occMatch.id, slug: occMatch.slug, name: occMatch.name });
        }
        consumed[i] = true;
        continue;
      }
    }

    // --- Pass 3: Multi-word occupation fallback (triplets then pairs, only ALL-unmatched) ---
    if (words.length <= 10) {
      for (let i = 0; i < words.length - 2; i++) {
        if (consumed[i] || consumed[i + 1] || consumed[i + 2]) continue;
        const triplet = `${words[i]} ${words[i + 1]} ${words[i + 2]}`;
        const match = exactTaxonomyMatch(triplet, occMap.get(triplet) ?? []);
        if (match) {
          if (!occupationIds.has(match.id)) {
            occupationIds.add(match.id);
            occupations.push({ id: match.id, slug: match.slug, name: match.name });
          }
          consumed[i] = consumed[i + 1] = consumed[i + 2] = true;
        }
      }
    }
    for (let i = 0; i < words.length - 1; i++) {
      if (consumed[i] || consumed[i + 1]) continue;
      const pair = `${words[i]} ${words[i + 1]}`;
      const match = exactTaxonomyMatch(pair, occMap.get(pair) ?? []);
      if (match) {
        if (!occupationIds.has(match.id)) {
          occupationIds.add(match.id);
          occupations.push({ id: match.id, slug: match.slug, name: match.name });
        }
        consumed[i] = consumed[i + 1] = true;
      }
    }

    // Remaining unmatched words → keywords
    for (let i = 0; i < words.length; i++) {
      if (!consumed[i]) keywords.push(words[i]);
    }
  }

  return {
    keywords: uniqCaseInsensitive(keywords),
    locations,
    occupations,
    seniorities,
    technologies,
    workMode,
  };
}
