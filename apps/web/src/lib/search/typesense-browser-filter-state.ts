import type { ParsedSearchFilters } from "@/lib/services/search-input";
import type { SelectedLocation } from "@/lib/search/types";
import {
  parseEmploymentTypeParam,
  parseWorkModeParam,
} from "@/lib/search/query-params";
import {
  getTypesenseBrowserConfig,
  invalidateTypesenseBrowserConfigIfUnauthorized,
  type TypesenseBrowserConfig,
} from "@/lib/search/typesense-browser-key";

const MAX_QUERY_LENGTH = 512;
const MAX_KEYWORDS = 20;
const MAX_KEYWORD_LENGTH = 120;
const MAX_SLUGS_PER_DIMENSION = 20;
const MAX_SLUG_LENGTH = 100;

type FilterDimension = "loc" | "occ" | "sen" | "tech";

type SearchRequest = {
  collection: string;
  [key: string]: unknown;
};

type SearchResult<T> = {
  hits: Array<{ document: T }>;
  error?: string;
};

type LocationDocument = {
  location_id: number;
  slug: string;
  type: string;
  parent_name?: string | null;
  name_en?: string;
  name_de?: string;
  name_fr?: string;
  name_it?: string;
};

type TaxonomyDocument = {
  occupation_id?: number;
  seniority_id?: number;
  technology_id?: number;
  slug: string;
  name?: string;
  locale?: string;
};

export type BrowserFilterStateResult = {
  parsed: ParsedSearchFilters;
  /** False means at least one URL filter was unsafe or could not be resolved. */
  complete: boolean;
};

type ParsedFilterInput = {
  keywords: { values: string[]; valid: boolean };
  byDimension: Record<
    FilterDimension,
    { values: string[]; valid: boolean }
  >;
  parsed: ParsedSearchFilters;
  valid: boolean;
};

function safeLocale(locale: string): "en" | "de" | "fr" | "it" {
  return locale === "de" || locale === "fr" || locale === "it"
    ? locale
    : "en";
}

function filterLiteral(value: string): string {
  return `\`${value.replace(/\\/g, "\\\\").replace(/`/g, "\\`")}\``;
}

function splitSlugs(raw: string | null): { values: string[]; valid: boolean } {
  if (!raw) return { values: [], valid: true };
  if (raw.length > MAX_QUERY_LENGTH) return { values: [], valid: false };

  const values: string[] = [];
  const seen = new Set<string>();
  for (const part of raw.split(",")) {
    const value = part.trim();
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    if (
      value.length > MAX_SLUG_LENGTH ||
      !/^[\p{L}\p{N}][\p{L}\p{N}._-]*$/u.test(value)
    ) {
      return { values: [], valid: false };
    }
    seen.add(key);
    values.push(value);
    if (values.length > MAX_SLUGS_PER_DIMENSION) {
      return { values: [], valid: false };
    }
  }
  return { values, valid: true };
}

function splitKeywords(raw: string | null): { values: string[]; valid: boolean } {
  if (!raw) return { values: [], valid: true };
  if (raw.length > MAX_QUERY_LENGTH || /[\u0000-\u001f\u007f]/.test(raw)) {
    return { values: [], valid: false };
  }
  const values = raw
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  if (
    values.length > MAX_KEYWORDS ||
    values.some((value) => value.length > MAX_KEYWORD_LENGTH)
  ) {
    return { values: [], valid: false };
  }
  return { values: [...new Set(values)], valid: true };
}

async function searchMany(
  config: TypesenseBrowserConfig,
  searches: SearchRequest[],
): Promise<SearchResult<Record<string, unknown>>[]> {
  const url = `${config.protocol}://${config.host}:${config.port}/multi_search`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-typesense-api-key": config.apiKey,
    },
    body: JSON.stringify({ searches }),
  });
  if (!response.ok) {
    invalidateTypesenseBrowserConfigIfUnauthorized(response.status);
    throw new Error(`typesense filter resolver ${response.status}`);
  }
  const body: unknown = await response.json();
  if (
    !isRecord(body) ||
    !Array.isArray(body.results) ||
    body.results.length !== searches.length
  ) {
    throw new Error("typesense filter resolver returned an incomplete result set");
  }
  const results: SearchResult<Record<string, unknown>>[] = [];
  for (const rawResult of body.results) {
    if (!isRecord(rawResult) || typeof rawResult.error === "string") {
      throw new Error("typesense filter resolver search failed");
    }
    if (!Array.isArray(rawResult.hits)) {
      throw new Error("typesense filter resolver returned malformed hits");
    }
    const hits = rawResult.hits.map((rawHit) => {
      if (!isRecord(rawHit) || !isRecord(rawHit.document)) {
        throw new Error("typesense filter resolver returned malformed documents");
      }
      return { document: rawHit.document };
    });
    results.push({ hits });
  }
  return results;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOptionalString(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || typeof value === "string";
}

