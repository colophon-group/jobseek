import "server-only";
import { measureSearchStage } from "@/lib/search/latency";

import {
  resolveLocationSlugs,
  suggestLocations,
  type LocationSuggestion,
} from "@/lib/services/locations";
// Service-tier callers go straight to plain service modules rather
// than `"use server"` action wrappers — see issues #3231 / #3329 / #3330.
import {
  resolveOccupationSlugs,
  resolveSenioritySlugs,
  resolveTechnologySlugs,
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/services/taxonomy";
import type { TaxonomySuggestion } from "@/lib/services/taxonomy";
import {
  migrateLegacyInternshipFilterParams,
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import { tokenizeSemanticSearchQuery, type TokenizedSemanticSearchQuery } from "@/lib/search/semantic-query";
import type { EmploymentType, SelectedLocation, WorkMode } from "@/lib/search/types";

export interface ParsedSearchFilters {
  keywords: string[];
  locations: SelectedLocation[];
  occupations: { id: number; slug: string; name: string }[];
  seniorities: { id: number; slug: string; name: string }[];
  technologies: { id: number; slug: string; name: string }[];
  workMode: WorkMode[];
  employmentTypes: EmploymentType[];
  /** Exact public-API slugs that could not be resolved. UI callers stay lenient. */
  unresolvedExplicitSlugs?: Partial<
    Record<"loc" | "occ" | "sen" | "tech", string[]>
  >;
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

/** Skip only spans guaranteed to be consumed by the existing work-mode passes. */
function semanticLookupCandidates(tokenized: TokenizedSemanticSearchQuery) {
  const singles = new Set<string>();
  const occupations = new Set<string>();
  for (const words of tokenized.segmentWords) {
    const consumed = words.map(() => false);
    for (const size of [3, 2]) {
      for (let i = 0; i <= words.length - size; i++) {
        if (consumed.slice(i, i + size).some(Boolean)) continue;
        if (WORK_MODE_MULTI_TOKEN[words.slice(i, i + size).join(" ").toLowerCase()]) {
          for (let j = i; j < i + size; j++) consumed[j] = true;
        }
      }
    }
    words.forEach((word, i) => {
      if (WORK_MODE_SINGLE_TOKEN[word.toLowerCase()]) consumed[i] = true;
      if (!consumed[i]) { singles.add(word); occupations.add(word); }
    });
    for (const size of words.length <= 10 ? [2, 3] : [2]) {
      for (let i = 0; i <= words.length - size; i++) {
        if (!consumed.slice(i, i + size).some(Boolean)) {
          occupations.add(words.slice(i, i + size).join(" "));
        }
      }
    }
  }
  return { singles, occupations };
}

async function lookupSemanticTerms(
  tokenized: TokenizedSemanticSearchQuery,
  params: { locale: string; qmode?: "literal"; userLat?: number; userLng?: number },
): Promise<[TaxonomySuggestion[][], LocationSuggestion[][], TaxonomySuggestion[][], TaxonomySuggestion[][]]> {
  if (params.qmode === "literal" || tokenized.singles.length === 0) return [[], [], [], []];
  const candidates = semanticLookupCandidates(tokenized);
  if (candidates.singles.size === 0) {
    return [tokenized.singles.map(() => []), tokenized.singles.map(() => []),
      tokenized.allCandidates.map(() => []), tokenized.singles.map(() => [])];
  }
  return measureSearchStage("interpret_terms", () => Promise.all([
    Promise.all(tokenized.singles.map((query) => candidates.singles.has(query)
      ? measureSearchStage("seniority_lookup", () => suggestSeniorities({ query, locale: params.locale })) : [])),
    Promise.all(tokenized.singles.map((query) => candidates.singles.has(query)
      ? measureSearchStage("location_lookup", () => suggestLocations({ query, locale: params.locale, userLat: params.userLat, userLng: params.userLng })) : [])),
    Promise.all(tokenized.allCandidates.map((query) => candidates.occupations.has(query)
      ? measureSearchStage("occupation_lookup", () => suggestOccupations({ query, locale: params.locale })) : [])),
    Promise.all(tokenized.singles.map((query) => candidates.singles.has(query)
      ? measureSearchStage("technology_lookup", () => suggestTechnologies({ query, locale: params.locale })) : [])),
  ]));
}

export async function parseSearchFilters(params: {
  q?: string;
  /** Preserve residual keywords from an already routed whole-query proposal. */
  qmode?: "literal";
  loc?: string;
  occ?: string;
  sen?: string;
  tech?: string;
  /** `wm` URL param — comma-separated WorkMode values (issue #2983). */
  wm?: string;
  /** `etype` URL param — comma-separated EmploymentType values (issue #3218). */
  etype?: string;
  locale: string;
  userLat?: number;
  userLng?: number;
}): Promise<ParsedSearchFilters> {
  const migratedLegacyFilters = migrateLegacyInternshipFilterParams(
    params.etype,
    params.sen,
  );
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

  const explicitSenSlugs = migratedLegacyFilters.seniority
    ? uniqCaseInsensitive(
        migratedLegacyFilters.seniority
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

  const tokenized = tokenizeSemanticSearchQuery(params.q ?? "");
  // Slug resolution and free-text suggestions have no data dependency.
  const [resolved, interpreted] = await Promise.all([
    measureSearchStage("resolve_filters", () => Promise.all([
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
    ])),
    lookupSemanticTerms(tokenized, params),
  ]);
  const [resolvedExplicitLocs, resolvedOccs, resolvedSens, resolvedTechs] = resolved;

  const unresolvedExplicitSlugs: NonNullable<
    ParsedSearchFilters["unresolvedExplicitSlugs"]
  > = {};
  for (const [name, requested, resolved] of [
    ["loc", explicitLocSlugs, resolvedExplicitLocs],
    ["occ", explicitOccSlugs, resolvedOccs],
    ["sen", explicitSenSlugs, resolvedSens],
    ["tech", explicitTechSlugs, resolvedTechs],
  ] as const) {
    const unresolved = requested.filter((slug) => !resolved.has(slug));
    if (unresolved.length > 0) unresolvedExplicitSlugs[name] = unresolved;
  }
  const unresolvedResult =
    Object.keys(unresolvedExplicitSlugs).length > 0
      ? { unresolvedExplicitSlugs }
      : {};

  const locations: SelectedLocation[] = explicitLocSlugs
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
  const employmentTypes: EmploymentType[] = parseEmploymentTypeParam(
    migratedLegacyFilters.employmentType,
  );

  if (params.qmode === "literal") {
    return {
      keywords: uniqCaseInsensitive((params.q ?? "").split(",").map((part) => part.trim()).filter(Boolean)),
      locations,
      occupations,
      seniorities,
      technologies,
      workMode,
      employmentTypes,
      ...unresolvedResult,
    };
  }

  const locationIds = new Set(locations.map((location) => location.id));
  const occupationIds = new Set(occupations.map((o) => o.id));
  const seniorityIds = new Set(seniorities.map((s) => s.id));
  const technologyIds = new Set(technologies.map((t) => t.id));

  // --- Word-level tokenization ---
  const { segmentWords, singles, allCandidates } = tokenized;
  if (singles.length === 0) {
    return {
      keywords: [],
      locations,
      occupations,
      seniorities,
      technologies,
      workMode,
      employmentTypes,
      ...unresolvedResult,
    };
  }

  const [senResults, locResults, occResults, techResults] = interpreted;

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
    employmentTypes,
    ...unresolvedResult,
  };
}
