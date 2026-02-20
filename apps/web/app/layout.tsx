import { cookies } from "next/headers";
import { StackProvider, StackTheme } from "@stackframe/stack";
import type { Metadata } from "next";
import type { ReactNode } from "react";
import { SpeedInsights } from "@vercel/speed-insights/next";
import { ThemeProvider } from "@/components/ThemeProvider";
import { stackServerApp } from "@/stack/server";
import { siteConfig } from "@/content/config";
import "./globals.css";

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

const stackLangMap = {
  en: "en-US",
  de: "de-DE",
  fr: "fr-FR",
  it: "it-IT",
} as const;

export default async function RootLayout({ children }: { children: ReactNode }) {
  const cookieStore = await cookies();
  const themeCookie = cookieStore.get("theme")?.value;
  const initialTheme = themeCookie === "light" ? "light" : "dark";
  const locale = cookieStore.get("locale")?.value ?? "en";
  const stackLang = stackLangMap[locale as keyof typeof stackLangMap] ?? "en-US";

  return (
    <html lang={locale} className={initialTheme === "dark" ? "dark" : undefined}>
      <body>
        <StackProvider app={stackServerApp} lang={stackLang}>
          <StackTheme>
            <ThemeProvider initialTheme={initialTheme}>
              {children}
            </ThemeProvider>
          </StackTheme>
        </StackProvider>
        <SpeedInsights />
      </body>
    </html>
  );
}
