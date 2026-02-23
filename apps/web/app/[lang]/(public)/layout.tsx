import type { ReactNode } from "react";
import { Trans } from "@lingui/react/macro";
import { initI18nForPage } from "@/lib/i18n";
import { HeaderShell } from "@/components/HeaderShell";
import { Footer } from "@/components/Footer";

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

export default async function PublicLayout({ children, params }: Props) {
  const locale = await initI18nForPage(params);

  return (
    <>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only fixed top-2 left-2 z-[100] rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-contrast focus:outline-none"
      >
        <Trans id="common.a11y.skipToContent" comment="Skip to main content link for keyboard users">Skip to content</Trans>
      </a>
      <HeaderShell />
      <div id="main-content">{children}</div>
      <Footer lang={locale} />
    </>
  );
}
