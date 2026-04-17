import { Suspense } from "react";
import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog, initI18nForPage } from "@/lib/i18n";
import {
  getCompanyBySlug,
  getSimilarCompanies,
} from "@/lib/actions/company";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { CompanyHead } from "./company-head";
import { CompanyContent } from "./company-content";
import { SimilarSection } from "./similar-section";

export const revalidate = 600; // ISR: cache metadata for 10 minutes

type Props = {
  params: Promise<{ lang: string; slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug, lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const [company, { i18n }] = await Promise.all([
    getCompanyBySlug(slug, locale),
    loadCatalog(locale),
  ]);
  if (!company) return {};

  const title = i18n._({
    id: "company.meta.title",
    message: "Jobs at {name}",
    values: { name: company.name },
  });
  const count = company.activeJobCount;
  const countText = count > 0
    ? i18n._({
        id: "company.meta.positionCount",
        message: "{count, plural, one {# open position} other {# open positions}}",
        values: { count },
      })
    : i18n._({ id: "company.meta.openPositions", message: "Open positions" });
  const description = company.description
    ? i18n._({
        id: "company.meta.descriptionWithInfo",
        message: "{countText} at {name}. {description}",
        values: { countText, name: company.name, description: company.description },
      })
    : i18n._({
        id: "company.meta.descriptionBasic",
        message: "{countText} at {name}",
        values: { countText, name: company.name },
      });
  const path = `/company/${slug}`;

  return {
    title,
    description,
    alternates: buildAlternates(path, locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}${path}`,
      type: "website",
    },
  };
}

async function CompanyNotFound() {
  const { i18n } = await loadCatalog(defaultLocale);
  const title = i18n._({
    id: "company.notFound.title",
    comment: "Heading shown when a company page slug does not exist",
    message: "Company not found",
  });
  const message = i18n._({
    id: "company.notFound.message",
    comment: "Body text shown when a company page slug does not exist",
    message: "The company you are looking for does not exist or has been removed.",
  });
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">{title}</h1>
      <p className="mt-2 text-muted">{message}</p>
    </div>
  );
}

export default async function CompanyPageRoute({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { slug } = await params;
  const sp = await searchParams;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return <CompanyNotFound />;

  // Fire-and-hold the similar-companies query. We do NOT `await` it
  // here — the head + postings list must stream to the browser
  // without waiting for Typesense. The Promise is unwrapped inside
  // <Suspense>; if Typesense is slow the rest of the page is already
  // visible by then.
  const similarPromise = getSimilarCompanies(company.id, company.industryId, {
    searchParams: sp,
    locale,
  });

  return (
    <div className="space-y-4">
      <CompanyHead
        company={company}
        locale={locale}
        backSearchParams={sp}
      />
      <Suspense fallback={null}>
        <SimilarSection
          promise={similarPromise}
          companyId={company.id}
          industryId={company.industryId}
          locale={locale}
        />
      </Suspense>
      <CompanyContent locale={locale} slug={slug} />
    </div>
  );
}
