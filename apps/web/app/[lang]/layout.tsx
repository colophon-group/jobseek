import { cookies } from "next/headers";
import { setI18n } from "@lingui/react/server";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { ThemeProvider } from "@/components/ThemeProvider";
import { type Locale, isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { siteConfig } from "@/content/config";
import "../globals.css";

export const metadata: Metadata = {
  title: { template: "%s | Job Seek", default: "Job Seek" },
  metadataBase: new URL(siteConfig.url),
  openGraph: {
    type: "website",
    siteName: "Job Seek",
    locale: "en",
  },
  twitter: { card: "summary" },
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

  const cookieStore = await cookies();
  const themeCookie = cookieStore.get("theme")?.value;
  const initialTheme = themeCookie === "light" ? "light" : "dark";

  return (
    <html lang={locale} className={initialTheme === "dark" ? "dark" : undefined}>
      <body>
        <ThemeProvider initialTheme={initialTheme}>
          <LinguiClientProvider locale={locale} messages={messages}>
            {children}
          </LinguiClientProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
