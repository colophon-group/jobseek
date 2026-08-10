import type { SearchResultCompany } from "./types";

export interface TypesenseCompanyDocument {
  id: string;
  name: string;
  slug: string;
  icon?: string;
  active_posting_count: number;
  year_posting_count: number;
}

export interface TypesensePostingCompanyFields {
  company_id: string;
  company_name: string;
  company_slug: string;
  company_icon?: string;
}

function nonBlank(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Resolve company identity for a grouped posting result.
 *
 * The company collection is the canonical source. Posting documents carry a
 * denormalized copy for search, but a newly synced company can briefly be
 * absent from the exporter's in-memory map. Older exporters wrote empty
 * company_name/company_slug values in that window. Never let an arbitrary
 * first posting turn that valid company into a blank card or `/company` URL.
 */
export function resolveTypesenseCompany(
  companyId: string,
  postingDocuments: readonly TypesensePostingCompanyFields[],
  canonical?: TypesenseCompanyDocument,
): SearchResultCompany["company"] | null {
  const id = nonBlank(companyId);
  if (!id) return null;

  const canonicalName = nonBlank(canonical?.name);
  const canonicalSlug = nonBlank(canonical?.slug);
  const embedded = postingDocuments.find(
    (document) =>
      nonBlank(document.company_name) !== null &&
      nonBlank(document.company_slug) !== null,
  );

  const name = canonicalName ?? nonBlank(embedded?.company_name);
  const slug = canonicalSlug ?? nonBlank(embedded?.company_slug);
  if (!name || !slug) return null;

  return {
    id,
    name,
    slug,
    icon:
      nonBlank(canonical?.icon) ??
      nonBlank(embedded?.company_icon) ??
      null,
  };
}
