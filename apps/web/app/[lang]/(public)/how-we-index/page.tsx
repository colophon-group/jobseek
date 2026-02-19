import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { HowWeIndexContent } from "@/components/HowWeIndexContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "indexing.meta.title", message: "How We Index" });
  const description = i18n.t({
    id: "indexing.meta.description",
    message: "How Job Seek discovers, crawls, and indexes job postings — and the safeguards we follow.",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}/how-we-index` },
  };
}

export default async function HowWeIndexPage({ params }: Props) {
  await initI18nForPage(params);
  return <HowWeIndexContent />;
}
