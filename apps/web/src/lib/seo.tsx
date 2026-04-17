import { siteConfig } from "@/content/config";
import { locales, type Locale } from "@/lib/i18n";

/**
 * Build hreflang alternates for Next.js Metadata API.
 * Returns canonical URL (without query params) and language alternates including x-default.
 */
export function buildAlternates(path: string, currentLocale: Locale) {
  const languages: Record<string, string> = {};
  for (const locale of locales) {
    languages[locale] = `${siteConfig.url}/${locale}${path}`;
  }
  languages["x-default"] = `${siteConfig.url}/en${path}`;
  return {
    canonical: `${siteConfig.url}/${currentLocale}${path}`,
    languages,
  };
}

/**
 * Fields consumed by `buildOrganizationJsonLd`. Defined as a `Pick` of
 * `CompanyDetail` so the two cannot drift silently.
 */
export type OrganizationJsonLdInput = {
  name: string;
  slug: string;
  description: string | null;
  logo: string | null;
  website: string | null;
  industryName: string | null;
  employeeCountRange: number | null;
  foundedYear: number | null;
};

const EMPLOYEE_RANGE_BOUNDS: Record<number, { min: number; max: number | null }> = {
  1: { min: 1, max: 10 },
  2: { min: 11, max: 50 },
  3: { min: 51, max: 200 },
  4: { min: 201, max: 500 },
  5: { min: 501, max: 1000 },
  6: { min: 1001, max: 5000 },
  7: { min: 5001, max: 10_000 },
  8: { min: 10_001, max: null },
};

/**
 * Only accept http(s) URLs. Anything else (javascript:, data:, malformed)
 * returns null so the JSON-LD `sameAs` doesn't leak a bogus link.
 */
function safeHttpUrl(input: string | null): string | null {
  if (!input) return null;
  try {
    const u = new URL(input);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/**
 * Build schema.org Organization payload for a company page.
 * `url` is the canonical jseek profile URL; `sameAs` points at the real website.
 */
export function buildOrganizationJsonLd(
  company: OrganizationJsonLdInput,
  locale: Locale,
): Record<string, unknown> {
  const data: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: company.name,
    url: `${siteConfig.url}/${locale}/company/${company.slug}`,
  };
  const description = company.description?.trim();
  if (description) data.description = description;
  const logo = safeHttpUrl(company.logo);
  if (logo) data.logo = logo;
  const website = safeHttpUrl(company.website);
  if (website) data.sameAs = [website];
  if (company.industryName) data.industry = company.industryName;
  // Use `!= null` so year 0 (rare but valid in some historical datasets)
  // survives; also emit ISO-8601 so validators accept it.
  if (company.foundedYear != null) {
    data.foundingDate = `${String(company.foundedYear).padStart(4, "0")}-01-01`;
  }
  if (company.employeeCountRange != null) {
    const bounds = EMPLOYEE_RANGE_BOUNDS[company.employeeCountRange];
    if (bounds) {
      data.numberOfEmployees = bounds.max === null
        ? { "@type": "QuantitativeValue", minValue: bounds.min }
        : { "@type": "QuantitativeValue", minValue: bounds.min, maxValue: bounds.max };
    }
  }
  return data;
}

/**
 * Build schema.org BreadcrumbList payload.
 * Each trail item is `{ name, path }` where `path` is relative to `siteConfig.url`
 * (without locale prefix — we add it here). Caller is responsible for
 * ordering the trail from root to current page.
 */
export function buildBreadcrumbJsonLd(
  trail: { name: string; path: string }[],
  locale: Locale,
): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: trail.map((item, idx) => ({
      "@type": "ListItem",
      position: idx + 1,
      name: item.name,
      item: `${siteConfig.url}/${locale}${item.path}`,
    })),
  };
}

/**
 * Render JSON-LD structured data as a sanitized <script> tag.
 *
 * Security: escapes `<` to `\u003c` so a `</script>` inside any string
 * value cannot close the script tag. `JSON.stringify` itself already
 * escapes `"` and `\`, which are the only other characters that could
 * break out of the JSON string context. The CDATA `]]>` and HTML
 * comment `<!--` sequences are not meaningful in a <script> body.
 *
 * Robustness: swallows `JSON.stringify` failures (circular refs, BigInt)
 * and skips rendering rather than crashing the page render.
 */
export function JsonLd({ data }: { data: Record<string, unknown> }) {
  let json: string;
  try {
    json = JSON.stringify(data).replace(/</g, "\\u003c");
  } catch (err) {
    console.error("[JsonLd] failed to serialise payload", err);
    return null;
  }
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: json }}
    />
  );
}

/**
 * Map numeric employee_count_range code to a display string.
 * Used by CompanyHead's visible meta row (not by JSON-LD — that uses
 * the QuantitativeValue bounds directly).
 */
export function formatEmployeeCount(range: number | null): string | null {
  if (range === null) return null;
  const bounds = EMPLOYEE_RANGE_BOUNDS[range];
  if (!bounds) return null;
  return bounds.max === null ? `${bounds.min}+` : `${bounds.min}-${bounds.max}`;
}
