import { safeExternalError } from "@/lib/safe-external-error";

export interface CompanyDetail {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  logo: string | null;
  website: string | null;
  description: string | null;
  industryId: number | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
  activeJobCount: number;
}

export interface CompanyLookupEnv {
  [key: string]: string | undefined;
  TYPESENSE_HOST?: string;
  TYPESENSE_PORT?: string;
  TYPESENSE_PROTOCOL?: string;
  TYPESENSE_SEARCH_KEY?: string;
}

export interface ResolveCompanyBySlugDeps {
  fetchFromTypesense: (slug: string, locale: string) => Promise<CompanyDetail | null>;
  isTypesenseUnavailableError: (err: unknown) => boolean;
  logger?: Pick<typeof console, "error">;
}

// Canonical company-slug shape: lowercase alphanumeric segments separated
// by single hyphens (mirrors apps/crawler SLUG_RE). The slug reaches here
// from a URL path segment, so a hostile caller could craft a string that
// escapes the Typesense filter clause when raw-interpolated. Reject
// non-conforming slugs up front; null falls through to a regular 404.
const SLUG_SHAPE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export function isSafeCompanySlug(slug: string): boolean {
  return SLUG_SHAPE.test(slug);
}

export function canResolveCompanyBySlugFromEnv(env: CompanyLookupEnv): boolean {
  return Boolean(
    env.TYPESENSE_HOST &&
      env.TYPESENSE_PORT &&
      env.TYPESENSE_PROTOCOL &&
      env.TYPESENSE_SEARCH_KEY,
  );
}

export async function resolveCompanyBySlug(
  slug: string,
  locale: string,
  deps: ResolveCompanyBySlugDeps,
): Promise<CompanyDetail | null> {
  const logger = deps.logger ?? console;
  if (!isSafeCompanySlug(slug)) return null;

  try {
    return await deps.fetchFromTypesense(slug, locale);
  } catch (err) {
    if (!deps.isTypesenseUnavailableError(err)) throw err;
    logger.error(
      "[company] Typesense unavailable; company detail degraded to not found",
      safeExternalError(err, {
        service: "typesense",
        operation: "company_detail_lookup",
      }),
    );
    return null;
  }
}

export function mapTypesenseCompanyHitToDetail(
  hit: Record<string, unknown>,
  slug: string,
  locale: string,
): CompanyDetail {
  const localeKey = (loc: string, base: string): string =>
    loc === "en" ? base : `${base}_${loc}`;
  const pickLocalized = (base: string): string | null => {
    const localized = hit[localeKey(locale, base)];
    if (typeof localized === "string" && localized.length > 0) return localized;
    const en = hit[base];
    return typeof en === "string" && en.length > 0 ? en : null;
  };

  return {
    id: String(hit.id),
    name: String(hit.name ?? ""),
    slug: String(hit.slug ?? slug),
    icon: typeof hit.icon === "string" ? hit.icon : null,
    logo: typeof hit.logo === "string" ? hit.logo : null,
    website: typeof hit.website === "string" ? hit.website : null,
    description: pickLocalized("description"),
    industryId: typeof hit.industry_id === "number" ? hit.industry_id : null,
    industryName: pickLocalized("industry_name"),
    employeeCountRange:
      typeof hit.employee_count_range === "number" ? hit.employee_count_range : null,
    foundedYear: typeof hit.founded_year === "number" ? hit.founded_year : null,
    activeJobCount: typeof hit.active_posting_count === "number" ? hit.active_posting_count : 0,
  };
}
