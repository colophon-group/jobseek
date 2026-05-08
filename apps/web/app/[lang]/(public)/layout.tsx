import type { ReactNode } from "react";
import { Trans } from "@lingui/react/macro";
import { defaultLocale, isLocale, type Locale } from "@/lib/i18n";
import { HeaderShell } from "@/components/HeaderShell";
import { Footer } from "@/components/Footer";

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
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only fixed top-2 left-2 z-[100] rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-contrast focus:outline-none"
      >
        <Trans id="common.a11y.skipToContent" comment="Skip to main content link for keyboard users">Skip to content</Trans>
      </a>
      <div className="fixed top-0 right-0 left-0 z-50">
        <HeaderShell />
      </div>
      <div id="main-content" className="pt-12">{children}</div>
      <Footer lang={locale} />
    </>
  );
}