function isLocationDocument(value: Record<string, unknown>): value is LocationDocument {
  return (
    Number.isInteger(value.location_id) &&
    typeof value.slug === "string" &&
    ["macro", "country", "region", "city"].includes(String(value.type)) &&
    isOptionalString(value.parent_name) &&
    isOptionalString(value.name_en) &&
    isOptionalString(value.name_de) &&
    isOptionalString(value.name_fr) &&
    isOptionalString(value.name_it)
  );
}

function taxonomyId(
  document: Record<string, unknown>,
  kind: Exclude<FilterDimension, "loc">,
): number | null {
  const key =
    kind === "occ"
      ? "occupation_id"
      : kind === "sen"
        ? "seniority_id"
        : "technology_id";
  const id = document[key];
  return Number.isInteger(id) ? (id as number) : null;
}

function isTaxonomyDocument(
  value: Record<string, unknown>,
  kind: Exclude<FilterDimension, "loc">,
): value is TaxonomyDocument {
  return (
    taxonomyId(value, kind) !== null &&
    typeof value.slug === "string" &&
    isOptionalString(value.name) &&
    isOptionalString(value.locale)
  );
}

function localizedLocationName(
  document: LocationDocument,
  locale: "en" | "de" | "fr" | "it",
): string {
  return document[`name_${locale}`] ?? document.name_en ?? document.slug;
}

function preferLocale(
  documents: TaxonomyDocument[],
  locale: "en" | "de" | "fr" | "it",
): Map<string, TaxonomyDocument> {
  const bySlug = new Map<string, TaxonomyDocument>();
  for (const document of documents) {
    const current = bySlug.get(document.slug);
    if (
      !current ||
      (document.locale === locale && current.locale !== locale)
    ) {
      bySlug.set(document.slug, document);
    }
  }
  return bySlug;
}

function parseFilterInput(searchParams: URLSearchParams): ParsedFilterInput {
  const keywords = splitKeywords(searchParams.get("q"));
  const byDimension = {
    loc: splitSlugs(searchParams.get("loc")),
    occ: splitSlugs(searchParams.get("occ")),
    sen: splitSlugs(searchParams.get("sen")),
    tech: splitSlugs(searchParams.get("tech")),
  } satisfies ParsedFilterInput["byDimension"];

  const unresolvedExplicitSlugs = Object.fromEntries(
    (Object.entries(byDimension) as Array<
      [FilterDimension, { values: string[]; valid: boolean }]
    >)
      .filter(([, value]) => value.values.length > 0)
      .map(([dimension, value]) => [dimension, value.values]),
  ) as NonNullable<ParsedSearchFilters["unresolvedExplicitSlugs"]>;
  const parsed: ParsedSearchFilters = {
    keywords: keywords.values,
    locations: [],
    occupations: [],
    seniorities: [],
    technologies: [],
    workMode: parseWorkModeParam(searchParams.get("wm")),
    employmentTypes: parseEmploymentTypeParam(searchParams.get("etype")),
    ...(Object.keys(unresolvedExplicitSlugs).length > 0
      ? { unresolvedExplicitSlugs }
      : {}),
  };
  return {
    keywords,
    byDimension,
    parsed,
    valid:
      keywords.valid &&
      Object.values(byDimension).every((value) => value.valid),
  };
}

/**
 * Preserve bounded URL state when the network cannot resolve taxonomy slugs.
 * Explicit slugs remain marked unresolved, so callers cannot accidentally
 * broaden a failed filtered search.
 */
export function parseCompanyFilterStateOffline(
  searchParams: URLSearchParams,
): BrowserFilterStateResult {
  const input = parseFilterInput(searchParams);
  return {
    parsed: input.parsed,
    complete:
      input.valid &&
      input.keywords.values.length === 0 &&
      input.parsed.unresolvedExplicitSlugs === undefined,
  };
}

/**
 * Resolve the canonical company-page URL vocabulary directly through the
 * scoped browser Typesense key. The helper is deliberately bounded because
 * every value originates in an untrusted URL. Missing/invalid slugs fail
 * closed: callers render an unavailable state instead of dropping the filter.
 */
