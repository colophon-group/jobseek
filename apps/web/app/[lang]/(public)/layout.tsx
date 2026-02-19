import type { ReactNode } from "react";
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
      <HeaderShell />
      {children}
      <Footer lang={locale} />
    </>
  );
}
