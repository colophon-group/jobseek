import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { getCompanyBySlug } from "@/lib/actions/company";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { CompanyContent } from "./company-content";

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

export default async function CompanyPageRoute({ params }: Props) {
  const { lang, slug } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;

  return <CompanyContent locale={locale} slug={slug} />;
}
