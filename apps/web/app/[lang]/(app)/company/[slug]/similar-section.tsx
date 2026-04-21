"use client";

import { SimilarCompaniesStrip } from "@/components/company/similar-companies-strip";
import type { Locale } from "@/lib/i18n";

type Props = {
  companyId: string;
  industryId: number | null;
  locale: Locale;
};

/**
 * Client wrapper that mounts the similar-companies strip with empty
 * initial state. The strip fetches page 0 on mount via a server
 * action; rendering it client-side keeps the parent company page
 * fully statically prerenderable (no `searchParams` / `headers()` /
 * `getSession()` reads on the server render path).
 */
export function SimilarSection({ companyId, industryId, locale }: Props) {
  if (industryId == null) return null;
  return (
    <SimilarCompaniesStrip
      companyId={companyId}
      industryId={industryId}
      initialCompanies={[]}
      initialHasMore={true}
      initialTruncated={false}
      locale={locale}
    />
  );
}
