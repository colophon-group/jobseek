import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { LicenseContent } from "@/components/LicenseContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "license.meta.title", message: "License" });
  const description = i18n.t({
    id: "license.meta.description",
    message: "Licensing terms for Job Seek application code (MIT) and job data (CC BY-NC 4.0).",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}/license` },
  };
}

export default async function LicensePage({ params }: Props) {
  await initI18nForPage(params);
  return <LicenseContent />;
}
