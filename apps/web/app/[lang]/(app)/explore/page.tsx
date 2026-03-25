import { Suspense } from "react";
import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog, initI18nForPage } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { ExploreContent } from "./explore-content";

type Props = {
  params: Promise<{ lang: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "explore.meta.title", message: "Explore Jobs" });
  const description = i18n._({
    id: "explore.meta.description",
    message: "Search jobs across hundreds of companies. Create watchlists to track new openings and get alerts.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/explore", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/explore`,
      type: "website",
    },
  };
}

export default async function AppPage({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const sp = await searchParams;

  return (
    <Suspense fallback={<ExploreSkeleton />}>
      <ExploreContent locale={locale} searchParams={sp} />
    </Suspense>
  );
}
