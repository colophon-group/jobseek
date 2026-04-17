import { SimilarCompaniesStrip } from "@/components/company/similar-companies-strip";
import type { SimilarCompaniesPage } from "@/lib/actions/company";
import type { Locale } from "@/lib/i18n";

type Props = {
  /**
   * Unawaited promise. Rendered under <Suspense> so the rest of the
   * company page (head + postings list) streams without waiting for
   * Typesense. Resolving a `hasMore=false` empty result silently hides
   * the section, matching the behaviour for companies with no
   * industry or no peers.
   */
  promise: Promise<SimilarCompaniesPage>;
  companyId: string;
  industryId: number | null;
  locale: Locale;
};

export async function SimilarSection({ promise, companyId, industryId, locale }: Props) {
  const { companies, hasMore, truncated } = await promise;
  if (companies.length === 0) return null;
  return (
    <>
      <hr className="border-divider" />
      <SimilarCompaniesStrip
        companyId={companyId}
        industryId={industryId}
        initialCompanies={companies}
        initialHasMore={hasMore}
        initialTruncated={truncated ?? false}
        locale={locale}
      />
    </>
  );
}
