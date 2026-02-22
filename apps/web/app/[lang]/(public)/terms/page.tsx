import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { TermsContent } from "@/components/TermsContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "terms.meta.title", message: "Terms of Service" });
  const description = i18n.t({
    id: "terms.meta.description",
    message: "Terms of Service for the Job Seek application.",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}/terms` },
  };
}

export default async function TermsPage({ params }: Props) {
  await initI18nForPage(params);
  return <TermsContent />;
}
