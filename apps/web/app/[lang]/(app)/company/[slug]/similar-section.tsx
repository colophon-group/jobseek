"use client";

import { SimilarCompaniesStrip } from "@/components/company/similar-companies-strip";
import type { SimilarCompaniesPage } from "@/lib/actions/company";
import type { Locale } from "@/lib/i18n";

type Props = {
  companyId: string;
  industryId: number | null;
  initialPage?: SimilarCompaniesPage;
  locale: Locale;
};

/**
 * Client wrapper for the filter-aware strip. Page zero arrives in the cached
 * company route snapshot, then refreshes browser-direct from Typesense so a
 * no-filter visit does not need a mount-time Server Action. The client still
 * owns URL filter changes and pagination, keeping `searchParams` / `headers()`
 * / `getSession()` out of the cached route render path.
 */
export function SimilarSection({
  companyId,
  industryId,
  initialPage,
  locale,
}: Props) {
  if (industryId == null) return null;
  return (
    <SimilarCompaniesStrip
      companyId={companyId}
      industryId={industryId}
      initialCompanies={initialPage?.companies ?? []}
      initialHasMore={initialPage?.hasMore ?? false}
      initialTruncated={initialPage?.truncated ?? false}
      locale={locale}
    />
  );
}