export async function resolveCompanyFilterStateDirect(
  searchParams: URLSearchParams,
  localeInput: string,
): Promise<BrowserFilterStateResult> {
  const locale = safeLocale(localeInput);
  const input = parseFilterInput(searchParams);
  const { byDimension, parsed: base } = input;
  // Free text needs the canonical semantic parser (work mode, taxonomy and
  // geo-aware location inference). This direct helper resolves only explicit
  // URL dimensions and refuses to reinterpret `q` as title keywords.
  if (!input.valid || input.keywords.values.length > 0) {
    return { parsed: base, complete: false };
  }

  const searches: SearchRequest[] = [];
  const kinds: FilterDimension[] = [];
  const addSearch = (kind: FilterDimension, search: SearchRequest) => {
    kinds.push(kind);
    searches.push(search);
  };
  const list = (values: string[]) =>
    values.map(filterLiteral).join(",");

  if (byDimension.loc.values.length > 0) {
    addSearch("loc", {
      collection: "location",
      q: "*",
      query_by: "name_en",
      filter_by: `slug:[${list(byDimension.loc.values)}]`,
      per_page: byDimension.loc.values.length,
      include_fields:
        "location_id,slug,type,parent_name,name_en,name_de,name_fr,name_it",
    });
  }
  const localizedFilter = (values: string[]) =>
    `slug:[${list(values)}] && locale:[${locale},en]`;
  if (byDimension.occ.values.length > 0) {
    addSearch("occ", {
      collection: "occupation",
      q: "*",
      query_by: "name",
      filter_by: localizedFilter(byDimension.occ.values),
      per_page: byDimension.occ.values.length * 2,
      include_fields: "occupation_id,slug,name,locale",
    });
  }
  if (byDimension.sen.values.length > 0) {
    addSearch("sen", {
      collection: "seniority",
      q: "*",
      query_by: "name",
      filter_by: localizedFilter(byDimension.sen.values),
      per_page: byDimension.sen.values.length * 2,
      include_fields: "seniority_id,slug,name,locale",
    });
  }
  if (byDimension.tech.values.length > 0) {
    addSearch("tech", {
      collection: "technology",
      q: "*",
      query_by: "name",
      filter_by: `slug:[${list(byDimension.tech.values)}]`,
      per_page: byDimension.tech.values.length,
      include_fields: "technology_id,slug,name",
    });
  }
  if (searches.length === 0) return { parsed: base, complete: true };

  const results = await searchMany(
    await getTypesenseBrowserConfig(),
    searches,
  );
  const resolved = new Set<string>();
  for (let index = 0; index < kinds.length; index += 1) {
    const kind = kinds[index];
    const documents = results[index].hits.map((hit) => hit.document);
    if (kind === "loc") {
      base.locations = documents.map((raw) => {
        if (!isLocationDocument(raw)) {
          throw new Error("typesense filter resolver returned a malformed location");
        }
        const document = raw;
        resolved.add(`loc:${document.slug}`);
        return {
          id: document.location_id,
          slug: document.slug,
          name: localizedLocationName(document, locale),
          type: document.type as SelectedLocation["type"],
          parentName: document.parent_name ?? null,
        };
      });
      continue;
    }

    const taxonomyKind = kind as Exclude<FilterDimension, "loc">;
    const taxonomyDocuments = documents.map((document) => {
      if (!isTaxonomyDocument(document, taxonomyKind)) {
        throw new Error("typesense filter resolver returned malformed taxonomy");
      }
      return document;
    });
    const preferred = preferLocale(taxonomyDocuments, locale);
    const target =
      kind === "occ"
        ? base.occupations
        : kind === "sen"
          ? base.seniorities
          : base.technologies;
    for (const document of preferred.values()) {
      const id =
        kind === "occ"
          ? document.occupation_id
          : kind === "sen"
            ? document.seniority_id
            : document.technology_id;
      if (!Number.isInteger(id)) continue;
      resolved.add(`${kind}:${document.slug}`);
      target.push({
        id: id as number,
        slug: document.slug,
        name: document.name ?? document.slug,
      });
    }
  }

  const missing = (Object.entries(byDimension) as Array<
    [FilterDimension, { values: string[]; valid: boolean }]
  >).some(([kind, value]) =>
    value.values.some((slug) => !resolved.has(`${kind}:${slug}`)),
  );
  if (!missing) delete base.unresolvedExplicitSlugs;
  return { parsed: base, complete: !missing };
}
