import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { fetchExploreDefaults } from "@/lib/actions/explore-data";
import { ExploreContent } from "./explore-content";

// ISR: prerender per locale and revalidate every 60s. The actual
// data is fetched server-side via ``fetchExploreDefaults`` (the
// anonymous, no-filter case) and embedded in the prerendered HTML
// as ``initialData``. ExploreContent re-fetches a personalized
// variant client-side only when the ``logged_in`` hint cookie or a
// filter searchParam is present — anonymous no-filter visitors
// get a pure CDN cache hit with no Vercel function invocation.
//
// IMPORTANT: do NOT add ``searchParams`` to Props or read
// ``headers()`` / ``cookies()`` here. The ISR test guard at
// ``apps/web/app/__tests__/isr-routes.test.ts`` enforces this — see
// #2640 + #2243.
export const revalidate = 60;

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "explore.meta.title", message: "Explore Jobs" });
  const description = i18n._({
    id: "explore.meta.description",
    message: "Search jobs across thousands of companies scraped directly from career pages. Filter by seniority, tech stack, salary, and location — then save watchlists and get alerts.",
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

export default async function AppPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const initialData = await fetchExploreDefaults({ locale });

  return <ExploreContent locale={locale} initialData={initialData} />;
}
