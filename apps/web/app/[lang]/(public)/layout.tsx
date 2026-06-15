import type { ReactNode } from "react";
import { defaultLocale, isLocale, type Locale } from "@/lib/i18n";
import { HeaderShell } from "@/components/HeaderShell";
import { Footer } from "@/components/Footer";
import { SkipToContentLink } from "@/components/SkipToContentLink";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

// i18n is initialized once in the parent `[lang]/layout.tsx` (loadCatalog +
// setI18n + <LinguiClientProvider>); this layout only resolves `locale` for
// the <Footer lang={locale}> prop. See #2883.
export default async function PublicLayout({ children, params }: Props) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;

  return (
    <>
      <SkipToContentLink />
      <div className="fixed top-0 right-0 left-0 z-50">
        <HeaderShell />
      </div>
      <div id="main-content" className="pt-12">{children}</div>
      <Footer lang={locale} />
    </>
  );
}
