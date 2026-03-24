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
 * Render JSON-LD structured data as a sanitized <script> tag.
 * Replaces `<` with `\u003c` to prevent XSS in embedded JSON.
 */
export function JsonLd({ data }: { data: Record<string, unknown> }) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: json }}
    />
  );
}

const EMPLOYEE_RANGES: Record<number, string> = {
  1: "1-10",
  2: "11-50",
  3: "51-200",
  4: "201-500",
  5: "501-1000",
  6: "1001-5000",
  7: "5001-10000",
  8: "10001+",
};

/**
 * Map numeric employee_count_range code to a display string for JSON-LD.
 */
export function formatEmployeeCount(range: number | null): string | null {
  if (range === null) return null;
  return EMPLOYEE_RANGES[range] ?? null;
}
