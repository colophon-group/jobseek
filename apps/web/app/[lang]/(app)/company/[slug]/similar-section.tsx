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
 * Globally ranked peers load directly from Typesense, independent of the
 * posting filters. A server snapshot is supplied only when browser-direct
 * search is disabled. Company facts remain in the cached route.
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
      key={`${companyId}:${industryId}`}
      companyId={companyId}
      industryId={industryId}
      initialCompanies={initialPage?.companies ?? []}
      initialHasMore={initialPage?.hasMore ?? false}
      initialTruncated={initialPage?.truncated ?? false}
      locale={locale}
    />
  );
}
