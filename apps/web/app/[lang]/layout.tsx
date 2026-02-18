import { setI18n } from "@lingui/react/server";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { type Locale, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "Jobseek",
  description: "Find your next opportunity",
};

type Props = {
  children: ReactNode;
  params: Promise<{ lang: string }>;
};

export default async function RootLayout({ children, params }: Props) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n, messages } = await loadCatalog(locale);
  setI18n(i18n);

  return (
    <html lang={locale}>
      <body>
        <LinguiClientProvider locale={locale} messages={messages}>
          {children}
        </LinguiClientProvider>
      </body>
    </html>
  );
}
