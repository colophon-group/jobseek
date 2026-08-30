"use server";

import { getGeoFromHeaders } from "@/lib/search/params";
import { getSemanticSearchQueryComplexity } from "@/lib/search/semantic-query";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/services/search-input";

const MAX_QUERY_LENGTH = 512;
const MAX_QUERY_TERMS = 12;
const MAX_OCCUPATION_CANDIDATES = 36;
const MAX_QUERY_TERM_LENGTH = 120;
const MAX_SLUGS_PER_DIMENSION = 20;
const MAX_SLUG_LENGTH = 100;

type CompanySemanticFilterInput = {
  q: string;
  loc?: string;
  occ?: string;
  sen?: string;
  tech?: string;
  wm?: string;
  etype?: string;
  locale: string;
};

export type CompanySemanticFilterResult = {
  parsed: ParsedSearchFilters;
  userLat: number | undefined;
  userLng: number | undefined;
};

function validQuery(raw: string): boolean {
  if (
    raw.length === 0 ||
    raw.length > MAX_QUERY_LENGTH ||
    /[\u0000-\u001f\u007f]/.test(raw)
  ) {
    return false;
  }
  const complexity = getSemanticSearchQueryComplexity(raw);
  return (
    complexity.uniqueTerms > 0 &&
    complexity.uniqueTerms <= MAX_QUERY_TERMS &&
    complexity.occupationCandidates <= MAX_OCCUPATION_CANDIDATES &&
    complexity.maxTermLength <= MAX_QUERY_TERM_LENGTH
  );
}

function validSlugList(raw: string | undefined): boolean {
  if (!raw) return true;
  if (raw.length > MAX_QUERY_LENGTH) return false;
  const values = raw.split(",").map((value) => value.trim()).filter(Boolean);
  return (
    values.length <= MAX_SLUGS_PER_DIMENSION &&
    values.every(
      (value) =>
        value.length <= MAX_SLUG_LENGTH &&
        /^[\p{L}\p{N}][\p{L}\p{N}._-]*$/u.test(value),
    )
  );
}

function validEnumList(raw: string | undefined): boolean {
  return raw === undefined || raw.length <= MAX_QUERY_LENGTH;
}

/**
 * Preserve the canonical semantic meaning of free-text `q` URLs without
 * re-running the full company-page initializer. This narrow, read-only action
 * is used only for `q` because the canonical parser needs server-side taxonomy
 * suggestions and request geolocation. Explicit-only URL filters stay on the
 * browser-direct Typesense path.
 */
export async function resolveCompanySemanticFilters(
  input: CompanySemanticFilterInput,
): Promise<CompanySemanticFilterResult | null> {
  if (
    !validQuery(input.q) ||
    !validSlugList(input.loc) ||
    !validSlugList(input.occ) ||
    !validSlugList(input.sen) ||
    !validSlugList(input.tech) ||
    !validEnumList(input.wm) ||
    !validEnumList(input.etype)
  ) {
    return null;
  }

  const locale =
    input.locale === "de" || input.locale === "fr" || input.locale === "it"
      ? input.locale
      : "en";
  const { userLat, userLng } = await getGeoFromHeaders();
  const parsed = await parseSearchFilters({
    q: input.q,
    loc: input.loc,
    occ: input.occ,
    sen: input.sen,
    tech: input.tech,
    wm: input.wm,
    etype: input.etype,
    locale,
    userLat,
    userLng,
  });
  return { parsed, userLat, userLng };
}
