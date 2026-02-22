import type { Metadata } from "next";
import { initI18nForPage, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { PrivacyPolicyContent } from "@/components/PrivacyPolicyContent";

type Props = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "privacy.meta.title", message: "Privacy Policy" });
  const description = i18n.t({
    id: "privacy.meta.description",
    message: "How Job Seek collects, uses, and protects your personal data.",
  });

  return {
    title,
    description,
    openGraph: { title, description, url: `/${locale}/privacy-policy` },
  };
}

export default async function PrivacyPolicyPage({ params }: Props) {
  await initI18nForPage(params);
  return <PrivacyPolicyContent />;
}
