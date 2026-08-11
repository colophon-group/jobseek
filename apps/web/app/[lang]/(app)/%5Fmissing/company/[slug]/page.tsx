import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { CompanyNotFoundState } from "../../../company/[slug]/company-not-found";

export const metadata: Metadata = {
  robots: { index: false, follow: true },
};

type Props = {
  params: Promise<{ lang: string; slug: string }>;
};

export default async function MissingCompanyPage({ params }: Props) {
  const { lang, slug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  return (
    <CompanyNotFoundState
      locale={locale}
      slug={slug}
      title={i18n._({
        id: "company.notFound.title",
        comment: "Heading shown when the company URL slug doesn't resolve to a known company",
        message: "Company not found",
      })}
      message={i18n._({
        id: "company.notFound.body",
        comment: "Body text for the company-not-found page; explains the company is either gone or never existed",
        message: "The company you are looking for does not exist or has been removed.",
      })}
      exploreLabel={i18n._({
        id: "company.notFound.explore",
        comment: "Primary recovery action on the company-not-found page",
        message: "Explore companies",
      })}
      requestLabel={i18n._({
        id: "company.notFound.request",
        comment: "Secondary action on the company-not-found page to request that company",
        message: "Request this company",
      })}
    />
  );
}
