import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n._({ id: "about.meta.title", message: "About" });
  const description = i18n._({
    id: "about.meta.description",
    message: "About Job Seek — the job search engine that scrapes career pages directly.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/about", locale),
    openGraph: { title, description, url: `${siteConfig.url}/${locale}/about` },
  };
}

export default async function AboutPage({ params }: Props) {
  await initI18nForPage(params);

  return (
    <div className="mx-auto max-w-[720px] px-4 py-16">
      <h1 className="text-3xl font-bold">About</h1>
      <p className="mt-4 text-muted">Coming soon.</p>
    </div>
  );
}
