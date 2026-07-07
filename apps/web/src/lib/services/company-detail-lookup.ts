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
  DATABASE_URL?: string;
  TYPESENSE_HOST?: string;
  TYPESENSE_PORT?: string;
  TYPESENSE_PROTOCOL?: string;
  TYPESENSE_SEARCH_KEY?: string;
}

export interface ResolveCompanyBySlugDeps {
  fetchFromTypesense: (slug: string, locale: string) => Promise<CompanyDetail | null>;
  fetchFromPostgres: (slug: string, locale: string) => Promise<CompanyDetail | null>;
  hasPostgresConfig: () => boolean;
  isTypesenseUnavailableError: (err: unknown) => boolean;
  logger?: Pick<typeof console, "error" | "warn">;
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
    env.DATABASE_URL ||
      (
        env.TYPESENSE_HOST &&
        env.TYPESENSE_PORT &&
        env.TYPESENSE_PROTOCOL &&
        env.TYPESENSE_SEARCH_KEY
      ),
  );
}

export async function resolveCompanyBySlug(
  slug: string,
  locale: string,
  deps: ResolveCompanyBySlugDeps,
): Promise<CompanyDetail | null> {
  const logger = deps.logger ?? console;
  let typesenseError: unknown;

  // Primary path: Typesense. Falls back to Postgres on either error or 0 hits
  // so brand-new companies (whose Typesense upsert lagged the latest sync)
  // still render. Bot traffic to nonexistent slugs pays the Postgres cost
  // (a cheap PK lookup on company.slug); cache layer above prevents
  // poisoning by not storing nulls.
  if (isSafeCompanySlug(slug)) {
    try {
      const fromTypesense = await deps.fetchFromTypesense(slug, locale);
      if (fromTypesense) return fromTypesense;
    } catch (err) {
      typesenseError = err;
    }
  }

  if (typesenseError && !deps.isTypesenseUnavailableError(typesenseError)) {
    throw typesenseError;
  }
  if (!deps.hasPostgresConfig()) {
    if (typesenseError) {
      logger.error("[company] Typesense failed and Postgres fallback is unavailable", typesenseError);
    }
    logger.warn("[company] Postgres fallback skipped because DATABASE_URL is not configured");
    return null;
  }
  if (typesenseError) {
    logger.error("[company] Typesense failed, falling back to Postgres", typesenseError);
  }
  return deps.fetchFromPostgres(slug, locale);
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

export interface PostgresCompanyRow {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
  logo: string | null;
  website: string | null;
  description: string | null;
  industry_id: number | null;
  industry_name: string | null;
  employee_count_range: number | null;
  founded_year: number | null;
}

export function mapPostgresCompanyRowToDetail(row: PostgresCompanyRow): CompanyDetail {
  return {
    id: row.id,
    name: row.name,
    slug: row.slug,
    icon: row.icon,
    logo: row.logo,
    website: row.website,
    description: row.description,
    industryId: row.industry_id,
    industryName: row.industry_name,
    employeeCountRange: row.employee_count_range,
    foundedYear: row.founded_year,
    // Postgres fallback skips the active count (the only Typesense-only fact).
    // Effect on the page: header strip shows "0 open positions" until Typesense
    // recovers; the postings list itself comes from a separate Typesense call
    // and already degrades independently.
    activeJobCount: 0,
  };
}
